from __future__ import annotations

from enum import Enum, auto
from typing import TYPE_CHECKING

from ..bus.messages import OutboundMessage
from ..memory.short_term import ShortTermMemory
from ..proactive.events import CONVERSATION_COMPLETED

if TYPE_CHECKING:
    from .runtime import AgentRuntime
    from .turn_context import TurnContext


class TurnState(Enum):
    """定义单轮 Agent 执行的 5 个工作状态。"""

    PREPARE = auto()
    RUN = auto()
    SAVE = auto()
    COMPACT = auto()
    RESPOND = auto()


_TRANSITIONS: dict[tuple[TurnState, str], TurnState | None] = {
    (TurnState.PREPARE, "ok"): TurnState.RUN,
    (TurnState.RUN, "ok"): TurnState.SAVE,
    (TurnState.SAVE, "ok"): TurnState.COMPACT,
    (TurnState.COMPACT, "ok"): TurnState.RESPOND,
    (TurnState.RESPOND, "ok"): None,
}


def next_state(state: TurnState, event: str) -> TurnState | None:
    """根据当前状态和事件返回下一状态。"""
    if event == "error":
        return TurnState.RESPOND if state is not TurnState.RESPOND else None
    return _TRANSITIONS[(state, event)]


async def run_state(runtime: AgentRuntime, ctx: TurnContext) -> str:
    """分发当前状态处理函数。"""
    if ctx.state is TurnState.PREPARE:
        return await prepare(runtime, ctx)
    if ctx.state is TurnState.RUN:
        return await run(runtime, ctx)
    if ctx.state is TurnState.SAVE:
        return await save(runtime, ctx)
    if ctx.state is TurnState.COMPACT:
        return await compact(runtime, ctx)
    return await respond(ctx)


async def prepare(runtime: AgentRuntime, ctx: TurnContext) -> str:
    """加载会话、处理命令并构建上下文。"""
    msg = ctx.inbound
    await runtime.sessions.cleanup_expired_sessions(runtime.settings.sessions.retention_days)
    ctx.shortcut_response = await runtime.sessions.handle_inbound_command(msg.session_id, msg)
    if ctx.shortcut_response is not None:
        return "ok"
    session = await runtime.sessions.load_or_create(msg.session_id, msg.user_id, msg.channel)
    ctx.session = session

    all_history = await runtime.sessions.all_messages(session.id)
    compacted_count = int(session.metadata.get("compacted_message_count", 0))
    ctx.history = all_history[compacted_count:]
    ctx.model_messages = runtime.context_builder.build(
        summary=session.summary,
        history=ctx.history,
        user_content=msg.content,
        tool_schemas=runtime.agent_loop.tools.schemas(),
        profile_memory=runtime.profile_memory.read(),
    )
    return "ok"


async def run(runtime: AgentRuntime, ctx: TurnContext) -> str:
    """执行 shortcut 或 AgentLoop。"""
    if ctx.shortcut_response is not None:
        ctx.final_content = ctx.shortcut_response
        return "ok"
    result = await runtime.agent_loop.run(ctx.model_messages)
    ctx.final_content = result.final_content
    ctx.tool_calls = result.tool_calls
    return "ok"


async def save(runtime: AgentRuntime, ctx: TurnContext) -> str:
    """保存消息、trace、token，并触发主动事件。"""
    session_id = ctx.inbound.session_id
    if ctx.shortcut_response is not None:
        ctx.saved_trace_count = len(ctx.trace)
        return "ok"
    previous_total = int((ctx.session.metadata if ctx.session else {}).get("session_total_tokens", 0))
    input_tokens = runtime.token_monitor.record(ctx.inbound.content, "", False, 0)["input_tokens"]
    output_tokens = runtime.token_monitor.record("", ctx.final_content, False, 0)["output_tokens"]
    await runtime.sessions.save_user_message(session_id, ctx.inbound.content, input_tokens)
    await runtime.sessions.save_assistant_message(session_id, ctx.final_content, output_tokens)
    ctx.compact_stats = await build_compaction_stats(runtime, session_id)
    ctx.should_compact = bool(ctx.compact_stats["should_compact"])
    usage = runtime.token_monitor.record(
        ctx.inbound.content,
        ctx.final_content,
        ctx.should_compact,
        previous_total,
    )
    usage.update(
        {
            "compacted_message_count": 0,
            "compacted_token_count": 0,
            "raw_window_message_count": 0,
            "raw_window_token_count": 0,
        }
    )
    ctx.token_usage = usage
    await runtime.sessions.store.update_metadata(
        session_id,
        {"session_total_tokens": usage["total_tokens"]},
    )
    for trace in ctx.trace:
        await runtime.sessions.store.save_trace(trace)
    ctx.saved_trace_count = len(ctx.trace)
    await runtime.proactive.emit(CONVERSATION_COMPLETED, {"session_id": session_id, "turn_id": ctx.turn_id})
    return "ok"


async def compact(runtime: AgentRuntime, ctx: TurnContext) -> str:
    """在消息落盘后执行短期记忆压缩。"""
    if ctx.shortcut_response is not None:
        return "ok"
    if ctx.session is None:
        await runtime.sessions.store.save_token_usage(ctx.turn_id, ctx.inbound.session_id, ctx.token_usage)
        return "ok"
    if not ctx.compact_stats:
        ctx.compact_stats = await build_compaction_stats(runtime, ctx.session.id)
    if not ctx.should_compact:
        ctx.token_usage.update(
            {
                "compacted": 0,
                "compacted_message_count": 0,
                "compacted_token_count": 0,
                "raw_window_message_count": int(ctx.compact_stats["raw_window_message_count"]),
                "raw_window_token_count": int(ctx.compact_stats["raw_window_token_count"]),
            }
        )
        await runtime.sessions.store.save_token_usage(ctx.turn_id, ctx.inbound.session_id, ctx.token_usage)
        return "ok"
    memory = ShortTermMemory(
        compact_token_threshold=runtime.settings.memory.compact_token_threshold,
        raw_window_token_limit=runtime.settings.memory.raw_window_token_limit,
    )
    all_history = await runtime.sessions.all_messages(ctx.session.id)
    compacted_count = int(ctx.session.metadata.get("compacted_message_count", 0))
    uncompacted_history = all_history[compacted_count:]
    recent_history = memory.recent_window(uncompacted_history)
    compact_source = uncompacted_history[: len(uncompacted_history) - len(recent_history)]
    compact_until = compacted_count + len(compact_source)
    if not compact_source:
        ctx.token_usage.update(
            {
                "compacted": 0,
                "compacted_message_count": 0,
                "compacted_token_count": 0,
                "raw_window_message_count": int(ctx.compact_stats["raw_window_message_count"]),
                "raw_window_token_count": int(ctx.compact_stats["raw_window_token_count"]),
            }
        )
        await runtime.sessions.store.save_token_usage(ctx.turn_id, ctx.inbound.session_id, ctx.token_usage)
        return "ok"
    summary = memory.compact(ctx.session.summary, compact_source)
    compacted_token_count = memory.count_tokens(compact_source)
    raw_window_token_count = memory.count_tokens(recent_history)
    await runtime.sessions.store.update_summary(ctx.session.id, summary)
    await runtime.sessions.store.update_metadata(
        ctx.session.id,
        {
            "compacted_message_count": compact_until,
        },
    )
    ctx.session.summary = summary
    ctx.session.metadata.update(
        {
            "compacted_message_count": compact_until,
        }
    )
    ctx.compact_stats.update(
        {
            "compacted_message_count": len(compact_source),
            "compacted_token_count": compacted_token_count,
            "raw_window_message_count": len(recent_history),
            "raw_window_token_count": raw_window_token_count,
        }
    )
    ctx.token_usage.update(
        {
            "compacted": 1,
            "compacted_message_count": len(compact_source),
            "compacted_token_count": compacted_token_count,
            "raw_window_message_count": len(recent_history),
            "raw_window_token_count": raw_window_token_count,
        }
    )
    await runtime.sessions.store.save_token_usage(ctx.turn_id, ctx.inbound.session_id, ctx.token_usage)
    return "ok"


async def respond(ctx: TurnContext) -> str:
    """构造出站消息。"""
    ctx.outbound = OutboundMessage.new(ctx.inbound.session_id, ctx.inbound.channel, ctx.final_content)
    return "ok"


async def save_remaining_traces(runtime: AgentRuntime, ctx: TurnContext) -> None:
    """补保存 SAVE 后才产生的状态 trace。"""
    if ctx.shortcut_response is not None:
        return
    for trace in ctx.trace[ctx.saved_trace_count:]:
        await runtime.sessions.store.save_trace(trace)
    ctx.saved_trace_count = len(ctx.trace)


async def build_compaction_stats(runtime: AgentRuntime, session_id: str) -> dict[str, int | bool]:
    """基于保存后的未压缩历史生成压缩统计。"""
    session = await runtime.sessions.store.load_session(session_id)
    if session is None:
        return {
            "should_compact": False,
            "compacted_message_count": 0,
            "compacted_token_count": 0,
            "raw_window_message_count": 0,
            "raw_window_token_count": 0,
        }
    all_history = await runtime.sessions.all_messages(session_id)
    compacted_count = int(session.metadata.get("compacted_message_count", 0))
    memory = ShortTermMemory(
        compact_token_threshold=runtime.settings.memory.compact_token_threshold,
        raw_window_token_limit=runtime.settings.memory.raw_window_token_limit,
    )
    uncompacted_history = all_history[compacted_count:]
    recent_history = memory.recent_window(uncompacted_history)
    compact_source = uncompacted_history[: len(uncompacted_history) - len(recent_history)]
    return {
        "should_compact": memory.should_compact(uncompacted_history),
        "compacted_message_count": len(compact_source),
        "compacted_token_count": memory.count_tokens(compact_source),
        "raw_window_message_count": len(recent_history),
        "raw_window_token_count": memory.count_tokens(recent_history),
    }


def compact_trace_metadata(ctx: TurnContext) -> dict[str, int]:
    """返回 COMPACT 状态需要暴露的最小监控字段。"""
    return {
        "compacted": int(ctx.token_usage.get("compacted", 0)),
        "compacted_message_count": int(ctx.token_usage.get("compacted_message_count", 0)),
        "compacted_token_count": int(ctx.token_usage.get("compacted_token_count", 0)),
        "raw_window_message_count": int(ctx.token_usage.get("raw_window_message_count", 0)),
        "raw_window_token_count": int(ctx.token_usage.get("raw_window_token_count", 0)),
    }

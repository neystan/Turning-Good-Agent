from __future__ import annotations

from enum import Enum, auto
from typing import TYPE_CHECKING
from uuid import uuid4

from ..bus.messages import OutboundMessage, utc_now_iso
from ..context.session_context import build_session_context, count_message_tokens
from ..context.system_prompt import SYSTEM_PROMPT
from ..memory.short_term import ShortTermMemory
from ..proactive.events import CONVERSATION_COMPLETED
from ..sessions.types import MessageRecord
from ..sessions.token_counter import count_content_tokens

if TYPE_CHECKING:
    from .runtime import AgentRuntime
    from .turn_context import TurnContext


class TurnState(Enum):
    """定义单轮 Agent 执行的显式工作状态。"""

    COMMAND = auto()
    SESSION = auto()
    BUILD = auto()
    RUN = auto()
    COMPACT = auto()
    SAVE = auto()
    RESPOND = auto()


_TRANSITIONS: dict[tuple[TurnState, str], TurnState | None] = {
    (TurnState.COMMAND, "ok"): TurnState.SESSION,
    (TurnState.COMMAND, "shortcut"): TurnState.RESPOND,
    (TurnState.SESSION, "ok"): TurnState.BUILD,
    (TurnState.BUILD, "ok"): TurnState.RUN,
    (TurnState.BUILD, "rejected"): TurnState.RESPOND,
    (TurnState.RUN, "ok"): TurnState.COMPACT,
    (TurnState.COMPACT, "ok"): TurnState.SAVE,
    (TurnState.SAVE, "ok"): TurnState.RESPOND,
    (TurnState.RESPOND, "ok"): None,
}


def next_state(state: TurnState, event: str) -> TurnState | None:
    """根据当前状态和事件返回下一状态。"""
    if event == "error":
        return TurnState.RESPOND if state is not TurnState.RESPOND else None
    return _TRANSITIONS[(state, event)]


async def run_state(runtime: AgentRuntime, ctx: TurnContext) -> str:
    """分发当前状态处理函数。"""
    if ctx.state is TurnState.COMMAND:
        return await command(runtime, ctx)
    if ctx.state is TurnState.SESSION:
        return await session(runtime, ctx)
    if ctx.state is TurnState.BUILD:
        return await build(runtime, ctx)
    if ctx.state is TurnState.RUN:
        return await run(runtime, ctx)
    if ctx.state is TurnState.COMPACT:
        return await compact(runtime, ctx)
    if ctx.state is TurnState.SAVE:
        return await save(runtime, ctx)
    return await respond(ctx)


async def command(runtime: AgentRuntime, ctx: TurnContext) -> str:
    """处理 slash command，命中后跳过模型链路。"""
    msg = ctx.inbound
    ctx.shortcut_response = await runtime.sessions.handle_inbound_command(msg.session_id, msg)
    if ctx.shortcut_response is not None:
        ctx.final_content = ctx.shortcut_response
        return "shortcut"
    return "ok"


async def session(runtime: AgentRuntime, ctx: TurnContext) -> str:
    """清理过期会话并加载/创建当前会话。"""
    msg = ctx.inbound
    await runtime.sessions.cleanup_expired_sessions(runtime.settings.sessions.retention_days)
    ctx.session = await runtime.sessions.load_or_create(msg.session_id, msg.user_id, msg.channel)
    return "ok"


async def build(runtime: AgentRuntime, ctx: TurnContext) -> str:
    """构建模型输入上下文。"""
    msg = ctx.inbound
    if ctx.session is None:
        raise RuntimeError("BUILD 缺少已加载的 session。")

    session_context = build_session_context(ctx.session, await runtime.sessions.all_messages(ctx.session.id))
    ctx.full_history = session_context.full_history
    ctx.uncompacted_history = session_context.uncompacted_history
    ctx.history = session_context.uncompacted_history
    if context_token_count(runtime, ctx.session.summary, ctx.uncompacted_history, msg.content) > runtime.settings.runtime.max_context_tokens:
        ctx.error = "上下文过大，已拒绝本轮请求。请先清理会话或提高 max_context_tokens。"
        ctx.final_content = f"请求失败：{ctx.error}"
        return "rejected"
    ctx.model_messages = runtime.context_builder.build(
        summary=session_context.summary,
        history=ctx.uncompacted_history,
        user_content=msg.content,
        tool_schemas=runtime.agent_loop.tools.schemas(),
        profile_memory=runtime.profile_memory.read(),
    )
    return "ok"


async def run(runtime: AgentRuntime, ctx: TurnContext) -> str:
    """执行 AgentLoop。"""
    result = await runtime.agent_loop.run(ctx.model_messages, ctx.on_delta)
    ctx.final_content = result.final_content
    ctx.tool_calls = result.tool_calls
    ctx.llm_usage = result.usage
    return "ok"


async def compact(runtime: AgentRuntime, ctx: TurnContext) -> str:
    """在本轮运行结束后更新短期记忆压缩状态。"""
    if ctx.session is None:
        return "ok"
    uncompacted_history = build_virtual_history(ctx)
    ctx.compact_stats = build_compaction_stats(runtime, ctx.session.summary, uncompacted_history)
    ctx.should_compact = bool(ctx.compact_stats["should_compact"])
    if not ctx.should_compact:
        ctx.session.uncompacted_history = uncompacted_history
        ctx.true_token_usage = await build_true_token_usage(runtime, ctx, compacted=False)
        return "ok"
    memory = ShortTermMemory(
        compact_token_threshold=runtime.settings.memory.compact_token_threshold,
        recent_window_token_limit=runtime.settings.memory.recent_window_token_limit,
    )
    recent_history = memory.recent_window(uncompacted_history)
    compact_source = uncompacted_history[: len(uncompacted_history) - len(recent_history)]
    if not compact_source:
        ctx.session.uncompacted_history = uncompacted_history
        ctx.true_token_usage = await build_true_token_usage(runtime, ctx, compacted=False)
        return "ok"
    print_status("正在压缩会话上下文...")
    summary, summary_usage = await memory.compact(ctx.session.summary, compact_source, runtime.agent_loop.llm)
    ctx.llm_usage = ctx.llm_usage.add(summary_usage)
    compacted_token_count = memory.count_tokens(compact_source)
    raw_window_token_count = memory.count_tokens(recent_history)
    ctx.session.summary = summary
    ctx.session.uncompacted_history = recent_history
    ctx.compact_stats.update(
        {
            "compacted_message_count": len(compact_source),
            "compacted_token_count": compacted_token_count,
            "raw_window_message_count": len(recent_history),
            "raw_window_token_count": raw_window_token_count,
        }
    )
    ctx.true_token_usage = await build_true_token_usage(runtime, ctx, compacted=True)
    print_status(
        (
            "压缩完成："
            f"已压缩 {len(compact_source)} 条消息，"
            f"压缩 {compacted_token_count} tokens，"
            f"保留最近 {raw_window_token_count} tokens 原文。"
        ),
    )
    return "ok"


async def save(runtime: AgentRuntime, ctx: TurnContext) -> str:
    """保存消息、trace、token，并触发主动事件。"""
    session_id = ctx.inbound.session_id
    if ctx.llm_usage is None:
        raise RuntimeError("本轮 LLM 响应缺少 usage，无法写入 true_token_usage.jsonl。")
    if not ctx.true_token_usage:
        raise RuntimeError("本轮缺少 true_token_usage，无法执行 SAVE。")
    if ctx.session is not None:
        ctx.context_tokens = save_context_token_breakdown(runtime, ctx)
    user_record = await runtime.sessions.save_user_message(
        session_id,
        ctx.inbound.content,
        count_content_tokens(ctx.inbound.content),
    )
    assistant_record = await runtime.sessions.save_assistant_message(
        session_id,
        ctx.final_content,
        count_content_tokens(ctx.final_content),
    )
    if ctx.session is not None:
        ctx.session.uncompacted_history = replace_current_turn_records(
            ctx.session.uncompacted_history,
            user_record,
            assistant_record,
        )
        await runtime.sessions.store.update_summary(session_id, ctx.session.summary)
        await runtime.sessions.store.update_uncompacted_history(session_id, ctx.session.uncompacted_history)
    await runtime.sessions.store.save_tool_calls(ctx.turn_id, session_id, ctx.tool_calls)
    await runtime.sessions.store.save_true_token_usage(ctx.turn_id, session_id, ctx.true_token_usage)
    await runtime.proactive.emit(CONVERSATION_COMPLETED, {"session_id": session_id, "turn_id": ctx.turn_id})
    return "ok"


async def respond(ctx: TurnContext) -> str:
    """构造出站消息。"""
    if ctx.error:
        ctx.outbound = OutboundMessage.error(ctx.inbound.session_id, ctx.inbound.channel, ctx.final_content)
    else:
        ctx.outbound = OutboundMessage.completed(ctx.inbound.session_id, ctx.inbound.channel, ctx.final_content)
    return "ok"


async def save_remaining_traces(runtime: AgentRuntime, ctx: TurnContext) -> None:
    """补保存尚未落盘的状态 trace。"""
    if ctx.shortcut_response is not None:
        return
    await runtime.sessions.store.save_turn_traces(ctx.trace[ctx.saved_trace_count:])
    ctx.saved_trace_count = len(ctx.trace)


async def build_true_token_usage(
    runtime: AgentRuntime,
    ctx: TurnContext,
    compacted: bool,
) -> dict[str, int]:
    """用真实 LLM usage 生成当前 turn 的完整 token 记录。"""
    if ctx.llm_usage is None:
        raise RuntimeError("本轮 LLM 响应缺少 usage，无法写入 true_token_usage.jsonl。")
    previous_total = await runtime.sessions.store.last_total_tokens(ctx.inbound.session_id)
    return runtime.token_monitor.record_llm_usage(
        ctx.llm_usage,
        previous_total_tokens=previous_total,
        compacted=compacted,
    )


def build_compaction_stats(
    runtime: AgentRuntime,
    summary: str,
    uncompacted_history: list[MessageRecord],
) -> dict[str, int | bool]:
    """基于压缩阈值和上下文上限生成压缩统计。"""
    memory = ShortTermMemory(
        compact_token_threshold=runtime.settings.memory.compact_token_threshold,
        recent_window_token_limit=runtime.settings.memory.recent_window_token_limit,
    )
    should_compact = memory.should_compact(uncompacted_history) or (
        context_token_count(runtime, summary, uncompacted_history, "") > runtime.settings.runtime.max_context_tokens
    )
    if not should_compact:
        return {
            "should_compact": False,
            "compacted_message_count": 0,
            "compacted_token_count": 0,
            "raw_window_message_count": len(uncompacted_history),
            "raw_window_token_count": memory.count_tokens(uncompacted_history),
        }
    recent_history = memory.recent_window(uncompacted_history)
    compact_source = uncompacted_history[: len(uncompacted_history) - len(recent_history)]
    return {
        "should_compact": True,
        "compacted_message_count": len(compact_source),
        "compacted_token_count": memory.count_tokens(compact_source),
        "raw_window_message_count": len(recent_history),
        "raw_window_token_count": memory.count_tokens(recent_history),
    }


def build_virtual_history(ctx: TurnContext) -> list[MessageRecord]:
    """返回包含本轮 user/assistant 的临时完整历史。"""
    if ctx.llm_usage is None:
        raise RuntimeError("本轮 LLM 响应缺少 usage，无法执行 COMPACT。")
    now = utc_now_iso()
    return [
        *ctx.history,
        MessageRecord(
            id=str(uuid4()),
            session_id=ctx.inbound.session_id,
            role="user",
            content=ctx.inbound.content,
            name=None,
            tool_call_id=None,
            token_count=count_content_tokens(ctx.inbound.content),
            created_at=now,
            metadata={},
        ),
        MessageRecord(
            id=str(uuid4()),
            session_id=ctx.inbound.session_id,
            role="assistant",
            content=ctx.final_content,
            name=None,
            tool_call_id=None,
            token_count=count_content_tokens(ctx.final_content),
            created_at=now,
            metadata={},
        ),
    ]


def replace_current_turn_records(
    history: list[MessageRecord],
    user_record: MessageRecord,
    assistant_record: MessageRecord,
) -> list[MessageRecord]:
    """用真实落盘消息替换本轮临时消息。"""
    if len(history) < 2:
        return history
    tail_user = history[-2]
    tail_assistant = history[-1]
    if (
        tail_user.role == "user"
        and tail_user.content == user_record.content
        and tail_assistant.role == "assistant"
        and tail_assistant.content == assistant_record.content
    ):
        return [*history[:-2], user_record, assistant_record]
    return history


def context_token_count(
    runtime: AgentRuntime,
    summary: str,
    uncompacted_history: list[MessageRecord],
    current_input: str,
) -> int:
    """计算本轮上下文预算权重。"""
    return context_token_breakdown(runtime, summary, uncompacted_history, current_input)["current_context_tokens"]


def context_token_breakdown(
    runtime: AgentRuntime,
    summary: str,
    history: list[MessageRecord],
    current_input: str,
) -> dict[str, int]:
    """返回本轮模型输入上下文 token 分解。"""
    profile_memory = runtime.profile_memory.read()
    tool_schemas = runtime.agent_loop.tools.schemas()
    openai_tools = runtime.agent_loop.tools.openai_tools()
    system_tokens = count_content_tokens(SYSTEM_PROMPT)
    profile_memory_tokens = count_content_tokens(f"长期偏好：{profile_memory}") if profile_memory else 0
    summary_tokens = count_content_tokens(f"会话摘要：{summary}") if summary else 0
    history_tokens = count_message_tokens(history)
    current_input_tokens = count_content_tokens(current_input)
    tool_schema_tokens = 0
    if tool_schemas:
        tool_schema_tokens += count_content_tokens(f"可用工具：{tool_schemas}")
    if openai_tools:
        tool_schema_tokens += count_content_tokens(str(openai_tools))
    current_context_tokens = (
        system_tokens
        + profile_memory_tokens
        + summary_tokens
        + history_tokens
        + current_input_tokens
        + tool_schema_tokens
    )
    return {
        "system_tokens": system_tokens,
        "profile_memory_tokens": profile_memory_tokens,
        "summary_tokens": summary_tokens,
        "history_tokens": history_tokens,
        "current_input_tokens": current_input_tokens,
        "tool_schema_tokens": tool_schema_tokens,
        "current_context_tokens": current_context_tokens,
    }


def save_context_token_breakdown(runtime: AgentRuntime, ctx: TurnContext) -> dict[str, int]:
    """返回 SAVE 状态的最终上下文 token 观测。"""
    if ctx.session is None:
        return {}
    current_turn_in_context = has_current_turn(ctx.session.uncompacted_history, ctx)
    previous_history = (
        history_without_current_turn(ctx.session.uncompacted_history, ctx)
        if current_turn_in_context
        else ctx.session.uncompacted_history
    )
    profile_memory = runtime.profile_memory.read()
    tool_schemas = runtime.agent_loop.tools.schemas()
    openai_tools = runtime.agent_loop.tools.openai_tools()
    system_tokens = count_content_tokens(SYSTEM_PROMPT)
    profile_memory_tokens = count_content_tokens(f"长期偏好：{profile_memory}") if profile_memory else 0
    summary_tokens = count_content_tokens(f"会话摘要：{ctx.session.summary}") if ctx.session.summary else 0
    history_tokens = count_message_tokens(previous_history)
    current_input_tokens = count_content_tokens(ctx.inbound.content)
    output_tokens = count_content_tokens(ctx.final_content)
    tool_schema_tokens = 0
    if tool_schemas:
        tool_schema_tokens += count_content_tokens(f"可用工具：{tool_schemas}")
    if openai_tools:
        tool_schema_tokens += count_content_tokens(str(openai_tools))
    current_context_tokens = (
        system_tokens
        + profile_memory_tokens
        + summary_tokens
        + history_tokens
        + tool_schema_tokens
    )
    if current_turn_in_context:
        current_context_tokens += current_input_tokens + output_tokens
    return {
        "system_tokens": system_tokens,
        "profile_memory_tokens": profile_memory_tokens,
        "summary_tokens": summary_tokens,
        "history_tokens": history_tokens,
        "current_input_tokens": current_input_tokens,
        "output_tokens": output_tokens,
        "tool_schema_tokens": tool_schema_tokens,
        "tool_count": len(ctx.tool_calls),
        "current_context_tokens": current_context_tokens,
    }


def has_current_turn(history: list[MessageRecord], ctx: TurnContext) -> bool:
    """判断最终未压缩历史是否仍包含本轮 user/assistant。"""
    if len(history) < 2:
        return False
    tail_user = history[-2]
    tail_assistant = history[-1]
    return (
        tail_user.role == "user"
        and tail_user.content == ctx.inbound.content
        and tail_assistant.role == "assistant"
        and tail_assistant.content == ctx.final_content
    )


def history_without_current_turn(history: list[MessageRecord], ctx: TurnContext) -> list[MessageRecord]:
    """从历史中移除本轮临时 user/assistant，避免 token 观测重复计数。"""
    if has_current_turn(history, ctx):
        return history[:-2]
    return history


def compact_trace_metadata(ctx: TurnContext) -> dict[str, int]:
    """返回 COMPACT 状态需要暴露的最小监控字段。"""
    return {
        "compacted": int(ctx.true_token_usage.get("compacted", 0)),
        "compacted_message_count": int(ctx.compact_stats.get("compacted_message_count", 0)),
        "compacted_token_count": int(ctx.compact_stats.get("compacted_token_count", 0)),
        "raw_window_message_count": int(ctx.compact_stats.get("raw_window_message_count", 0)),
        "raw_window_token_count": int(ctx.compact_stats.get("raw_window_token_count", 0)),
    }


def print_status(content: str) -> None:
    """直接打印 Runtime 状态提示。"""
    print(f"\n[系统] {content}", flush=True)


def run_trace_metadata(ctx: TurnContext) -> dict[str, int | list[str]]:
    """返回 RUN 状态需要暴露的工具调用统计。"""
    return {
        "tool_call_count": len(ctx.tool_calls),
        "tool_names": [record["tool_name"] for record in ctx.tool_calls],
    }


def save_trace_metadata(ctx: TurnContext) -> dict[str, int]:
    """返回 SAVE 状态的上下文 token 观测。"""
    return dict(ctx.context_tokens)

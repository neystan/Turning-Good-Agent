import time

from ..bus.messages import InboundMessage, OutboundMessage
from ..config.settings import Settings
from ..context.builder import ContextBuilder
from ..llm.client import LLMProvider
from ..memory.long_term import ProfileMemory
from ..memory.short_term import ShortTermMemory
from ..observability.token_monitor import TokenMonitor
from ..observability.trace import StateTrace
from ..proactive.events import CONVERSATION_COMPLETED
from ..proactive.manager import ProactiveManager
from ..sessions.manager import SessionManager
from ..sessions.store import JsonlSessionStore
from ..tools.builtin_tools import EchoTool, NowTool
from ..tools.registry import ToolRegistry
from .agent_loop import AgentLoop
from .state import TurnState, next_state
from .turn_context import TurnContext


class AgentRuntime:
    """驱动单轮消息处理的 6 状态 Runtime。"""

    def __init__(
        self,
        settings: Settings,
        sessions: SessionManager,
        context_builder: ContextBuilder,
        agent_loop: AgentLoop,
        profile_memory: ProfileMemory,
        proactive: ProactiveManager,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.context_builder = context_builder
        self.agent_loop = agent_loop
        self.profile_memory = profile_memory
        self.proactive = proactive
        self.token_monitor = TokenMonitor()
        self.last_trace: list[StateTrace] = []

    @classmethod
    def create_default(cls, settings: Settings, llm: LLMProvider) -> "AgentRuntime":
        """创建 MVP 默认 Runtime。"""
        store = JsonlSessionStore(settings.data_dir)
        sessions = SessionManager(store)
        tools = ToolRegistry()
        tools.register(EchoTool())
        tools.register(NowTool())
        return cls(
            settings=settings,
            sessions=sessions,
            context_builder=ContextBuilder(),
            agent_loop=AgentLoop(llm, tools, settings.runtime),
            profile_memory=ProfileMemory(),
            proactive=ProactiveManager(),
        )

    async def run_turn(self, msg: InboundMessage) -> OutboundMessage:
        """执行一轮消息处理并返回出站消息。"""
        ctx = TurnContext(inbound=msg)
        lock = self.sessions.locks.lock_for(msg.session_id)
        async with lock:
            while ctx.state is not TurnState.DONE:
                started = time.perf_counter()
                try:
                    event = await self._run_state(ctx)
                except Exception as exc:
                    ctx.error = str(exc)
                    event = "error"
                duration_ms = (time.perf_counter() - started) * 1000
                metadata = self._compact_trace_metadata(ctx) if ctx.state is TurnState.COMPACT else {}
                ctx.trace.append(
                    StateTrace(ctx.turn_id, msg.session_id, ctx.state.name, duration_ms, event, ctx.error, metadata)
                )
                ctx.state = next_state(ctx.state, event)
            await self._save_remaining_traces(ctx)
        self.last_trace = ctx.trace
        return ctx.outbound or OutboundMessage.new(msg.session_id, msg.channel, ctx.final_content)

    async def _run_state(self, ctx: TurnContext) -> str:
        """分发当前状态处理函数。"""
        if ctx.state is TurnState.PREPARE:
            return await self._prepare(ctx)
        if ctx.state is TurnState.RUN:
            return await self._run(ctx)
        if ctx.state is TurnState.SAVE:
            return await self._save(ctx)
        if ctx.state is TurnState.COMPACT:
            return await self._compact(ctx)
        if ctx.state is TurnState.RESPOND:
            return await self._respond(ctx)
        return "ok"

    async def _prepare(self, ctx: TurnContext) -> str:
        """加载会话、处理命令并构建上下文。"""
        msg = ctx.inbound
        await self.sessions.cleanup_expired_sessions(self.settings.sessions.retention_days)
        ctx.shortcut_response = await self.sessions.handle_inbound_command(msg.session_id, msg)
        if ctx.shortcut_response is not None:
            return "ok"
        session = await self.sessions.load_or_create(msg.session_id, msg.user_id, msg.channel)
        ctx.session = session

        all_history = await self.sessions.all_messages(session.id)
        compacted_count = int(session.metadata.get("compacted_message_count", 0))
        ctx.history = all_history[compacted_count:]
        ctx.model_messages = self.context_builder.build(
            summary=session.summary,
            history=ctx.history,
            user_content=msg.content,
            tool_schemas=self.agent_loop.tools.schemas(),
            profile_memory=self.profile_memory.read(),
        )
        return "ok"

    async def _run(self, ctx: TurnContext) -> str:
        """执行 shortcut 或 AgentLoop。"""
        if ctx.shortcut_response is not None:
            ctx.final_content = ctx.shortcut_response
            return "ok"
        result = await self.agent_loop.run(ctx.model_messages)
        ctx.final_content = result.final_content
        ctx.tool_calls = result.tool_calls
        return "ok"

    async def _save(self, ctx: TurnContext) -> str:
        """保存消息、trace、token，并触发主动事件。"""
        session_id = ctx.inbound.session_id
        if ctx.shortcut_response is not None:
            ctx.saved_trace_count = len(ctx.trace)
            return "ok"
        previous_total = int((ctx.session.metadata if ctx.session else {}).get("session_total_tokens", 0))
        input_tokens = self.token_monitor.record(ctx.inbound.content, "", False, 0)["input_tokens"]
        output_tokens = self.token_monitor.record("", ctx.final_content, False, 0)["output_tokens"]
        await self.sessions.save_user_message(session_id, ctx.inbound.content, input_tokens)
        await self.sessions.save_assistant_message(session_id, ctx.final_content, output_tokens)
        ctx.compact_stats = await self._build_compaction_stats(session_id)
        ctx.should_compact = bool(ctx.compact_stats["should_compact"])
        usage = self.token_monitor.record(
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
        await self.sessions.store.update_metadata(
            session_id,
            {"session_total_tokens": usage["total_tokens"]},
        )
        for trace in ctx.trace:
            await self.sessions.store.save_trace(trace)
        ctx.saved_trace_count = len(ctx.trace)
        await self.proactive.emit(CONVERSATION_COMPLETED, {"session_id": session_id, "turn_id": ctx.turn_id})
        return "ok"

    async def _compact(self, ctx: TurnContext) -> str:
        """在消息落盘后执行短期记忆压缩。"""
        if ctx.shortcut_response is not None:
            return "ok"
        if ctx.session is None:
            await self.sessions.store.save_token_usage(ctx.turn_id, ctx.inbound.session_id, ctx.token_usage)
            return "ok"
        if not ctx.compact_stats:
            ctx.compact_stats = await self._build_compaction_stats(ctx.session.id)
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
            await self.sessions.store.save_token_usage(ctx.turn_id, ctx.inbound.session_id, ctx.token_usage)
            return "ok"
        memory = ShortTermMemory(
            compact_token_threshold=self.settings.memory.compact_token_threshold,
            raw_window_token_limit=self.settings.memory.raw_window_token_limit,
        )
        all_history = await self.sessions.all_messages(ctx.session.id)
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
            await self.sessions.store.save_token_usage(ctx.turn_id, ctx.inbound.session_id, ctx.token_usage)
            return "ok"
        summary = memory.compact(ctx.session.summary, compact_source)
        compacted_token_count = memory.count_tokens(compact_source)
        raw_window_token_count = memory.count_tokens(recent_history)
        await self.sessions.store.update_summary(ctx.session.id, summary)
        await self.sessions.store.update_metadata(
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
        await self.sessions.store.save_token_usage(ctx.turn_id, ctx.inbound.session_id, ctx.token_usage)
        return "ok"

    async def _respond(self, ctx: TurnContext) -> str:
        """构造出站消息。"""
        ctx.outbound = OutboundMessage.new(ctx.inbound.session_id, ctx.inbound.channel, ctx.final_content)
        return "ok"

    async def _save_remaining_traces(self, ctx: TurnContext) -> None:
        """补保存 SAVE 后才产生的状态 trace。"""
        if ctx.shortcut_response is not None:
            return
        for trace in ctx.trace[ctx.saved_trace_count :]:
            await self.sessions.store.save_trace(trace)
        ctx.saved_trace_count = len(ctx.trace)

    async def _build_compaction_stats(self, session_id: str) -> dict[str, int | str | bool]:
        """基于保存后的未压缩历史生成压缩统计。"""
        session = await self.sessions.store.load_session(session_id)
        if session is None:
            return {
                "should_compact": False,
                "compacted_message_count": 0,
                "compacted_token_count": 0,
                "raw_window_message_count": 0,
                "raw_window_token_count": 0,
            }
        all_history = await self.sessions.all_messages(session_id)
        compacted_count = int(session.metadata.get("compacted_message_count", 0))
        memory = ShortTermMemory(
            compact_token_threshold=self.settings.memory.compact_token_threshold,
            raw_window_token_limit=self.settings.memory.raw_window_token_limit,
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

    def _compact_trace_metadata(self, ctx: TurnContext) -> dict[str, int]:
        """返回 COMPACT 状态需要暴露的最小监控字段。"""
        return {
            "compacted": int(ctx.token_usage.get("compacted", 0)),
            "compacted_message_count": int(ctx.token_usage.get("compacted_message_count", 0)),
            "compacted_token_count": int(ctx.token_usage.get("compacted_token_count", 0)),
            "raw_window_message_count": int(ctx.token_usage.get("raw_window_message_count", 0)),
            "raw_window_token_count": int(ctx.token_usage.get("raw_window_token_count", 0)),
        }

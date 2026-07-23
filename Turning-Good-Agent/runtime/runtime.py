import time

from ..bus.messages import InboundMessage, OutboundMessage
from ..channels.base import ChannelRouter
from ..config.settings import Settings
from ..context.builder import ContextBuilder
from ..hooks.channel_status import ChannelStatusHook
from ..hooks.manager import HookManager
from ..hooks.tool_permission import ToolPermissionHook
from ..hooks.tool_result_truncation import ToolResultTruncationHook
from ..hooks.turn_monitor import TurnMonitorHook
from ..llm.client import LLMProvider
from ..memory.long_term import ProfileMemory
from ..mcp.manager import McpManager
from ..observability.token_monitor import TokenMonitor
from ..observability.trace import StateTrace
from ..proactive.manager import ProactiveManager
from ..sessions.manager import SessionManager
from ..sessions.store import JsonlSessionStore
from ..tools.loader import ToolLoader
from ..tools.registry import ToolRegistry
from .agent_loop import AgentLoop
from .state import (
    TurnState,
    compact_trace_metadata,
    next_state,
    run_state,
    run_trace_metadata,
    save_remaining_traces,
    save_trace_metadata,
)
from .turn_context import TurnContext


def validate_tool_permission_settings(tools: ToolRegistry, settings: Settings) -> None:
    """校验审批工具均已注册且不会并行执行。"""
    for name in settings.tool_permissions.approval_required_tools:
        if not tools.has(name):
            raise ValueError(f"审批工具未注册：{name}")
        if bool(getattr(tools.get(name), "parallel_safe", False)):
            raise ValueError(f"审批工具不能设置 parallel_safe=true：{name}")


class AgentRuntime:
    """驱动单轮消息处理的状态机 Runtime。"""

    def __init__(
        self,
        settings: Settings,
        sessions: SessionManager,
        context_builder: ContextBuilder,
        agent_loop: AgentLoop,
        profile_memory: ProfileMemory,
        proactive: ProactiveManager,
        hooks: HookManager,
        mcp: McpManager,
    ) -> None:
        """初始化 Runtime 依赖和唯一 Hook 管理器。"""
        self.settings = settings
        self.sessions = sessions
        self.context_builder = context_builder
        self.agent_loop = agent_loop
        self.profile_memory = profile_memory
        self.proactive = proactive
        self.hooks = hooks
        self.mcp = mcp
        self.channel_router = ChannelRouter()
        self.token_monitor = TokenMonitor()
        self.last_trace: list[StateTrace] = []

    @classmethod
    def create_default(cls, settings: Settings, llm: LLMProvider) -> "AgentRuntime":
        """创建 MVP 默认 Runtime 配置"""
        store = JsonlSessionStore(settings.data_dir)
        sessions = SessionManager(store)
        tools = ToolRegistry()
        ToolLoader().load(tools, settings)
        validate_tool_permission_settings(tools, settings)
        mcp = McpManager(settings.mcp)
        hooks = HookManager()
        hooks.register(ToolPermissionHook(frozenset(settings.tool_permissions.approval_required_tools), tools, mcp))
        hooks.register(ToolResultTruncationHook(settings.runtime.max_tool_result_tokens))
        hooks.register(ChannelStatusHook())
        hooks.register(TurnMonitorHook())
        return cls(
            settings=settings,
            sessions=sessions,
            context_builder=ContextBuilder(),
            agent_loop=AgentLoop(llm, tools, settings.runtime, settings.llm.streaming_enabled, hooks=hooks),
            profile_memory=ProfileMemory(),
            proactive=ProactiveManager(),
            hooks=hooks,
            mcp=mcp,
        )

    async def run_turn(
        self,
        msg: InboundMessage,
    ) -> OutboundMessage:
        """执行一轮消息处理并返回出站消息。"""
        turn_started = time.perf_counter()
        ctx = TurnContext(inbound=msg, channel_adapter=self.channel_router.create(msg.channel))
        lock_wait_started = time.perf_counter()
        lock = self.sessions.locks.lock_for(msg.session_id)
        async with lock:
            session_lock_wait_ms = (time.perf_counter() - lock_wait_started) * 1000
            while True:
                started = time.perf_counter()
                try:
                    event = await run_state(self, ctx)
                except Exception as exc:
                    ctx.error = str(exc)
                    ctx.final_content = f"请求失败：{ctx.error}"
                    event = "error"
                duration_ms = (time.perf_counter() - started) * 1000
                metadata = {}
                if ctx.state is TurnState.RUN:
                    metadata = run_trace_metadata(ctx)
                elif ctx.state is TurnState.COMPACT:
                    metadata = compact_trace_metadata(ctx)
                elif ctx.state is TurnState.SAVE:
                    metadata = save_trace_metadata(ctx)
                next_turn_state = next_state(ctx.state, event)
                if (
                    ctx.state is TurnState.RESPOND
                    and next_turn_state is None
                    and ctx.shortcut_response is None
                    and ctx.session is not None
                ):
                    turn_duration_ms = (time.perf_counter() - turn_started) * 1000
                    metadata.update(await self.hooks.run_after_turn(ctx, turn_duration_ms, session_lock_wait_ms))
                ctx.trace.append(
                    StateTrace(ctx.turn_id, msg.session_id, ctx.state.name, duration_ms, event, ctx.error, metadata)
                )
                if next_turn_state is None:
                    break
                ctx.state = next_turn_state
            await save_remaining_traces(self, ctx)
        self.last_trace = ctx.trace
        outbound = ctx.outbound or OutboundMessage.new(msg.session_id, msg.channel, ctx.final_content)
        if outbound.event_type == "response.error":
            await ctx.channel_adapter.on_error(outbound.content)
        else:
            await ctx.channel_adapter.on_completed(outbound.content)
        return outbound

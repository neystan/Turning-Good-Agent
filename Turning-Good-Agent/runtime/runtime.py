import time
from collections.abc import Callable

from ..bus.messages import InboundMessage, OutboundMessage
from ..config.settings import Settings
from ..context.builder import ContextBuilder
from ..llm.client import LLMProvider
from ..memory.long_term import ProfileMemory
from ..observability.token_monitor import TokenMonitor
from ..observability.trace import StateTrace
from ..proactive.manager import ProactiveManager
from ..sessions.manager import SessionManager
from ..sessions.store import JsonlSessionStore
from ..tools.loader import ToolLoader
from ..tools.registry import ToolRegistry
from .agent_loop import AgentLoop
from .state import TurnState, compact_trace_metadata, next_state, run_state, run_trace_metadata, save_remaining_traces
from .turn_context import TurnContext


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
        """创建 MVP 默认 Runtime 配置"""
        store = JsonlSessionStore(settings.data_dir)
        sessions = SessionManager(store)
        tools = ToolRegistry()
        ToolLoader().load(tools, settings)
        return cls(
            settings=settings,
            sessions=sessions,
            context_builder=ContextBuilder(),
            agent_loop=AgentLoop(llm, tools, settings.runtime, settings.llm.streaming_enabled),
            profile_memory=ProfileMemory(),
            proactive=ProactiveManager(),
        )

    async def run_turn(self, msg: InboundMessage, on_delta: Callable[[str], object] | None = None) -> OutboundMessage:
        """执行一轮消息处理并返回出站消息。"""
        ctx = TurnContext(inbound=msg, on_delta=on_delta)
        lock = self.sessions.locks.lock_for(msg.session_id)
        async with lock:
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
                ctx.trace.append(
                    StateTrace(ctx.turn_id, msg.session_id, ctx.state.name, duration_ms, event, ctx.error, metadata)
                )
                next_turn_state = next_state(ctx.state, event)
                if next_turn_state is None:
                    break
                ctx.state = next_turn_state
            await save_remaining_traces(self, ctx)
        self.last_trace = ctx.trace
        return ctx.outbound or OutboundMessage.new(msg.session_id, msg.channel, ctx.final_content)

import asyncio
import logging
import time
from pathlib import Path

from ..bus.messages import ChannelRoute, InboundMessage, OutboundMessage
from ..channels.base import ChannelRouter
from ..channels.policy import ChannelToolPolicy, DefaultChannelToolPolicy
from ..config.settings import Settings
from ..context.builder import ContextBuilder
from ..hooks.channel_status import ChannelStatusHook
from ..hooks.manager import HookManager
from ..hooks.tool_permission import ToolPermissionHook, validate_tool_permission_tools
from ..hooks.tool_result_truncation import ToolResultTruncationHook
from ..hooks.turn_monitor import TurnMonitorHook
from ..llm.client import LLMProvider
from ..memory.long_term import ProfileMemory
from ..mcp.manager import McpManager
from ..mcp.control_tools import register_mcp_control_tools
from ..observability.token_monitor import TokenMonitor
from ..observability.trace import StateTrace
from ..proactive.manager import ProactiveManager
from ..skills.load_skill_tool import LoadSkillTool
from ..skills.install_skill_tool import InstallSkillTool
from ..skills.manager import SkillManager
from ..skills.skill_draft_tools import CreateSkillDraftTool, PublishSkillDraftTool
from ..sessions.manager import SessionManager
from ..sessions.store import JsonlSessionStore
from ..tools.loader import ToolLoader
from ..tools.registry import ToolRegistry
from ..gateway.principals import GatewayPrincipalResolver
from .agent_loop import AgentLoop
from .principal_context import (
    PrincipalRuntimeContext,
    PrincipalSessionReader,
    RuntimePrincipalResolver,
)
from .state import (
    TurnState,
    compact_trace_metadata,
    next_state,
    run_state,
    run_trace_metadata,
    save_remaining_traces,
    save_trace_metadata,
)
from .turn_context import (
    TurnContext,
    reset_current_inbound,
    reset_current_principal_context,
    reset_current_route,
    current_principal_context,
    set_current_inbound,
    set_current_principal_context,
    set_current_route,
)

logger = logging.getLogger(__name__)


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
        skills: SkillManager,
        principal_resolver: RuntimePrincipalResolver | None = None,
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
        self.skills = skills
        self.channel_router = ChannelRouter()
        self._channel_tool_policy: ChannelToolPolicy = DefaultChannelToolPolicy()
        self._principal_resolver = principal_resolver
        self.token_monitor = TokenMonitor()
        self.last_trace: list[StateTrace] = []
        self._mcp_started = False
        self._mcp_start_lock = asyncio.Lock()
        self._skills_started = False
        self._activity_lock = asyncio.Lock()
        self._active_turns = 0

    @classmethod
    def create_default(cls, settings: Settings, llm: LLMProvider) -> "AgentRuntime":
        """创建 MVP 默认 Runtime 配置"""
        store = JsonlSessionStore(settings.data_dir)
        sessions = SessionManager(store, settings)
        tools = ToolRegistry()
        ToolLoader().load(tools, settings)
        skills = SkillManager(Path.cwd() / settings.skills.directory, settings.skills)
        tools.register(LoadSkillTool(skills))
        tools.register(InstallSkillTool(skills))
        tools.register(CreateSkillDraftTool(skills))
        tools.register(PublishSkillDraftTool(skills))
        mcp = McpManager(settings.mcp)
        register_mcp_control_tools(mcp, tools)
        configured_mcp_tools = {
            tool_name if tool_name.startswith(f"mcp_{server_name}_") else f"mcp_{server_name}_{tool_name}"
            for server_name, server in settings.mcp.servers.items()
            for tool_name in server.enabled_tools
        }
        validate_tool_permission_tools(
            tools,
            settings.tool_permissions.approval_required_tools,
            allowed_unregistered_names=configured_mcp_tools,
        )
        hooks = HookManager()
        hooks.register(ToolPermissionHook(frozenset(settings.tool_permissions.approval_required_tools), tools))
        hooks.register(ToolResultTruncationHook(settings.runtime.max_tool_result_tokens))
        hooks.register(ChannelStatusHook())
        hooks.register(TurnMonitorHook())
        profile_memory = ProfileMemory(settings.data_dir, settings.proactive)
        runtime = cls(
            settings=settings,
            sessions=sessions,
            context_builder=ContextBuilder(),
            agent_loop=AgentLoop(
                llm,
                tools,  
                settings.runtime,
                settings.llm.streaming_enabled,
                hooks=hooks,
                attachment_context_token_limit=settings.mcp.attachment_context_token_limit,
                skills=settings.skills,
            ),
            profile_memory=profile_memory,
            proactive=ProactiveManager(),
            hooks=hooks,
            mcp=mcp,
            skills=skills,
        )
        runtime.set_principal_resolver(
            GatewayPrincipalResolver(
                settings,
                owner_sessions=sessions,
                owner_profile_memory=profile_memory,
                tools=tools,
                policy=runtime._channel_tool_policy,
            )
        )
        return runtime

    async def run_turn(
        self,
        msg: InboundMessage,
    ) -> OutboundMessage:
        """执行一轮消息处理，并在整个排队/运行期间标记 Runtime 非空闲。"""
        await self._begin_turn()
        route_token = set_current_route(msg.route)
        inbound_token = set_current_inbound(msg)
        principal_token = None
        try:
            if hasattr(self, "_principal_resolver"):
                principal_context = self.resolve_principal_context(msg.route)
                principal_token = set_current_principal_context(principal_context)
            return await self._run_turn(msg)
        finally:
            if principal_token is not None:
                reset_current_principal_context(principal_token)
            reset_current_inbound(inbound_token)
            reset_current_route(route_token)
            await self._end_turn()

    async def _run_turn(
        self,
        msg: InboundMessage,
        principal_context: PrincipalRuntimeContext | None = None,
    ) -> OutboundMessage:
        """执行一轮消息处理并返回出站消息。"""
        principal_context = principal_context or current_principal_context()
        turn_started = time.perf_counter()
        ctx = TurnContext(
            inbound=msg,
            channel_adapter=self.channel_router.create(msg),
            allowed_tool_names=(
                principal_context.allowed_tool_names
                if principal_context is not None
                else self.allowed_tool_names_for(msg.route)
            ),
            principal_context=principal_context,
        )
        lock_wait_started = time.perf_counter()
        sessions = principal_context.sessions if principal_context is not None else self.sessions
        lock = sessions.locks.lock_for(msg.session_id)
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

    def set_channel_tool_policy(self, policy: ChannelToolPolicy) -> None:
        """替换当前 Runtime 使用的 Channel 工具策略。"""
        self._channel_tool_policy = policy
        if self._principal_resolver is not None and hasattr(
            self._principal_resolver, "set_channel_tool_policy"
        ):
            self._principal_resolver.set_channel_tool_policy(policy)  # type: ignore[attr-defined]

    def set_principal_resolver(self, resolver: RuntimePrincipalResolver) -> None:
        """注入按主体解析 Runtime Context 的组件。"""
        self._principal_resolver = resolver

    def resolve_principal_context(self, route: ChannelRoute) -> PrincipalRuntimeContext:
        """解析可信路由对应的主体 Context，并保留 Owner 兼容回退。"""
        principal_resolver = getattr(self, "_principal_resolver", None)
        if principal_resolver is not None:
            return principal_resolver.resolve(route)
        return PrincipalRuntimeContext(
            principal_id=route.principal_id,
            data_root=self.settings.data_dir,
            profile_memory=self.profile_memory,
            sessions=PrincipalSessionReader(self.sessions, route.principal_id),
            allowed_tool_names=self.allowed_tool_names_for(route),
        )

    def allowed_tool_names_for(self, route: ChannelRoute) -> frozenset[str] | None:
        """按可信路由读取当前注册表和设置计算工具 allowlist。"""
        return self._channel_tool_policy.allowed_tool_names(
            route, self.agent_loop.tools, self.settings
        )

    def is_globally_idle(self) -> bool:
        """返回是否没有排队或运行中的前台 turn。"""
        return self._active_turns == 0

    async def _begin_turn(self) -> None:
        """标记 Runtime 开始处理。"""
        async with self._activity_lock:
            self._active_turns += 1

    async def _end_turn(self) -> None:
        """标记 Runtime 结束处理。"""
        async with self._activity_lock:
            self._active_turns = max(0, self._active_turns - 1)

    async def close(self) -> None:
        """关闭 Runtime 持有的 MCP 连接。"""
        try:
            await self.mcp.close()
        except Exception:
            logger.exception("关闭 MCP Runtime 失败")

    async def start(self) -> None:
        """启动 Runtime 的后台依赖。"""
        if not self._skills_started:
            self.skills.scan()
            self._skills_started = True
        if self._mcp_started:
            return
        async with self._mcp_start_lock:
            if self._mcp_started:
                return
            try:
                await self.mcp.start_background(self.agent_loop.tools)
            except Exception:
                logger.exception("启动 MCP Runtime 失败")
            finally:
                self._mcp_started = True

from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from ..sessions.token_counter import count_content_tokens
from ..sessions.types import MessageRecord
from ..skills.manager import SkillManager
from ..channels.registry import ChannelConflictError
from ..llm.client import LLMProvider
from ..llm.factory import build_llm
from ..runtime.turn_context import current_inbound, current_route
from ..bus.messages import ChannelRoute
from .breakbeat import (
    BreakbeatManager,
    CompleteBreakbeatTool,
    DeleteBreakbeatTool,
    ListBreakbeatTool,
    RunBreakbeatTool,
)
from .cron import CreateCronTool, CronManager, DeleteCronTool, ListCronsTool
from .dream import DreamManager, ReadProfileMemoryTool, RunDreamTool
from .events import CONVERSATION_COMPLETED
from .executor import ProactiveExecutionResult, ProactiveExecutor
from .incidents import IncidentMonitor, ListIncidentsTool
from .notifications import GatewayNotificationPublisher
from .skill_evolution import (
    DeleteSkillDraftTool,
    ListSkillDraftsTool,
    RunSkillEvolutionTool,
    SkillEvolutionManager,
)
from .store import ProactiveStore
from .workspace import ProactiveWorkspace

if TYPE_CHECKING:
    from ..channels.registry import ChannelAccountRegistry
    from ..runtime.runtime import AgentRuntime


logger = logging.getLogger(__name__)


class _BoundedExecutor:
    """让所有后台模型工作共享独立且有界的并发池。"""

    def __init__(
        self,
        delegate: ProactiveExecutor,
        semaphore: asyncio.Semaphore,
        *,
        use_review_model: bool = False,
    ) -> None:
        """初始化对象状态。"""
        self._delegate = delegate
        self._semaphore = semaphore
        self._use_review_model = use_review_model

    async def run(
        self,
        capability: str,
        prompt: str,
        *,
        allowed_tool_names: frozenset[str] | None = None,
        on_started: Callable[[], Awaitable[None]] | None = None,
    ) -> ProactiveExecutionResult:
        """取得后台容量后再通知调用方任务真正开始。"""
        async with self._semaphore:
            if on_started is not None:
                await on_started()
            return await self._delegate.run(
                capability,
                prompt,
                allowed_tool_names=allowed_tool_names,
                use_review_model=self._use_review_model,
            )


class ProactiveService:
    """由唯一 Gateway 启动的主动能力生命周期与后台调度装配层。"""

    def __init__(
        self,
        runtime: "AgentRuntime",
        *,
        notification_publisher: GatewayNotificationPublisher,
        principal_registry: "ChannelAccountRegistry | None" = None,
        on_domain_change: Callable[[str], Awaitable[None]] | None = None,
        on_idle: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """初始化对象状态。"""
        self.runtime = runtime
        self.notifications = notification_publisher
        self._principal_registry = principal_registry
        self._owner_principal_id = runtime.settings.gateway.principal_id
        self._workspaces: dict[str, ProactiveWorkspace] = {}
        self._retired_principal_ids: set[str] = set()
        self._principal_retirement_locks: dict[str, asyncio.Lock] = {}
        self._on_domain_change = on_domain_change
        self._on_idle = on_idle
        self._background_semaphore = asyncio.Semaphore(
            runtime.settings.proactive.background_max_concurrency
        )
        self._schedule_event = asyncio.Event()
        review_llm = self._review_provider()
        executor = ProactiveExecutor(
            runtime.agent_loop,
            approval_required_tool_names=frozenset(
                runtime.settings.tool_permissions.approval_required_tools
            ),
            proactive_tool_names=self._installed_tool_names,
            review_llm=review_llm,
        )
        self._primary_executor = _BoundedExecutor(executor, self._background_semaphore)
        self._review_executor = _BoundedExecutor(
            executor,
            self._background_semaphore,
            use_review_model=review_llm is not None,
        )
        self._running = False
        self._accepting_work = False
        owner = self.workspace_for(self._owner_principal_id)
        self.store = owner.store
        self.incidents = owner.incidents
        self.cron = owner.cron
        self.breakbeat = owner.breakbeat
        self.dream = owner.dream
        self._skills = owner.draft_skills
        self.skill_evolution = owner.skill_evolution
        self._scheduler_task: asyncio.Task[None] | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._breakbeat_tasks: dict[str, asyncio.Task[None]] = {}
        self._dream_tasks: dict[str, asyncio.Task[None]] = {}
        self._skill_evolution_tasks: dict[str, asyncio.Task[None]] = {}
        self._observation_tasks: dict[str, set[asyncio.Task[None]]] = {}
        self._mcp_listener: Callable[[Any], None] | None = None
        self._installed_tools: list[Any] = []
        runtime.proactive.register(self)

    def workspace_for(self, principal_id: str) -> ProactiveWorkspace:
        """懒创建并缓存一个主体的主动 workspace。"""
        if not isinstance(principal_id, str) or not principal_id:
            raise ValueError("principal_id 不能为空")
        if principal_id in self._retired_principal_ids:
            raise RuntimeError("主体正在删除")
        workspace = self._workspaces.get(principal_id)
        if workspace is not None:
            return workspace
        route = ChannelRoute(principal_id, "proactive", "proactive", "proactive")
        resolver = getattr(self.runtime, "resolve_principal_context", None)
        if callable(resolver):
            context = resolver(route)
        elif principal_id == self._owner_principal_id:
            context = SimpleNamespace(
                principal_id=principal_id,
                data_root=self.runtime.settings.data_dir,
                profile_memory=self.runtime.profile_memory,
                sessions=self.runtime.sessions,
            )
        else:
            raise RuntimeError("Runtime 未配置主体 resolver")
        if context.principal_id != principal_id:
            raise PermissionError("主体 resolver 返回了不匹配的 principal")
        workspace = self._create_workspace(context)
        self._workspaces[principal_id] = workspace
        if self._running:
            self._initialize_workspace_schedule(workspace, datetime.now(UTC))
            self._wake_scheduler()
        return workspace

    def workspace_for_route(self, route: ChannelRoute) -> ProactiveWorkspace:
        """只从可信 ChannelRoute 解析 workspace。"""
        if not isinstance(route, ChannelRoute) or not route.principal_id:
            raise ValueError("route 必须包含 principal_id")
        if not callable(getattr(self.runtime, "resolve_principal_context", None)):
            return self.workspace_for(self._owner_principal_id)
        return self.workspace_for(route.principal_id)

    def _create_workspace(self, context: Any) -> ProactiveWorkspace:
        """使用共享执行器装配一个主体自己的领域 manager。"""
        principal_id = context.principal_id
        store = ProactiveStore(context.data_root)
        draft_skills = (
            getattr(self.runtime, "skills", None)
            if principal_id == self._owner_principal_id
            else SkillManager(context.data_root / "skills", self.runtime.settings.skills)
        )
        if draft_skills is None:
            draft_skills = SkillManager(
                Path.cwd() / self.runtime.settings.skills.directory,
                self.runtime.settings.skills,
            )
        incidents = IncidentMonitor(
            store,
            self.notifications,
            principal_id=principal_id,
            state_changed=lambda: self._workspace_domain_changed(principal_id, "incident"),
        )
        cron = CronManager(
            store,
            self._primary_executor,
            self.notifications,
            self.runtime.settings.proactive,
            principal_id=principal_id,
            incidents=incidents,
            schedule_changed=lambda: self._cron_schedule_changed(principal_id),
            idle_changed=self._request_idle_check,
            available_tool_names=self._available_tool_names,
        )
        breakbeat = BreakbeatManager(
            store,
            context.sessions,
            self._review_executor,
            self.runtime.settings.proactive,
            schedule_changed=self._wake_scheduler,
            cron_jobs=cron.list_jobs,
        )
        dream = DreamManager(
            store,
            context.sessions,
            self._primary_executor,
            context.profile_memory,
            self.runtime.settings.proactive,
            schedule_changed=self._wake_scheduler,
        )
        skill_evolution = SkillEvolutionManager(
            store,
            context.sessions,
            self._review_executor,
            self._primary_executor,
            draft_skills,
            self.runtime.settings.proactive,
            schedule_changed=self._wake_scheduler,
        )
        return ProactiveWorkspace(
            principal_id=principal_id,
            store=store,
            profile_memory=context.profile_memory,
            draft_skills=draft_skills,
            sessions=context.sessions,
            cron=cron,
            breakbeat=breakbeat,
            dream=dream,
            skill_evolution=skill_evolution,
            incidents=incidents,
        )

    def _review_provider(self) -> LLMProvider | None:
        """按可选 review_model 构建单次调用覆盖 Provider，不创建第二个 Loop。"""
        proactive = self.runtime.settings.proactive
        if proactive.review_model is None:
            return None
        settings = deepcopy(self.runtime.settings)
        settings.llm.provider = proactive.review_provider
        settings.llm.api_key = proactive.review_api_key
        settings.llm.base_url = proactive.review_base_url
        settings.llm.model = proactive.review_model
        return build_llm(settings)

    @property
    def running(self) -> bool:
        """返回同进程调度器是否已启动。"""
        return self._running

    @property
    def is_idle(self) -> bool:
        """报告可安全替换 Runtime 的主动能力空闲点。"""
        return (
            all(workspace.cron.is_idle for workspace in self._workspaces.values())
            and not self._background_tasks
        )

    def is_principal_idle(self, principal_id: str) -> bool:
        """只判断一个独立主体，避免因其它主体后台任务阻塞删除。"""
        workspace = self._workspaces.get(principal_id)
        return (
            (workspace is None or workspace.cron.is_idle)
            and not _task_active(self._breakbeat_tasks.get(principal_id))
            and not _task_active(self._dream_tasks.get(principal_id))
            and not _task_active(self._skill_evolution_tasks.get(principal_id))
            and not any(_task_active(task) for task in self._observation_tasks.get(principal_id, ()))
        )

    async def retire_principal(self, principal_id: str) -> None:
        """在主体空闲时停止其主动投影；不向 Host 暴露 reservation。"""
        if not principal_id or principal_id == self._owner_principal_id:
            raise ValueError("Owner 主体不能退役")
        lock = self._principal_retirement_locks.setdefault(principal_id, asyncio.Lock())
        async with lock:
            if not self.is_principal_idle(principal_id):
                raise ChannelConflictError("binding_busy", "独立主体仍有后台任务")
            self._retired_principal_ids.add(principal_id)
            workspace = self._workspaces.pop(principal_id, None)
            if workspace is not None:
                await workspace.cron.close()
            self._breakbeat_tasks.pop(principal_id, None)
            self._dream_tasks.pop(principal_id, None)
            self._skill_evolution_tasks.pop(principal_id, None)
            self._observation_tasks.pop(principal_id, None)
            self._wake_scheduler()

    def restore_principal(self, principal_id: str) -> None:
        """删除后续文件清理失败时允许该主体在下次调度恢复。"""
        self._retired_principal_ids.discard(principal_id)
        self._wake_scheduler()

    def _available_tool_names(self) -> frozenset[str]:
        """在每次 Cron 触发时返回当前 Runtime 的 Tool 快照。"""
        return frozenset(
            (*self.runtime.agent_loop.tools.tool_names, *(tool.name for tool in self._installed_tools))
        )

    def _installed_tool_names(self) -> frozenset[str]:
        """返回当前服务拥有的主动 Tool 名称。"""
        return frozenset(tool.name for tool in self._installed_tools)

    def install_tools(self) -> None:
        """将本服务拥有的交互式主动 Tool 注册到当前 Runtime。"""
        registry = self.runtime.agent_loop.tools
        tools = [
            CreateCronTool(self),
            ListCronsTool(self),
            DeleteCronTool(self),
            RunBreakbeatTool(
                self,
                active_session_id=self._current_session_id,
                current_message=self._current_message_record,
                on_completed=self._manual_breakbeat_completed,
            ),
            ListBreakbeatTool(self),
            CompleteBreakbeatTool(self),
            DeleteBreakbeatTool(self),
            RunDreamTool(
                self,
                active_session_id=self._current_session_id,
                current_message=self._current_message_record,
                on_completed=self._manual_dream_completed,
            ),
            ReadProfileMemoryTool(self),
            RunSkillEvolutionTool(
                self,
                on_completed=self._manual_skill_evolution_completed,
            ),
            ListSkillDraftsTool(self),
            DeleteSkillDraftTool(self),
            ListIncidentsTool(self),
        ]
        for tool in tools:
            if not registry.has(tool.name):
                registry.register(tool)
                self._installed_tools.append(tool)

    def uninstall_tools(self) -> None:
        """仅移除仍由本服务注册的主动 Tool。"""
        registry = self.runtime.agent_loop.tools
        for tool in self._installed_tools:
            registry.unregister(tool)
        self._installed_tools.clear()

    async def start(self) -> None:
        """恢复 durable 状态并启动未来触发，不补跑错过时间。"""
        if self._running or not self.runtime.settings.proactive.enabled:
            return
        self._accepting_work = True
        now = datetime.now(UTC)
        self._refresh_known_workspaces()
        for workspace in tuple(self._workspaces.values()):
            self._initialize_workspace_schedule(workspace, now)
        self._running = True
        self._install_mcp_listener()
        self._scheduler_task = asyncio.create_task(
            self._scheduler_loop(), name="proactive-scheduler"
        )

    async def stop(self) -> None:
        """停止调度、取消尚未结束的后台工作并保留已有 durable 结果。"""
        self._running = False
        self._accepting_work = False
        self._remove_mcp_listener()
        for task in (self._scheduler_task,):
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *(task for task in (self._scheduler_task,) if task is not None),
            return_exceptions=True,
        )
        self._scheduler_task = None
        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()
        self._breakbeat_tasks.clear()
        self._dream_tasks.clear()
        self._skill_evolution_tasks.clear()
        self._observation_tasks.clear()
        for workspace in tuple(self._workspaces.values()):
            await workspace.cron.close()

    async def drain_for_replacement(self) -> None:
        """停止接收新工作，并等待当前后台能力到达安全空闲点。"""
        self._accepting_work = False
        self._wake_scheduler()
        for workspace in tuple(self._workspaces.values()):
            await workspace.cron.wait_for_jobs()
        while self._background_tasks:
            await asyncio.gather(*tuple(self._background_tasks), return_exceptions=True)

    async def handle(self, event: str, payload: dict[str, object]) -> None:
        """接收 SAVE 后的轻量事实，不延长前台回复路径。"""
        if event != CONVERSATION_COMPLETED or not self._running or not self._accepting_work:
            return
        session_id = payload.get("session_id")
        principal_id = payload.get("principal_id")
        if (
            isinstance(principal_id, str)
            and principal_id
            and isinstance(session_id, str)
            and session_id
        ):
            if principal_id in self._retired_principal_ids:
                return
            assistant_message_id = payload.get("completed_assistant_message_id")
            workspace = self.workspace_for(principal_id)
            self._spawn_for_principal(
                principal_id,
                self._observe_session(
                    workspace,
                    session_id,
                    assistant_message_id if isinstance(assistant_message_id, str) else None,
                )
            )

    async def _scheduler_loop(self) -> None:
        """等待最早 deadline 或显式计划变更，不做每秒全量扫描。"""
        while self._running:
            self._schedule_event.clear()
            if not self._accepting_work:
                await self._schedule_event.wait()
                continue
            now = datetime.now(UTC)
            self._refresh_known_workspaces()
            for workspace in tuple(self._workspaces.values()):
                try:
                    await self._run_workspace_due(workspace, now)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    try:
                        await workspace.incidents.report_failure(
                            fingerprint="proactive:scheduler",
                            source="proactive",
                            message="主动任务调度器执行失败。",
                        )
                    except Exception:
                        logger.exception(
                            "主体 %s 的主动调度异常记录失败", workspace.principal_id
                        )
            deadline = _earliest_deadline(
                *(
                    self._workspace_deadlines(workspace, now)
                    for workspace in self._workspaces.values()
                )
            )
            if self._schedule_event.is_set():
                continue
            timeout = (
                None
                if deadline is None
                else max(0.0, (deadline - datetime.now(UTC)).total_seconds())
            )
            try:
                if timeout is None:
                    await self._schedule_event.wait()
                else:
                    await asyncio.wait_for(self._schedule_event.wait(), timeout=timeout)
            except TimeoutError:
                pass

    def _refresh_known_workspaces(self) -> None:
        """从 Registry 恢复所有已知主体，但从不删除已撤销主体。"""
        if self._principal_registry is None:
            return
        for principal_id in self._principal_registry.principal_ids():
            if principal_id in self._workspaces or principal_id in self._retired_principal_ids:
                continue
            self.workspace_for(principal_id)

    @staticmethod
    def _initialize_workspace_schedule(workspace: ProactiveWorkspace, now: datetime) -> None:
        """初始化未来 deadline，不补跑已错过的工作。"""
        workspace.cron.initialize_schedule(now)
        workspace.breakbeat.initialize_schedule(now)
        workspace.dream.initialize_schedule(now)
        workspace.skill_evolution.initialize_schedule(now)

    async def _run_workspace_due(self, workspace: ProactiveWorkspace, now: datetime) -> None:
        """在唯一调度循环中推进一个主体到期的能力。"""
        principal_id = workspace.principal_id
        lock = self._principal_retirement_locks.setdefault(principal_id, asyncio.Lock())
        async with lock:
            if principal_id in self._retired_principal_ids:
                return
            if await workspace.cron.run_due(now):
                await self._workspace_domain_changed(principal_id, "cron")
            if (
                _due(workspace.breakbeat.next_run_at, now)
                and not workspace.breakbeat.running
                and not _task_active(self._breakbeat_tasks.get(principal_id))
            ):
                self._breakbeat_tasks[principal_id] = self._spawn(
                    self._run_breakbeat_tick(workspace), wake_on_done=True
                )
            if (
                _due(workspace.dream.next_run_at, now)
                and not workspace.dream.running
                and not _task_active(self._dream_tasks.get(principal_id))
            ):
                self._dream_tasks[principal_id] = self._spawn(
                    self._run_dream_tick(workspace), wake_on_done=True
                )
            if (
                _due(workspace.skill_evolution.next_run_at, now)
                and not workspace.skill_evolution.running
                and not _task_active(self._skill_evolution_tasks.get(principal_id))
            ):
                self._skill_evolution_tasks[principal_id] = self._spawn(
                    self._run_skill_evolution_tick(workspace), wake_on_done=True
                )

    def _workspace_deadlines(
        self, workspace: ProactiveWorkspace, now: datetime
    ) -> datetime | None:
        """返回一个 workspace 当前可调度能力的最早 deadline。"""
        principal_id = workspace.principal_id
        return _earliest_deadline(
            workspace.cron.next_deadline(now),
            None
            if _task_active(self._breakbeat_tasks.get(principal_id))
            else workspace.breakbeat.next_run_at,
            None
            if _task_active(self._dream_tasks.get(principal_id))
            else workspace.dream.next_run_at,
            None
            if _task_active(self._skill_evolution_tasks.get(principal_id))
            else workspace.skill_evolution.next_run_at,
        )

    async def _observe_session(
        self,
        workspace: ProactiveWorkspace,
        session_id: str,
        completed_assistant_message_id: str | None,
    ) -> None:
        """执行当前处理。"""
        try:
            await workspace.skill_evolution.maybe_observe(
                session_id,
                completed_assistant_message_id=completed_assistant_message_id,
            )
        except Exception:
            await workspace.incidents.report_failure(
                fingerprint=f"proactive:observation:{session_id}",
                source="skill_evolution",
                message="Skill Observation 审阅失败。",
            )
        finally:
            await self._workspace_domain_changed(workspace.principal_id, "skill")

    async def _run_breakbeat_tick(self, workspace: ProactiveWorkspace | None = None) -> None:
        """执行当前处理。"""
        workspace = workspace or self.workspace_for(self._owner_principal_id)
        try:
            outcome = await workspace.breakbeat.run(manual=False, scope="global")
            status = getattr(outcome, "status", "success")
            created = getattr(outcome, "created", 0)
            should_notify = getattr(outcome, "should_notify", created > 0)
            if status == "success" and (created or should_notify):
                await self.notifications.enqueue(
                    event_type="proactive.breakbeat.completed",
                    content=outcome.summary,
                    source_id="breakbeat",
                    notification_scope="all_subscribed",
                    principal_id=workspace.principal_id,
                )
            elif status == "partial":
                await self.notifications.enqueue(
                    event_type="proactive.breakbeat.partial",
                    content=outcome.summary,
                    source_id="breakbeat",
                    notification_scope="all_subscribed",
                    principal_id=workspace.principal_id,
                )
            elif status == "failed":
                await self.notifications.enqueue(
                    event_type="proactive.breakbeat.failed",
                    content=outcome.summary,
                    source_id="breakbeat",
                    notification_scope="all_subscribed",
                    principal_id=workspace.principal_id,
                )
            if status != "success":
                await workspace.incidents.report_failure(
                    fingerprint="proactive:breakbeat",
                    source="breakbeat",
                    message="Breakbeat 审阅失败。",
                    notify=False,
                )
            else:
                await workspace.incidents.report_recovery(
                    fingerprint="proactive:breakbeat",
                    source="breakbeat",
                    message="Breakbeat 审阅已恢复。",
                    notify=False,
                )
        except Exception:
            await workspace.incidents.report_failure(
                fingerprint="proactive:breakbeat",
                source="breakbeat",
                message="Breakbeat 审阅失败。",
                notify=False,
            )
            try:
                await self.notifications.enqueue(
                    event_type="proactive.breakbeat.failed",
                    content="Breakbeat 执行失败，请稍后重试。",
                    source_id="breakbeat",
                    notification_scope="all_subscribed",
                    principal_id=workspace.principal_id,
                )
            except Exception:
                logger.exception("Breakbeat 失败结果通知发布失败")
        finally:
            await self._workspace_domain_changed(workspace.principal_id, "breakbeat")

    async def _run_dream_tick(self, workspace: ProactiveWorkspace | None = None) -> None:
        """执行当前处理。"""
        workspace = workspace or self.workspace_for(self._owner_principal_id)
        try:
            outcome = await workspace.dream.run(manual=False, scope="global")
            if outcome.changed:
                await self.notifications.enqueue(
                    event_type="proactive.dream.completed",
                    content=outcome.summary,
                    source_id="dream",
                    notification_scope="all_subscribed",
                    principal_id=workspace.principal_id,
                )
            if outcome.errors:
                await workspace.incidents.report_failure(
                    fingerprint="proactive:dream",
                    source="dream",
                    message="Dream 审阅失败。",
                )
            else:
                await workspace.incidents.report_recovery(
                    fingerprint="proactive:dream",
                    source="dream",
                    message="Dream 审阅已恢复。",
                )
        except Exception:
            await workspace.incidents.report_failure(
                fingerprint="proactive:dream",
                source="dream",
                message="Dream 审阅失败。",
            )
        finally:
            await self._workspace_domain_changed(workspace.principal_id, "dream")

    async def _run_skill_evolution_tick(
        self, workspace: ProactiveWorkspace | None = None
    ) -> None:
        """执行当前处理。"""
        workspace = workspace or self.workspace_for(self._owner_principal_id)
        try:
            outcome = await workspace.skill_evolution.run_evolution(manual=False)
            if outcome.created_count:
                await self.notifications.enqueue(
                    event_type="proactive.skill_evolution.completed",
                    content=outcome.summary,
                    source_id="skill_evolution",
                    notification_scope="all_subscribed",
                    principal_id=workspace.principal_id,
                )
            await workspace.incidents.report_recovery(
                fingerprint="proactive:skill_evolution",
                source="skill_evolution",
                message="Skill 自进化审阅已恢复。",
            )
        except Exception:
            await workspace.incidents.report_failure(
                fingerprint="proactive:skill_evolution",
                source="skill_evolution",
                message="Skill 自进化审阅失败。",
            )
        finally:
            await self._workspace_domain_changed(workspace.principal_id, "skill")

    def _spawn(
        self, coroutine: Awaitable[None], *, wake_on_done: bool = False
    ) -> asyncio.Task[None]:
        """执行当前处理。"""
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(
            lambda completed: self._background_finished(
                completed, wake_on_done=wake_on_done
            )
        )
        return task

    def _spawn_for_principal(
        self,
        principal_id: str,
        coroutine: Awaitable[None],
        *,
        wake_on_done: bool = False,
    ) -> asyncio.Task[None]:
        task = self._spawn(coroutine, wake_on_done=wake_on_done)
        bucket = self._observation_tasks.setdefault(principal_id, set())
        bucket.add(task)

        def remove(completed: asyncio.Task[None]) -> None:
            bucket.discard(completed)
            if not bucket:
                self._observation_tasks.pop(principal_id, None)

        task.add_done_callback(remove)
        return task

    def _background_finished(self, task: asyncio.Task[None], *, wake_on_done: bool) -> None:
        """先清除任务投影，再让 Gateway 判断是否已到安全空闲点。"""
        self._background_tasks.discard(task)
        if wake_on_done:
            self._wake_scheduler()
        self._request_idle_check()

    def _wake_scheduler(self) -> None:
        """通知 deadline 循环立即重新计算等待时长。"""
        self._schedule_event.set()

    def _cron_schedule_changed(self, principal_id: str) -> None:
        """Cron 状态改变时同时刷新 deadline 与 Web 运行时投影。"""
        self._wake_scheduler()
        if (
            self._running
            and self._on_domain_change is not None
            and principal_id == self._owner_principal_id
        ):
            self._spawn(self._workspace_domain_changed(principal_id, "cron"))

    def _request_idle_check(self) -> None:
        """异步通知 Gateway；Supervisor 会再次验证所有 Channel 和后台任务状态。"""
        if self._on_idle is not None:
            asyncio.create_task(self._notify_idle())

    async def _notify_idle(self) -> None:
        try:
            assert self._on_idle is not None
            await self._on_idle()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("主动任务结束后的 Gateway 空闲检查失败")

    async def _domain_changed(self, domain: str) -> None:
        if self._on_domain_change is not None:
            await self._on_domain_change(domain)

    async def _workspace_domain_changed(self, principal_id: str, domain: str) -> None:
        """Web read model 只投影 Owner workspace。"""
        if principal_id == self._owner_principal_id:
            await self._domain_changed(domain)

    async def _manual_breakbeat_completed(
        self, outcome: Any, route: ChannelRoute | None = None
    ) -> None:
        status = getattr(outcome, "status", "success")
        should_notify = getattr(
            outcome,
            "should_notify",
            status != "success" or getattr(outcome, "created", 0) > 0,
        )
        if should_notify:
            event_type = "proactive.breakbeat.completed"
            if status == "partial":
                event_type = "proactive.breakbeat.partial"
            elif status == "failed":
                event_type = "proactive.breakbeat.failed"
            await self._publish_manual_result(
                domain="breakbeat",
                event_type=event_type,
                content=outcome.summary,
                source_id="breakbeat",
                route=route,
            )

    async def _manual_dream_completed(
        self, outcome: Any, route: ChannelRoute | None = None
    ) -> None:
        if outcome.changed:
            await self._publish_manual_result(
                domain="dream",
                event_type="proactive.dream.completed",
                content=outcome.summary,
                source_id="dream",
                route=route,
            )

    async def _manual_skill_evolution_completed(
        self, outcome: Any, route: ChannelRoute | None = None
    ) -> None:
        if outcome.created_count:
            await self._publish_manual_result(
                domain="skill",
                event_type="proactive.skill_evolution.completed",
                content=outcome.summary,
                source_id="skill_evolution",
                route=route,
            )

    async def run_catalog_action(
        self,
        action_name: str,
        scope: str,
        session_id: str,
        route: ChannelRoute | None,
    ) -> str:
        """执行 Catalog 直接触发的主动能力，并保持其结果在主动工作面。"""
        if not self._running or not self._accepting_work:
            raise RuntimeError("主动能力当前不可用")
        if route is None:
            raise PermissionError("Catalog 主动动作必须包含 route")
        if route.session_id != session_id:
            raise PermissionError("Catalog session 与 route 不匹配")
        workspace = self.workspace_for_route(route)
        if action_name == "run_breakbeat":
            outcome = await workspace.breakbeat.run(
                manual=True,
                scope=scope,
                session_id=session_id if scope == "session" else None,
            )
            await self._manual_breakbeat_completed(outcome, route)
            if not outcome.should_notify:
                await self._publish_manual_result(
                    domain="breakbeat",
                    event_type="proactive.breakbeat.completed",
                    content=outcome.summary,
                    source_id="breakbeat",
                    route=route,
                )
            await self._workspace_domain_changed(workspace.principal_id, "breakbeat")
            return outcome.summary
        if action_name == "run_dream":
            outcome = await workspace.dream.run(
                manual=True,
                scope=scope,
                session_id=session_id if scope == "session" else None,
            )
            await self._manual_dream_completed(outcome, route)
            if not outcome.changed:
                await self._publish_manual_result(
                    domain="dream",
                    event_type="proactive.dream.reviewed",
                    content=outcome.summary,
                    source_id="dream",
                    route=route,
                )
            await self._workspace_domain_changed(workspace.principal_id, "dream")
            return outcome.summary
        if action_name == "run_skill_evolution":
            outcome = await workspace.skill_evolution.run_evolution(manual=True)
            await self._manual_skill_evolution_completed(outcome, route)
            if not outcome.created_count:
                await self._publish_manual_result(
                    domain="skill",
                    event_type="proactive.skill_evolution.completed",
                    content=outcome.summary,
                    source_id="skill_evolution",
                    route=route,
                )
            await self._workspace_domain_changed(workspace.principal_id, "skill")
            return outcome.summary
        raise ValueError("不支持的主动 Catalog 动作")

    async def _publish_manual_result(
        self,
        *,
        domain: str,
        event_type: str,
        content: str,
        source_id: str,
        route: ChannelRoute | None = None,
    ) -> None:
        route = route or current_route()
        if route is not None:
            try:
                await self.notifications.enqueue(
                    event_type=event_type,
                    content=content,
                    source_id=source_id,
                    notification_scope="origin",
                    origin_route=route,
                    principal_id=route.principal_id,
                )
            except Exception:
                logger.exception("手动主动结果通知发布失败")
        if route is not None:
            await self._workspace_domain_changed(route.principal_id, domain)

    def _current_message_record(self) -> MessageRecord | None:
        """把当前 turn 尚未 SAVE 的入站消息转换为一次性审阅记录。"""
        message = current_inbound()
        if message is None:
            return None
        return MessageRecord(
            id=message.id,
            session_id=message.session_id,
            role="user",
            content=message.content,
            name=None,
            tool_call_id=None,
            token_count=count_content_tokens(message.content),
            created_at=message.created_at,
            metadata=dict(message.metadata),
        )

    @staticmethod
    def _current_session_id() -> str | None:
        """从当前 turn 路由读取会话，不保留 Channel 全局可变状态。"""
        route = current_route()
        return None if route is None else route.session_id

    def _install_mcp_listener(self) -> None:
        """执行当前处理。"""
        add_listener = getattr(self.runtime.mcp, "add_status_listener", None)
        if not callable(add_listener) or self._mcp_listener is not None:
            return

        def listener(status: Any) -> None:
            """接收状态变更通知。"""
            self._spawn(self._observe_mcp_status(status))

        self._mcp_listener = listener
        add_listener(listener)

    def _remove_mcp_listener(self) -> None:
        """执行当前处理。"""
        remove_listener = getattr(self.runtime.mcp, "remove_status_listener", None)
        if self._mcp_listener is not None and callable(remove_listener):
            remove_listener(self._mcp_listener)
        self._mcp_listener = None

    async def _observe_mcp_status(self, status: Any) -> None:
        """执行当前处理。"""
        name = str(getattr(status, "name", "unknown"))
        state = str(getattr(status, "state", ""))
        if state == "failed":
            await self.incidents.report_failure(
                fingerprint=f"mcp:{name}:connection",
                source="mcp",
                message=f"MCP Server {name} 连接失败。",
            )
        elif state == "connected":
            await self.incidents.report_recovery(
                fingerprint=f"mcp:{name}:connection",
                source="mcp",
                message=f"MCP Server {name} 已恢复连接。",
            )


def _earliest_deadline(*deadlines: datetime | None) -> datetime | None:
    """返回现有能力 deadline 中最早的一项。"""
    available = [deadline for deadline in deadlines if deadline is not None]
    return min(available, default=None)


def _due(deadline: datetime | None, now: datetime) -> bool:
    """判断一个能力级 deadline 是否到期。"""
    return deadline is not None and now >= deadline


def _task_active(task: asyncio.Task[None] | None) -> bool:
    """判断能力级后台 Task 是否尚未结束。"""
    return task is not None and not task.done()

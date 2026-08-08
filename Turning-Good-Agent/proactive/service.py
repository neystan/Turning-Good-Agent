from __future__ import annotations

import asyncio
import logging
from copy import deepcopy
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..sessions.token_counter import count_content_tokens
from ..sessions.types import MessageRecord
from ..skills.manager import SkillManager
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

if TYPE_CHECKING:
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
        on_domain_change: Callable[[str], Awaitable[None]] | None = None,
        on_idle: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """初始化对象状态。"""
        self.runtime = runtime
        self.store = ProactiveStore(runtime.settings.data_dir)
        self.notifications = notification_publisher
        self._on_domain_change = on_domain_change
        self._on_idle = on_idle
        self.incidents = IncidentMonitor(
            self.store,
            self.notifications,
            state_changed=lambda: self._domain_changed("incident"),
        )
        self._background_semaphore = asyncio.Semaphore(runtime.settings.proactive.background_max_concurrency)
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
        primary = _BoundedExecutor(executor, self._background_semaphore)
        review = _BoundedExecutor(
            executor,
            self._background_semaphore,
            use_review_model=review_llm is not None,
        )
        self.cron = CronManager(
            self.store,
            primary,
            self.notifications,
            runtime.settings.proactive,
            incidents=self.incidents,
            schedule_changed=self._cron_schedule_changed,
            idle_changed=self._request_idle_check,
            available_tool_names=self._available_tool_names,
        )
        self.breakbeat = BreakbeatManager(
            self.store,
            runtime.sessions,
            review,
            runtime.settings.proactive,
            schedule_changed=self._wake_scheduler,
            cron_jobs=self.cron.list_jobs,
        )
        self.dream = DreamManager(
            self.store,
            runtime.sessions,
            primary,
            runtime.profile_memory,
            runtime.settings.proactive,
            schedule_changed=self._wake_scheduler,
        )
        skills = getattr(runtime, "skills", None) or SkillManager(
            Path.cwd() / runtime.settings.skills.directory,
            runtime.settings.skills,
        )
        self._skills = skills
        self.skill_evolution = SkillEvolutionManager(
            self.store,
            runtime.sessions,
            review,
            primary,
            skills,
            runtime.settings.proactive,
            schedule_changed=self._wake_scheduler,
        )
        self._running = False
        self._accepting_work = False
        self._scheduler_task: asyncio.Task[None] | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._breakbeat_task: asyncio.Task[None] | None = None
        self._dream_task: asyncio.Task[None] | None = None
        self._skill_evolution_task: asyncio.Task[None] | None = None
        self._mcp_listener: Callable[[Any], None] | None = None
        self._installed_tools: list[Any] = []
        runtime.proactive.register(self)

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
        return self.cron.is_idle and not self._background_tasks

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
            CreateCronTool(self.cron),
            ListCronsTool(self.cron),
            DeleteCronTool(self.cron),
            RunBreakbeatTool(
                self.breakbeat,
                active_session_id=self._current_session_id,
                current_message=self._current_message_record,
                on_completed=self._manual_breakbeat_completed,
            ),
            ListBreakbeatTool(self.breakbeat),
            CompleteBreakbeatTool(self.breakbeat),
            DeleteBreakbeatTool(self.breakbeat),
            RunDreamTool(
                self.dream,
                active_session_id=self._current_session_id,
                current_message=self._current_message_record,
                on_completed=self._manual_dream_completed,
            ),
            ReadProfileMemoryTool(self.runtime.profile_memory),
            RunSkillEvolutionTool(
                self.skill_evolution,
                on_completed=self._manual_skill_evolution_completed,
            ),
            ListSkillDraftsTool(self.skill_evolution),
            DeleteSkillDraftTool(self._skills),
            ListIncidentsTool(self.incidents),
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
        self._running = True
        self._accepting_work = True
        now = datetime.now(UTC)
        self.cron.initialize_schedule(now)
        self.breakbeat.initialize_schedule(now)
        self.dream.initialize_schedule(now)
        self.skill_evolution.initialize_schedule(now)
        self._install_mcp_listener()
        self._scheduler_task = asyncio.create_task(self._scheduler_loop(), name="proactive-scheduler")

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
        self._breakbeat_task = None
        self._dream_task = None
        self._skill_evolution_task = None
        await self.cron.close()

    async def drain_for_replacement(self) -> None:
        """停止接收新工作，并等待当前后台能力到达安全空闲点。"""
        self._accepting_work = False
        self._wake_scheduler()
        await self.cron.wait_for_jobs()
        while self._background_tasks:
            await asyncio.gather(*tuple(self._background_tasks), return_exceptions=True)

    async def handle(self, event: str, payload: dict[str, object]) -> None:
        """接收 SAVE 后的轻量事实，不延长前台回复路径。"""
        if event != CONVERSATION_COMPLETED or not self._running or not self._accepting_work:
            return
        session_id = payload.get("session_id")
        if isinstance(session_id, str) and session_id:
            assistant_message_id = payload.get("completed_assistant_message_id")
            self._spawn(
                self._observe_session(
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
            try:
                if await self.cron.run_due(now):
                    await self._domain_changed("cron")
                if _due(self.breakbeat.next_run_at, now) and not self.breakbeat.running and not _task_active(
                    self._breakbeat_task
                ):
                    self._breakbeat_task = self._spawn(self._run_breakbeat_tick(), wake_on_done=True)
                if _due(self.dream.next_run_at, now) and not self.dream.running and not _task_active(self._dream_task):
                    self._dream_task = self._spawn(self._run_dream_tick(), wake_on_done=True)
                if (
                    _due(self.skill_evolution.next_run_at, now)
                    and not self.skill_evolution.running
                    and not _task_active(self._skill_evolution_task)
                ):
                    self._skill_evolution_task = self._spawn(
                        self._run_skill_evolution_tick(),
                        wake_on_done=True,
                    )
            except asyncio.CancelledError:
                raise
            except Exception:
                await self.incidents.report_failure(
                    fingerprint="proactive:scheduler",
                    source="proactive",
                    message="主动任务调度器执行失败。",
                )
            deadline = _earliest_deadline(
                self.cron.next_deadline(now),
                None if _task_active(self._breakbeat_task) else self.breakbeat.next_run_at,
                None if _task_active(self._dream_task) else self.dream.next_run_at,
                None if _task_active(self._skill_evolution_task) else self.skill_evolution.next_run_at,
            )
            if self._schedule_event.is_set():
                continue
            timeout = None if deadline is None else max(0.0, (deadline - datetime.now(UTC)).total_seconds())
            try:
                if timeout is None:
                    await self._schedule_event.wait()
                else:
                    await asyncio.wait_for(self._schedule_event.wait(), timeout=timeout)
            except TimeoutError:
                pass

    async def _observe_session(
        self,
        session_id: str,
        completed_assistant_message_id: str | None,
    ) -> None:
        """执行当前处理。"""
        try:
            await self.skill_evolution.maybe_observe(
                session_id,
                completed_assistant_message_id=completed_assistant_message_id,
            )
        except Exception:
            await self.incidents.report_failure(
                fingerprint=f"proactive:observation:{session_id}",
                source="skill_evolution",
                message="Skill Observation 审阅失败。",
            )
        finally:
            await self._domain_changed("skill")

    async def _run_breakbeat_tick(self) -> None:
        """执行当前处理。"""
        try:
            outcome = await self.breakbeat.run(manual=False, scope="global")
            status = getattr(outcome, "status", "success")
            created = getattr(outcome, "created", 0)
            should_notify = getattr(outcome, "should_notify", created > 0)
            if status == "success" and (created or should_notify):
                await self.notifications.enqueue(
                    event_type="proactive.breakbeat.completed",
                    content=outcome.summary,
                    source_id="breakbeat",
                    notification_scope="all_subscribed",
                )
            elif status == "partial":
                await self.notifications.enqueue(
                    event_type="proactive.breakbeat.partial",
                    content=outcome.summary,
                    source_id="breakbeat",
                    notification_scope="all_subscribed",
                )
            elif status == "failed":
                await self.notifications.enqueue(
                    event_type="proactive.breakbeat.failed",
                    content=outcome.summary,
                    source_id="breakbeat",
                    notification_scope="all_subscribed",
                )
            if status != "success":
                await self.incidents.report_failure(
                    fingerprint="proactive:breakbeat",
                    source="breakbeat",
                    message="Breakbeat 审阅失败。",
                    notify=False,
                )
            else:
                await self.incidents.report_recovery(
                    fingerprint="proactive:breakbeat",
                    source="breakbeat",
                    message="Breakbeat 审阅已恢复。",
                    notify=False,
                )
        except Exception:
            await self.incidents.report_failure(
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
                )
            except Exception:
                logger.exception("Breakbeat 失败结果通知发布失败")
        finally:
            await self._domain_changed("breakbeat")

    async def _run_dream_tick(self) -> None:
        """执行当前处理。"""
        try:
            outcome = await self.dream.run(manual=False, scope="global")
            if outcome.changed:
                await self.notifications.enqueue(
                    event_type="proactive.dream.completed",
                    content=outcome.summary,
                    source_id="dream",
                    notification_scope="all_subscribed",
                )
            if outcome.errors:
                await self.incidents.report_failure(
                    fingerprint="proactive:dream",
                    source="dream",
                    message="Dream 审阅失败。",
                )
            else:
                await self.incidents.report_recovery(
                    fingerprint="proactive:dream",
                    source="dream",
                    message="Dream 审阅已恢复。",
                )
        except Exception:
            await self.incidents.report_failure(
                fingerprint="proactive:dream",
                source="dream",
                message="Dream 审阅失败。",
            )
        finally:
            await self._domain_changed("dream")

    async def _run_skill_evolution_tick(self) -> None:
        """执行当前处理。"""
        try:
            outcome = await self.skill_evolution.run_evolution(manual=False)
            if outcome.created_count:
                await self.notifications.enqueue(
                    event_type="proactive.skill_evolution.completed",
                    content=outcome.summary,
                    source_id="skill_evolution",
                    notification_scope="all_subscribed",
                )
            await self.incidents.report_recovery(
                fingerprint="proactive:skill_evolution",
                source="skill_evolution",
                message="Skill 自进化审阅已恢复。",
            )
        except Exception:
            await self.incidents.report_failure(
                fingerprint="proactive:skill_evolution",
                source="skill_evolution",
                message="Skill 自进化审阅失败。",
            )
        finally:
            await self._domain_changed("skill")

    def _spawn(self, coroutine: Awaitable[None], *, wake_on_done: bool = False) -> asyncio.Task[None]:
        """执行当前处理。"""
        task = asyncio.create_task(coroutine)
        self._background_tasks.add(task)
        task.add_done_callback(lambda completed: self._background_finished(completed, wake_on_done=wake_on_done))
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

    def _cron_schedule_changed(self) -> None:
        """Cron 状态改变时同时刷新 deadline 与 Web 运行时投影。"""
        self._wake_scheduler()
        if self._running and self._on_domain_change is not None:
            self._spawn(self._domain_changed("cron"))

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

    async def _manual_breakbeat_completed(self, outcome: Any) -> None:
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
            )

    async def _manual_dream_completed(self, outcome: Any) -> None:
        if outcome.changed:
            await self._publish_manual_result(
                domain="dream",
                event_type="proactive.dream.completed",
                content=outcome.summary,
                source_id="dream",
            )

    async def _manual_skill_evolution_completed(self, outcome: Any) -> None:
        if outcome.created_count:
            await self._publish_manual_result(
                domain="skill",
                event_type="proactive.skill_evolution.completed",
                content=outcome.summary,
                source_id="skill_evolution",
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
        if action_name == "run_breakbeat":
            outcome = await self.breakbeat.run(
                manual=True,
                scope=scope,
                session_id=session_id if scope == "session" else None,
            )
            await self._manual_breakbeat_completed(outcome)
            if not outcome.should_notify:
                await self._publish_manual_result(
                    domain="breakbeat",
                    event_type="proactive.breakbeat.completed",
                    content=outcome.summary,
                    source_id="breakbeat",
                )
            await self._domain_changed("breakbeat")
            return outcome.summary
        if action_name == "run_dream":
            outcome = await self.dream.run(
                manual=True,
                scope=scope,
                session_id=session_id if scope == "session" else None,
            )
            await self._manual_dream_completed(outcome)
            if not outcome.changed:
                await self._publish_manual_result(
                    domain="dream",
                    event_type="proactive.dream.reviewed",
                    content=outcome.summary,
                    source_id="dream",
                )
            await self._domain_changed("dream")
            return outcome.summary
        if action_name == "run_skill_evolution":
            outcome = await self.skill_evolution.run_evolution(manual=True)
            await self._manual_skill_evolution_completed(outcome)
            if not outcome.created_count:
                await self._publish_manual_result(
                    domain="skill",
                    event_type="proactive.skill_evolution.completed",
                    content=outcome.summary,
                    source_id="skill_evolution",
                )
            await self._domain_changed("skill")
            return outcome.summary
        raise ValueError("不支持的主动 Catalog 动作")

    async def _publish_manual_result(
        self,
        *,
        domain: str,
        event_type: str,
        content: str,
        source_id: str,
    ) -> None:
        route = current_route()
        if route is not None:
            try:
                await self.notifications.enqueue(
                    event_type=event_type,
                    content=content,
                    source_id=source_id,
                    notification_scope="origin",
                    origin_route=route,
                )
            except Exception:
                logger.exception("手动主动结果通知发布失败")
        await self._domain_changed(domain)

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

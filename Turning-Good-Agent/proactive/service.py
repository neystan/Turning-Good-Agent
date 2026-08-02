from __future__ import annotations

import asyncio
from copy import deepcopy
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ..bus.messages import InboundMessage
from ..sessions.token_counter import count_content_tokens
from ..sessions.types import MessageRecord
from ..skills.manager import SkillManager
from ..llm.factory import build_llm
from ..runtime.agent_loop import AgentLoop
from .breakbeat import (
    BreakbeatManager,
    CompleteBreakbeatTool,
    DeleteBreakbeatTool,
    ListBreakbeatTool,
    RunBreakbeatTool,
)
from .cron import CreateCronTool, CronManager, DeleteCronTool, ListCronsTool
from .delivery import DeliveryGate
from .dream import DreamManager, ReadProfileMemoryTool, RunDreamTool
from .events import CONVERSATION_COMPLETED
from .executor import ProactiveExecutionResult, ProactiveExecutor
from .incidents import IncidentMonitor, ListIncidentsTool
from .skill_evolution import (
    DeleteSkillDraftTool,
    ListSkillDraftsTool,
    RunSkillEvolutionTool,
    SkillEvolutionManager,
)
from .store import ProactiveStore

if TYPE_CHECKING:
    from ..bus.queue import AsyncMessageBus
    from ..runtime.runtime import AgentRuntime


class _BoundedExecutor:
    """让所有后台模型工作共享独立且有界的并发池。"""

    def __init__(self, delegate: ProactiveExecutor, semaphore: asyncio.Semaphore) -> None:
        """初始化对象状态。"""
        self._delegate = delegate
        self._semaphore = semaphore

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
            )


class ProactiveService:
    """仅供 tga chat 启动的主动能力生命周期与后台调度装配层。"""

    def __init__(
        self,
        runtime: "AgentRuntime",
        bus: "AsyncMessageBus",
        *,
        target_channel: str,
        active_session_id: Callable[[], str | None] | None = None,
        active_inbound_message: Callable[[], InboundMessage | None] | None = None,
        delivery_sink: Any | None = None,
        on_domain_change: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        """初始化对象状态。"""
        self.runtime = runtime
        self._bus = bus
        self._target_channel = target_channel
        self._active_session_id = active_session_id or (lambda: None)
        self._active_inbound_message = active_inbound_message or (lambda: None)
        self.store = ProactiveStore(runtime.settings.data_dir)
        self.delivery = delivery_sink or DeliveryGate(
            self.store,
            bus,
            idle_probe=runtime.is_globally_idle,
            active_session_id=self._active_session_id if target_channel == "cli" else None,
        )
        self._on_domain_change = on_domain_change
        self.incidents = IncidentMonitor(
            self.store,
            self.delivery,
            target_channel=target_channel,
            state_changed=lambda: self._domain_changed("incident"),
        )
        self._background_semaphore = asyncio.Semaphore(runtime.settings.proactive.background_max_concurrency)
        self._schedule_event = asyncio.Event()
        primary = _BoundedExecutor(
            ProactiveExecutor(
                runtime.agent_loop,
                approval_required_tool_names=frozenset(
                    runtime.settings.tool_permissions.approval_required_tools
                ),
            ),
            self._background_semaphore,
        )
        review = _BoundedExecutor(
            ProactiveExecutor(
                self._review_loop(),
                approval_required_tool_names=frozenset(
                    runtime.settings.tool_permissions.approval_required_tools
                ),
            ),
            self._background_semaphore,
        )
        self.cron = CronManager(
            self.store,
            primary,
            self.delivery,
            runtime.settings.proactive,
            target_channel=target_channel,
            incidents=self.incidents,
            schedule_changed=self._cron_schedule_changed,
        )
        self.breakbeat = BreakbeatManager(
            self.store,
            runtime.sessions,
            review,
            runtime.settings.proactive,
            schedule_changed=self._wake_scheduler,
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
        self._scheduler_task: asyncio.Task[None] | None = None
        self._delivery_task: asyncio.Task[None] | None = None
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._breakbeat_task: asyncio.Task[None] | None = None
        self._dream_task: asyncio.Task[None] | None = None
        self._skill_evolution_task: asyncio.Task[None] | None = None
        self._mcp_listener: Callable[[Any], None] | None = None
        runtime.proactive.register(self)

    def _review_loop(self) -> AgentLoop:
        """按可选 review_model 创建轻量模型入口，不创建第二个 Runtime。"""
        proactive = self.runtime.settings.proactive
        if proactive.review_model is None:
            return self.runtime.agent_loop
        settings = deepcopy(self.runtime.settings)
        settings.llm.provider = proactive.review_provider
        settings.llm.api_key = proactive.review_api_key
        settings.llm.base_url = proactive.review_base_url
        settings.llm.model = proactive.review_model
        return AgentLoop(
            build_llm(settings),
            self.runtime.agent_loop.tools,
            self.runtime.settings.runtime,
            streaming_enabled=False,
            hooks=self.runtime.hooks,
            attachment_context_token_limit=self.runtime.agent_loop.attachment_context_token_limit,
            skills=self.runtime.settings.skills,
        )

    @property
    def running(self) -> bool:
        """返回同进程调度器是否已启动。"""
        return self._running

    def install_tools(self) -> None:
        """仅在 CLI Runtime 上注册主动能力 Tool。"""
        registry = self.runtime.agent_loop.tools
        tools = [
            CreateCronTool(self.cron),
            ListCronsTool(self.cron),
            DeleteCronTool(self.cron),
            RunBreakbeatTool(
                self.breakbeat,
                active_session_id=self._active_session_id,
                current_message=self._current_message_record,
            ),
            ListBreakbeatTool(self.breakbeat),
            CompleteBreakbeatTool(self.breakbeat),
            DeleteBreakbeatTool(self.breakbeat),
            RunDreamTool(
                self.dream,
                active_session_id=self._active_session_id,
                current_message=self._current_message_record,
            ),
            ReadProfileMemoryTool(self.runtime.profile_memory),
            RunSkillEvolutionTool(self.skill_evolution),
            ListSkillDraftsTool(self.skill_evolution),
            DeleteSkillDraftTool(self._skills),
            ListIncidentsTool(self.incidents),
        ]
        for tool in tools:
            if not registry.has(tool.name):
                registry.register(tool)

    async def start(self) -> None:
        """恢复 durable 状态并启动未来触发，不补跑错过时间。"""
        if self._running or not self.runtime.settings.proactive.enabled:
            return
        self._running = True
        now = datetime.now(UTC)
        self.breakbeat.initialize_schedule(now)
        self.dream.initialize_schedule(now)
        self.skill_evolution.initialize_schedule(now)
        self._install_mcp_listener()
        self._scheduler_task = asyncio.create_task(self._scheduler_loop(), name="proactive-scheduler")
        if callable(getattr(self.delivery, "flush", None)):
            self._delivery_task = asyncio.create_task(self._delivery_loop(), name="proactive-delivery")

    async def stop(self) -> None:
        """停止调度、取消尚未结束的后台工作并保留已有 durable 结果。"""
        self._running = False
        self._remove_mcp_listener()
        for task in (self._scheduler_task, self._delivery_task):
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *(task for task in (self._scheduler_task, self._delivery_task) if task is not None),
            return_exceptions=True,
        )
        self._scheduler_task = None
        self._delivery_task = None
        for task in list(self._background_tasks):
            task.cancel()
        if self._background_tasks:
            await asyncio.gather(*self._background_tasks, return_exceptions=True)
        self._background_tasks.clear()
        self._breakbeat_task = None
        self._dream_task = None
        self._skill_evolution_task = None
        await self.cron.close()

    async def handle(self, event: str, payload: dict[str, object]) -> None:
        """接收 SAVE 后的轻量事实，不延长前台回复路径。"""
        if event != CONVERSATION_COMPLETED or not self._running:
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

    async def _delivery_loop(self) -> None:
        """执行当前处理。"""
        while self._running:
            try:
                await self.delivery.flush()
            except asyncio.CancelledError:
                raise
            except Exception:
                await self.incidents.report_failure(
                    fingerprint="proactive:delivery",
                    source="proactive",
                    message="主动通知投递失败。",
                )
            await asyncio.sleep(0.1)

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
            if outcome.should_notify:
                await self.delivery.enqueue(
                    target_channel=self._target_channel,
                    event_type="proactive.breakbeat.completed",
                    content=outcome.summary,
                    source_id="breakbeat",
                )
            if outcome.errors:
                await self.incidents.report_failure(
                    fingerprint="proactive:breakbeat",
                    source="breakbeat",
                    message="Breakbeat 审阅失败。",
                )
            else:
                await self.incidents.report_recovery(
                    fingerprint="proactive:breakbeat",
                    source="breakbeat",
                    message="Breakbeat 审阅已恢复。",
                )
        except Exception:
            await self.incidents.report_failure(
                fingerprint="proactive:breakbeat",
                source="breakbeat",
                message="Breakbeat 审阅失败。",
            )
        finally:
            await self._domain_changed("breakbeat")

    async def _run_dream_tick(self) -> None:
        """执行当前处理。"""
        try:
            outcome = await self.dream.run(manual=False, scope="global")
            if outcome.changed:
                await self.delivery.enqueue(
                    target_channel=self._target_channel,
                    event_type="proactive.dream.completed",
                    content=outcome.summary,
                    source_id="dream",
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
                await self.delivery.enqueue(
                    target_channel=self._target_channel,
                    event_type="proactive.skill_evolution.completed",
                    content=outcome.summary,
                    source_id="skill_evolution",
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
        task.add_done_callback(self._background_tasks.discard)
        if wake_on_done:
            task.add_done_callback(lambda _: self._wake_scheduler())
        return task

    def _wake_scheduler(self) -> None:
        """通知 deadline 循环立即重新计算等待时长。"""
        self._schedule_event.set()

    def _cron_schedule_changed(self) -> None:
        """Cron 状态改变时同时刷新 deadline 与 Web 运行时投影。"""
        self._wake_scheduler()
        if self._running and self._on_domain_change is not None:
            self._spawn(self._domain_changed("cron"))

    async def _domain_changed(self, domain: str) -> None:
        if self._on_domain_change is not None:
            await self._on_domain_change(domain)

    def _current_message_record(self) -> MessageRecord | None:
        """把尚未 SAVE 的 CLI 入站消息转换为一次性审阅记录。"""
        message = self._active_inbound_message()
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

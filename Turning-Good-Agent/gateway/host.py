from __future__ import annotations

import asyncio
import os
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any

from ..bus.messages import ChannelRoute, OutboundMessage, Recipient
from ..bus.queue import AsyncMessageBus
from ..channels.cli_gateway import CliGatewayCoordinator, CliGatewayTransport
from ..channels.manager import ChannelManager
from ..channels.web import WebChannelTransport
from ..config.settings import Settings
from ..llm.factory import build_llm
from ..proactive.notifications import (
    GatewayNotificationPublisher,
    NotificationFanout,
    NotificationSubscription,
)
from ..proactive.service import ProactiveService
from ..runtime.runtime import AgentRuntime
from ..web.backend.config_control import WebConfigControlService
from ..web.backend.coordinator import WebSessionCoordinator
from ..web.backend.proactive_control import WebProactiveControlService
from ..web.backend.proactive_events import GatewayProactiveState, ProactiveEventHub
from .auth import load_or_create_gateway_token
from .catalog_actions import CatalogActionExecutor
from .instance_lock import GatewayInstanceLock
from .runtime_supervisor import RuntimeSupervisor
from .turns import GatewayTurnCoordinator


RuntimeFactory = Callable[[], Awaitable[AgentRuntime]]


class GatewayHost:
    """唯一装配 Runtime、消息总线、Channel 和主动能力的进程 Host。"""

    def __init__(
        self,
        settings: Settings,
        *,
        runtime: AgentRuntime | None = None,
        runtime_factory: RuntimeFactory | None = None,
    ) -> None:
        self.settings = settings
        self.bus = AsyncMessageBus()
        self.instance_lock = GatewayInstanceLock(settings.data_dir)
        self.proactive_owner_state = GatewayProactiveState(owner_id=f"gateway-{os.getpid()}")
        self._config_path = settings.local_config_path or Path.cwd() / "settings.local.json"
        self._runtime_factory = runtime_factory or self._build_replacement_runtime
        self._uses_default_runtime_factory = runtime_factory is None
        initial_runtime = runtime or AgentRuntime.create_default(settings, build_llm(settings))
        self._initial_runtime = initial_runtime
        self._started = False
        self._lifecycle_lock = asyncio.Lock()
        self._turn_slots = asyncio.Semaphore(settings.web.max_concurrent_sessions)

        self.web_coordinator = WebSessionCoordinator(
            submit=self._submit_turn,
            complete_route_turn=self._complete_route_turn,
            runtime_provider=self._acquire_runtime,
            principal_id=settings.gateway.principal_id,
            event_buffer_size=settings.web.event_buffer_size,
            on_idle=self._notify_idle,
            bus=self.bus,
            execution_semaphore=self._turn_slots,
        )
        self.proactive_control = WebProactiveControlService(initial_runtime)
        self.proactive_events = ProactiveEventHub(self.proactive_control, self.proactive_owner_state)
        self.web_transport = WebChannelTransport(self.web_coordinator, self._publish_web_notice)
        self.cli_transport = CliGatewayTransport()
        self.cli_coordinator = CliGatewayCoordinator(
            self.bus,
            submit=self._submit_turn,
            complete_route_turn=self._complete_route_turn,
            discard_pending_route=self._discard_pending_route,
            on_idle=self._notify_idle,
            execution_semaphore=self._turn_slots,
        )
        self.channel_manager = ChannelManager(self.bus, (self.web_transport, self.cli_transport))
        self.channel_manager.add_delivery_listener(self.cli_coordinator.on_delivery)
        self.notification_fanout = NotificationFanout(
            subscriptions=self._notification_subscriptions,
            classify_recipient=self._classify_notification_recipient,
        )
        self.notification_publisher = GatewayNotificationPublisher(
            fanout=self.notification_fanout,
            publish_outbound=self.bus.publish_outbound,
            active_cli_route=self.cli_transport.active_route,
            principal_id=settings.gateway.principal_id,
        )
        self.proactive_service = self._create_proactive_service(initial_runtime)
        self._install_proactive_tools(self.proactive_service)
        self.catalog_actions = CatalogActionExecutor(
            runtime_provider=self._acquire_runtime,
            proactive_provider=lambda: self.proactive_service,
            web_coordinator=self.web_coordinator,
        )

        self.turn_coordinator = GatewayTurnCoordinator(
            self.bus,
            self._acquire_runtime,
            max_concurrent_turns=settings.web.max_concurrent_sessions,
            on_idle=self._notify_idle,
            execution_semaphore=self._turn_slots,
        )
        self.config_control = WebConfigControlService(
            self._config_path,
            native_tool_names=self._native_tool_names(initial_runtime),
            live_tool_names=self._live_tool_names,
            unavailable_approval_names=self._unavailable_approval_names,
        )
        initial_config = self.config_control.read_desired()
        self.runtime_supervisor = RuntimeSupervisor(
            initial_runtime,
            runtime_factory=self._runtime_factory,
            idle_probe=self._is_globally_idle,
            active_revision=initial_config.revision,
            on_prepare=self._prepare_runtime,
            on_activate=self._activate_runtime,
        )
        self.web_coordinator.register_runtime(initial_runtime)
        self.cli_coordinator.register_runtime(initial_runtime)
        self.proactive_control.replace_runtime(initial_runtime, self.proactive_service)

    @property
    def current_runtime(self) -> AgentRuntime:
        """返回当前已提交的唯一 Runtime。"""
        supervisor = getattr(self, "runtime_supervisor", None)
        return self._initial_runtime if supervisor is None else supervisor.current_runtime

    async def start(self) -> None:
        """获取单实例锁并启动 Gateway 所有长期运行组件。"""
        async with self._lifecycle_lock:
            if self._started:
                return
            self.instance_lock.acquire()
            try:
                if self.settings.gateway.auth_token is None:
                    self.settings.gateway.auth_token = load_or_create_gateway_token(self._config_path)
                await self.runtime_supervisor.start()
                await self.channel_manager.start()
                await self.web_coordinator.start()
                await self.cli_coordinator.start()
                await self.current_runtime.start()
                await self.proactive_service.start()
                await self.turn_coordinator.start()
                self._started = True
                await self.proactive_events.publish_all()
            except BaseException:
                await self._stop_components()
                self.instance_lock.close()
                raise

    async def close(self) -> None:
        """停止 Gateway 全部组件并释放单实例锁。"""
        async with self._lifecycle_lock:
            if not self._started:
                return
            self._started = False
            try:
                await self.runtime_supervisor.stop_reloads()
                await self._stop_components()
            finally:
                self.instance_lock.close()

    async def _submit_turn(self, message: Any, *, dispatch: Any) -> bool:
        return await self.turn_coordinator.submit(message, dispatch=dispatch)

    async def _complete_route_turn(self, route: ChannelRoute, request_id: str) -> None:
        await self.turn_coordinator.complete_route_turn(route, request_id)

    async def _discard_pending_route(self, route: ChannelRoute) -> None:
        await self.turn_coordinator.discard_pending_route(route)

    async def _acquire_runtime(self) -> AgentRuntime:
        return await self.runtime_supervisor.acquire_runtime()

    async def _notify_idle(self) -> None:
        await self.runtime_supervisor.notify_idle()

    def _is_globally_idle(self) -> bool:
        return (
            self.turn_coordinator.is_globally_idle()
            and self.web_coordinator.is_globally_idle()
            and self.cli_coordinator.is_globally_idle()
            and bool(self.proactive_service.is_idle)
        )

    async def _build_replacement_runtime(self) -> AgentRuntime:
        replacement_settings = Settings.load(local_config_path=self._config_path)
        return AgentRuntime.create_default(replacement_settings, build_llm(replacement_settings))

    async def request_runtime_reload(self, revision: str):
        """把本次 Apply 的完整设置快照绑定到对应 replacement Runtime。"""
        runtime_factory = self._runtime_factory
        if self._uses_default_runtime_factory:
            replacement_settings = Settings.load(local_config_path=self._config_path)

            async def runtime_factory() -> AgentRuntime:
                return AgentRuntime.create_default(
                    replacement_settings,
                    build_llm(replacement_settings),
                )

        return await self.runtime_supervisor.request_reload(revision, runtime_factory=runtime_factory)

    def _create_proactive_service(self, runtime: AgentRuntime) -> ProactiveService:
        return ProactiveService(
            runtime,
            notification_publisher=self.notification_publisher,
            on_domain_change=self._publish_domain_change,
            on_idle=self._notify_idle,
        )

    async def _prepare_runtime(self, replacement: AgentRuntime) -> None:
        self.web_coordinator.register_runtime(replacement)
        self.cli_coordinator.register_runtime(replacement)

    async def _activate_runtime(self, replacement: AgentRuntime) -> None:
        previous = self.proactive_service
        candidate: ProactiveService | None = None
        await previous.drain_for_replacement()
        previous.uninstall_tools()
        await previous.stop()
        try:
            candidate = self._create_proactive_service(replacement)
            self._install_proactive_tools(candidate)
            await candidate.start()
            self.proactive_service = candidate
            self.proactive_control.replace_runtime(replacement, candidate)
            await self.proactive_events.publish_all()
        except BaseException:
            if candidate is not None:
                candidate.uninstall_tools()
                await candidate.stop()
            self.proactive_service = previous
            self.proactive_control.replace_runtime(previous.runtime, previous)
            self._install_proactive_tools(previous)
            await previous.start()
            raise

    async def _publish_domain_change(self, domain: str) -> None:
        await self.proactive_events.publish_snapshot(domain, changed=True)  # type: ignore[arg-type]

    async def _publish_web_notice(self, message: OutboundMessage) -> bool:
        """将主动结果投影到应用级 Web Hub，绝不写入聊天会话。"""
        domain = _web_domain(message.event_type)
        await self.proactive_events.publish_notice(
            domain=domain,  # type: ignore[arg-type]
            entity_id=str(message.metadata.get("proactive_source_id", message.event_id)),
            severity=_web_notice_severity(message.event_type),
            title=_web_notice_title(message.event_type),
            message=message.content,
            target=_web_target(domain),
        )
        await self.proactive_events.publish_snapshot(domain, changed=False)  # type: ignore[arg-type]
        return True

    def _notification_subscriptions(self, principal_id: str) -> Iterable[NotificationSubscription]:
        if principal_id != self.settings.gateway.principal_id:
            return ()
        subscriptions = [
            NotificationSubscription(
                Recipient(principal_id, "web", "proactive"),
                enabled=True,
            ),
        ]
        active_cli_route = self.cli_transport.active_route(principal_id)
        if active_cli_route is not None:
            subscriptions.append(
                NotificationSubscription(
                    Recipient(
                        active_cli_route.principal_id,
                        active_cli_route.channel,
                        active_cli_route.conversation_id,
                    ),
                    enabled=True,
                )
            )
        return tuple(subscriptions)

    def _classify_notification_recipient(self, recipient: Recipient) -> bool:
        if recipient.channel == "web":
            return True
        if recipient.channel == "cli":
            active_cli_route = self.cli_transport.active_route(recipient.principal_id)
            return active_cli_route is not None and (
                active_cli_route.channel,
                active_cli_route.conversation_id,
            ) == (recipient.channel, recipient.conversation_id)
        return False

    def _live_tool_names(self) -> set[str]:
        return set(self.current_runtime.agent_loop.tools.tool_names)

    def _unavailable_approval_names(self) -> set[str]:
        runtime = self.current_runtime
        return set(runtime.settings.tool_permissions.approval_required_tools) - self._live_tool_names()

    @staticmethod
    def _native_tool_names(runtime: AgentRuntime) -> set[str]:
        return {name for name in runtime.agent_loop.tools.tool_names if not name.startswith("mcp_")}

    @staticmethod
    def _install_proactive_tools(service: ProactiveService) -> None:
        if service.runtime.settings.proactive.enabled:
            service.install_tools()

    async def _stop_components(self) -> None:
        await self.catalog_actions.close()
        await self.turn_coordinator.close()
        await self.proactive_service.stop()
        await self.web_coordinator.close()
        await self.cli_coordinator.close()
        await self.channel_manager.close()
        await self.runtime_supervisor.close()


def _web_domain(event_type: str) -> str:
    if ".cron." in event_type:
        return "cron"
    if ".breakbeat." in event_type:
        return "breakbeat"
    if ".dream." in event_type:
        return "dream"
    if ".skill_evolution." in event_type:
        return "skill"
    return "incident"


def _web_target(domain: str) -> str:
    return {
        "cron": "#proactive/cron",
        "breakbeat": "#proactive/breakbeat",
        "dream": "#proactive/memory",
        "skill": "#proactive/skills",
        "incident": "#proactive/incidents",
    }[domain]


def _web_notice_title(event_type: str) -> str:
    return {
        "proactive.cron.completed": "定时提醒已完成",
        "proactive.breakbeat.completed": "Breakbeat 已完成",
        "proactive.breakbeat.partial": "Breakbeat 部分完成",
        "proactive.breakbeat.failed": "Breakbeat 执行失败",
        "proactive.dream.completed": "长期记忆已更新",
        "proactive.dream.reviewed": "Dream 审阅已完成",
        "proactive.skill_evolution.completed": "Skill 演进已完成",
        "proactive.incident.opened": "发现主动能力异常",
        "proactive.incident.resolved": "主动能力已恢复",
    }.get(event_type, "主动能力更新")


def _web_notice_severity(event_type: str) -> str:
    """将领域结果映射到 Web notice 的可见严重级别。"""
    if event_type in {"proactive.incident.opened", "proactive.breakbeat.failed"}:
        return "error"
    if event_type == "proactive.breakbeat.partial":
        return "warning"
    return "info"

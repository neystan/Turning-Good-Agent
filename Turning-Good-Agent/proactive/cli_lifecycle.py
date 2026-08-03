from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from ..bus.messages import InboundMessage
from ..bus.queue import AsyncMessageBus
from .bridge import CliBridgeDeliverySink, CliProactiveBridge
from .delivery import DeliveryGate
from .store import ProactiveStore
from .ownership import OwnershipState, ProactiveOwnershipLease
from .service import ProactiveService


logger = logging.getLogger(__name__)


class CliProactiveLifecycle:
    """让 CLI 在唯一主动服务所有权下保持可聊天、可自动接管。"""

    def __init__(
        self,
        runtime: Any,
        bus: AsyncMessageBus,
        *,
        target_channel: str,
        active_session_id: Callable[[], str | None],
        active_inbound_message: Callable[[], InboundMessage | None],
        ownership: ProactiveOwnershipLease | None = None,
        service_factory: Callable[[Any], ProactiveService] | None = None,
        poll_seconds: float = 1.0,
    ) -> None:
        self._runtime = runtime
        self._bus = bus
        self._target_channel = target_channel
        self._active_session_id = active_session_id
        self._active_inbound_message = active_inbound_message
        self._ownership = ownership or ProactiveOwnershipLease(runtime.settings.data_dir, owner_kind="cli")
        self._service_factory = service_factory or self._create_service
        self._poll_seconds = poll_seconds
        self._service: ProactiveService | None = None
        self._bridge = CliProactiveBridge(runtime, self._ownership.state, lambda: self._service)
        self._monitor_task: asyncio.Task[None] | None = None
        self._started = False
        self._lock = asyncio.Lock()

    @property
    def service(self) -> ProactiveService | None:
        return self._service

    @property
    def ownership(self) -> ProactiveOwnershipLease:
        return self._ownership

    async def start(self) -> OwnershipState:
        """启动后保持轮询，因此 readonly CLI 可在所有者退出后接管。"""
        self._started = True
        async with self._lock:
            await self._reconcile()
        self._monitor_task = asyncio.create_task(self._monitor(), name="cli-proactive-takeover")
        return self._ownership.state()

    async def stop(self) -> None:
        """先停止主动 Scheduler，再释放租约。"""
        self._started = False
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            await asyncio.gather(self._monitor_task, return_exceptions=True)
            self._monitor_task = None
        async with self._lock:
            await self._stop_service()
            await self._ownership.release_owner()

    async def _monitor(self) -> None:
        while self._started:
            await asyncio.sleep(self._poll_seconds)
            try:
                async with self._lock:
                    await self._reconcile()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("主动服务接管失败，将在下一轮重试")

    async def _reconcile(self) -> None:
        if not self._enabled():
            await self._stop_service()
            await self._ownership.release_owner()
            return
        if self._service is not None:
            if not self._ownership.state().writable:
                await self._stop_service()
            else:
                await self._bridge.retry_snapshots()
            return
        state = await self._ownership.try_takeover()
        if not state.writable:
            return
        service = self._service_factory(self._runtime)
        try:
            service.install_tools()
            await service.start()
        except Exception:
            await service.stop()
            await self._ownership.release_owner()
            raise
        self._service = service
        await self._publish_bridge_all()

    async def _stop_service(self) -> None:
        if self._service is not None:
            await self._service.stop()
            self._service = None

    def _create_service(self, runtime: Any) -> ProactiveService:
        delivery = DeliveryGate(
            ProactiveStore(runtime.settings.data_dir),
            self._bus,
            idle_probe=runtime.is_globally_idle,
            active_session_id=self._active_session_id,
        )
        return ProactiveService(
            runtime,
            self._bus,
            target_channel=self._target_channel,
            active_session_id=self._active_session_id,
            active_inbound_message=self._active_inbound_message,
            delivery_sink=CliBridgeDeliverySink(delivery, self._bridge),
            on_domain_change=self._bridge.publish_snapshot,
        )

    async def _publish_bridge_all(self) -> None:
        for domain in ("cron", "breakbeat", "dream", "skill", "incident"):
            await self._bridge.publish_snapshot(domain)

    def _enabled(self) -> bool:
        return bool(self._runtime.settings.proactive.enabled)

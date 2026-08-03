from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from .proactive_ownership import OwnershipState, ProactiveOwnershipLease


logger = logging.getLogger(__name__)


class WebProactiveLifecycle:
    """把唯一 Web 所有者的主动服务与 Runtime 原子地编排在一起。"""

    def __init__(
        self,
        runtime: Any,
        ownership: ProactiveOwnershipLease,
        *,
        service_factory: Callable[[Any], Any],
        bind_runtime: Callable[[Any, Any | None], None],
        publish_state: Callable[[], Awaitable[None]] | None = None,
        notify_idle: Callable[[], Awaitable[None]] | None = None,
        poll_seconds: float = 1.0,
    ) -> None:
        self._runtime = runtime
        self._ownership = ownership
        self._service_factory = service_factory
        self._bind_runtime = bind_runtime
        self._publish_state = publish_state
        self._notify_idle_callback = notify_idle
        self._poll_seconds = poll_seconds
        self._service: Any | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._started = False
        self._lock = asyncio.Lock()

    @property
    def service(self) -> Any | None:
        return self._service

    def is_idle(self) -> bool:
        """报告主动后台工作是否已到达可替换的安全空闲点。"""
        service = self._service
        if service is None:
            return True
        state = getattr(service, "is_idle", True)
        return bool(state() if callable(state) else state)

    async def start(self) -> OwnershipState:
        """仅在启用且获得租约后启动 Scheduler；只读 Host 保留聊天能力。"""
        self._started = True
        async with self._lock:
            await self._reconcile_current_runtime()
        self._monitor_task = asyncio.create_task(self._monitor(), name="web-proactive-takeover")
        await self._publish()
        return self._ownership.state()

    async def stop(self) -> None:
        """先停止后台服务，再释放跨进程租约。"""
        self._started = False
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            await asyncio.gather(self._monitor_task, return_exceptions=True)
            self._monitor_task = None
        async with self._lock:
            await self._stop_service()
            await self._ownership.release_owner()
        await self._publish()

    async def activate_replacement(self, replacement: Any) -> None:
        """在候选服务可启动前保留旧绑定；失败则重启旧服务并抛出。"""
        async with self._lock:
            old_service = self._service
            if not self._enabled(replacement):
                await self._drain_service()
                await self._stop_service()
                await self._ownership.release_owner()
                self._runtime = replacement
                self._bind_runtime(replacement, None)
                return
            if not self._ownership.state().writable:
                await self._stop_service()
                self._runtime = replacement
                self._bind_runtime(replacement, None)
                return
            if old_service is not None:
                await self._drain_service()
                await old_service.stop()
            candidate: Any | None = None
            try:
                candidate = self._service_factory(replacement)
                await candidate.start()
            except Exception:
                if candidate is not None:
                    await candidate.stop()
                if old_service is not None:
                    await old_service.start()
                raise
            self._runtime = replacement
            self._service = candidate
            self._bind_runtime(replacement, candidate)
        await self._publish()

    async def _monitor(self) -> None:
        while self._started:
            await asyncio.sleep(self._poll_seconds)
            try:
                async with self._lock:
                    previous_state = self._publication_state()
                    await self._reconcile_current_runtime()
                    state_changed = previous_state != self._publication_state()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("主动服务接管失败，将在下一轮重试")
                state_changed = False
            if state_changed:
                await self._publish()
            await self._notify_idle()

    async def _reconcile_current_runtime(self) -> None:
        if not self._enabled(self._runtime):
            await self._stop_service()
            await self._ownership.release_owner()
            self._bind_runtime(self._runtime, None)
            return
        if self._service is not None:
            if not self._ownership.state().writable:
                await self._stop_service()
                self._bind_runtime(self._runtime, None)
            return
        await self._ensure_owner_service()

    async def _ensure_owner_service(self) -> None:
        state = await self._ownership.try_takeover()
        if not state.writable or self._service is not None:
            self._bind_runtime(self._runtime, self._service)
            return
        candidate = self._service_factory(self._runtime)
        try:
            await candidate.start()
        except Exception:
            await candidate.stop()
            await self._ownership.release_owner()
            raise
        self._service = candidate
        self._bind_runtime(self._runtime, candidate)

    async def _stop_service(self) -> None:
        if self._service is not None:
            await self._service.stop()
            self._service = None

    async def _drain_service(self) -> None:
        if self._service is None:
            return
        drain = getattr(self._service, "drain_for_replacement", None)
        if callable(drain):
            await drain()

    async def _publish(self) -> None:
        if self._publish_state is not None:
            await self._publish_state()

    async def _notify_idle(self) -> None:
        if self._notify_idle_callback is not None:
            await self._notify_idle_callback()

    def _publication_state(self) -> tuple[bool, OwnershipState, int | None]:
        service_id = id(self._service) if self._service is not None else None
        return self._enabled(self._runtime), self._ownership.state(), service_id

    @staticmethod
    def _enabled(runtime: Any) -> bool:
        settings = getattr(runtime, "settings", None)
        proactive = getattr(settings, "proactive", None)
        return bool(getattr(proactive, "enabled", False))

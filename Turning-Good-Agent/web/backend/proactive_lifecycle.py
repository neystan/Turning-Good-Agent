from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from .proactive_ownership import OwnershipState, ProactiveOwnershipLease


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
        poll_seconds: float = 1.0,
    ) -> None:
        self._runtime = runtime
        self._ownership = ownership
        self._service_factory = service_factory
        self._bind_runtime = bind_runtime
        self._publish_state = publish_state
        self._poll_seconds = poll_seconds
        self._service: Any | None = None
        self._monitor_task: asyncio.Task[None] | None = None
        self._started = False
        self._lock = asyncio.Lock()

    @property
    def service(self) -> Any | None:
        return self._service

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
            async with self._lock:
                await self._reconcile_current_runtime()
            await self._publish()

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

    async def _publish(self) -> None:
        if self._publish_state is not None:
            await self._publish_state()

    @staticmethod
    def _enabled(runtime: Any) -> bool:
        settings = getattr(runtime, "settings", None)
        proactive = getattr(settings, "proactive", None)
        return bool(getattr(proactive, "enabled", False))

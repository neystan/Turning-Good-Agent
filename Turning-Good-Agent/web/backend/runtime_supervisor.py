from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable, Callable, Generic, TypeVar
import inspect


RuntimeT = TypeVar("RuntimeT")


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    state: str
    desired_revision: str
    active_revision: str
    last_apply_error: str | None


class RuntimeSupervisor(Generic[RuntimeT]):
    """在所有 Web turn 终态后原子替换整个 Runtime。"""

    def __init__(
        self,
        initial_runtime: RuntimeT,
        *,
        runtime_factory: Callable[[], Awaitable[RuntimeT]],
        idle_probe: Callable[[], bool],
        active_revision: str,
        on_prepare: Callable[[RuntimeT], object] | None = None,
        on_activate: Callable[[RuntimeT], object] | None = None,
    ) -> None:
        self._runtime = initial_runtime
        self._runtime_factory = runtime_factory
        self._idle_probe = idle_probe
        self._on_prepare = on_prepare
        self._on_activate = on_activate
        self._desired_revision = active_revision
        self._active_revision = active_revision
        self._state = "active"
        self._last_apply_error: str | None = None
        self._ready = asyncio.Event()
        self._ready.set()
        self._replace_lock = asyncio.Lock()

    @property
    def current_runtime(self) -> RuntimeT:
        return self._runtime

    async def start(self) -> None:
        """保留与 FastAPI lifespan 一致的监督器启动入口。"""

    async def close(self) -> None:
        runtime = self._runtime
        close = getattr(runtime, "close", None)
        if callable(close):
            await close()

    async def acquire_runtime(self) -> RuntimeT:
        await self._ready.wait()
        return self._runtime

    async def request_reload(self, revision: str) -> RuntimeStatus:
        self._desired_revision = revision
        self._last_apply_error = None
        if not self._idle_probe():
            self._state = "pending"
            return self.status()
        await self._reload()
        return self.status()

    async def notify_idle(self) -> None:
        if self._state == "pending" and self._idle_probe():
            await self._reload()

    def status(self) -> RuntimeStatus:
        return RuntimeStatus(self._state, self._desired_revision, self._active_revision, self._last_apply_error)

    async def _reload(self) -> None:
        async with self._replace_lock:
            if not self._idle_probe():
                self._state = "pending"
                return
            self._state = "applying"
            self._ready.clear()
            replacement: RuntimeT | None = None
            activated = False
            try:
                replacement = await self._runtime_factory()
                if self._on_prepare is not None:
                    await _await_if_needed(self._on_prepare(replacement))
                start = getattr(replacement, "start", None)
                if callable(start):
                    await start()
                if self._on_activate is not None:
                    await _await_if_needed(self._on_activate(replacement))
                previous = self._runtime
                self._runtime = replacement
                activated = True
                self._active_revision = self._desired_revision
                self._state = "active"
                close = getattr(previous, "close", None)
                if callable(close):
                    await close()
            except Exception as exc:
                self._state = "failed"
                self._last_apply_error = str(exc)
                if replacement is not None and not activated:
                    close = getattr(replacement, "close", None)
                    if callable(close):
                        await close()
            finally:
                self._ready.set()


async def _await_if_needed(value: object) -> None:
    if inspect.isawaitable(value):
        await value

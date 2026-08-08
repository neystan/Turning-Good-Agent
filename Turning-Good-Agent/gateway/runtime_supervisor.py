from __future__ import annotations

import asyncio
import inspect
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Generic, TypeVar


RuntimeT = TypeVar("RuntimeT")
logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class RuntimeStatus:
    state: str
    desired_revision: str
    active_revision: str
    last_apply_error: str | None


class RuntimeSupervisor(Generic[RuntimeT]):
    """在所有 Gateway turn 终态后原子替换整个 Runtime。"""

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
        self._desired_runtime_factory = runtime_factory
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
        self._closing = False

    @property
    def current_runtime(self) -> RuntimeT:
        return self._runtime

    async def start(self) -> None:
        """保留与 FastAPI lifespan 一致的监督器启动入口。"""
        self._closing = False

    async def stop_reloads(self) -> None:
        """阻止新的替换，并等待当前替换到达不会再激活候选的边界。"""
        self._closing = True
        async with self._replace_lock:
            pass

    async def close(self) -> None:
        await self.stop_reloads()
        runtime = self._runtime
        close = getattr(runtime, "close", None)
        if callable(close):
            await close()

    async def acquire_runtime(self) -> RuntimeT:
        await self._ready.wait()
        return self._runtime

    async def request_reload(
        self,
        revision: str,
        *,
        runtime_factory: Callable[[], Awaitable[RuntimeT]] | None = None,
    ) -> RuntimeStatus:
        if self._closing:
            raise RuntimeError("RuntimeSupervisor 已关闭")
        self._desired_revision = revision
        self._desired_runtime_factory = runtime_factory or self._runtime_factory
        self._last_apply_error = None
        if not self._idle_probe():
            self._state = "pending"
            return self.status()
        await self._reload()
        return self.status()

    async def notify_idle(self) -> None:
        if not self._closing and self._state == "pending" and self._idle_probe():
            await self._reload()

    def status(self) -> RuntimeStatus:
        return RuntimeStatus(self._state, self._desired_revision, self._active_revision, self._last_apply_error)

    async def _reload(self) -> None:
        async with self._replace_lock:
            if self._closing:
                return
            if not self._idle_probe():
                self._state = "pending"
                return
            replacement_revision = self._desired_revision
            replacement_factory = self._desired_runtime_factory
            self._state = "applying"
            self._ready.clear()
            replacement: RuntimeT | None = None
            activated = False
            try:
                replacement = await replacement_factory()
                if await self._discard_if_closing(replacement):
                    return
                if self._on_prepare is not None:
                    await _await_if_needed(self._on_prepare(replacement))
                if await self._discard_if_closing(replacement):
                    return
                start = getattr(replacement, "start", None)
                if callable(start):
                    await start()
                if await self._discard_if_closing(replacement):
                    return
                if self._on_activate is not None:
                    await _await_if_needed(self._on_activate(replacement))
                if await self._discard_if_closing(replacement):
                    return
                previous = self._runtime
                self._runtime = replacement
                activated = True
                self._active_revision = replacement_revision
                self._state = "active" if self._desired_revision == replacement_revision else "pending"
                await _close_previous_after_commit(previous)
            except asyncio.CancelledError:
                if replacement is not None and not activated:
                    await _close_failed_candidate(replacement)
                if self._closing:
                    self._state = "active"
                    self._last_apply_error = None
                else:
                    self._state = "failed"
                    self._last_apply_error = "Runtime 替换已取消"
                raise
            except Exception as exc:
                self._state = "failed"
                self._last_apply_error = str(exc)
                if replacement is not None and not activated:
                    await _close_failed_candidate(replacement)
            finally:
                self._ready.set()

    async def _discard_if_closing(self, replacement: RuntimeT) -> bool:
        """关闭期间丢弃候选，绝不在 Gateway 生命周期外提交它。"""
        if not self._closing:
            return False
        await _close_failed_candidate(replacement)
        self._state = "active"
        return True


async def _await_if_needed(value: object) -> None:
    if inspect.isawaitable(value):
        await value


async def _close_previous_after_commit(runtime: object) -> None:
    close = getattr(runtime, "close", None)
    if not callable(close):
        return
    try:
        await close()
    except Exception:
        logger.exception("Runtime 替换已提交，但关闭旧 Runtime 失败")


async def _close_failed_candidate(runtime: object) -> None:
    close = getattr(runtime, "close", None)
    if not callable(close):
        return
    try:
        await close()
    except Exception:
        logger.exception("Runtime 替换失败，候选 Runtime 清理失败")

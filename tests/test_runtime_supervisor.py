from __future__ import annotations

import asyncio
import importlib

supervisor_module = importlib.import_module("Turning-Good-Agent.web.backend.runtime_supervisor")


class FakeRuntime:
    def __init__(self) -> None:
        self.start_calls = 0
        self.close_calls = 0

    async def start(self) -> None:
        self.start_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


def test_reload_waits_for_idle_then_replaces_runtime() -> None:
    asyncio.run(_reload_waits_for_idle_then_replaces_runtime())


async def _reload_waits_for_idle_then_replaces_runtime() -> None:
    old_runtime = FakeRuntime()
    replacement = FakeRuntime()
    idle = False

    async def factory() -> FakeRuntime:
        return replacement

    supervisor = supervisor_module.RuntimeSupervisor(
        old_runtime,
        runtime_factory=factory,
        idle_probe=lambda: idle,
        active_revision="sha256:old",
    )

    await supervisor.request_reload("sha256:new")
    assert supervisor.status().state == "pending"
    assert old_runtime.close_calls == 0

    idle = True
    await supervisor.notify_idle()

    assert await supervisor.acquire_runtime() is replacement
    assert supervisor.status().active_revision == "sha256:new"
    assert replacement.start_calls == 1
    assert old_runtime.close_calls == 1


def test_failed_replacement_keeps_old_runtime_active() -> None:
    asyncio.run(_failed_replacement_keeps_old_runtime_active())


async def _failed_replacement_keeps_old_runtime_active() -> None:
    old_runtime = FakeRuntime()

    async def factory() -> FakeRuntime:
        raise RuntimeError("bad config")

    supervisor = supervisor_module.RuntimeSupervisor(
        old_runtime,
        runtime_factory=factory,
        idle_probe=lambda: True,
        active_revision="sha256:old",
    )

    await supervisor.request_reload("sha256:bad")

    assert await supervisor.acquire_runtime() is old_runtime
    assert supervisor.status().state == "failed"
    assert supervisor.status().last_apply_error == "bad config"

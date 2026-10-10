from __future__ import annotations

import asyncio
import importlib

import pytest


supervisor_module = importlib.import_module("Turning-Good-Agent.gateway.runtime_supervisor")


class FakeRuntime:
    def __init__(self) -> None:
        self.start_calls = 0
        self.close_calls = 0

    async def start(self) -> None:
        self.start_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


class FailingCloseRuntime(FakeRuntime):
    async def close(self) -> None:
        await super().close()
        raise RuntimeError("close failed")


class BlockingCloseRuntime(FakeRuntime):
    def __init__(self) -> None:
        super().__init__()
        self.close_started = asyncio.Event()
        self.release_close = asyncio.Event()

    async def close(self) -> None:
        await super().close()
        if self.close_calls == 1:
            self.close_started.set()
            await self.release_close.wait()


def test_gateway_reload_waits_for_idle_then_replaces_runtime() -> None:
    """Would fail if Gateway replaced its only Runtime while a turn was still active."""
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


def test_gateway_failed_replacement_keeps_old_runtime_active() -> None:
    """Would fail if a bad candidate displaced Gateway's healthy Runtime."""
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


def test_gateway_commits_replacement_despite_old_runtime_close_failure() -> None:
    """Would fail if post-commit cleanup rolled back an already active replacement."""
    asyncio.run(_old_runtime_close_failure_does_not_rollback_committed_replacement())


async def _old_runtime_close_failure_does_not_rollback_committed_replacement() -> None:
    old_runtime = FailingCloseRuntime()
    replacement = FakeRuntime()

    async def factory() -> FakeRuntime:
        return replacement

    supervisor = supervisor_module.RuntimeSupervisor(
        old_runtime,
        runtime_factory=factory,
        idle_probe=lambda: True,
        active_revision="sha256:old",
    )
    await supervisor.request_reload("sha256:new")

    assert supervisor.current_runtime is replacement
    assert supervisor.status().state == "active"
    assert supervisor.status().active_revision == "sha256:new"
    assert old_runtime.close_calls == 1


def test_gateway_cancelled_replacement_restores_a_stable_failed_state() -> None:
    """Would fail if cancelling an Apply left the sole Runtime stuck in applying."""
    asyncio.run(_cancelled_replacement_restores_a_stable_failed_state())


async def _cancelled_replacement_restores_a_stable_failed_state() -> None:
    old_runtime = FakeRuntime()
    replacement = FakeRuntime()
    prepare_started = asyncio.Event()
    hold_prepare = asyncio.Event()

    async def factory() -> FakeRuntime:
        return replacement

    async def prepare(_: FakeRuntime) -> None:
        prepare_started.set()
        await hold_prepare.wait()

    supervisor = supervisor_module.RuntimeSupervisor(
        old_runtime,
        runtime_factory=factory,
        idle_probe=lambda: True,
        active_revision="sha256:old",
        on_prepare=prepare,
    )
    request = asyncio.create_task(supervisor.request_reload("sha256:new"))
    await asyncio.wait_for(prepare_started.wait(), timeout=1)

    request.cancel()
    with pytest.raises(asyncio.CancelledError):
        await request

    status = supervisor.status()
    assert await supervisor.acquire_runtime() is old_runtime
    assert status.state == "failed"
    assert status.active_revision == "sha256:old"
    assert status.desired_revision == "sha256:new"
    assert replacement.close_calls == 1


def test_gateway_binds_active_revision_to_the_runtime_it_commits() -> None:
    """Would fail if a later Apply made an earlier candidate appear to be the newer config."""
    asyncio.run(_active_revision_is_bound_to_committed_runtime())


async def _active_revision_is_bound_to_committed_runtime() -> None:
    old_runtime = BlockingCloseRuntime()
    first_replacement = FakeRuntime()
    second_replacement = FakeRuntime()
    first_factory_started = asyncio.Event()
    release_first_factory = asyncio.Event()
    factory_calls = 0

    async def factory() -> FakeRuntime:
        nonlocal factory_calls
        factory_calls += 1
        if factory_calls == 1:
            first_factory_started.set()
            await release_first_factory.wait()
            return first_replacement
        return second_replacement

    supervisor = supervisor_module.RuntimeSupervisor(
        old_runtime,
        runtime_factory=factory,
        idle_probe=lambda: True,
        active_revision="sha256:old",
    )
    first_request = asyncio.create_task(supervisor.request_reload("sha256:first"))
    await asyncio.wait_for(first_factory_started.wait(), timeout=1)
    second_request = asyncio.create_task(supervisor.request_reload("sha256:second"))
    await asyncio.sleep(0)

    release_first_factory.set()
    await asyncio.wait_for(old_runtime.close_started.wait(), timeout=1)

    assert supervisor.current_runtime is first_replacement
    assert supervisor.status().active_revision == "sha256:first"
    assert supervisor.status().desired_revision == "sha256:second"

    old_runtime.release_close.set()
    await asyncio.gather(first_request, second_request)

    assert supervisor.current_runtime is second_replacement
    assert supervisor.status().active_revision == "sha256:second"


def test_gateway_binds_each_pending_revision_to_its_requested_factory() -> None:
    """Would fail if a newer config file changed the Runtime built for an earlier Apply."""
    asyncio.run(_pending_revision_keeps_its_requested_factory())


async def _pending_revision_keeps_its_requested_factory() -> None:
    old_runtime = BlockingCloseRuntime()
    first_replacement = FakeRuntime()
    second_replacement = FakeRuntime()
    first_factory_started = asyncio.Event()
    release_first_factory = asyncio.Event()

    async def default_factory() -> FakeRuntime:
        raise AssertionError("the default factory must not replace a request-bound snapshot")

    async def first_factory() -> FakeRuntime:
        first_factory_started.set()
        await release_first_factory.wait()
        return first_replacement

    async def second_factory() -> FakeRuntime:
        return second_replacement

    supervisor = supervisor_module.RuntimeSupervisor(
        old_runtime,
        runtime_factory=default_factory,
        idle_probe=lambda: True,
        active_revision="sha256:old",
    )
    first_request = asyncio.create_task(
        supervisor.request_reload("sha256:first", runtime_factory=first_factory)
    )
    await asyncio.wait_for(first_factory_started.wait(), timeout=1)
    second_request = asyncio.create_task(
        supervisor.request_reload("sha256:second", runtime_factory=second_factory)
    )
    await asyncio.sleep(0)

    release_first_factory.set()
    await asyncio.wait_for(old_runtime.close_started.wait(), timeout=1)

    assert supervisor.current_runtime is first_replacement
    assert supervisor.status().active_revision == "sha256:first"

    old_runtime.release_close.set()
    await asyncio.gather(first_request, second_request)

    assert supervisor.current_runtime is second_replacement
    assert supervisor.status().active_revision == "sha256:second"


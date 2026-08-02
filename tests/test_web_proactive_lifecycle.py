from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace


lifecycle_module = importlib.import_module("Turning-Good-Agent.web.backend.proactive_lifecycle")
ownership_module = importlib.import_module("Turning-Good-Agent.web.backend.proactive_ownership")
delivery_module = importlib.import_module("Turning-Good-Agent.proactive.delivery")
service_module = importlib.import_module("Turning-Good-Agent.proactive.service")
manager_module = importlib.import_module("Turning-Good-Agent.proactive.manager")
memory_module = importlib.import_module("Turning-Good-Agent.memory.long_term")
tools_module = importlib.import_module("Turning-Good-Agent.tools.registry")
bus_module = importlib.import_module("Turning-Good-Agent.bus.queue")
supervisor_module = importlib.import_module("Turning-Good-Agent.web.backend.runtime_supervisor")
control_module = importlib.import_module("Turning-Good-Agent.web.backend.proactive_control")


class FakeRuntime:
    def __init__(self, data_dir: Path, *, enabled: bool = True) -> None:
        self.settings = SimpleNamespace(data_dir=data_dir, proactive=SimpleNamespace(enabled=enabled))
        self.close_calls = 0
        self.start_calls = 0

    async def start(self) -> None:
        self.start_calls += 1

    async def close(self) -> None:
        self.close_calls += 1


class EmptySessions:
    async def list_sessions(self):
        return []

    async def all_messages(self, session_id: str):
        del session_id
        return []


class EmptyLoop:
    def __init__(self) -> None:
        self.tools = tools_module.ToolRegistry()


class FakeMcp:
    def add_status_listener(self, listener) -> None:
        del listener

    def remove_status_listener(self, listener) -> None:
        del listener


class FakeService:
    def __init__(self, *, fail_start: bool = False, events: list[str] | None = None) -> None:
        self.fail_start = fail_start
        self.events = events if events is not None else []
        self.start_calls = 0
        self.stop_calls = 0

    async def start(self) -> None:
        self.start_calls += 1
        self.events.append("service.start")
        if self.fail_start:
            raise RuntimeError("proactive start failed")

    async def stop(self) -> None:
        self.stop_calls += 1
        self.events.append("service.stop")


class FakeLease:
    def __init__(self, *, writable: bool, events: list[str] | None = None) -> None:
        self.writable = writable
        self.events = events if events is not None else []
        self.release_calls = 0
        self.acquire_calls = 0

    def state(self):
        return ownership_module.OwnershipState(
            "owner" if self.writable else "readonly",
            "self" if self.writable else "other",
            "web" if self.writable else "cli",
            1,
        )

    async def try_takeover(self):
        self.acquire_calls += 1
        return self.state()

    async def release_owner(self) -> None:
        self.release_calls += 1
        self.events.append("lease.release")
        self.writable = False


def test_web_owner_starts_service_and_stops_before_releasing_lease(tmp_path: Path) -> None:
    asyncio.run(_web_owner_starts_service_and_stops_before_releasing_lease(tmp_path))


async def _web_owner_starts_service_and_stops_before_releasing_lease(tmp_path: Path) -> None:
    events: list[str] = []
    runtime = FakeRuntime(tmp_path)
    lease = FakeLease(writable=True, events=events)
    service = FakeService(events=events)
    bindings: list[tuple[object, object | None]] = []
    lifecycle = lifecycle_module.WebProactiveLifecycle(
        runtime,
        lease,
        service_factory=lambda _: service,
        bind_runtime=lambda current, active: bindings.append((current, active)),
        poll_seconds=60,
    )

    state = await lifecycle.start()
    assert state.writable is True
    assert service.start_calls == 1
    assert bindings[-1] == (runtime, service)

    await lifecycle.stop()
    assert events.index("service.stop") < events.index("lease.release")


def test_readonly_or_disabled_web_never_starts_scheduler(tmp_path: Path) -> None:
    asyncio.run(_readonly_or_disabled_web_never_starts_scheduler(tmp_path))


async def _readonly_or_disabled_web_never_starts_scheduler(tmp_path: Path) -> None:
    created: list[FakeService] = []

    def factory(_: object) -> FakeService:
        service = FakeService()
        created.append(service)
        return service

    readonly = lifecycle_module.WebProactiveLifecycle(
        FakeRuntime(tmp_path),
        FakeLease(writable=False),
        service_factory=factory,
        bind_runtime=lambda *_: None,
        poll_seconds=60,
    )
    assert (await readonly.start()).writable is False
    await readonly.stop()

    disabled_lease = FakeLease(writable=True)
    disabled = lifecycle_module.WebProactiveLifecycle(
        FakeRuntime(tmp_path, enabled=False),
        disabled_lease,
        service_factory=factory,
        bind_runtime=lambda *_: None,
        poll_seconds=60,
    )
    await disabled.start()
    await disabled.stop()

    assert not created
    assert disabled_lease.acquire_calls == 0


def test_takeover_starts_scheduler_only_after_lease_becomes_writable(tmp_path: Path) -> None:
    asyncio.run(_takeover_starts_scheduler_only_after_lease_becomes_writable(tmp_path))


async def _takeover_starts_scheduler_only_after_lease_becomes_writable(tmp_path: Path) -> None:
    runtime = FakeRuntime(tmp_path)
    lease = FakeLease(writable=False)
    created: list[FakeService] = []

    def factory(_: object) -> FakeService:
        service = FakeService()
        created.append(service)
        return service

    lifecycle = lifecycle_module.WebProactiveLifecycle(
        runtime,
        lease,
        service_factory=factory,
        bind_runtime=lambda *_: None,
        poll_seconds=0.001,
    )
    await lifecycle.start()
    assert not created
    lease.writable = True
    for _ in range(50):
        if created:
            break
        await asyncio.sleep(0.002)
    await lifecycle.stop()
    assert len(created) == 1
    assert created[0].start_calls == 1


def test_failed_runtime_replacement_retains_old_proactive_binding(tmp_path: Path) -> None:
    asyncio.run(_failed_runtime_replacement_retains_old_proactive_binding(tmp_path))


async def _failed_runtime_replacement_retains_old_proactive_binding(tmp_path: Path) -> None:
    old_runtime = FakeRuntime(tmp_path)
    replacement = FakeRuntime(tmp_path)
    lease = FakeLease(writable=True)
    old_service = FakeService()
    failed_service = FakeService(fail_start=True)
    bindings: list[tuple[object, object | None]] = []
    services = iter((old_service, failed_service))
    lifecycle = lifecycle_module.WebProactiveLifecycle(
        old_runtime,
        lease,
        service_factory=lambda _: next(services),
        bind_runtime=lambda current, active: bindings.append((current, active)),
        poll_seconds=60,
    )
    await lifecycle.start()

    supervisor = supervisor_module.RuntimeSupervisor(
        old_runtime,
        runtime_factory=lambda: _return(replacement),
        idle_probe=lambda: True,
        active_revision="old",
        on_activate=lifecycle.activate_replacement,
    )
    await supervisor.request_reload("new")

    assert supervisor.current_runtime is old_runtime
    assert supervisor.status().state == "failed"
    assert lifecycle.service is old_service
    assert old_service.stop_calls == 1
    assert old_service.start_calls == 2
    assert bindings[-1] == (old_runtime, old_service)
    assert replacement.close_calls == 1
    await lifecycle.stop()


async def _return(value: object) -> object:
    return value


def test_successful_replacement_stops_old_scheduler_before_starting_new(tmp_path: Path) -> None:
    asyncio.run(_successful_replacement_stops_old_scheduler_before_starting_new(tmp_path))


async def _successful_replacement_stops_old_scheduler_before_starting_new(tmp_path: Path) -> None:
    events: list[str] = []
    old_runtime = FakeRuntime(tmp_path)
    replacement = FakeRuntime(tmp_path)
    old_service = FakeService(events=events)
    replacement_service = FakeService(events=events)
    services = iter((old_service, replacement_service))
    bindings: list[tuple[object, object | None]] = []
    lifecycle = lifecycle_module.WebProactiveLifecycle(
        old_runtime,
        FakeLease(writable=True, events=events),
        service_factory=lambda _: next(services),
        bind_runtime=lambda current, active: bindings.append((current, active)),
        poll_seconds=60,
    )
    await lifecycle.start()
    supervisor = supervisor_module.RuntimeSupervisor(
        old_runtime,
        runtime_factory=lambda: _return(replacement),
        idle_probe=lambda: True,
        active_revision="old",
        on_activate=lifecycle.activate_replacement,
    )

    await supervisor.request_reload("new")

    assert supervisor.current_runtime is replacement
    assert lifecycle.service is replacement_service
    assert bindings[-1] == (replacement, replacement_service)
    assert events.index("service.stop") < events.index("service.start", 1)
    await lifecycle.stop()


def test_control_binding_replaces_runtime_service_and_store_together(tmp_path: Path) -> None:
    old_runtime = FakeRuntime(tmp_path / "old")
    replacement = FakeRuntime(tmp_path / "new")
    old_service = object()
    replacement_service = object()
    control = control_module.WebProactiveControlService(old_runtime, proactive_service=old_service)

    control.replace_runtime(replacement, replacement_service)

    assert control.runtime is replacement
    assert control.proactive_service is replacement_service
    assert control.store.root == replacement.settings.data_dir / "proactive"


def test_web_delivery_sink_never_reads_or_writes_pending_deliveries(tmp_path: Path) -> None:
    asyncio.run(_web_delivery_sink_never_reads_or_writes_pending_deliveries(tmp_path))


async def _web_delivery_sink_never_reads_or_writes_pending_deliveries(tmp_path: Path) -> None:
    pending = tmp_path / "proactive" / "pending_deliveries.json"
    pending.parent.mkdir()
    pending.write_text('{"deliveries":[{"kept":"for-cli"}]}', encoding="utf-8")
    notices: list[dict[str, str]] = []

    async def publish(**notice: str) -> None:
        notices.append(notice)

    sink = delivery_module.WebDeliverySink(publish)
    await sink.enqueue(
        target_channel="web",
        event_type="proactive.cron.completed",
        content="done",
        source_id="cron-1",
    )
    assert pending.read_text(encoding="utf-8") == '{"deliveries":[{"kept":"for-cli"}]}'
    assert notices == [
        {
            "domain": "cron",
            "entity_id": "cron-1",
            "severity": "info",
            "title": "定时提醒已完成",
            "message": "done",
        }
    ]
    assert not hasattr(sink, "flush")


def test_web_service_uses_memory_only_delivery_sink(tmp_path: Path) -> None:
    asyncio.run(_web_service_uses_memory_only_delivery_sink(tmp_path))


async def _web_service_uses_memory_only_delivery_sink(tmp_path: Path) -> None:
    settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
    settings = settings_module.Settings(data_dir=tmp_path)
    runtime = SimpleNamespace(
        settings=settings,
        agent_loop=EmptyLoop(),
        sessions=EmptySessions(),
        profile_memory=memory_module.ProfileMemory(tmp_path, settings.proactive),
        mcp=FakeMcp(),
        proactive=manager_module.ProactiveManager(),
        is_globally_idle=lambda: True,
    )
    notices: list[dict[str, str]] = []

    async def publish(**notice: str) -> None:
        notices.append(notice)

    service = service_module.ProactiveService(
        runtime,
        bus_module.AsyncMessageBus(),
        target_channel="web",
        delivery_sink=delivery_module.WebDeliverySink(publish),
    )
    await service.start()
    assert service._delivery_task is None
    await service.delivery.enqueue(
        target_channel="web",
        event_type="proactive.dream.completed",
        content="updated",
        source_id="dream",
    )
    assert not (tmp_path / "proactive" / "pending_deliveries.json").exists()
    assert notices[0]["domain"] == "dream"
    await service.stop()

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


lifecycle_module = importlib.import_module("Turning-Good-Agent.web.backend.proactive_lifecycle")
ownership_module = importlib.import_module("Turning-Good-Agent.web.backend.proactive_ownership")
cli_lifecycle_module = importlib.import_module("Turning-Good-Agent.proactive.cli_lifecycle")
delivery_module = importlib.import_module("Turning-Good-Agent.proactive.delivery")
service_module = importlib.import_module("Turning-Good-Agent.proactive.service")
manager_module = importlib.import_module("Turning-Good-Agent.proactive.manager")
memory_module = importlib.import_module("Turning-Good-Agent.memory.long_term")
settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
tools_module = importlib.import_module("Turning-Good-Agent.tools.registry")
bus_module = importlib.import_module("Turning-Good-Agent.bus.queue")
supervisor_module = importlib.import_module("Turning-Good-Agent.web.backend.runtime_supervisor")
control_module = importlib.import_module("Turning-Good-Agent.web.backend.proactive_control")
app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
skills_module = importlib.import_module("Turning-Good-Agent.skills.manager")


ProactiveService = service_module.ProactiveService
Settings = settings_module.Settings
SkillManager = skills_module.SkillManager

PROACTIVE_TOOL_NAMES = {
    "create_cron",
    "list_crons",
    "delete_cron",
    "run_breakbeat",
    "list_breakbeat",
    "complete_breakbeat",
    "delete_breakbeat",
    "run_dream",
    "read_profile_memory",
    "run_skill_evolution",
    "list_skill_drafts",
    "delete_skill_draft",
    "list_incidents",
}


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
    statuses: dict[str, object] = {}

    def add_status_listener(self, listener) -> None:
        del listener

    def remove_status_listener(self, listener) -> None:
        del listener


class FakeService:
    def __init__(
        self,
        *,
        name: str = "service",
        fail_start: bool = False,
        events: list[str] | None = None,
    ) -> None:
        self.name = name
        self.fail_start = fail_start
        self.events = events if events is not None else []
        self.start_calls = 0
        self.stop_calls = 0

    async def start(self) -> None:
        self.start_calls += 1
        self.events.append(f"{self.name}.start")
        if self.fail_start:
            raise RuntimeError("proactive start failed")

    async def stop(self) -> None:
        self.stop_calls += 1
        self.events.append(f"{self.name}.stop")

    def install_tools(self) -> None:
        self.events.append(f"{self.name}.install_tools")

    def uninstall_tools(self) -> None:
        self.events.append(f"{self.name}.uninstall_tools")


class NamedTool:
    def __init__(self, name: str) -> None:
        self.name = name


def test_registry_unregistration_requires_tool_identity() -> None:
    registry = tools_module.ToolRegistry()
    installed = NamedTool("run_breakbeat")
    replacement = NamedTool("run_breakbeat")
    registry.register(installed)
    registry.register(replacement)

    assert registry.unregister(installed) is False
    assert registry.get("run_breakbeat") is replacement
    assert registry.unregister(replacement) is True
    assert not registry.has("run_breakbeat")


class IdleAwareFakeService(FakeService):
    def __init__(self, *, idle: bool, **kwargs) -> None:
        super().__init__(**kwargs)
        self.idle = idle
        self.drain_calls = 0

    @property
    def is_idle(self) -> bool:
        return self.idle

    async def drain_for_replacement(self) -> None:
        self.drain_calls += 1
        self.idle = True


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
    assert events.index("service.install_tools") < events.index("service.start")
    assert events.index("service.uninstall_tools") < events.index("service.stop")
    assert events.index("service.stop") < events.index("lease.release")


def test_web_lifecycle_exposes_real_proactive_tools_only_while_writable(tmp_path: Path) -> None:
    asyncio.run(_web_lifecycle_exposes_real_proactive_tools_only_while_writable(tmp_path))


async def _web_lifecycle_exposes_real_proactive_tools_only_while_writable(tmp_path: Path) -> None:
    def runtime_at(root: Path) -> SimpleNamespace:
        settings = Settings(data_dir=root)
        return SimpleNamespace(
            settings=settings,
            agent_loop=EmptyLoop(),
            sessions=EmptySessions(),
            profile_memory=memory_module.ProfileMemory(root, settings.proactive),
            mcp=FakeMcp(),
            proactive=manager_module.ProactiveManager(),
            is_globally_idle=lambda: True,
        )

    def create_service(runtime: object) -> ProactiveService:
        return ProactiveService(runtime, bus_module.AsyncMessageBus(), target_channel="web")

    owner_runtime = runtime_at(tmp_path / "owner")
    owner = lifecycle_module.WebProactiveLifecycle(
        owner_runtime,
        FakeLease(writable=True),
        service_factory=create_service,
        bind_runtime=lambda *_: None,
        poll_seconds=60,
    )
    await owner.start()
    assert PROACTIVE_TOOL_NAMES <= set(owner_runtime.agent_loop.tools.tool_names)
    await owner.stop()
    assert not (PROACTIVE_TOOL_NAMES & set(owner_runtime.agent_loop.tools.tool_names))

    readonly_runtime = runtime_at(tmp_path / "readonly")
    readonly = lifecycle_module.WebProactiveLifecycle(
        readonly_runtime,
        FakeLease(writable=False),
        service_factory=create_service,
        bind_runtime=lambda *_: None,
        poll_seconds=0.001,
    )
    await readonly.start()
    assert not (PROACTIVE_TOOL_NAMES & set(readonly_runtime.agent_loop.tools.tool_names))
    readonly._ownership.writable = True
    await _wait_for(lambda: PROACTIVE_TOOL_NAMES <= set(readonly_runtime.agent_loop.tools.tool_names))
    await readonly.stop()
    assert not (PROACTIVE_TOOL_NAMES & set(readonly_runtime.agent_loop.tools.tool_names))


def test_writable_fastapi_lifespan_exposes_proactive_tools_in_catalog(tmp_path: Path) -> None:
    async def noop() -> None:
        return None

    settings = Settings(data_dir=tmp_path)
    settings.tool_permissions.approval_required_tools = []
    settings.local_config_path = tmp_path / "settings.local.json"
    settings.local_config_path.write_text(
        '{"data_dir":".","tool_permissions":{"approval_required_tools":[]}}',
        encoding="utf-8",
    )
    runtime = SimpleNamespace(
        settings=settings,
        agent_loop=EmptyLoop(),
        sessions=EmptySessions(),
        profile_memory=memory_module.ProfileMemory(tmp_path, settings.proactive),
        mcp=FakeMcp(),
        proactive=manager_module.ProactiveManager(),
        skills=SkillManager(tmp_path / ".skills", settings.skills),
        channel_router=SimpleNamespace(register=lambda *_: None),
        is_globally_idle=lambda: True,
        start=noop,
        close=noop,
    )
    app = app_module.create_app(settings, runtime)

    with TestClient(app) as client:
        response = client.get("/api/control/tools")
        assert response.status_code == 200
        assert {"run_breakbeat", "create_cron"} <= {item["name"] for item in response.json()["tools"]}

    assert not (PROACTIVE_TOOL_NAMES & set(runtime.agent_loop.tools.tool_names))


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


def test_monitor_publishes_only_on_ownership_transition(tmp_path: Path) -> None:
    asyncio.run(_monitor_publishes_only_on_ownership_transition(tmp_path))


async def _monitor_publishes_only_on_ownership_transition(tmp_path: Path) -> None:
    lease = FakeLease(writable=False)
    created: list[FakeService] = []
    publish_calls = 0
    monitor_cycles = 0

    def factory(_: object) -> FakeService:
        service = FakeService()
        created.append(service)
        return service

    async def publish_state() -> None:
        nonlocal publish_calls
        publish_calls += 1

    async def notify_idle() -> None:
        nonlocal monitor_cycles
        monitor_cycles += 1

    lifecycle = lifecycle_module.WebProactiveLifecycle(
        FakeRuntime(tmp_path),
        lease,
        service_factory=factory,
        bind_runtime=lambda *_: None,
        publish_state=publish_state,
        notify_idle=notify_idle,
        poll_seconds=0.001,
    )
    await lifecycle.start()
    assert publish_calls == 1

    await _wait_for(lambda: monitor_cycles >= 3)
    assert publish_calls == 1

    lease.writable = True
    await _wait_for(lambda: bool(created))
    await _wait_for(lambda: monitor_cycles >= 4)
    assert publish_calls == 2

    await lifecycle.stop()
    assert publish_calls == 3


def test_disabled_web_monitors_and_reconciles_bidirectional_enabled_state(tmp_path: Path) -> None:
    asyncio.run(_disabled_web_monitors_and_reconciles_bidirectional_enabled_state(tmp_path))


async def _disabled_web_monitors_and_reconciles_bidirectional_enabled_state(tmp_path: Path) -> None:
    runtime = FakeRuntime(tmp_path, enabled=False)
    lease = ownership_module.ProactiveOwnershipLease(tmp_path, owner_kind="web", heartbeat_seconds=60)
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
    assert lease.state().writable is False

    runtime.settings.proactive.enabled = True
    await _wait_for(lambda: bool(created))
    assert created[0].start_calls == 1
    assert lease.state().writable is True

    runtime.settings.proactive.enabled = False
    await _wait_for(lambda: created[0].stop_calls == 1 and not lease.state().writable)
    assert lease.state().writable is False
    await lifecycle.stop()


def test_cli_readonly_host_automatically_takes_over_real_lease(tmp_path: Path) -> None:
    asyncio.run(_cli_readonly_host_automatically_takes_over_real_lease(tmp_path))


async def _cli_readonly_host_automatically_takes_over_real_lease(tmp_path: Path) -> None:
    web_owner = ownership_module.ProactiveOwnershipLease(tmp_path, owner_kind="web", heartbeat_seconds=60)
    assert (await web_owner.acquire_owner()).writable is True
    runtime = FakeRuntime(tmp_path)
    cli_lease = ownership_module.ProactiveOwnershipLease(tmp_path, owner_kind="cli", heartbeat_seconds=60)
    created: list[FakeService] = []

    def factory(_: object) -> FakeService:
        service = FakeService()
        created.append(service)
        return service

    lifecycle = cli_lifecycle_module.CliProactiveLifecycle(
        runtime,
        bus_module.AsyncMessageBus(),
        target_channel="cli",
        active_session_id=lambda: "cli-session",
        active_inbound_message=lambda: None,
        ownership=cli_lease,
        service_factory=factory,
        poll_seconds=0.001,
    )
    assert (await lifecycle.start()).writable is False
    assert not created

    await web_owner.release_owner()
    await _wait_for(lambda: bool(created))
    assert created[0].start_calls == 1
    assert cli_lease.state().writable is True
    await lifecycle.stop()
    assert cli_lease.state().writable is False


def test_failed_runtime_replacement_retains_old_proactive_binding(tmp_path: Path) -> None:
    asyncio.run(_failed_runtime_replacement_retains_old_proactive_binding(tmp_path))


async def _failed_runtime_replacement_retains_old_proactive_binding(tmp_path: Path) -> None:
    events: list[str] = []
    old_runtime = FakeRuntime(tmp_path)
    replacement = FakeRuntime(tmp_path)
    lease = FakeLease(writable=True, events=events)
    old_service = FakeService(name="old", events=events)
    failed_service = FakeService(name="candidate", fail_start=True, events=events)
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
    assert events.index("old.uninstall_tools") < events.index("old.stop")
    assert events.index("candidate.install_tools") < events.index("candidate.start")
    assert events.index("candidate.uninstall_tools") < events.index("candidate.stop")
    assert events.index("old.install_tools", 1) < events.index("old.start", 2)
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
    old_service = FakeService(name="old", events=events)
    replacement_service = FakeService(name="replacement", events=events)
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
    assert events.index("old.uninstall_tools") < events.index("old.stop")
    assert events.index("replacement.install_tools") < events.index("replacement.start")
    assert events.index("old.stop") < events.index("replacement.start")
    await lifecycle.stop()


def test_disabled_or_lost_owner_replacement_stops_old_scheduler(tmp_path: Path) -> None:
    asyncio.run(_disabled_or_lost_owner_replacement_stops_old_scheduler(tmp_path))


async def _disabled_or_lost_owner_replacement_stops_old_scheduler(tmp_path: Path) -> None:
    events: list[str] = []
    old_runtime = FakeRuntime(tmp_path)
    disabled_runtime = FakeRuntime(tmp_path, enabled=False)
    lease = FakeLease(writable=True, events=events)
    service = FakeService(events=events)
    lifecycle = lifecycle_module.WebProactiveLifecycle(
        old_runtime,
        lease,
        service_factory=lambda _: service,
        bind_runtime=lambda *_: None,
        poll_seconds=60,
    )
    await lifecycle.start()
    await lifecycle.activate_replacement(disabled_runtime)

    assert lifecycle.service is None
    assert service.stop_calls == 1
    assert lease.release_calls == 1
    assert events.index("service.stop") < events.index("lease.release")
    await lifecycle.stop()


def test_runtime_reload_stays_pending_until_proactive_service_is_idle(tmp_path: Path) -> None:
    asyncio.run(_runtime_reload_stays_pending_until_proactive_service_is_idle(tmp_path))


async def _runtime_reload_stays_pending_until_proactive_service_is_idle(tmp_path: Path) -> None:
    old_runtime = FakeRuntime(tmp_path)
    replacement = FakeRuntime(tmp_path)
    old_service = IdleAwareFakeService(idle=False)
    replacement_service = IdleAwareFakeService(idle=True)
    services = iter((old_service, replacement_service))
    lifecycle = lifecycle_module.WebProactiveLifecycle(
        old_runtime,
        FakeLease(writable=True),
        service_factory=lambda _: next(services),
        bind_runtime=lambda *_: None,
        poll_seconds=60,
    )
    await lifecycle.start()
    supervisor = supervisor_module.RuntimeSupervisor(
        old_runtime,
        runtime_factory=lambda: _return(replacement),
        idle_probe=lifecycle.is_idle,
        active_revision="old",
        on_activate=lifecycle.activate_replacement,
    )

    await supervisor.request_reload("new")

    assert supervisor.status().state == "pending"
    assert old_service.stop_calls == 0
    assert old_service.drain_calls == 0

    old_service.idle = True
    await supervisor.notify_idle()

    assert supervisor.current_runtime is replacement
    assert old_service.drain_calls == 1
    assert old_service.stop_calls == 1
    await lifecycle.stop()


def test_takeover_monitor_retries_after_transient_service_start_failure(tmp_path: Path) -> None:
    asyncio.run(_takeover_monitor_retries_after_transient_service_start_failure(tmp_path))


async def _takeover_monitor_retries_after_transient_service_start_failure(tmp_path: Path) -> None:
    class RetriableLease(FakeLease):
        async def release_owner(self) -> None:
            self.release_calls += 1
            self.events.append("lease.release")

    lease = RetriableLease(writable=False)
    services = [FakeService(fail_start=True), FakeService()]
    lifecycle = lifecycle_module.WebProactiveLifecycle(
        FakeRuntime(tmp_path),
        lease,
        service_factory=lambda _: services.pop(0),
        bind_runtime=lambda *_: None,
        poll_seconds=0.001,
    )
    await lifecycle.start()
    lease.writable = True

    await _wait_for(lambda: lifecycle.service is not None)

    assert lifecycle.service is not None
    assert lifecycle.service.start_calls == 1
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


async def _wait_for(predicate, *, attempts: int = 100) -> None:
    for _ in range(attempts):
        if predicate():
            return
        await asyncio.sleep(0.002)
    assert predicate()

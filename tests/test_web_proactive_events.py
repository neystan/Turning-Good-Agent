from __future__ import annotations

import asyncio
import importlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
memory_module = importlib.import_module("Turning-Good-Agent.memory.long_term")
control_module = importlib.import_module("Turning-Good-Agent.web.backend.proactive_control")
events_module = importlib.import_module("Turning-Good-Agent.web.backend.proactive_events")
ownership_module = importlib.import_module("Turning-Good-Agent.web.backend.proactive_ownership")
app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
bridge_module = importlib.import_module("Turning-Good-Agent.proactive.bridge")
cli_lifecycle_module = importlib.import_module("Turning-Good-Agent.proactive.cli_lifecycle")
bus_module = importlib.import_module("Turning-Good-Agent.bus.queue")
manager_module = importlib.import_module("Turning-Good-Agent.proactive.manager")
tools_module = importlib.import_module("Turning-Good-Agent.tools.registry")


def _hub(tmp_path: Path, owner=None):
    settings = settings_module.Settings(data_dir=tmp_path)
    runtime = SimpleNamespace(
        settings=settings,
        profile_memory=memory_module.ProfileMemory(tmp_path, settings.proactive),
        skills=SimpleNamespace(directory=tmp_path / ".skills", list_drafts=lambda: []),
    )
    controller = control_module.WebProactiveControlService(runtime)
    owner = owner or ownership_module.OwnershipState("owner", "web", "web", 1)
    ownership = owner if callable(owner) else lambda: owner
    return controller, events_module.ProactiveEventHub(controller, ownership)


def _web_app(tmp_path: Path):
    async def noop() -> None:
        return None

    settings = settings_module.Settings(data_dir=tmp_path)
    settings.proactive.enabled = False
    settings.local_config_path = tmp_path / "settings.local.json"
    settings.local_config_path.write_text('{"data_dir":".","tool_permissions":{"approval_required_tools":[]}}', encoding="utf-8")
    runtime = SimpleNamespace(
        settings=settings,
        profile_memory=memory_module.ProfileMemory(tmp_path, settings.proactive),
        skills=SimpleNamespace(directory=tmp_path / ".skills", list_drafts=lambda: []),
        agent_loop=SimpleNamespace(tools=SimpleNamespace(tool_names=[])),
        mcp=SimpleNamespace(statuses={}),
        channel_router=SimpleNamespace(register=lambda *_: None),
        start=noop,
        close=noop,
    )
    return app_module.create_app(settings, runtime)


def test_initial_snapshot_and_revision_ordered_events(tmp_path: Path) -> None:
    async def run() -> None:
        controller, hub = _hub(tmp_path)
        initial = hub.initial_snapshots()
        queue = await hub.subscribe()

        update = await hub.publish_snapshot("cron")
        notice = await hub.publish_notice(
            domain="dream",
            entity_id="dream",
            severity="info",
            title="Dream 已更新",
            message="画像已更新。",
            target="#proactive/memory",
        )

        assert len(initial) == 5
        assert update["proactive_revision"] == 1
        assert notice["proactive_revision"] == 2
        assert (await queue.get()) == update
        assert (await queue.get()) == notice
        assert controller.proactive_revision == 2
        await hub.unsubscribe(queue)

    asyncio.run(run())


def test_bridge_event_advances_revision_without_replay(tmp_path: Path) -> None:
    async def run() -> None:
        owner = ownership_module.OwnershipState("readonly", "cli-owner", "cli", 1)
        controller, hub = _hub(tmp_path, owner)
        queue = await hub.subscribe()
        payload = {
            "type": "snapshot",
            "domain": "breakbeat",
            "data": {"items": []},
            "runtime": {"running": False},
            "owner": {"mode": "readonly", "owner_id": "cli-owner", "owner_kind": "cli", "owner_pid": 1},
            "proactive_revision": 7,
        }

        await hub.accept_bridge_event(payload)

        assert controller.proactive_revision == 7
        assert await queue.get() == payload
        await hub.unsubscribe(queue)

    asyncio.run(run())


def _bridge_snapshot(*, revision: int = 1, owner_kind: str = "cli") -> dict[str, object]:
    return {
        "type": "snapshot",
        "domain": "breakbeat",
        "data": {"items": []},
        "runtime": {"running": False, "next_run_at": None, "entity_states": {}},
        "owner": {"mode": "readonly", "owner_id": "cli-owner", "owner_kind": owner_kind, "owner_pid": 1},
        "proactive_revision": revision,
    }


def test_bridge_rejects_foreign_malformed_and_out_of_order_events(tmp_path: Path) -> None:
    async def run() -> None:
        owner = ownership_module.OwnershipState("readonly", "cli-owner", "cli", 1)
        _, hub = _hub(tmp_path, owner)

        with pytest.raises(ValueError, match="owner"):
            await hub.accept_bridge_event(_bridge_snapshot(owner_kind="web"))
        with pytest.raises(ValueError, match="bridge payload"):
            await hub.accept_bridge_event({"type": "snapshot", "proactive_revision": 1})

        await hub.accept_bridge_event(_bridge_snapshot(revision=2))
        with pytest.raises(ValueError, match="revision"):
            await hub.accept_bridge_event(_bridge_snapshot(revision=1))

    asyncio.run(run())


def test_bridge_coalesces_only_identical_revision_payloads(tmp_path: Path) -> None:
    async def run() -> None:
        owner = ownership_module.OwnershipState("readonly", "cli-owner", "cli", 1)
        _, hub = _hub(tmp_path, owner)
        queue = await hub.subscribe()
        payload = _bridge_snapshot(revision=3)

        await hub.accept_bridge_event(payload)
        await hub.accept_bridge_event(dict(payload))

        assert await queue.get() == payload
        assert queue.empty()
        await hub.unsubscribe(queue)

    asyncio.run(run())


def test_bridge_rejects_cli_event_from_another_owner(tmp_path: Path) -> None:
    async def run() -> None:
        owner = ownership_module.OwnershipState("readonly", "active-cli", "cli", 1)
        _, hub = _hub(tmp_path, owner)

        with pytest.raises(ValueError, match="foreign"):
            await hub.accept_bridge_event(_bridge_snapshot())

    asyncio.run(run())


def test_loopback_bridge_delivers_snapshot_and_retains_it_for_reconnect(tmp_path: Path) -> None:
    async def run() -> None:
        spec = importlib.util.find_spec("Turning-Good-Agent.proactive.bridge")
        assert spec is not None
        bridge_module = importlib.import_module("Turning-Good-Agent.proactive.bridge")
        owner = ownership_module.OwnershipState("readonly", "cli-owner", "cli", 1)
        _, hub = _hub(tmp_path, owner)
        queue = await hub.subscribe()
        listener = bridge_module.ProactiveBridgeListener(tmp_path, hub.accept_bridge_event)
        await listener.start()
        payload = _bridge_snapshot(revision=5)
        payload["data"] = {"items": [{"id": "from-cli"}]}
        try:
            await bridge_module.ProactiveBridgePublisher(tmp_path).publish(payload)
            assert await queue.get() == payload
            snapshots = {snapshot["domain"]: snapshot for snapshot in hub.initial_snapshots()}
            assert snapshots["breakbeat"] == payload
        finally:
            await listener.stop()
            await hub.unsubscribe(queue)

    asyncio.run(run())


def test_bridge_defaults_to_loopback_transport(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "TGA_PROACTIVE_BRIDGE_HOST",
        "TGA_PROACTIVE_BRIDGE_BIND_HOST",
        "TGA_PROACTIVE_BRIDGE_ALLOW_NON_LOOPBACK",
    ):
        monkeypatch.delenv(name, raising=False)

    publisher = bridge_module.ProactiveBridgePublisher(tmp_path)
    listener = bridge_module.ProactiveBridgeListener(tmp_path, lambda _: asyncio.sleep(0))

    assert publisher._host == "127.0.0.1"
    assert listener._bind_host == "127.0.0.1"
    assert listener._allow_non_loopback is False


def test_bridge_uses_explicit_docker_transport_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TGA_PROACTIVE_BRIDGE_HOST", "backend")
    monkeypatch.setenv("TGA_PROACTIVE_BRIDGE_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("TGA_PROACTIVE_BRIDGE_ALLOW_NON_LOOPBACK", "1")

    publisher = bridge_module.ProactiveBridgePublisher(tmp_path)
    listener = bridge_module.ProactiveBridgeListener(tmp_path, lambda _: asyncio.sleep(0))

    assert publisher._host == "backend"
    assert listener._bind_host == "0.0.0.0"
    assert listener._allow_non_loopback is True


def test_websocket_receives_loopback_bridge_updates_and_reconnects_with_snapshots(tmp_path: Path) -> None:
    app = _web_app(tmp_path)
    ownership = app.state.proactive_ownership
    ownership._path.parent.mkdir(parents=True, exist_ok=True)
    ownership._path.write_text('{"owner_id":"cli-owner","owner_kind":"cli","owner_pid":1}', encoding="utf-8")
    payload = _bridge_snapshot(revision=9)
    payload["data"] = {"items": [{"id": "from-cli"}]}

    with TestClient(app) as client:
        with client.websocket_connect("/ws/proactive") as websocket:
            initial = [websocket.receive_json() for _ in range(5)]
            assert {message["domain"] for message in initial} == {"cron", "breakbeat", "dream", "skill", "incident"}
            asyncio.run(importlib.import_module("Turning-Good-Agent.proactive.bridge").ProactiveBridgePublisher(tmp_path).publish(payload))
            assert websocket.receive_json() == payload
        with client.websocket_connect("/ws/proactive") as websocket:
            reconnect = [websocket.receive_json() for _ in range(5)]

    snapshots = {message["domain"]: message for message in reconnect}
    assert snapshots["breakbeat"] == payload


def test_proactive_websocket_unsubscribes_when_client_has_already_closed(tmp_path: Path) -> None:
    class ClosedWebSocket:
        async def accept(self) -> None:
            return None

        async def send_json(self, payload: dict[str, object]) -> None:
            del payload
            raise RuntimeError("Unexpected ASGI message 'websocket.send', after sending 'websocket.close'.")

    app = _web_app(tmp_path)
    endpoint = next(route.endpoint for route in app.routes if getattr(route, "path", None) == "/ws/proactive")

    asyncio.run(endpoint(ClosedWebSocket()))

    assert app.state.proactive_events._queues == set()


def test_cli_bridge_callback_publishes_complete_snapshot(tmp_path: Path) -> None:
    async def run() -> None:
        bridge_module = importlib.import_module("Turning-Good-Agent.proactive.bridge")
        cli_owner = ownership_module.OwnershipState("owner", "cli-owner", "cli", 1)
        readonly_owner = ownership_module.OwnershipState("readonly", "cli-owner", "cli", 1)
        _, hub = _hub(tmp_path, readonly_owner)
        queue = await hub.subscribe()
        listener = bridge_module.ProactiveBridgeListener(tmp_path, hub.accept_bridge_event)
        runtime = SimpleNamespace(
            settings=settings_module.Settings(data_dir=tmp_path),
            profile_memory=memory_module.ProfileMemory(tmp_path, settings_module.Settings(data_dir=tmp_path).proactive),
            skills=SimpleNamespace(directory=tmp_path / ".skills", list_drafts=lambda: []),
        )
        service = SimpleNamespace(breakbeat=SimpleNamespace(running=False, next_run_at=None))
        bridge = bridge_module.CliProactiveBridge(runtime, lambda: cli_owner, lambda: service)
        await listener.start()
        try:
            await bridge.publish_snapshot("breakbeat")
            payload = await queue.get()
            assert payload["type"] == "snapshot"
            assert payload["domain"] == "breakbeat"
            assert payload["data"] == {"items": [], "cursors": {}, "next_run_at": None, "usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}
            assert payload["runtime"] == {"running": False, "next_run_at": None, "entity_states": {"service": "idle"}}
        finally:
            await listener.stop()
            await hub.unsubscribe(queue)

    asyncio.run(run())


def test_cli_bridge_retries_unsent_snapshot_when_web_host_starts(tmp_path: Path) -> None:
    async def run() -> None:
        cli_owner = ownership_module.OwnershipState("owner", "cli-owner", "cli", 1)
        readonly_owner = ownership_module.OwnershipState("readonly", "cli-owner", "cli", 1)
        _, hub = _hub(tmp_path, readonly_owner)
        queue = await hub.subscribe()
        runtime = SimpleNamespace(
            settings=settings_module.Settings(data_dir=tmp_path),
            profile_memory=memory_module.ProfileMemory(tmp_path, settings_module.Settings(data_dir=tmp_path).proactive),
            skills=SimpleNamespace(directory=tmp_path / ".skills", list_drafts=lambda: []),
        )
        service = SimpleNamespace(breakbeat=SimpleNamespace(running=False, next_run_at=None))
        bridge = bridge_module.CliProactiveBridge(runtime, lambda: cli_owner, lambda: service)

        await bridge.publish_snapshot("breakbeat")
        listener = bridge_module.ProactiveBridgeListener(tmp_path, hub.accept_bridge_event)
        await listener.start()
        try:
            await bridge.retry_snapshots()
            assert (await queue.get())["domain"] == "breakbeat"
        finally:
            await listener.stop()
            await hub.unsubscribe(queue)

    asyncio.run(run())


def test_new_cli_owner_rebases_bridge_revisions_after_handoff(tmp_path: Path) -> None:
    async def run() -> None:
        current_owner = ownership_module.OwnershipState("readonly", "cli-a", "cli", 1)
        _, hub = _hub(tmp_path, lambda: current_owner)
        queue = await hub.subscribe()
        listener = bridge_module.ProactiveBridgeListener(tmp_path, hub.accept_bridge_event)
        runtime = SimpleNamespace(
            settings=settings_module.Settings(data_dir=tmp_path),
            profile_memory=memory_module.ProfileMemory(tmp_path, settings_module.Settings(data_dir=tmp_path).proactive),
            skills=SimpleNamespace(directory=tmp_path / ".skills", list_drafts=lambda: []),
        )
        service = SimpleNamespace(breakbeat=SimpleNamespace(running=False, next_run_at=None))
        owner_a = ownership_module.OwnershipState("owner", "cli-a", "cli", 1)
        owner_b = ownership_module.OwnershipState("owner", "cli-b", "cli", 2)
        first = bridge_module.CliProactiveBridge(runtime, lambda: owner_a, lambda: service)
        second = bridge_module.CliProactiveBridge(runtime, lambda: owner_b, lambda: service)
        await listener.start()
        try:
            await first.publish_snapshot("breakbeat")
            first_event = await queue.get()
            current_owner = ownership_module.OwnershipState("readonly", "cli-b", "cli", 2)
            await second.publish_snapshot("breakbeat")
            handoff_event = await asyncio.wait_for(queue.get(), timeout=1)
            assert handoff_event["owner"]["owner_id"] == "cli-b"
            assert handoff_event["proactive_revision"] > first_event["proactive_revision"]
        finally:
            await listener.stop()
            await hub.unsubscribe(queue)

    asyncio.run(run())


def test_newer_successful_snapshot_replaces_unsent_retry(tmp_path: Path) -> None:
    async def run() -> None:
        owner = ownership_module.OwnershipState("owner", "cli-owner", "cli", 1)
        readonly_owner = ownership_module.OwnershipState("readonly", "cli-owner", "cli", 1)
        _, hub = _hub(tmp_path, readonly_owner)
        queue = await hub.subscribe()
        runtime = SimpleNamespace(
            settings=settings_module.Settings(data_dir=tmp_path),
            profile_memory=memory_module.ProfileMemory(tmp_path, settings_module.Settings(data_dir=tmp_path).proactive),
            skills=SimpleNamespace(directory=tmp_path / ".skills", list_drafts=lambda: []),
        )
        service = SimpleNamespace(breakbeat=SimpleNamespace(running=False, next_run_at=None))
        bridge = bridge_module.CliProactiveBridge(runtime, lambda: owner, lambda: service)

        await bridge.publish_snapshot("breakbeat")
        listener = bridge_module.ProactiveBridgeListener(tmp_path, hub.accept_bridge_event)
        await listener.start()
        try:
            await bridge.publish_snapshot("breakbeat")
            await queue.get()
            await bridge.retry_snapshots()
            assert bridge._pending_snapshots == {}
            assert queue.empty()
        finally:
            await listener.stop()
            await hub.unsubscribe(queue)

    asyncio.run(run())


def test_cli_bridge_publishes_complete_notice(tmp_path: Path) -> None:
    async def run() -> None:
        cli_owner = ownership_module.OwnershipState("owner", "cli-owner", "cli", 1)
        readonly_owner = ownership_module.OwnershipState("readonly", "cli-owner", "cli", 1)
        _, hub = _hub(tmp_path, readonly_owner)
        queue = await hub.subscribe()
        listener = bridge_module.ProactiveBridgeListener(tmp_path, hub.accept_bridge_event)
        runtime = SimpleNamespace(
            settings=settings_module.Settings(data_dir=tmp_path),
            profile_memory=memory_module.ProfileMemory(tmp_path, settings_module.Settings(data_dir=tmp_path).proactive),
            skills=SimpleNamespace(directory=tmp_path / ".skills", list_drafts=lambda: []),
        )
        service = SimpleNamespace(incidents=SimpleNamespace(running=False, next_run_at=None))
        bridge = bridge_module.CliProactiveBridge(runtime, lambda: cli_owner, lambda: service)
        await listener.start()
        try:
            await bridge.publish_notice(
                event_type="proactive.incident.opened",
                content="scheduler failed",
                source_id="proactive:scheduler",
            )
            payload = await queue.get()
            assert payload["type"] == "notice"
            assert payload["domain"] == "incident"
            assert payload["severity"] == "error"
            assert payload["target"] == "#proactive/incidents"
            assert {"data", "runtime", "owner", "proactive_revision"} <= payload.keys()
        finally:
            await listener.stop()
            await hub.unsubscribe(queue)

    asyncio.run(run())


def test_cli_lifecycle_wires_service_changes_to_loopback_bridge(tmp_path: Path) -> None:
    class EmptySessions:
        async def list_sessions(self):
            return []

        async def all_messages(self, session_id: str):
            del session_id
            return []

    class EmptyLoop:
        def __init__(self) -> None:
            self.tools = tools_module.ToolRegistry()

    class EmptyMcp:
        def add_status_listener(self, listener) -> None:
            del listener

        def remove_status_listener(self, listener) -> None:
            del listener

    async def run() -> None:
        settings = settings_module.Settings(data_dir=tmp_path)
        runtime = SimpleNamespace(
            settings=settings,
            agent_loop=EmptyLoop(),
            sessions=EmptySessions(),
            profile_memory=memory_module.ProfileMemory(tmp_path, settings.proactive),
            skills=SimpleNamespace(directory=tmp_path / ".skills", list_drafts=lambda: []),
            mcp=EmptyMcp(),
            proactive=manager_module.ProactiveManager(),
            is_globally_idle=lambda: True,
        )
        cli_owner = ownership_module.ProactiveOwnershipLease(tmp_path, owner_kind="cli", owner_id="cli-owner")
        readonly_owner = ownership_module.OwnershipState("readonly", "cli-owner", "cli", 1)
        _, hub = _hub(tmp_path, readonly_owner)
        listener = bridge_module.ProactiveBridgeListener(tmp_path, hub.accept_bridge_event)
        lifecycle = cli_lifecycle_module.CliProactiveLifecycle(
            runtime,
            bus_module.AsyncMessageBus(),
            target_channel="cli",
            active_session_id=lambda: None,
            active_inbound_message=lambda: None,
            ownership=cli_owner,
            poll_seconds=60,
        )
        await listener.start()
        queue = None
        try:
            await lifecycle.start()
            assert lifecycle.service is not None
            queue = await hub.subscribe()
            await lifecycle.service._domain_changed("breakbeat")
            payload = await asyncio.wait_for(queue.get(), timeout=1)
            assert payload["type"] == "snapshot"
            assert payload["domain"] == "breakbeat"
        finally:
            await lifecycle.stop()
            await listener.stop()
            if queue is not None:
                await hub.unsubscribe(queue)

    asyncio.run(run())

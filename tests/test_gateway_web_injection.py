from __future__ import annotations

import asyncio
import importlib
import importlib.util
import json
import threading
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


def test_web_app_uses_the_injected_gateway_lifecycle_and_services() -> None:
    """Would fail if Web assembled a second Host instead of using Gateway services."""

    async def run() -> None:
        app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")

        class Gateway:
            def __init__(self) -> None:
                self.settings = SimpleNamespace()
                self.web_coordinator = object()
                self.runtime_supervisor = object()
                self.config_control = object()
                self.proactive_control = object()
                self.proactive_events = object()
                self.proactive_owner_state = object()
                self.started = 0
                self.closed = 0

            async def start(self) -> None:
                self.started += 1

            async def close(self) -> None:
                self.closed += 1

        gateway = Gateway()
        app = app_module.create_app(gateway)

        assert app.state.gateway is gateway
        assert app.state.coordinator is gateway.web_coordinator
        assert app.state.runtime_supervisor is gateway.runtime_supervisor
        assert app.state.config_control is gateway.config_control
        assert app.state.proactive_control is gateway.proactive_control
        assert app.state.proactive_events is gateway.proactive_events

        async with app.router.lifespan_context(app):
            assert gateway.started == 1
            assert gateway.closed == 0

        assert gateway.closed == 1

    asyncio.run(run())


def test_gateway_host_projects_web_proactive_notices_to_the_application_hub(tmp_path: Path) -> None:
    """Would fail if Gateway sent a Web proactive result through a chat session or local Host."""

    async def run() -> None:
        gateway_module = importlib.import_module("Turning-Good-Agent.gateway.host")
        messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
        runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
        settings_module = importlib.import_module("Turning-Good-Agent.config.settings")

        state_dir = tmp_path / "state"
        config_path = tmp_path / "gateway-test.json"
        config_path.write_text(json.dumps({"data_dir": "state"}), encoding="utf-8")
        settings = settings_module.Settings(data_dir=state_dir)
        settings.local_config_path = config_path
        settings.proactive.enabled = False
        runtime = runtime_module.AgentRuntime.create_default(settings, object())
        host = gateway_module.GatewayHost(settings, runtime=runtime)

        await host.start()
        queue = await host.proactive_events.subscribe()
        try:
            notice = messages_module.OutboundMessage(
                "notice-1",
                "proactive",
                recipient=messages_module.Recipient(settings.gateway.principal_id, "web", "proactive"),
                content="background result",
                event_type="proactive.cron.completed",
                disposition="proactive_notification",
            )

            assert await host.channel_manager.deliver(notice) is True
            payload = await asyncio.wait_for(queue.get(), timeout=1)
            assert payload["type"] == "notice"
            assert payload["domain"] == "cron"
            assert payload["message"] == "background result"
            assert runtime.proactive.handlers == [host.proactive_service]
        finally:
            await host.proactive_events.unsubscribe(queue)
            await host.close()

    asyncio.run(run())


def test_development_app_delegates_runtime_assembly_to_gateway(monkeypatch, tmp_path) -> None:
    """Would fail if the development Web entrypoint built its own Runtime before Gateway."""
    dev_module = importlib.import_module("Turning-Good-Agent.web.backend.dev")
    runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
    settings = SimpleNamespace(
        local_config_path=tmp_path / "settings.local.json",
        gateway=SimpleNamespace(auth_token=None),
    )
    created: list[object] = []

    class Gateway:
        def __init__(self, selected_settings: object) -> None:
            assert selected_settings is settings
            created.append(self)

    def no_local_runtime(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("development app must not create a local Runtime")

    monkeypatch.setattr(dev_module, "Settings", SimpleNamespace(load=lambda: settings))
    monkeypatch.setattr(dev_module, "GatewayHost", Gateway, raising=False)
    monkeypatch.setattr(dev_module, "build_llm", lambda _settings: object(), raising=False)
    monkeypatch.setattr(runtime_module.AgentRuntime, "create_default", no_local_runtime)
    monkeypatch.setattr(dev_module, "create_app", lambda gateway: {"gateway": gateway})

    assert dev_module.create_development_app() == {"gateway": created[0]}


def test_websocket_new_message_lets_the_gateway_create_the_web_session() -> None:
    """Would fail if a new browser message were treated as an unknown existing session."""
    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
    received: list[tuple[object, str, object]] = []

    class Coordinator:
        async def send(self, session_id, content, *, client_action_id=None):
            received.append((session_id, content, client_action_id))
            return "derived-session", "request-1"

    class Gateway:
        def __init__(self) -> None:
            store = SimpleNamespace(
                load_session=lambda _session_id: (_ for _ in ()).throw(
                    AssertionError("a new message must not load an existing session")
                )
            )
            self.settings = SimpleNamespace()
            self.web_coordinator = Coordinator()
            self.runtime_supervisor = SimpleNamespace(
                current_runtime=SimpleNamespace(sessions=SimpleNamespace(store=store))
            )
            self.config_control = object()
            self.proactive_control = object()
            self.proactive_events = object()
            self.proactive_owner_state = object()

        async def start(self) -> None:
            return None

        async def close(self) -> None:
            return None

    app = app_module.create_app(Gateway())
    with TestClient(app) as client:
        with client.websocket_connect("/ws/web") as websocket:
            websocket.send_json({"type": "message.send", "content": "hello"})
            assert websocket.receive_json() == {
                "type": "message.accepted",
                "session_id": "derived-session",
                "request_id": "request-1",
                "client_action_id": None,
            }

    assert received == [(None, "hello", None)]


def test_websocket_rejects_non_boolean_approval_values() -> None:
    """Would fail if Web approval text such as `"false"` became a true decision."""
    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
    resolved = threading.Event()
    calls: list[tuple[str, str, bool]] = []

    class Coordinator:
        async def resolve_approval(self, session_id, approval_id, approved):
            calls.append((session_id, approval_id, approved))
            resolved.set()
            return False

    class Gateway:
        def __init__(self) -> None:
            self.settings = SimpleNamespace()
            self.web_coordinator = Coordinator()
            self.runtime_supervisor = object()
            self.config_control = object()
            self.proactive_control = object()
            self.proactive_events = object()
            self.proactive_owner_state = object()

        async def start(self) -> None:
            return None

        async def close(self) -> None:
            return None

    app = app_module.create_app(Gateway())
    with TestClient(app) as client:
        with client.websocket_connect("/ws/web") as websocket:
            for action in (
                {
                    "type": "approval.resolve",
                    "session_id": "web-session",
                    "approval_id": "approval-1",
                    "approved": "false",
                    "client_action_id": "invalid-string",
                },
                {
                    "type": "approval.resolve",
                    "session_id": "web-session",
                    "approval_id": "approval-1",
                    "approved": 0,
                    "client_action_id": "invalid-number",
                },
                {
                    "type": "approval.resolve",
                    "session_id": "web-session",
                    "approval_id": "approval-1",
                    "client_action_id": "invalid-missing",
                },
            ):
                websocket.send_json(action)
                assert websocket.receive_json() == {
                    "type": "error",
                    "message": "approved 必须是布尔值",
                    "client_action_id": action["client_action_id"],
                }

            assert calls == []
            websocket.send_json(
                {
                    "type": "approval.resolve",
                    "session_id": "web-session",
                    "approval_id": "approval-1",
                    "approved": False,
                }
            )
            assert resolved.wait(timeout=1)

    assert calls == [("web-session", "approval-1", False)]


def test_gateway_runtime_reload_keeps_the_previous_proactive_service_on_activation_failure(
    tmp_path: Path, monkeypatch
) -> None:
    """Would fail if a failed Gateway activation left Web bound to its half-replaced service."""

    async def run() -> None:
        gateway_module = importlib.import_module("Turning-Good-Agent.gateway.host")
        runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
        settings_module = importlib.import_module("Turning-Good-Agent.config.settings")

        state_dir = tmp_path / "state"
        config_path = tmp_path / "gateway-test.json"
        config_path.write_text(json.dumps({"data_dir": "state"}), encoding="utf-8")
        settings = settings_module.Settings(data_dir=state_dir)
        settings.local_config_path = config_path
        settings.proactive.enabled = False
        initial_runtime = runtime_module.AgentRuntime.create_default(settings, object())
        replacement_runtime = runtime_module.AgentRuntime.create_default(settings, object())

        async def replacement_factory():
            return replacement_runtime

        host = gateway_module.GatewayHost(
            settings,
            runtime=initial_runtime,
            runtime_factory=replacement_factory,
        )
        await host.start()
        previous_service = host.proactive_service

        async def projection_failure() -> None:
            raise RuntimeError("projection failed")

        monkeypatch.setattr(host.proactive_events, "publish_all", projection_failure)
        try:
            status = await host.runtime_supervisor.request_reload("replacement")

            assert status.state == "failed"
            assert host.current_runtime is initial_runtime
            assert host.proactive_service is previous_service
            assert host.proactive_control.runtime is initial_runtime
        finally:
            await host.close()

    asyncio.run(run())


def test_gateway_reload_does_not_restore_a_cron_deleted_during_replacement(
    tmp_path: Path, monkeypatch
) -> None:
    """Would fail if a prepared ProactiveService kept a stale Cron snapshot."""

    async def run() -> None:
        gateway_module = importlib.import_module("Turning-Good-Agent.gateway.host")
        runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
        settings_module = importlib.import_module("Turning-Good-Agent.config.settings")

        state_dir = tmp_path / "state"
        config_path = tmp_path / "gateway-test.json"
        config_path.write_text(json.dumps({"data_dir": "state"}), encoding="utf-8")
        settings = settings_module.Settings(data_dir=state_dir)
        settings.local_config_path = config_path
        settings.proactive.enabled = False
        initial_runtime = runtime_module.AgentRuntime.create_default(settings, object())
        replacement_runtime = runtime_module.AgentRuntime.create_default(settings, object())
        replacement_started = asyncio.Event()
        allow_replacement = asyncio.Event()
        original_start = replacement_runtime.start

        async def blocked_start() -> None:
            replacement_started.set()
            await allow_replacement.wait()
            await original_start()

        monkeypatch.setattr(replacement_runtime, "start", blocked_start)

        async def replacement_factory():
            return replacement_runtime

        host = gateway_module.GatewayHost(
            settings,
            runtime=initial_runtime,
            runtime_factory=replacement_factory,
        )
        await host.start()
        job = host.proactive_service.cron.create_job("不要复活。", recurring=True, cron="0 9 * * *")
        reload_task = asyncio.create_task(host.runtime_supervisor.request_reload("replacement"))
        await asyncio.wait_for(replacement_started.wait(), timeout=1)
        try:
            await host.proactive_control.delete_cron(job.id)
            allow_replacement.set()
            status = await reload_task

            assert status.state == "active"
            assert host.proactive_service.cron.list_jobs() == []
            assert json.loads((state_dir / "proactive" / "cron.json").read_text(encoding="utf-8"))["jobs"] == []
        finally:
            allow_replacement.set()
            await asyncio.gather(reload_task, return_exceptions=True)
            await host.close()

    asyncio.run(run())


def test_gateway_retries_a_pending_reload_after_proactive_work_finishes(tmp_path: Path) -> None:
    """Would fail if background completion left a Gateway config Apply pending forever."""

    async def run() -> None:
        gateway_module = importlib.import_module("Turning-Good-Agent.gateway.host")
        runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
        settings_module = importlib.import_module("Turning-Good-Agent.config.settings")

        state_dir = tmp_path / "state"
        config_path = tmp_path / "gateway-test.json"
        settings = settings_module.Settings(data_dir=state_dir)
        settings.local_config_path = config_path
        settings.proactive.enabled = False
        initial_runtime = runtime_module.AgentRuntime.create_default(settings, object())
        replacement_runtime = runtime_module.AgentRuntime.create_default(settings, object())
        replacement_started = asyncio.Event()

        async def replacement_factory():
            replacement_started.set()
            return replacement_runtime

        host = gateway_module.GatewayHost(
            settings,
            runtime=initial_runtime,
            runtime_factory=replacement_factory,
        )
        await host.start()
        release_work = asyncio.Event()
        work = host.proactive_service._spawn(release_work.wait())
        try:
            status = await host.runtime_supervisor.request_reload("replacement")
            assert status.state == "pending"

            release_work.set()
            await asyncio.wait_for(work, timeout=1)
            await asyncio.wait_for(replacement_started.wait(), timeout=1)

            assert host.current_runtime is replacement_runtime
            assert host.runtime_supervisor.status().state == "active"
        finally:
            release_work.set()
            await host.close()

    asyncio.run(run())


def test_gateway_reload_binds_each_requested_revision_to_its_loaded_settings_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    """Would fail if a later Apply changed the Runtime assembled for an earlier revision."""

    async def run() -> None:
        gateway_module = importlib.import_module("Turning-Good-Agent.gateway.host")
        runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
        settings_module = importlib.import_module("Turning-Good-Agent.config.settings")

        config_path = tmp_path / "settings.local.json"

        def write_config(max_tool_rounds: int) -> None:
            config_path.write_text(
                json.dumps(
                    {
                        "data_dir": "state",
                        "gateway": {"principal_id": "stan", "auth_token": "test-token"},
                        "proactive": {"enabled": False},
                        "runtime": {"max_tool_rounds": max_tool_rounds},
                    }
                ),
                encoding="utf-8",
            )

        write_config(3)
        settings = settings_module.Settings.load(local_config_path=config_path)
        initial_runtime = runtime_module.AgentRuntime.create_default(settings, object())
        built: list[tuple[object, int]] = []
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        original_create_default = runtime_module.AgentRuntime.create_default

        def create_replacement_runtime(replacement_settings, replacement_llm):
            replacement = original_create_default(replacement_settings, replacement_llm)
            built.append((replacement, replacement_settings.runtime.max_tool_rounds))
            if len(built) == 1:
                original_start = replacement.start

                async def blocked_start() -> None:
                    first_started.set()
                    await release_first.wait()
                    await original_start()

                monkeypatch.setattr(replacement, "start", blocked_start)
            return replacement

        monkeypatch.setattr(
            gateway_module.AgentRuntime,
            "create_default",
            staticmethod(create_replacement_runtime),
        )
        monkeypatch.setattr(gateway_module, "build_llm", lambda _settings: object())
        host = gateway_module.GatewayHost(settings, runtime=initial_runtime)
        await host.start()
        first_request = None
        second_request = None
        try:
            write_config(7)
            first_request = asyncio.create_task(host.request_runtime_reload("revision-A"))
            await asyncio.wait_for(first_started.wait(), timeout=1)

            write_config(9)
            second_request = asyncio.create_task(host.request_runtime_reload("revision-B"))
            await asyncio.sleep(0)
            assert [max_tool_rounds for _, max_tool_rounds in built] == [7]

            release_first.set()
            await asyncio.gather(first_request, second_request)

            assert [max_tool_rounds for _, max_tool_rounds in built] == [7, 9]
            assert built[0][0].settings.runtime.max_tool_rounds == 7
            assert host.current_runtime.settings.runtime.max_tool_rounds == 9
            status = host.runtime_supervisor.status()
            assert (status.active_revision, status.desired_revision, status.state) == (
                "revision-B",
                "revision-B",
                "active",
            )
        finally:
            release_first.set()
            await asyncio.gather(
                *(request for request in (first_request, second_request) if request is not None),
                return_exceptions=True,
            )
            await host.close()

    asyncio.run(run())


def test_gateway_close_waits_for_reload_and_never_leaves_a_candidate_scheduler_running(tmp_path: Path) -> None:
    """Would fail if shutdown released the Gateway lock before an active reload had stopped."""

    async def run() -> None:
        gateway_module = importlib.import_module("Turning-Good-Agent.gateway.host")
        runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
        settings_module = importlib.import_module("Turning-Good-Agent.config.settings")

        state_dir = tmp_path / "state"
        config_path = tmp_path / "gateway-test.json"
        settings = settings_module.Settings(data_dir=state_dir)
        settings.local_config_path = config_path
        initial_runtime = runtime_module.AgentRuntime.create_default(settings, object())
        replacement_runtime = runtime_module.AgentRuntime.create_default(settings, object())
        factory_started = asyncio.Event()
        allow_factory = asyncio.Event()

        async def replacement_factory():
            factory_started.set()
            await allow_factory.wait()
            return replacement_runtime

        host = gateway_module.GatewayHost(
            settings,
            runtime=initial_runtime,
            runtime_factory=replacement_factory,
        )
        await host.start()
        initial_service = host.proactive_service
        reload_task = asyncio.create_task(host.runtime_supervisor.request_reload("replacement"))
        await asyncio.wait_for(factory_started.wait(), timeout=1)
        close_task = asyncio.create_task(host.close())
        close_waited_for_reload = False
        try:
            try:
                await asyncio.wait_for(asyncio.shield(close_task), timeout=0.05)
            except TimeoutError:
                close_waited_for_reload = True
            allow_factory.set()
            await asyncio.gather(reload_task, close_task)

            assert close_waited_for_reload
            assert host.current_runtime is initial_runtime
            assert host.proactive_service is initial_service
            assert not host.proactive_service.running
        finally:
            allow_factory.set()
            await asyncio.gather(reload_task, close_task, return_exceptions=True)
            if host.proactive_service.running:
                await host.proactive_service.stop()
            await host.current_runtime.close()

    asyncio.run(run())


def test_gateway_proactive_projection_has_no_bridge_or_lease_dependency() -> None:
    """Would fail if the Web projection still required a competing Host or Bridge."""
    events_module = importlib.import_module("Turning-Good-Agent.web.backend.proactive_events")

    owner = events_module.GatewayProactiveState(owner_id="gateway-test", owner_pid=42)

    assert owner.to_dict() == {
        "mode": "owner",
        "writable": True,
        "owner_id": "gateway-test",
        "owner_kind": "gateway",
        "owner_pid": 42,
    }
    assert owner.writable is True
    assert not hasattr(events_module.ProactiveEventHub, "accept_bridge_event")
    assert importlib.util.find_spec("Turning-Good-Agent.proactive.ownership") is None
    assert importlib.util.find_spec("Turning-Good-Agent.proactive.bridge") is None
    assert importlib.util.find_spec("Turning-Good-Agent.proactive.cli_lifecycle") is None
    assert importlib.util.find_spec("Turning-Good-Agent.web.backend.proactive_ownership") is None
    assert importlib.util.find_spec("Turning-Good-Agent.web.backend.proactive_lifecycle") is None
    assert importlib.util.find_spec("Turning-Good-Agent.web.backend.runtime_supervisor") is None
    assert importlib.util.find_spec("Turning-Good-Agent.channels.cli") is None

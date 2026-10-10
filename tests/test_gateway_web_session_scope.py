from __future__ import annotations

import asyncio
import importlib
import json
import threading
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from starlette.websockets import WebSocketDisconnect


def test_websocket_uses_the_web_route_without_a_root_alias() -> None:
    """Would fail if Web chat remained on `/ws` or `/ws/web` did not accept its protocol."""

    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
    received: list[tuple[object, str, object]] = []
    guided: list[tuple[str, str]] = []
    stopped: list[str] = []
    approvals: list[tuple[str, str, bool]] = []
    completed = threading.Event()

    class Coordinator:
        async def send(self, session_id, content, *, client_action_id=None):
            received.append((session_id, content, client_action_id))
            return "web-session", "request-1"

        async def send_guidance(self, session_id, content):
            guided.append((session_id, content))

        async def stop(self, session_id):
            stopped.append(session_id)

        async def resolve_approval(self, session_id, approval_id, approved):
            approvals.append((session_id, approval_id, approved))
            completed.set()
            return True

    class Gateway:
        def __init__(self) -> None:
            self.settings = SimpleNamespace()
            self.web_coordinator = Coordinator()
            self.runtime_supervisor = SimpleNamespace()
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
                "session_id": "web-session",
                "request_id": "request-1",
                "client_action_id": None,
            }
            websocket.send_json({"type": "guidance.send", "session_id": "web-session", "content": "focus"})
            websocket.send_json({"type": "task.stop", "session_id": "web-session"})
            websocket.send_json(
                {
                    "type": "approval.resolve",
                    "session_id": "web-session",
                    "approval_id": "approval-1",
                    "approved": True,
                }
            )
            assert completed.wait(timeout=1)
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws"):
                pass

    assert received == [(None, "hello", None)]
    assert guided == [("web-session", "focus")]
    assert stopped == ["web-session"]
    assert approvals == [("web-session", "approval-1", True)]


def test_web_session_api_exposes_only_the_web_channel_routes(tmp_path: Path) -> None:
    """Would fail if Web REST could list or read a CLI conversation."""

    gateway_module = importlib.import_module("Turning-Good-Agent.gateway.host")
    routing_module = importlib.import_module("Turning-Good-Agent.gateway.routing")
    runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
    settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
    messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")

    state_dir = tmp_path / "state"
    config_path = tmp_path / "settings.local.json"
    config_path.write_text(json.dumps({"data_dir": "state"}), encoding="utf-8")
    settings = settings_module.Settings(data_dir=state_dir)
    settings.local_config_path = config_path
    settings.proactive.enabled = False
    runtime = runtime_module.AgentRuntime.create_default(settings, object())
    principal_id = settings.gateway.principal_id
    web_route = messages_module.ChannelRoute(
        principal_id,
        "web",
        "web-conversation",
        routing_module.derive_session_id(principal_id, "web", "web-conversation"),
    )
    cli_route = messages_module.ChannelRoute(
        principal_id,
        "cli",
        "cli-conversation",
        routing_module.derive_session_id(principal_id, "cli", "cli-conversation"),
    )

    async def create_sessions() -> None:
        await runtime.sessions.load_or_create(web_route)
        await runtime.sessions.load_or_create(cli_route)

    asyncio.run(create_sessions())
    app = app_module.create_app(gateway_module.GatewayHost(settings, runtime=runtime))

    with TestClient(app) as client:
        listed = client.get("/api/sessions")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [web_route.session_id]
        assert client.get(f"/api/sessions/{cli_route.session_id}").status_code == 404
        assert client.get(f"/api/sessions/{cli_route.session_id}/messages").status_code == 404
        assert client.get(f"/api/sessions/{cli_route.session_id}/observability").status_code == 404
        assert client.get(f"/api/sessions/{cli_route.session_id}/context-window").status_code == 404
        assert client.patch(f"/api/sessions/{cli_route.session_id}", json={"pinned": True}).status_code == 404
        assert client.delete(f"/api/sessions/{cli_route.session_id}").status_code == 404
        assert client.get(f"/api/control/sessions/{cli_route.session_id}/context").status_code == 404
        assert client.get(f"/api/control/sessions/{cli_route.session_id}/tool-calls").status_code == 404


def test_websocket_subscription_rejects_a_non_web_session_before_event_hub() -> None:
    """Would fail if `/ws/web` subscribed to a CLI session's Web event stream."""

    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")

    class Store:
        async def load_session(self, session_id: str):
            assert session_id == "cli-session"
            return SimpleNamespace(principal_id="local-user", channel="cli")

    class Hub:
        def subscribe(self, session_id: str, after_event_id: int | None):
            queue: asyncio.Queue[object] = asyncio.Queue()
            return ([SimpleNamespace(as_dict=lambda: {"type": "unexpected", "session_id": session_id})], queue)

        def unsubscribe(self, session_id: str, queue: asyncio.Queue[object] | None) -> None:
            del session_id, queue

    class Coordinator:
        hub = Hub()

    class Gateway:
        def __init__(self) -> None:
            self.settings = SimpleNamespace(gateway=SimpleNamespace(principal_id="local-user", auth_token="token"))
            self.web_coordinator = Coordinator()
            self.runtime_supervisor = SimpleNamespace(
                current_runtime=SimpleNamespace(sessions=SimpleNamespace(store=Store()))
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
            websocket.send_json(
                {
                    "type": "session.subscribe",
                    "session_id": "cli-session",
                    "client_action_id": "scope-check",
                }
            )
            assert websocket.receive_json() == {
                "type": "error",
                "message": "会话不存在",
                "client_action_id": "scope-check",
            }


def test_web_normal_messages_queue_without_becoming_active_guidance() -> None:
    """Would fail if a second normal Web send mutated the active turn or dispatched early."""

    async def run() -> None:
        bus_module = importlib.import_module("Turning-Good-Agent.bus.queue")
        coordinator_module = importlib.import_module("Turning-Good-Agent.web.backend.coordinator")
        messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
        turns_module = importlib.import_module("Turning-Good-Agent.gateway.turns")

        class Store:
            route = None

            async def load_session(self, session_id: str):
                route = self.route
                if route is None or route.session_id != session_id:
                    return None
                return SimpleNamespace(
                    id=route.session_id,
                    principal_id=route.principal_id,
                    channel=route.channel,
                    conversation_id=route.conversation_id,
                )

        class Runtime:
            def __init__(self) -> None:
                self.sessions = SimpleNamespace(store=Store())
                self.started: list[str] = []
                self.changed = asyncio.Condition()
                self.releases: dict[str, asyncio.Event] = {}

            async def run_turn(self, message):
                self.releases[message.id] = asyncio.Event()
                async with self.changed:
                    self.started.append(message.id)
                    self.changed.notify_all()
                await self.releases[message.id].wait()
                return messages_module.OutboundMessage.completed(
                    message.session_id,
                    messages_module.Recipient(
                        message.route.principal_id,
                        message.route.channel,
                        message.route.conversation_id,
                    ),
                    message.id,
                )

            async def wait_started(self, request_id: str) -> None:
                async with self.changed:
                    await asyncio.wait_for(
                        self.changed.wait_for(lambda: request_id in self.started),
                        timeout=1,
                    )

        bus = bus_module.AsyncMessageBus()
        runtime = Runtime()
        turns = turns_module.GatewayTurnCoordinator(bus, lambda: runtime, max_concurrent_turns=1)
        coordinator = coordinator_module.WebSessionCoordinator(
            submit=turns.submit,
            complete_route_turn=turns.complete_route_turn,
            runtime_provider=lambda: runtime,
            principal_id="local-user",
            event_buffer_size=16,
            bus=bus,
        )

        await turns.start()
        try:
            session_id, first_request = await coordinator.send(None, "first")
            runtime.sessions.store.route = coordinator.controls[session_id].route
            _, second_request = await coordinator.send(session_id, "second")
            await runtime.wait_started(first_request)

            assert second_request != first_request
            assert runtime.started == [first_request]
            assert list(coordinator.controls[session_id].guidance) == []
            assert coordinator.controls[session_id].request_id == first_request

            runtime.releases[first_request].set()
            outbound = await asyncio.wait_for(bus.consume_outbound(), timeout=1)
            assert await coordinator.deliver(outbound) is True
            await runtime.wait_started(second_request)

            assert coordinator.controls[session_id].request_id == second_request
            assert runtime.started == [first_request, second_request]
        finally:
            for release in runtime.releases.values():
                release.set()
            await turns.close()

    asyncio.run(run())

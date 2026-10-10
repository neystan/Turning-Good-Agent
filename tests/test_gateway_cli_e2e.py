from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient


def test_gateway_uses_the_existing_web_port_and_accepts_container_listener_overrides() -> None:
    """Would fail if Docker needed a second public port or a removed `tga web` command."""
    cli_module = importlib.import_module("Turning-Good-Agent.cli")
    settings_module = importlib.import_module("Turning-Good-Agent.config.settings")

    args = cli_module.build_parser().parse_args(
        ["gateway", "--host", "0.0.0.0", "--port", "8000"]
    )

    assert args.host == "0.0.0.0"
    assert args.port == 8000
    assert settings_module.Settings().gateway.port == settings_module.Settings().web.port == 8000


def test_gateway_preserves_the_explicit_listener_host(tmp_path: Path, monkeypatch) -> None:
    """Would fail if the Gateway object replaced Uvicorn's listener address."""
    cli_module = importlib.import_module("Turning-Good-Agent.cli")
    gateway_host_module = importlib.import_module("Turning-Good-Agent.gateway.host")
    settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
    web_app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
    import uvicorn

    settings = settings_module.Settings(data_dir=tmp_path / "state")
    settings.local_config_path = tmp_path / "settings.local.json"
    settings.local_config_path.write_text("{}", encoding="utf-8")
    listener: dict[str, object] = {}

    class Gateway:
        def __init__(self, selected_settings: object) -> None:
            self.settings = selected_settings

    monkeypatch.setattr(settings_module.Settings, "load", lambda: settings)
    monkeypatch.setattr(gateway_host_module, "GatewayHost", Gateway)
    monkeypatch.setattr(web_app_module, "create_app", lambda gateway: {"gateway": gateway})
    monkeypatch.setattr(
        uvicorn,
        "run",
        lambda application, *, host, port: listener.update(
            {"application": application, "host": host, "port": port}
        ),
    )

    cli_module.gateway(host="0.0.0.0", port=8123)

    assert listener["host"] == "0.0.0.0"
    assert listener["port"] == 8123


def test_development_factory_defers_the_local_gateway_token_until_host_start(tmp_path: Path, monkeypatch) -> None:
    """Would fail if a losing reload worker could write a competing Gateway credential."""
    dev_module = importlib.import_module("Turning-Good-Agent.web.backend.dev")
    settings_module = importlib.import_module("Turning-Good-Agent.config.settings")

    config_path = tmp_path / "settings.local.json"
    config_path.write_text("{}", encoding="utf-8")
    settings = settings_module.Settings(data_dir=tmp_path / "state")
    settings.local_config_path = config_path
    created: list[object] = []

    class Gateway:
        def __init__(self, selected_settings: object) -> None:
            self.settings = selected_settings
            created.append(self)

    monkeypatch.setattr(dev_module, "Settings", SimpleNamespace(load=lambda: settings))
    monkeypatch.setattr(dev_module, "GatewayHost", Gateway)
    monkeypatch.setattr(dev_module, "create_app", lambda gateway: {"gateway": gateway})

    assert dev_module.create_development_app() == {"gateway": created[0]}

    assert created[0].settings.gateway.auth_token is None
    assert json.loads(config_path.read_text(encoding="utf-8")) == {}


def test_gateway_host_routes_a_cli_turn_over_the_authenticated_websocket(tmp_path: Path) -> None:
    """Would fail if the CLI endpoint bypassed Gateway Bus or ChannelManager delivery."""
    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
    gateway_module = importlib.import_module("Turning-Good-Agent.gateway.host")
    llm_types = importlib.import_module("Turning-Good-Agent.llm.types")
    runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
    settings_module = importlib.import_module("Turning-Good-Agent.config.settings")

    class ReplyLLM:
        async def complete(self, _messages, _tools):
            return llm_types.LLMResponse(
                content="Gateway reply",
                usage=llm_types.LLMUsage(input_tokens=1, output_tokens=1, total_tokens=2),
            )

    settings = settings_module.Settings(data_dir=tmp_path / "state")
    settings.local_config_path = tmp_path / "settings.local.json"
    settings.gateway.auth_token = "test-token"
    settings.llm.streaming_enabled = False
    settings.proactive.enabled = False
    runtime = runtime_module.AgentRuntime.create_default(settings, ReplyLLM())
    host = gateway_module.GatewayHost(settings, runtime=runtime)

    with TestClient(app_module.create_app(host)) as client:
        with client.websocket_connect("/ws/cli", headers={"Authorization": "Bearer test-token"}) as websocket:
            websocket.send_json(
                {"type": "cli.connect", "connection_id": "terminal-1", "conversation_id": "research"}
            )
            ready = websocket.receive_json()
            assert ready["type"] == "cli.ready"
            assert ready["conversation_id"] == "research"

            websocket.send_json(
                {"type": "message.send", "request_id": "request-1", "content": "hello gateway"}
            )
            assert websocket.receive_json() == {
                "type": "message.accepted",
                "session_id": ready["session_id"],
                "request_id": "request-1",
            }
            assert websocket.receive_json() == {
                "type": "response.completed",
                "session_id": ready["session_id"],
                "request_id": "request-1",
                "content": "Gateway reply",
            }

    assert host.cli_transport.active_route(settings.gateway.principal_id) is None


def test_cli_disconnect_stops_active_turn_and_discards_only_its_route_queue() -> None:
    """Would fail if disconnect cancelled Runtime work or leaked/dispatched queued CLI input."""

    async def run() -> None:
        base_module = importlib.import_module("Turning-Good-Agent.channels.base")
        bus_module = importlib.import_module("Turning-Good-Agent.bus.queue")
        cli_module = importlib.import_module("Turning-Good-Agent.channels.cli_gateway")
        messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
        turns_module = importlib.import_module("Turning-Good-Agent.gateway.turns")

        class Runtime:
            def __init__(self) -> None:
                self.channel_router = base_module.ChannelRouter()
                self.started: list[str] = []
                self.changed = asyncio.Condition()
                self.released: set[str] = set()
                self.cancelled: set[str] = set()

            async def run_turn(self, message):
                adapter = self.channel_router.create(message)
                async with self.changed:
                    self.started.append(message.id)
                    self.changed.notify_all()
                try:
                    while not adapter.is_stop_requested() and message.id not in self.released:
                        await asyncio.sleep(0)
                except asyncio.CancelledError:
                    self.cancelled.add(message.id)
                    raise
                outbound = messages_module.OutboundMessage.completed(
                    message.session_id,
                    messages_module.Recipient(
                        message.route.principal_id,
                        message.route.channel,
                        message.route.conversation_id,
                    ),
                    message.id,
                )
                if adapter.is_stop_requested():
                    outbound.metadata["outcome"] = "cancelled"
                return outbound

            async def wait_started(self, request_id: str) -> None:
                async with self.changed:
                    await asyncio.wait_for(
                        self.changed.wait_for(lambda: request_id in self.started),
                        timeout=1,
                    )

        bus = bus_module.AsyncMessageBus()
        runtime = Runtime()
        turns = turns_module.GatewayTurnCoordinator(bus, lambda: runtime, max_concurrent_turns=2)
        cli = cli_module.CliGatewayCoordinator(
            bus,
            submit=turns.submit,
            complete_route_turn=turns.complete_route_turn,
            discard_pending_route=turns.discard_pending_route,
        )
        cli.register_runtime(runtime)
        disconnected_route = messages_module.ChannelRoute("stan", "cli", "research", "session-1")
        other_route = messages_module.ChannelRoute("stan", "cli", "review", "session-2")

        await turns.start()
        try:
            assert await cli.send(disconnected_route, "request-1", "first") == "request-1"
            assert await cli.send(disconnected_route, "request-2", "second") == "request-2"
            assert await cli.send(other_route, "request-3", "other") == "request-3"
            await runtime.wait_started("request-1")
            await runtime.wait_started("request-3")

            assert runtime.started == ["request-1", "request-3"]
            assert await runtime.channel_router.create(
                messages_module.InboundMessage("probe", route=disconnected_route, content="")
            ).consume_guidance() == []

            await cli.disconnect(disconnected_route)
            terminal = await asyncio.wait_for(bus.consume_outbound(), timeout=1)
            assert terminal.event_id == "request-1"
            await cli.on_delivery(terminal, accepted=False)
            await asyncio.sleep(0)

            assert "request-2" not in runtime.started
            assert runtime.cancelled == set()
            assert runtime.channel_router.create(
                messages_module.InboundMessage("probe", route=other_route, content="")
            ).is_stop_requested() is False
        finally:
            runtime.released.add("request-3")
            await asyncio.sleep(0)
            await turns.close()

    asyncio.run(run())

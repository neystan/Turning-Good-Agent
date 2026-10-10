from __future__ import annotations

import asyncio
import importlib


messages = importlib.import_module("Turning-Good-Agent.bus.messages")
routing = importlib.import_module("Turning-Good-Agent.gateway.routing")

ChannelRoute = messages.ChannelRoute
OutboundMessage = messages.OutboundMessage
Recipient = messages.Recipient


def test_channel_capabilities_default_to_no_guidance_for_cli() -> None:
    """Would fail if a new or CLI Channel could accept guidance by default."""
    base_module = importlib.import_module("Turning-Good-Agent.channels.base")
    transport_module = importlib.import_module("Turning-Good-Agent.channels.cli_gateway")

    assert base_module.ChannelCapabilities().supports_guidance is False
    assert transport_module.CliGatewayTransport.capabilities.supports_guidance is False


def test_cli_gateway_transport_derives_and_tracks_the_latest_live_route() -> None:
    """Would fail if a CLI session switch did not update its Gateway route."""

    async def run() -> None:
        transport_module = importlib.import_module("Turning-Good-Agent.channels.cli_gateway")
        transport = transport_module.CliGatewayTransport()

        async def sender(_: dict[str, object]) -> None:
            return None

        first = transport.register("connection-1", "stan", "research", sender)
        second = transport.register("connection-2", "stan", "planning", sender)

        assert first == ChannelRoute(
            "stan", "cli", "research", routing.derive_session_id("stan", "cli", "research")
        )
        assert transport.active_route("stan") == second

        transport.activate("connection-1")
        assert transport.active_route("stan") == first

        rebound = transport.bind_route("connection-1", "review")

        assert rebound == ChannelRoute(
            "stan", "cli", "review", routing.derive_session_id("stan", "cli", "review")
        )
        assert transport.active_route("stan") == rebound

        transport.deregister("connection-1")
        assert transport.active_route("stan") == second
        transport.deregister("connection-2")
        assert transport.active_route("stan") is None

    asyncio.run(run())


def test_cli_gateway_transport_rejects_a_duplicate_live_connection_id() -> None:
    """Would fail if a reconnect could overwrite a live connection then be deregistered by it."""

    async def run() -> None:
        transport_module = importlib.import_module("Turning-Good-Agent.channels.cli_gateway")
        transport = transport_module.CliGatewayTransport()

        async def sender(_: dict[str, object]) -> None:
            return None

        original = transport.register("connection-1", "stan", "research", sender)

        try:
            transport.register("connection-1", "stan", "planning", sender)
        except ValueError as exc:
            assert str(exc) == "CLI connection_id is already registered"
        else:
            raise AssertionError("duplicate connection_id must be rejected")

        assert transport.active_route("stan") == original
        transport.deregister("connection-1")
        assert transport.active_route("stan") is None

    asyncio.run(run())


def test_cli_gateway_transport_sends_normal_output_to_its_exact_conversation() -> None:
    """Would fail if a reply fell through to another CLI conversation."""

    async def run() -> None:
        transport_module = importlib.import_module("Turning-Good-Agent.channels.cli_gateway")
        transport = transport_module.CliGatewayTransport()
        research_events: list[dict[str, object]] = []
        planning_events: list[dict[str, object]] = []

        async def research_sender(payload: dict[str, object]) -> None:
            research_events.append(payload)

        async def planning_sender(payload: dict[str, object]) -> None:
            planning_events.append(payload)

        research = transport.register("connection-1", "stan", "research", research_sender)
        transport.register("connection-2", "stan", "planning", planning_sender)
        message = OutboundMessage(
            id="outbound-1",
            session_id=research.session_id,
            recipient=Recipient("stan", "cli", "research"),
            content="检索完成",
            event_type="response.delta",
            metadata={
                "request_id": "request-1",
                "tool_call_id": "call-1",
                "tool_name": "read_file",
                "args": {"path": "note.txt"},
                "internal_only": "must not cross the CLI boundary",
            },
        )

        assert await transport.send(message) is True
        assert research_events == [
            {
                "type": "response.delta",
                "session_id": research.session_id,
                "request_id": "request-1",
                "content": "检索完成",
                "tool_call_id": "call-1",
                "tool_name": "read_file",
                "args": {"path": "note.txt"},
            }
        ]
        assert planning_events == []

        missing = OutboundMessage(
            id="outbound-2",
            session_id="unknown-session",
            recipient=Recipient("stan", "cli", "unknown"),
            content="不得改投",
        )
        assert await transport.send(missing) is False
        assert len(research_events) == 1
        assert planning_events == []

    asyncio.run(run())


def test_cli_gateway_transport_discards_proactive_notifications_for_a_stale_route() -> None:
    """Would fail if a CLI notification was replayed into a session the user left."""

    async def run() -> None:
        transport_module = importlib.import_module("Turning-Good-Agent.channels.cli_gateway")
        transport = transport_module.CliGatewayTransport()
        received: list[dict[str, object]] = []

        async def sender(payload: dict[str, object]) -> None:
            received.append(payload)

        previous = transport.register("connection-1", "stan", "research", sender)
        current = transport.bind_route("connection-1", "planning")
        stale_notification = OutboundMessage(
            id="proactive-1",
            session_id="proactive",
            recipient=Recipient("stan", "cli", previous.conversation_id),
            content="旧会话任务完成",
            event_type="proactive.cron.completed",
            disposition="proactive_notification",
        )
        current_notification = OutboundMessage(
            id="proactive-2",
            session_id="proactive",
            recipient=Recipient("stan", "cli", current.conversation_id),
            content="当前会话任务完成",
            event_type="proactive.cron.completed",
            disposition="proactive_notification",
        )

        assert await transport.send(stale_notification) is False
        assert received == []
        assert await transport.send(current_notification) is True
        assert received[0]["content"] == "当前会话任务完成"

    asyncio.run(run())


def test_cli_gateway_coordinator_submits_routed_turns_and_cleans_up_terminal_delivery() -> None:
    """Would fail if remote CLI input bypassed Gateway turns or left controls active forever."""

    async def run() -> None:
        bus_module = importlib.import_module("Turning-Good-Agent.bus.queue")
        base_module = importlib.import_module("Turning-Good-Agent.channels.base")
        transport_module = importlib.import_module("Turning-Good-Agent.channels.cli_gateway")
        bus = bus_module.AsyncMessageBus()
        submitted = []
        completed = []
        became_idle = asyncio.Event()

        async def submit(message, *, dispatch):
            submitted.append(message)
            await dispatch(message)
            return True

        async def complete_route_turn(route, request_id):
            completed.append((route, request_id))

        async def discard_pending_route(route):
            del route

        async def on_idle() -> None:
            became_idle.set()

        coordinator = transport_module.CliGatewayCoordinator(
            bus,
            submit=submit,
            complete_route_turn=complete_route_turn,
            discard_pending_route=discard_pending_route,
            on_idle=on_idle,
        )
        runtime = type("Runtime", (), {"channel_router": base_module.ChannelRouter()})()
        coordinator.register_runtime(runtime)
        route = ChannelRoute("stan", "cli", "research", "opaque-session")

        request_id = await coordinator.send(route, "request-1", "开始检索")

        assert request_id == "request-1"
        assert submitted[0].route == route
        assert submitted[0].content == "开始检索"
        adapter = runtime.channel_router.create(submitted[0])
        await adapter.on_delta("流式内容")
        delta = await bus.consume_outbound()
        assert delta.recipient == Recipient("stan", "cli", "research")
        assert delta.event_type == "response.delta"
        assert delta.metadata["request_id"] == "request-1"

        await coordinator.stop(route)
        terminal = OutboundMessage.completed(
            "opaque-session",
            Recipient("stan", "cli", "research"),
            "已停止",
            event_id="request-1",
        )
        terminal.metadata["request_id"] = "request-1"

        await coordinator.on_delivery(terminal, accepted=False)

        assert coordinator.is_globally_idle() is True
        assert completed == [(route, "request-1")]
        await asyncio.wait_for(became_idle.wait(), timeout=1)

    asyncio.run(run())

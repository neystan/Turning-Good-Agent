from __future__ import annotations

import asyncio
import importlib


bus_module = importlib.import_module("Turning-Good-Agent.bus.queue")
messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
manager_module = importlib.import_module("Turning-Good-Agent.channels.manager")
im_module = importlib.import_module("Turning-Good-Agent.channels.im")

AsyncMessageBus = bus_module.AsyncMessageBus
ChannelRoute = messages_module.ChannelRoute
InboundMessage = messages_module.InboundMessage
OutboundMessage = messages_module.OutboundMessage
Recipient = messages_module.Recipient


class RecordingTransport:
    def __init__(self, name: str, accepted: bool = True) -> None:
        self.name = name
        self.accepted = accepted
        self.messages: list[OutboundMessage] = []

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None

    async def send(self, message: OutboundMessage) -> bool:
        self.messages.append(message)
        return self.accepted


class FailingTransport(RecordingTransport):
    async def send(self, message: OutboundMessage) -> bool:
        self.messages.append(message)
        raise RuntimeError("transport failed")


def test_channel_manager_notifies_im_listener_when_no_transport_exists() -> None:
    async def run() -> None:
        bus = AsyncMessageBus()
        completed: list[tuple[ChannelRoute, str]] = []

        async def submit(message, *, dispatch):
            await dispatch(message)
            return True

        async def complete(route, request_id):
            completed.append((route, request_id))

        im = im_module.ImGatewayCoordinator(
            bus,
            submit=submit,
            complete_route_turn=complete,
        )
        route = ChannelRoute("owner", "weixin", "binding", "opaque-session")
        inbound = InboundMessage("request-1", route=route, content="hello")
        assert await im.accept(inbound) is True
        await bus.consume_inbound()

        manager = manager_module.ChannelManager(bus)
        terminal = OutboundMessage(
            "terminal-1",
            route.session_id,
            recipient=Recipient("owner", "weixin", "binding"),
            content="error",
            event_type="response.error",
            metadata={"request_id": "request-1"},
        )
        manager.add_delivery_listener(im.on_delivery)
        assert await manager.deliver(terminal) is False
        assert completed == [(route, "request-1")]

    asyncio.run(run())


def test_channel_manager_calls_im_listener_after_rejected_transport_delivery() -> None:
    async def run() -> None:
        bus = AsyncMessageBus()
        transport = RecordingTransport("weixin", accepted=False)
        manager = manager_module.ChannelManager(bus, [transport])
        observed: list[tuple[OutboundMessage, bool]] = []

        async def listener(message: OutboundMessage, accepted: bool) -> None:
            observed.append((message, accepted))

        manager.add_delivery_listener(listener)
        message = OutboundMessage(
            "terminal-2",
            "opaque-session",
            recipient=Recipient("owner", "weixin", "binding"),
            content="done",
            event_type="response.completed",
        )
        assert await manager.deliver(message) is False
        assert observed == [(message, False)]

    asyncio.run(run())


def test_channel_manager_calls_im_listener_after_transport_exception() -> None:
    async def run() -> None:
        bus = AsyncMessageBus()
        completed: list[tuple[ChannelRoute, str]] = []

        async def submit(message, *, dispatch):
            await dispatch(message)
            return True

        async def complete(route, request_id):
            completed.append((route, request_id))

        im = im_module.ImGatewayCoordinator(
            bus,
            submit=submit,
            complete_route_turn=complete,
        )
        route = ChannelRoute("owner", "feishu", "bot:chat", "opaque-session")
        inbound = InboundMessage("request-3", route=route, content="hello")
        assert await im.accept(inbound) is True
        await bus.consume_inbound()

        manager = manager_module.ChannelManager(bus, [FailingTransport("feishu")])
        manager.add_delivery_listener(im.on_delivery)
        terminal = OutboundMessage(
            "terminal-3",
            route.session_id,
            recipient=Recipient("owner", "feishu", "bot:chat"),
            content="failed",
            event_type="response.error",
            metadata={"request_id": inbound.id},
        )
        assert await manager.deliver(terminal) is False
        assert completed == [(route, inbound.id)]

    asyncio.run(run())

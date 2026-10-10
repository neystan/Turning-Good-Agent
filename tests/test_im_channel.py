from __future__ import annotations

import asyncio
import importlib


bus_module = importlib.import_module("Turning-Good-Agent.bus.queue")
messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
turns_module = importlib.import_module("Turning-Good-Agent.gateway.turns")
im_module = importlib.import_module("Turning-Good-Agent.channels.im")
base_module = importlib.import_module("Turning-Good-Agent.channels.base")

AsyncMessageBus = bus_module.AsyncMessageBus
ChannelRoute = messages_module.ChannelRoute
InboundMessage = messages_module.InboundMessage
OutboundMessage = messages_module.OutboundMessage
Recipient = messages_module.Recipient


def _route(channel: str = "weixin", conversation: str = "binding") -> ChannelRoute:
    return ChannelRoute("owner", channel, conversation, f"session-{channel}-{conversation}")


def test_im_adapter_discards_deltas_for_all_im_channels() -> None:
    async def run() -> None:
        for channel in ("feishu", "weixin"):
            bus = AsyncMessageBus()
            route = _route(channel)
            adapter = im_module.ImChannelAdapter(bus, im_module.ImTurnControl("request-1", route))

            await adapter.on_delta("")
            await adapter.on_delta("hello")

            assert bus.outbound.empty()

    asyncio.run(run())


def test_im_adapter_does_not_publish_status_tool_data_or_reasoning() -> None:
    async def run() -> None:
        bus = AsyncMessageBus()
        adapter = im_module.ImChannelAdapter(bus, im_module.ImTurnControl("request-1", _route()))

        await adapter.on_status("working")
        await adapter.on_tool_started("tool-1", "safe_tool", {"secret": "must not leak"})
        await adapter.on_tool_finished("tool-1", "safe_tool", False)
        await adapter.on_completed("final")
        await adapter.on_error("error")

        assert bus.outbound.empty()
        assert await adapter.consume_guidance() == []
        assert adapter.is_stop_requested() is False

    asyncio.run(run())


def test_im_adapter_rejects_tool_approval_defensively() -> None:
    async def run() -> None:
        adapter = im_module.ImChannelAdapter(
            AsyncMessageBus(), im_module.ImTurnControl("request-1", _route())
        )
        result = await adapter.request_tool_approval(object())
        assert result == "当前 Channel 不支持工具审批。"

    asyncio.run(run())


def test_im_coordinator_routes_through_shared_gateway_and_releases_failed_terminal_fifo() -> None:
    async def run() -> None:
        bus = AsyncMessageBus()
        gateway = turns_module.GatewayTurnCoordinator(
            bus,
            lambda: object(),
            max_concurrent_turns=1,
        )
        coordinator = im_module.ImGatewayCoordinator(bus, gateway)
        first_route = _route()
        first = InboundMessage("request-1", route=first_route, content="first")
        second = InboundMessage("request-2", route=first_route, content="second")

        assert await coordinator.accept(first) is True
        assert await coordinator.accept(second) is True
        assert await bus.consume_inbound() is first

        terminal = OutboundMessage(
            "terminal-1",
            first_route.session_id,
            recipient=Recipient("owner", "weixin", "binding"),
            content="failed",
            event_type="response.error",
            metadata={"request_id": "request-1"},
        )
        await coordinator.on_delivery(terminal, accepted=False)

        assert await bus.consume_inbound() is second
        assert coordinator.is_globally_idle() is False

        second_terminal = OutboundMessage(
            "terminal-2",
            first_route.session_id,
            recipient=Recipient("owner", "weixin", "binding"),
            content="done",
            event_type="response.completed",
            metadata={"request_id": "request-2"},
        )
        await coordinator.on_delivery(second_terminal, accepted=True)
        assert coordinator.is_globally_idle() is True

    asyncio.run(run())


def test_im_busy_until_terminal_delivery() -> None:
    async def run() -> None:
        bus = AsyncMessageBus()
        completed: list[tuple[ChannelRoute, str]] = []

        async def submit(message, *, dispatch):
            await dispatch(message)
            return True

        async def complete(route, request_id):
            completed.append((route, request_id))

        coordinator = im_module.ImGatewayCoordinator(
            bus,
            submit=submit,
            complete_route_turn=complete,
        )
        route = _route("weixin", "binding-a")
        message = InboundMessage("request-1", route=route, content="hello")

        assert await coordinator.accept(message) is True
        assert coordinator.is_globally_idle() is False
        await bus.consume_inbound()

        terminal = OutboundMessage(
            "terminal-1",
            route.session_id,
            recipient=Recipient("owner", "weixin", "binding-a"),
            content="done",
            event_type="response.completed",
            metadata={"request_id": "request-1"},
        )
        await coordinator.on_delivery(terminal, accepted=True)

        assert completed == [(route, "request-1")]
        assert coordinator.is_globally_idle() is True

    asyncio.run(run())


def test_im_coordinator_ignores_delta_and_proactive_delivery_for_fifo() -> None:
    async def run() -> None:
        bus = AsyncMessageBus()
        completed: list[tuple[ChannelRoute, str]] = []

        async def submit(message, *, dispatch):
            await dispatch(message)
            return True

        async def complete(route, request_id):
            completed.append((route, request_id))

        coordinator = im_module.ImGatewayCoordinator(
            bus,
            submit=submit,
            complete_route_turn=complete,
        )
        route = _route("feishu", "bot:chat")
        message = InboundMessage("request-1", route=route, content="hello")
        assert await coordinator.accept(message) is True
        await bus.consume_inbound()

        delta = OutboundMessage(
            "delta",
            route.session_id,
            recipient=Recipient("owner", "feishu", "bot:chat"),
            content="part",
            event_type="response.delta",
            metadata={"request_id": "request-1"},
        )
        notice = OutboundMessage(
            "notice",
            "proactive",
            recipient=Recipient("owner", "feishu", "bot:chat"),
            content="notice",
            event_type="proactive.cron.completed",
            disposition="proactive_notification",
        )
        await coordinator.on_delivery(delta, accepted=True)
        await coordinator.on_delivery(notice, accepted=False)
        assert completed == []
        assert coordinator.is_globally_idle() is False

    asyncio.run(run())


def test_im_coordinator_rejects_non_im_or_empty_messages_without_bus_publish() -> None:
    async def run() -> None:
        bus = AsyncMessageBus()

        async def submit(*args, **kwargs):
            raise AssertionError("invalid messages must not reach GatewayTurnCoordinator")

        async def complete(*args):
            raise AssertionError("invalid messages must not complete a route")

        coordinator = im_module.ImGatewayCoordinator(
            bus,
            submit=submit,
            complete_route_turn=complete,
        )
        assert await coordinator.accept(
            InboundMessage("web", route=_route("web"), content="hello")
        ) is False
        assert await coordinator.accept(InboundMessage("empty", route=_route(), content=" \t")) is False
        assert bus.inbound.empty()

    asyncio.run(run())


def test_im_coordinator_registers_factory_on_each_runtime() -> None:
    async def run() -> None:
        bus = AsyncMessageBus()
        runtime = type("Runtime", (), {"channel_router": base_module.ChannelRouter()})()

        async def submit(message, *, dispatch):
            await dispatch(message)
            return True

        async def complete(route, request_id):
            del route, request_id

        coordinator = im_module.ImGatewayCoordinator(
            bus,
            submit=submit,
            complete_route_turn=complete,
        )
        coordinator.register_runtime(runtime)

        for channel in im_module.IM_CHANNELS:
            route = _route(channel, f"{channel}-conversation")
            message = InboundMessage(f"request-{channel}", route=route, content="hello")
            assert await coordinator.accept(message) is True
            adapter = runtime.channel_router.create(message)
            assert isinstance(adapter, im_module.ImChannelAdapter)
            await bus.consume_inbound()
            terminal = OutboundMessage(
                f"terminal-{channel}",
                route.session_id,
                recipient=Recipient("owner", channel, route.conversation_id),
                content="done",
                event_type="response.completed",
                metadata={"request_id": message.id},
            )
            await coordinator.on_delivery(terminal, accepted=True)

    asyncio.run(run())


def test_im_runtime_factory_rejects_a_stale_request_id() -> None:
    async def run() -> None:
        bus = AsyncMessageBus()
        runtime = type("Runtime", (), {"channel_router": base_module.ChannelRouter()})()

        async def submit(message, *, dispatch):
            await dispatch(message)
            return True

        async def complete(route, request_id):
            del route, request_id

        coordinator = im_module.ImGatewayCoordinator(
            bus,
            submit=submit,
            complete_route_turn=complete,
        )
        coordinator.register_runtime(runtime)
        route = _route()
        message = InboundMessage("request-1", route=route, content="hello")
        assert await coordinator.accept(message) is True
        stale = InboundMessage("request-2", route=route, content="stale")
        try:
            runtime.channel_router.create(stale)
        except RuntimeError as exc:
            assert str(exc) == "当前 IM 会话请求不一致"
        else:
            raise AssertionError("stale request must not reuse the active adapter control")
        await bus.consume_inbound()

    asyncio.run(run())


def test_im_close_blocks_a_dispatch_already_waiting_at_gateway_boundary() -> None:
    async def run() -> None:
        bus = AsyncMessageBus()
        dispatch_started = asyncio.Event()
        release_dispatch = asyncio.Event()

        async def submit(message, *, dispatch):
            dispatch_started.set()
            await release_dispatch.wait()
            return await dispatch(message)

        async def complete(route, request_id):
            del route, request_id

        coordinator = im_module.ImGatewayCoordinator(
            bus,
            submit=submit,
            complete_route_turn=complete,
        )
        route = _route()
        accepted = asyncio.create_task(
            coordinator.accept(InboundMessage("request-1", route=route, content="hello"))
        )
        await dispatch_started.wait()
        await coordinator.close()
        release_dispatch.set()
        try:
            await accepted
        except RuntimeError as exc:
            assert str(exc) == "Gateway IM 控制器已关闭"
        else:
            raise AssertionError("a close-raced dispatch must be rejected")
        assert bus.inbound.empty()
        assert coordinator.is_globally_idle() is True

    asyncio.run(run())

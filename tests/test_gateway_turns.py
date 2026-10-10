from __future__ import annotations

import asyncio
import importlib


def test_route_matcher_keeps_exact_and_prefixed_routes_separate() -> None:
    exact = lambda route: route == ("owner", "weixin", "binding-a")
    prefix = lambda route: (
        route[0] == "owner" and route[1] == "feishu" and route[2].startswith("account-a:")
    )

    assert exact(("owner", "weixin", "binding-a"))
    assert not exact(("owner", "weixin", "binding-a:child"))
    assert prefix(("owner", "feishu", "account-a:chat-a"))
    assert not prefix(("owner", "web", "account-a:chat-a"))


def test_reservation_blocks_matching_route_not_other_route() -> None:
    async def run() -> None:
        turns_module = importlib.import_module("Turning-Good-Agent.gateway.turns")
        messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
        bus_module = importlib.import_module("Turning-Good-Agent.bus.queue")
        coordinator = turns_module.GatewayTurnCoordinator(
            bus_module.AsyncMessageBus(),
            lambda: object(),
            max_concurrent_turns=2,
        )
        matching = messages_module.InboundMessage(
            "matching",
            route=messages_module.ChannelRoute(
                "owner", "weixin", "binding-a", "session-matching"
            ),
            content="matching",
        )
        other_binding = messages_module.InboundMessage(
            "other-binding",
            route=messages_module.ChannelRoute(
                "owner", "weixin", "binding-b", "session-other-binding"
            ),
            content="other binding",
        )
        other_channel = messages_module.InboundMessage(
            "other-channel",
            route=messages_module.ChannelRoute(
                "owner", "web", "binding-a", "session-other-channel"
            ),
            content="other channel",
        )
        dispatched_ids: list[str] = []

        async def dispatch(message: object) -> None:
            dispatched_ids.append(message.id)

        matcher = lambda route: route == ("owner", "weixin", "binding-a")
        reservation = await coordinator.reserve_matching_routes_if_idle(matcher)
        assert reservation is not None
        assert await coordinator.submit(matching, dispatch=dispatch) is False
        assert await coordinator.submit(other_binding, dispatch=dispatch) is True
        assert await coordinator.submit(other_channel, dispatch=dispatch) is True
        assert dispatched_ids == ["other-binding", "other-channel"]
        await coordinator.release_route_block(reservation)
        assert await coordinator.submit(matching, dispatch=dispatch) is True

    asyncio.run(run())


def test_idle_only_reservation_never_reserves_a_busy_route() -> None:
    async def run() -> None:
        turns_module = importlib.import_module("Turning-Good-Agent.gateway.turns")
        messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
        bus_module = importlib.import_module("Turning-Good-Agent.bus.queue")
        coordinator = turns_module.GatewayTurnCoordinator(
            bus_module.AsyncMessageBus(),
            lambda: object(),
            max_concurrent_turns=2,
        )
        message = messages_module.InboundMessage(
            "busy",
            route=messages_module.ChannelRoute(
                "owner", "weixin", "binding-a", "session-busy"
            ),
            content="busy",
        )

        async def dispatch(_message) -> None:
            return None

        assert await coordinator.submit(message, dispatch=dispatch) is True
        matcher = lambda route: route == ("owner", "weixin", "binding-a")
        assert await coordinator.reserve_matching_routes_if_idle(matcher) is None
        assert coordinator._route_blocks == {}

    asyncio.run(run())


def test_scope_busy_distinguishes_active_pending_and_nonmatching_routes() -> None:
    async def run() -> None:
        turns_module = importlib.import_module("Turning-Good-Agent.gateway.turns")
        messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
        bus_module = importlib.import_module("Turning-Good-Agent.bus.queue")
        coordinator = turns_module.GatewayTurnCoordinator(
            bus_module.AsyncMessageBus(),
            lambda: object(),
            max_concurrent_turns=2,
        )
        matcher = lambda route: route == ("owner", "weixin", "binding-a")
        active_route = messages_module.ChannelRoute(
            "owner", "weixin", "binding-a", "active-session"
        )
        pending_route = messages_module.ChannelRoute(
            "owner", "weixin", "binding-a", "pending-session"
        )
        nonmatching_route = messages_module.ChannelRoute(
            "owner", "weixin", "binding-b", "other-session"
        )

        coordinator._active_routes[coordinator._route_key(active_route)] = "active"
        assert await coordinator.reserve_matching_routes_if_idle(matcher) is None
        coordinator._active_routes.clear()

        coordinator._pending_routes[coordinator._route_key(pending_route)] = asyncio.Queue()
        assert await coordinator.reserve_matching_routes_if_idle(matcher) is None
        coordinator._pending_routes.clear()

        coordinator._active_routes[coordinator._route_key(nonmatching_route)] = "other"
        block = await coordinator.reserve_matching_routes_if_idle(matcher)
        assert block is not None
        await coordinator.release_route_block(block)

    asyncio.run(run())


def test_same_channel_and_conversation_different_principals_do_not_share_fifo() -> None:
    """Would fail if one principal's active turn queued another principal's turn."""

    async def run() -> None:
        turns_module = importlib.import_module("Turning-Good-Agent.gateway.turns")
        messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
        bus_module = importlib.import_module("Turning-Good-Agent.bus.queue")
        coordinator = turns_module.GatewayTurnCoordinator(
            bus_module.AsyncMessageBus(),
            lambda: object(),
            max_concurrent_turns=2,
        )
        first_route = messages_module.ChannelRoute("principal-a", "weixin", "binding", "session-a")
        second_route = messages_module.ChannelRoute("principal-b", "weixin", "binding", "session-b")
        first = messages_module.InboundMessage("a", route=first_route, content="first")
        second = messages_module.InboundMessage("b", route=second_route, content="second")
        dispatched_ids: list[str] = []

        async def dispatch(message: object) -> None:
            dispatched_ids.append(message.id)

        assert await coordinator.submit(first, dispatch=dispatch) is True
        assert await coordinator.submit(second, dispatch=dispatch) is True
        assert dispatched_ids == ["a", "b"]

    asyncio.run(run())


def test_dispatch_failure_rolls_back_route_and_allows_next_submission() -> None:
    async def run() -> None:
        turns_module = importlib.import_module("Turning-Good-Agent.gateway.turns")
        messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
        bus_module = importlib.import_module("Turning-Good-Agent.bus.queue")

        class FailingBus(bus_module.AsyncMessageBus):
            def __init__(self) -> None:
                super().__init__()
                self.fail_once = True

            async def publish_inbound(self, message):
                if self.fail_once:
                    self.fail_once = False
                    del message
                    raise RuntimeError("inbound bus unavailable")
                await super().publish_inbound(message)

        bus = FailingBus()
        coordinator = turns_module.GatewayTurnCoordinator(
            bus,
            lambda: object(),
            max_concurrent_turns=1,
        )
        route = messages_module.ChannelRoute("principal", "weixin", "binding", "session")
        first = messages_module.InboundMessage("first", route=route, content="first")

        async def dispatch(message):
            await bus.publish_inbound(message)

        try:
            await coordinator.submit(first, dispatch=dispatch)
        except RuntimeError as exc:
            assert str(exc) == "inbound bus unavailable"
        else:
            raise AssertionError("dispatch failure must be propagated")

        assert coordinator.is_globally_idle() is True
        second = messages_module.InboundMessage("second", route=route, content="second")
        assert await coordinator.submit(second, dispatch=dispatch) is True
        assert await bus.consume_inbound() is second

    asyncio.run(run())


def test_close_clears_route_state_before_a_same_host_restart() -> None:
    async def run() -> None:
        turns_module = importlib.import_module("Turning-Good-Agent.gateway.turns")
        messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
        bus_module = importlib.import_module("Turning-Good-Agent.bus.queue")
        coordinator = turns_module.GatewayTurnCoordinator(
            bus_module.AsyncMessageBus(),
            lambda: object(),
            max_concurrent_turns=1,
        )
        route = messages_module.ChannelRoute("principal", "weixin", "binding", "session")
        dispatched: list[str] = []

        async def dispatch(message) -> None:
            dispatched.append(message.id)

        assert await coordinator.submit(
            messages_module.InboundMessage("first", route=route, content="first"), dispatch=dispatch
        ) is True
        await coordinator.close()
        assert coordinator.is_globally_idle() is True

        await coordinator.start()
        try:
            assert await coordinator.submit(
                messages_module.InboundMessage("second", route=route, content="second"),
                dispatch=dispatch,
            ) is True
        finally:
            await coordinator.close()

        assert dispatched == ["first", "second"]

    asyncio.run(run())


def test_terminal_reply_retains_weixin_binding_epoch() -> None:
    async def run() -> None:
        turns_module = importlib.import_module("Turning-Good-Agent.gateway.turns")
        messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
        bus_module = importlib.import_module("Turning-Good-Agent.bus.queue")

        class Runtime:
            async def run_turn(self, message):
                return messages_module.OutboundMessage(
                    "reply",
                    message.session_id,
                    recipient=messages_module.Recipient(
                        message.route.principal_id,
                        message.route.channel,
                        message.route.conversation_id,
                    ),
                    content="done",
                )

        bus = bus_module.AsyncMessageBus()
        coordinator = turns_module.GatewayTurnCoordinator(
            bus,
            lambda: Runtime(),
            max_concurrent_turns=1,
        )
        message = messages_module.InboundMessage(
            "request",
            route=messages_module.ChannelRoute("owner", "weixin", "binding", "session"),
            content="hello",
            metadata={"binding_epoch": 3},
        )

        await coordinator._run_message(message)
        outbound = await bus.consume_outbound()
        assert outbound.metadata["binding_epoch"] == 3
        assert outbound.metadata["request_id"] == "request"

    asyncio.run(run())

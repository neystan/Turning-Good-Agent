from __future__ import annotations

import importlib
import asyncio
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest


messages = importlib.import_module("Turning-Good-Agent.bus.messages")
turn_context = importlib.import_module("Turning-Good-Agent.runtime.turn_context")

ChannelRoute = messages.ChannelRoute
InboundMessage = messages.InboundMessage
OutboundMessage = messages.OutboundMessage
Recipient = messages.Recipient


def test_inbound_message_exposes_immutable_route_and_legacy_aliases() -> None:
    """Would fail if a routed inbound message lost channel identity at the legacy boundary."""
    route = ChannelRoute("principal-1", "cli", "conversation-1", "session-1")

    message = InboundMessage.new("hello", route)

    assert message.session_id == "session-1"
    assert message.route == route
    assert (message.user_id, message.channel) == ("principal-1", "cli")
    with pytest.raises(FrozenInstanceError):
        route.channel = "web"  # type: ignore[misc]


def test_outbound_message_carries_recipient_event_and_disposition() -> None:
    """Would fail if proactive delivery could not be routed without a chat-channel string."""
    recipient = Recipient("principal-1", "web", "conversation-1")

    message = OutboundMessage.completed("session-1", recipient, "done", event_id="event-1")

    assert message.recipient == recipient
    assert message.target_channel == "web"
    assert message.event_id == "event-1"
    assert message.disposition == "chat_reply"


def test_current_route_context_is_scoped_and_resettable() -> None:
    """Would fail if a prior turn's route leaked into a later task context."""
    route = ChannelRoute("principal-1", "cli", "conversation-1", "session-1")

    assert turn_context.current_route() is None
    token = turn_context.set_current_route(route)
    try:
        assert turn_context.current_route() == route
    finally:
        turn_context.reset_current_route(token)
    assert turn_context.current_route() is None


def test_runtime_response_uses_the_inbound_route_recipient() -> None:
    """Would fail if a normal reply used a broad Channel name instead of its originating conversation."""

    async def run() -> None:
        state = importlib.import_module("Turning-Good-Agent.runtime.state")
        route = ChannelRoute("principal-1", "web", "conversation-1", "session-1")
        context = SimpleNamespace(
            inbound=InboundMessage.new("hello", route),
            error=None,
            final_content="reply",
            cancelled=False,
            outbound=None,
        )

        await state.respond(context)

        assert context.outbound.recipient == Recipient("principal-1", "web", "conversation-1")
        assert context.outbound.event_id == context.inbound.id

    asyncio.run(run())


def test_runtime_turn_sets_and_resets_the_route_context(monkeypatch: pytest.MonkeyPatch) -> None:
    """Would fail if a background action could observe a previous concurrent turn's route."""

    async def run() -> None:
        runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
        route = ChannelRoute("principal-1", "cli", "conversation-1", "session-1")
        runtime = runtime_module.AgentRuntime.__new__(runtime_module.AgentRuntime)
        runtime._active_turns = 0
        runtime._activity_lock = asyncio.Lock()

        async def fake_run_turn(self, message):
            del self
            assert turn_context.current_route() == route
            assert turn_context.current_inbound() == message
            return OutboundMessage.completed("session-1", Recipient("principal-1", "cli", "conversation-1"), "done")

        monkeypatch.setattr(runtime_module.AgentRuntime, "_run_turn", fake_run_turn)
        result = await runtime.run_turn(InboundMessage.new("hello", route))

        assert result.content == "done"
        assert turn_context.current_route() is None
        assert turn_context.current_inbound() is None

    asyncio.run(run())


def test_runtime_session_state_preserves_the_full_channel_route() -> None:
    """Would fail if an opaque session ID overwrote the external conversation ID."""

    async def run() -> None:
        state = importlib.import_module("Turning-Good-Agent.runtime.state")
        route = ChannelRoute("principal-1", "web", "browser-conversation", "opaque-session")
        inbound = InboundMessage.new("hello", route)
        captured: list[ChannelRoute] = []

        class Sessions:
            async def cleanup_expired_sessions(self, retention_days: int) -> None:
                assert retention_days == 7

            async def load_or_create(self, received_route: ChannelRoute) -> str:
                captured.append(received_route)
                return "session"

        runtime = SimpleNamespace(sessions=Sessions(), settings=SimpleNamespace(sessions=SimpleNamespace(retention_days=7)))
        context = SimpleNamespace(inbound=inbound, session=None)

        assert await state.session(runtime, context) == "ok"
        assert context.session == "session"
        assert captured == [route]

    asyncio.run(run())

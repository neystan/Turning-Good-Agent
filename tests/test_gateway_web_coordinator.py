from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace


messages = importlib.import_module("Turning-Good-Agent.bus.messages")
routing = importlib.import_module("Turning-Good-Agent.gateway.routing")
bus_module = importlib.import_module("Turning-Good-Agent.bus.queue")

OutboundMessage = messages.OutboundMessage
Recipient = messages.Recipient
ChannelRoute = messages.ChannelRoute
AsyncMessageBus = bus_module.AsyncMessageBus


def test_web_coordinator_submits_a_routed_turn_and_finishes_only_via_channel_delivery() -> None:
    """Would fail if Web still consumed the Bus or bypassed Gateway turn routing."""

    async def run() -> None:
        coordinator_module = importlib.import_module("Turning-Good-Agent.web.backend.coordinator")
        submitted = []
        completed = []
        became_idle = asyncio.Event()
        bus = AsyncMessageBus()

        async def submit(message, *, dispatch):
            submitted.append(message)
            await dispatch(message)
            return True

        async def complete_route_turn(route, request_id):
            completed.append((route, request_id))

        async def runtime_provider():
            return SimpleNamespace()

        async def on_idle() -> None:
            became_idle.set()

        coordinator = coordinator_module.WebSessionCoordinator(
            submit=submit,
            complete_route_turn=complete_route_turn,
            runtime_provider=runtime_provider,
            principal_id="local-user",
            event_buffer_size=16,
            on_idle=on_idle,
            bus=bus,
        )

        session_id, request_id = await coordinator.send(None, "hello", client_action_id="browser-action")

        assert len(submitted) == 1
        inbound = submitted[0]
        assert inbound.route.principal_id == "local-user"
        assert inbound.route.channel == "web"
        assert inbound.route.conversation_id.startswith("web-")
        assert inbound.session_id == routing.derive_session_id(
            "local-user", "web", inbound.route.conversation_id
        )
        assert session_id == inbound.session_id
        assert coordinator.is_active(session_id) is True

        outbound = OutboundMessage.completed(
            session_id,
            Recipient("local-user", "web", inbound.route.conversation_id),
            "reply",
            event_id=request_id,
        )
        outbound.metadata["request_id"] = request_id

        assert await coordinator.deliver(outbound) is True
        await asyncio.wait_for(became_idle.wait(), timeout=1)
        assert coordinator.is_active(session_id) is False
        assert completed == [(inbound.route, request_id)]

        replay, queue = await coordinator.hub.subscribe(session_id, after_event_id=0)
        coordinator.hub.unsubscribe(session_id, queue)
        assert [event.type for event in replay][-2:] == ["response.completed", "task.completed"]

    asyncio.run(run())


def test_web_coordinator_accepts_attachment_only_and_resolves_before_bus() -> None:
    async def run() -> None:
        coordinator_module = importlib.import_module("Turning-Good-Agent.web.backend.coordinator")
        models = importlib.import_module("Turning-Good-Agent.web.attachments.models")
        bus = AsyncMessageBus()
        submitted = []
        resolved_attachment = SimpleNamespace(metadata=SimpleNamespace(source="document"))

        class Attachments:
            def resolve(self, owner, ids):
                assert owner.channel == "web"
                assert ids == ["att_one"]
                return [resolved_attachment]

        async def submit(message, *, dispatch):
            submitted.append(message)
            await dispatch(message)
            return True

        coordinator = coordinator_module.WebSessionCoordinator(
            submit=submit,
            complete_route_turn=lambda *_args: None,
            runtime_provider=lambda: SimpleNamespace(),
            principal_id="local-user",
            event_buffer_size=16,
            bus=bus,
            attachment_service=Attachments(),
        )
        await coordinator.send(None, "", attachment_ids=["att_one"])
        assert submitted[0].content == ""
        assert submitted[0].attachments == [resolved_attachment]

    asyncio.run(run())


def test_web_coordinator_can_create_an_empty_session_for_attachment_upload() -> None:
    async def run() -> None:
        coordinator_module = importlib.import_module("Turning-Good-Agent.web.backend.coordinator")
        created = []

        class Sessions:
            async def load_or_create(self, route):
                created.append(route)
                return SimpleNamespace(
                    id=route.session_id,
                    principal_id=route.principal_id,
                    channel=route.channel,
                    conversation_id=route.conversation_id,
                )

        runtime = SimpleNamespace(sessions=Sessions())
        coordinator = coordinator_module.WebSessionCoordinator(
            submit=lambda *_args, **_kwargs: None,
            complete_route_turn=lambda *_args: None,
            runtime_provider=lambda: runtime,
            principal_id="local-user",
            event_buffer_size=16,
        )

        session = await coordinator.create_session()

        assert session.id
        assert session.channel == "web"
        assert session.principal_id == "local-user"
        assert created and created[0].session_id == session.id

    asyncio.run(run())


def test_web_turn_adapter_keeps_the_gateway_recipient_on_stream_events() -> None:
    """Would fail if a Web delta returned to a broad legacy Channel target."""

    async def run() -> None:
        adapter_module = importlib.import_module("Turning-Good-Agent.channels.web")
        coordinator_module = importlib.import_module("Turning-Good-Agent.web.backend.coordinator")
        bus = AsyncMessageBus()
        route = ChannelRoute("local-user", "web", "browser-conversation", "opaque-session")
        control = coordinator_module.WebTurnControl("opaque-session", "request-1", route)
        adapter = adapter_module.WebChannelAdapter(bus, control, route=route)

        await adapter.on_delta("streamed")

        outbound = await bus.consume_outbound()
        assert outbound.recipient == Recipient("local-user", "web", "browser-conversation")
        assert outbound.event_id == "request-1"
        assert outbound.metadata["request_id"] == "request-1"

    asyncio.run(run())


def test_web_turn_adapter_publishes_tool_lifecycle_with_arguments() -> None:
    async def run() -> None:
        adapter_module = importlib.import_module("Turning-Good-Agent.channels.web")
        coordinator_module = importlib.import_module("Turning-Good-Agent.web.backend.coordinator")
        bus = AsyncMessageBus()
        route = ChannelRoute("local-user", "web", "browser-conversation", "opaque-session")
        control = coordinator_module.WebTurnControl("opaque-session", "request-1", route)
        adapter = adapter_module.WebChannelAdapter(bus, control, route=route)

        await adapter.on_tool_started("tool-1", "run_breakbeat", {"scope": "global"})
        started = await bus.consume_outbound()
        await adapter.on_tool_finished("tool-1", "run_breakbeat", False)
        finished = await bus.consume_outbound()

        assert started.event_type == "tool.started"
        assert started.metadata == {
            "request_id": "request-1",
            "tool_call_id": "tool-1",
            "tool_name": "run_breakbeat",
            "args": {"scope": "global"},
        }
        assert finished.event_type == "tool.finished"
        assert finished.metadata["tool_call_id"] == "tool-1"
        assert finished.metadata["tool_name"] == "run_breakbeat"
        assert finished.metadata["failed"] is False

    asyncio.run(run())


def test_web_multi_agent_event_payload_is_safe_and_cursor_is_monotonic() -> None:
    """Multi-Agent 事件只能投影有限字段，并沿用单调 SessionEvent cursor。"""

    async def run() -> None:
        coordinator_module = importlib.import_module("Turning-Good-Agent.web.backend.coordinator")
        adapter_module = importlib.import_module("Turning-Good-Agent.channels.web")
        bus = AsyncMessageBus()
        route = ChannelRoute("local-user", "web", "browser-conversation", "opaque-session")
        control = coordinator_module.WebTurnControl("opaque-session", "request-1", route)
        coordinator = coordinator_module.WebSessionCoordinator(
            submit=lambda *_args, **_kwargs: None,
            complete_route_turn=lambda *_args: None,
            runtime_provider=lambda: SimpleNamespace(),
            principal_id="local-user",
            event_buffer_size=16,
            bus=bus,
        )
        coordinator.controls[route.session_id] = control
        adapter = adapter_module.WebChannelAdapter(bus, control, route=route)

        await adapter.on_multi_agent_event(
            "multi_agent.node.updated",
            {
                "run_id": "run-1",
                "node_id": "node-1",
                "status": "running",
                "raw_prompt": "must not escape",
                "worker_output": "must not escape",
            },
        )
        outbound = await bus.consume_outbound()
        assert await coordinator.deliver(outbound) is True
        replay, queue = await coordinator.hub.subscribe(route.session_id, after_event_id=0)
        coordinator.hub.unsubscribe(route.session_id, queue)
        event = replay[-1]
        assert event.type == "multi_agent.node.updated"
        assert event.payload == {"run_id": "run-1", "node_id": "node-1", "status": "running"}
        assert [item.event_id for item in replay] == sorted(item.event_id for item in replay)

    asyncio.run(run())


# 验证重连快照直接从父 Session 的有界读模型恢复 Multi-Agent 摘要。
def test_web_snapshot_reads_multi_agent_runs_from_parent_session() -> None:
    # 构造带主体隔离 Reader 的最小 Runtime。
    async def run() -> None:
        coordinator_module = importlib.import_module("Turning-Good-Agent.web.backend.coordinator")
        route = ChannelRoute("local-user", "web", "browser-conversation", "opaque-session")

        class Store:
            # 返回当前 Web 主体所属的正常会话。
            async def load_session(self, session_id):
                assert session_id == route.session_id
                return SimpleNamespace(
                    id=route.session_id,
                    principal_id=route.principal_id,
                    channel="web",
                    conversation_id=route.conversation_id,
                )

        class Reader:
            # 返回已持久化的安全 Run 摘要。
            async def read_multi_agent_runs(self, session_id, limit=20):
                assert (session_id, limit) == (route.session_id, 20)
                return [{"run_id": "run-1", "status": "completed", "nodes": []}]

        runtime = SimpleNamespace(
            sessions=SimpleNamespace(store=Store()),
            resolve_principal_context=lambda supplied: SimpleNamespace(sessions=Reader()),
        )
        coordinator = coordinator_module.WebSessionCoordinator(
            submit=lambda *_args, **_kwargs: None,
            complete_route_turn=lambda *_args: None,
            runtime_provider=lambda: runtime,
            principal_id=route.principal_id,
            event_buffer_size=16,
        )

        replay, queue = await coordinator.hub.subscribe(route.session_id, after_event_id=None)
        coordinator.hub.unsubscribe(route.session_id, queue)

        assert replay[0].payload["multi_agent_runs"] == [
            {"run_id": "run-1", "status": "completed", "nodes": []}
        ]

    asyncio.run(run())

def test_web_transport_keeps_proactive_notifications_out_of_chat_sessions() -> None:
    """Would fail if a proactive notice were projected as a normal chat reply."""

    async def run() -> None:
        adapter_module = importlib.import_module("Turning-Good-Agent.channels.web")
        chat_messages = []
        notices = []

        class Coordinator:
            async def deliver(self, message):
                chat_messages.append(message)
                return True

        async def publish_notice(message):
            notices.append(message)
            return True

        transport = adapter_module.WebChannelTransport(Coordinator(), publish_notice)
        chat = OutboundMessage.completed(
            "opaque-session",
            Recipient("local-user", "web", "browser-conversation"),
            "reply",
            event_id="request-1",
        )
        notice = OutboundMessage(
            "notice-1",
            "proactive",
            recipient=Recipient("local-user", "web", "proactive"),
            content="background result",
            event_type="proactive.cron.completed",
            event_id="event-1",
            disposition="proactive_notification",
        )

        assert await transport.send(chat) is True
        assert await transport.send(notice) is True
        assert chat_messages == [chat]
        assert notices == [notice]

    asyncio.run(run())

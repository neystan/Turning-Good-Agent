from __future__ import annotations

import asyncio
import importlib
import json
from pathlib import Path


def test_automatic_notification_targets_web_and_the_current_online_cli_session(tmp_path: Path) -> None:
    """Would fail if an online CLI were omitted or persisted for later replay."""

    async def run() -> None:
        gateway_module = importlib.import_module("Turning-Good-Agent.gateway.host")
        runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
        settings_module = importlib.import_module("Turning-Good-Agent.config.settings")

        state_dir = tmp_path / "state"
        config_path = tmp_path / "settings.local.json"
        config_path.write_text(json.dumps({"data_dir": "state"}), encoding="utf-8")
        settings = settings_module.Settings(data_dir=state_dir)
        settings.local_config_path = config_path
        settings.proactive.enabled = False
        runtime = runtime_module.AgentRuntime.create_default(settings, object())
        host = gateway_module.GatewayHost(settings, runtime=runtime)
        received: list[dict[str, object]] = []
        received_event = asyncio.Event()
        published = []
        publish_outbound = host.bus.publish_outbound

        async def record_publish(message) -> None:
            published.append(message)
            await publish_outbound(message)

        host.notification_publisher._publish_outbound = record_publish

        async def send(payload: dict[str, object]) -> bool:
            received.append(payload)
            received_event.set()
            return True

        host.cli_transport.register("cli-connection", settings.gateway.principal_id, "first", send)
        current_route = host.cli_transport.bind_route("cli-connection", "second")

        await host.channel_manager.start()
        try:
            delivered = await host.notification_publisher.enqueue(
                event_type="proactive.cron.completed",
                content="scheduled result",
                source_id="cron_1",
                notification_scope="all_subscribed",
                principal_id=settings.gateway.principal_id,
            )
            await asyncio.wait_for(received_event.wait(), timeout=1)
        finally:
            await host.channel_manager.close()

        assert {
            (message.recipient.channel, message.recipient.conversation_id)
            for message in delivered
        } == {("web", "proactive"), ("cli", "second")}
        assert {
            (message.recipient.channel, message.recipient.conversation_id)
            for message in published
        } == {("web", "proactive"), ("cli", "second")}
        assert received == [
            {
                "type": "proactive.cron.completed",
                "session_id": "proactive",
                "content": "scheduled result",
            }
        ]
        assert current_route.conversation_id == "second"
        assert not hasattr(host, "delivery_outbox")
        assert not hasattr(host, "delivery_worker")

    asyncio.run(run())


def test_web_notice_severity_reflects_breakbeat_outcome(tmp_path: Path) -> None:
    """Partial and failed Breakbeat outcomes must retain their user-visible severity."""

    async def run() -> None:
        gateway_module = importlib.import_module("Turning-Good-Agent.gateway.host")
        messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
        runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
        settings_module = importlib.import_module("Turning-Good-Agent.config.settings")

        config_path = tmp_path / "settings.local.json"
        config_path.write_text(json.dumps({"data_dir": "state"}), encoding="utf-8")
        settings = settings_module.Settings(data_dir=tmp_path / "state")
        settings.local_config_path = config_path
        settings.proactive.enabled = False
        runtime = runtime_module.AgentRuntime.create_default(settings, object())
        host = gateway_module.GatewayHost(settings, runtime=runtime)
        queue = await host.proactive_events.subscribe()
        recipient = messages_module.Recipient(settings.gateway.principal_id, "web", "proactive")

        async def next_notice() -> dict[str, object]:
            for _ in range(4):
                payload = await asyncio.wait_for(queue.get(), timeout=1)
                if payload["type"] == "notice":
                    return payload
            raise AssertionError("未收到主动通知")

        try:
            for event_type, expected, expected_title in (
                ("proactive.breakbeat.completed", "info", "Breakbeat 已完成"),
                ("proactive.breakbeat.partial", "warning", "Breakbeat 部分完成"),
                ("proactive.breakbeat.failed", "error", "Breakbeat 执行失败"),
            ):
                await host._publish_web_notice(
                    messages_module.OutboundMessage(
                        event_type,
                        "proactive",
                        recipient=recipient,
                        content="Breakbeat result",
                        event_type=event_type,
                        disposition="proactive_notification",
                    )
                )
                notice = await next_notice()
                assert notice["severity"] == expected
                assert notice["title"] == expected_title
        finally:
            await host.proactive_events.unsubscribe(queue)

    asyncio.run(run())

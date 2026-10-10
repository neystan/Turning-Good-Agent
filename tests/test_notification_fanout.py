from __future__ import annotations

import asyncio
import importlib


messages = importlib.import_module("Turning-Good-Agent.bus.messages")
notifications = importlib.import_module("Turning-Good-Agent.proactive.notifications")

ChannelRoute = messages.ChannelRoute
Recipient = messages.Recipient
NotificationFanout = notifications.NotificationFanout
NotificationSubscription = notifications.NotificationSubscription
ProactiveResultEvent = notifications.ProactiveResultEvent


def test_all_subscribed_fanout_publishes_each_online_recipient_once_and_skips_unknown_channels() -> None:
    async def run() -> None:
        web = Recipient("stan", "web", "browser-a")
        online_cli = Recipient("stan", "cli", "terminal-a")
        offline_cli = Recipient("stan", "cli", "terminal-b")
        feishu = Recipient("stan", "feishu", "chat-a")
        subscriptions = [
            NotificationSubscription(web, enabled=True),
            NotificationSubscription(web, enabled=True),
            NotificationSubscription(online_cli, enabled=True),
            NotificationSubscription(offline_cli, enabled=True),
            NotificationSubscription(feishu, enabled=True),
        ]
        online = {
            web: True,
            online_cli: True,
            offline_cli: False,
            feishu: False,
        }
        sent = []
        fanout = NotificationFanout(
            subscriptions=lambda _: subscriptions,
            classify_recipient=lambda recipient: online[recipient],
        )
        event = ProactiveResultEvent(
            id="event-1",
            event_type="proactive.cron.completed",
            content="任务完成",
            source_id="cron-1",
            notification_scope="all_subscribed",
            principal_id="stan",
            origin_route=ChannelRoute("stan", "web", "browser-a", "web-session-a"),
        )

        delivered = await fanout.dispatch(event, publish_outbound=lambda message: _append(sent, message))

        assert [(message.recipient, message.disposition) for message in delivered] == [
            (web, "proactive_notification"),
            (online_cli, "proactive_notification"),
        ]
        assert sent == list(delivered)

    asyncio.run(run())


def test_origin_scope_emits_only_the_origin_even_when_other_channels_subscribe() -> None:
    async def run() -> None:
        origin = ChannelRoute("stan", "cli", "terminal-b", "cli-session-b")
        other = Recipient("stan", "web", "browser-b")
        sent = []
        fanout = NotificationFanout(
            subscriptions=lambda _: [NotificationSubscription(other, enabled=True)],
            classify_recipient=lambda _: True,
        )
        event = ProactiveResultEvent(
            id="event-2",
            event_type="proactive.skill_evolution.completed",
            content="演进完成",
            source_id="skill-1",
            notification_scope="origin",
            principal_id="stan",
            origin_route=origin,
        )

        delivered = await fanout.dispatch(event, publish_outbound=lambda message: _append(sent, message))

        assert [message.recipient for message in delivered] == [
            Recipient("stan", "cli", "terminal-b")
        ]
        assert sent == list(delivered)

    asyncio.run(run())


def test_gateway_notification_publisher_resolves_cli_to_the_current_live_session(tmp_path) -> None:
    """Would fail if a switched or offline CLI received a stored/replayed notification."""

    async def run() -> None:
        current = ChannelRoute("stan", "cli", "planning", "cli-planning")
        sent = []
        fanout = NotificationFanout(
            subscriptions=lambda principal: [
                NotificationSubscription(Recipient(principal, "web", "proactive"), enabled=True),
                NotificationSubscription(Recipient(principal, "cli", current.conversation_id), enabled=True),
            ],
            classify_recipient=lambda recipient: recipient.channel in {"web", "cli"},
        )
        publisher = notifications.GatewayNotificationPublisher(
            fanout=fanout,
            publish_outbound=lambda message: _append(sent, message),
            active_cli_route=lambda _: current,
        )
        stale_origin = ChannelRoute("stan", "cli", "research", "cli-research")
        event = ProactiveResultEvent(
            id="event-origin",
            event_type="proactive.skill_evolution.completed",
            content="完成",
            source_id="skill-1",
            notification_scope="origin",
            principal_id="stan",
            origin_route=stale_origin,
        )

        delivered = await publisher.publish(event)

        assert [message.recipient for message in delivered] == [
            Recipient("stan", "cli", "planning")
        ]
        assert [message.recipient for message in sent] == [Recipient("stan", "cli", "planning")]

    asyncio.run(run())


async def _append(items, value) -> None:
    items.append(value)

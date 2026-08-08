from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, replace
from typing import Literal, TypeVar
from uuid import uuid4

from ..bus.messages import ChannelRoute, OutboundMessage, Recipient

NotificationScope = Literal["origin", "all_subscribed"]
_Value = TypeVar("_Value")


@dataclass(frozen=True, slots=True)
class ProactiveResultEvent:
    """一个已经完成的主动任务结果，尚未展开为 Channel 投递。"""

    id: str
    event_type: str
    content: str
    source_id: str
    notification_scope: NotificationScope
    principal_id: str
    origin_route: ChannelRoute | None = None

    def __post_init__(self) -> None:
        """拒绝没有明确通知范围或主体的结果事件。"""
        if self.notification_scope not in {"origin", "all_subscribed"}:
            raise ValueError("不支持的主动通知范围")
        if not self.principal_id:
            raise ValueError("主动通知必须包含 principal_id")
        if self.notification_scope == "origin" and self.origin_route is None:
            raise ValueError("origin 通知必须包含来源路由")
        if self.origin_route is not None and self.origin_route.principal_id != self.principal_id:
            raise ValueError("来源路由必须属于事件 principal_id")

    @property
    def session_id(self) -> str:
        """为出站消息保留来源会话；系统事件使用独立非聊天键。"""
        return self.origin_route.session_id if self.origin_route is not None else "proactive"


@dataclass(frozen=True, slots=True)
class NotificationSubscription:
    """一个经过授权的主动通知收件目标。"""

    recipient: Recipient
    enabled: bool


SubscriptionLookup = Callable[[str], Iterable[NotificationSubscription] | Awaitable[Iterable[NotificationSubscription]]]
RecipientOnline = Callable[[Recipient], bool | Awaitable[bool]]
OutboundPublisher = Callable[[OutboundMessage], Awaitable[object]]
ActiveCliRoute = Callable[[str], ChannelRoute | None | Awaitable[ChannelRoute | None]]


class NotificationFanout:
    """把一个主动结果展开为明确收件人的出站消息，不负责执行任务。"""

    def __init__(
        self,
        *,
        subscriptions: SubscriptionLookup,
        classify_recipient: RecipientOnline,
    ) -> None:
        self._subscriptions = subscriptions
        self._classify_recipient = classify_recipient

    async def dispatch(
        self,
        event: ProactiveResultEvent,
        *,
        publish_outbound: OutboundPublisher,
    ) -> tuple[OutboundMessage, ...]:
        """只将当前在线收件人的消息写入共享出站 Bus。"""
        delivered: list[OutboundMessage] = []
        for recipient in await self._recipients_for(event):
            if not await _resolve(self._classify_recipient(recipient)):
                continue
            message = self._message_for(event, recipient)
            await publish_outbound(message)
            delivered.append(message)
        return tuple(delivered)

    async def _recipients_for(self, event: ProactiveResultEvent) -> tuple[Recipient, ...]:
        if event.notification_scope == "origin":
            assert event.origin_route is not None
            return (
                Recipient(
                    event.origin_route.principal_id,
                    event.origin_route.channel,
                    event.origin_route.conversation_id,
                ),
            )
        subscriptions = await _resolve(self._subscriptions(event.principal_id))
        seen: set[tuple[str, str, str]] = set()
        recipients: list[Recipient] = []
        for subscription in subscriptions:
            if not subscription.enabled:
                continue
            recipient = subscription.recipient
            if recipient.principal_id != event.principal_id:
                continue
            key = (recipient.principal_id, recipient.channel, recipient.conversation_id)
            if key not in seen:
                seen.add(key)
                recipients.append(recipient)
        return tuple(recipients)

    @staticmethod
    def _message_for(event: ProactiveResultEvent, recipient: Recipient) -> OutboundMessage:
        return OutboundMessage(
            id=f"{event.id}:{recipient.channel}:{recipient.conversation_id}",
            event_id=event.id,
            session_id=event.session_id,
            recipient=recipient,
            content=event.content,
            event_type=event.event_type,
            disposition="proactive_notification",
            metadata={"proactive_source_id": event.source_id},
        )


class GatewayNotificationPublisher:
    """集中执行主动结果的动态在线 CLI 与 Web 投递策略。"""

    def __init__(
        self,
        *,
        fanout: NotificationFanout,
        publish_outbound: OutboundPublisher,
        active_cli_route: ActiveCliRoute,
        principal_id: str | None = None,
    ) -> None:
        self._fanout = fanout
        self._publish_outbound = publish_outbound
        self._active_cli_route = active_cli_route
        self._principal_id = principal_id

    async def publish(self, event: ProactiveResultEvent) -> tuple[OutboundMessage, ...]:
        """按结果产生时的实际在线 CLI 路由展开并发送一次。"""
        resolved_event = await self._resolve_origin(event)
        if resolved_event is None:
            return ()
        return await self._fanout.dispatch(
            resolved_event,
            publish_outbound=self._publish_outbound,
        )

    async def enqueue(
        self,
        *,
        event_type: str,
        content: str,
        source_id: str,
        target_channel: str | None = None,
        notification_scope: NotificationScope = "all_subscribed",
        origin_route: ChannelRoute | None = None,
        principal_id: str | None = None,
    ) -> tuple[OutboundMessage, ...]:
        """兼容领域 Manager 的轻量 enqueue 调用，并归一化为结果事件。"""
        del target_channel
        selected_principal = principal_id or (
            origin_route.principal_id if origin_route is not None else self._principal_id
        )
        if not selected_principal:
            raise ValueError("主动通知缺少 principal_id")
        return await self.publish(
            ProactiveResultEvent(
                id=str(uuid4()),
                event_type=event_type,
                content=content,
                source_id=source_id,
                notification_scope=notification_scope,
                principal_id=selected_principal,
                origin_route=origin_route,
            )
        )

    async def _resolve_origin(self, event: ProactiveResultEvent) -> ProactiveResultEvent | None:
        if event.notification_scope != "origin" or event.origin_route is None:
            return event
        route = event.origin_route
        if route.channel == "cli":
            active_route = await _resolve(self._active_cli_route(event.principal_id))
            if active_route is None:
                return None
            if active_route.principal_id != event.principal_id:
                return None
            return replace(event, origin_route=active_route)
        if route.channel == "web":
            return replace(
                event,
                origin_route=ChannelRoute(event.principal_id, "web", "proactive", "proactive"),
            )
        return event


async def _resolve(value: _Value | Awaitable[_Value]) -> _Value:
    """同时允许 Gateway 注入同步快照或异步 Channel 查询。"""
    if inspect.isawaitable(value):
        return await value
    return value

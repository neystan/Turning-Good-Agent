from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def utc_now_iso() -> str:
    """返回 UTC ISO 时间字符串。"""
    return datetime.now(UTC).isoformat()


@dataclass(frozen=True, slots=True)
class ChannelRoute:
    """标识一条聊天会话所属的 Channel 路由。"""

    principal_id: str
    channel: str
    conversation_id: str
    session_id: str


@dataclass(frozen=True, slots=True)
class Recipient:
    """标识一条出站消息的目标会话。"""

    principal_id: str
    channel: str
    conversation_id: str


@dataclass(slots=True, init=False)
class InboundMessage:
    """表示来自任意 Channel 的用户输入。"""

    id: str
    session_id: str
    route: ChannelRoute
    content: str
    attachments: list[dict[str, Any]]
    metadata: dict[str, Any]
    created_at: str

    def __init__(
        self,
        id: str,
        session_id: str | None = None,
        user_id: str | None = None,
        channel: str | None = None,
        content: str = "",
        attachments: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: str | None = None,
        *,
        route: ChannelRoute | None = None,
    ) -> None:
        """兼容旧字段构造，同时在边界处归一化为 ChannelRoute。"""
        if route is None:
            if session_id is None or user_id is None or channel is None:
                raise TypeError("route 或 session_id、user_id、channel 均为必填")
            route = ChannelRoute(user_id, channel, session_id, session_id)
        else:
            if session_id is not None and session_id != route.session_id:
                raise ValueError("session_id 必须与 route 一致")
            if user_id is not None and user_id != route.principal_id:
                raise ValueError("user_id 必须与 route 一致")
            if channel is not None and channel != route.channel:
                raise ValueError("channel 必须与 route 一致")
            session_id = route.session_id
        self.id = id
        self.session_id = session_id
        self.route = route
        self.content = content
        self.attachments = list(attachments or [])
        self.metadata = dict(metadata or {})
        self.created_at = created_at or utc_now_iso()

    @property
    def user_id(self) -> str:
        """旧调用方使用的 principal_id 别名。"""
        return self.route.principal_id

    @property
    def channel(self) -> str:
        """旧调用方使用的 Channel 别名。"""
        return self.route.channel

    @classmethod
    def new(
        cls,
        content: str,
        session_id: str | ChannelRoute | None = None,
        user_id: str | None = None,
        channel: str | None = None,
        *,
        route: ChannelRoute | None = None,
    ) -> "InboundMessage":
        """创建一条带默认 ID 和时间的入站消息。"""
        if isinstance(session_id, ChannelRoute):
            if route is not None and route != session_id:
                raise ValueError("route 参数不一致")
            route = session_id
            session_id = None
        return cls(
            id=str(uuid4()),
            session_id=session_id,
            user_id=user_id,
            channel=channel,
            content=content,
            route=route,
        )


@dataclass(slots=True, init=False)
class OutboundMessage:
    """表示 Runtime 返回给 Channel 的输出。"""

    id: str
    session_id: str
    recipient: Recipient
    content: str
    event_type: str
    event_id: str
    disposition: str
    metadata: dict[str, Any]
    created_at: str

    def __init__(
        self,
        id: str,
        session_id: str,
        target_channel: str | Recipient | None = None,
        content: str = "",
        event_type: str = "response.completed",
        metadata: dict[str, Any] | None = None,
        created_at: str | None = None,
        *,
        recipient: Recipient | None = None,
        event_id: str | None = None,
        disposition: str = "chat_reply",
    ) -> None:
        """兼容旧 target_channel 构造，同时提供明确的 Recipient。"""
        if isinstance(target_channel, Recipient):
            if recipient is not None and recipient != target_channel:
                raise ValueError("recipient 参数不一致")
            recipient = target_channel
            target_channel = None
        if recipient is None:
            if target_channel is None:
                raise TypeError("recipient 或 target_channel 为必填")
            recipient = Recipient("legacy", target_channel, session_id)
        elif target_channel is not None and target_channel != recipient.channel:
            raise ValueError("target_channel 必须与 recipient 一致")
        if disposition not in {"chat_reply", "proactive_notification"}:
            raise ValueError("不支持的 disposition")
        self.id = id
        self.session_id = session_id
        self.recipient = recipient
        self.content = content
        self.event_type = event_type
        self.event_id = event_id or id
        self.disposition = disposition
        self.metadata = dict(metadata or {})
        self.created_at = created_at or utc_now_iso()

    @property
    def target_channel(self) -> str:
        """旧调用方使用的 recipient.channel 别名。"""
        return self.recipient.channel

    @classmethod
    def new(
        cls,
        session_id: str,
        target_channel: str | Recipient,
        content: str,
        *,
        event_id: str | None = None,
        disposition: str = "chat_reply",
    ) -> "OutboundMessage":
        """创建一条出站消息。"""
        return cls(
            id=str(uuid4()),
            session_id=session_id,
            target_channel=target_channel,
            content=content,
            event_id=event_id,
            disposition=disposition,
        )

    @classmethod
    def started(cls, session_id: str, target_channel: str | Recipient) -> "OutboundMessage":
        """创建响应开始事件。"""
        return cls(
            id=str(uuid4()),
            session_id=session_id,
            target_channel=target_channel,
            content="",
            event_type="response.started",
        )

    @classmethod
    def delta(
        cls, session_id: str, target_channel: str | Recipient, content: str
    ) -> "OutboundMessage":
        """创建响应增量事件。"""
        return cls(
            id=str(uuid4()),
            session_id=session_id,
            target_channel=target_channel,
            content=content,
            event_type="response.delta",
        )

    @classmethod
    def completed(
        cls,
        session_id: str,
        target_channel: str | Recipient,
        content: str,
        *,
        event_id: str | None = None,
        disposition: str = "chat_reply",
    ) -> "OutboundMessage":
        """创建响应完成事件。"""
        return cls.new(
            session_id,
            target_channel,
            content,
            event_id=event_id,
            disposition=disposition,
        )

    @classmethod
    def error(
        cls, session_id: str, target_channel: str | Recipient, content: str
    ) -> "OutboundMessage":
        """创建响应错误事件。"""
        return cls(
            id=str(uuid4()),
            session_id=session_id,
            target_channel=target_channel,
            content=content,
            event_type="response.error",
        )

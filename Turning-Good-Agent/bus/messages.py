from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def utc_now_iso() -> str:
    """返回 UTC ISO 时间字符串。"""
    return datetime.now(UTC).isoformat()


@dataclass(slots=True)
class InboundMessage:
    """表示来自任意 Channel 的用户输入。"""

    id: str
    session_id: str
    user_id: str
    channel: str
    content: str
    attachments: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)

    @classmethod
    def new(cls, content: str, session_id: str, user_id: str, channel: str) -> "InboundMessage":
        """创建一条带默认 ID 和时间的入站消息。"""
        return cls(
            id=str(uuid4()),
            session_id=session_id,
            user_id=user_id,
            channel=channel,
            content=content,
        )


@dataclass(slots=True)
class OutboundMessage:
    """表示 Runtime 返回给 Channel 的输出。"""

    id: str
    session_id: str
    target_channel: str
    content: str
    event_type: str = "response.completed"
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=utc_now_iso)

    @classmethod
    def new(cls, session_id: str, target_channel: str, content: str) -> "OutboundMessage":
        """创建一条出站消息。"""
        return cls(id=str(uuid4()), session_id=session_id, target_channel=target_channel, content=content)

    @classmethod
    def started(cls, session_id: str, target_channel: str) -> "OutboundMessage":
        """创建响应开始事件。"""
        return cls(
            id=str(uuid4()),
            session_id=session_id,
            target_channel=target_channel,
            content="",
            event_type="response.started",
        )

    @classmethod
    def delta(cls, session_id: str, target_channel: str, content: str) -> "OutboundMessage":
        """创建响应增量事件。"""
        return cls(
            id=str(uuid4()),
            session_id=session_id,
            target_channel=target_channel,
            content=content,
            event_type="response.delta",
        )

    @classmethod
    def completed(cls, session_id: str, target_channel: str, content: str) -> "OutboundMessage":
        """创建响应完成事件。"""
        return cls.new(session_id, target_channel, content)

    @classmethod
    def error(cls, session_id: str, target_channel: str, content: str) -> "OutboundMessage":
        """创建响应错误事件。"""
        return cls(
            id=str(uuid4()),
            session_id=session_id,
            target_channel=target_channel,
            content=content,
            event_type="response.error",
        )

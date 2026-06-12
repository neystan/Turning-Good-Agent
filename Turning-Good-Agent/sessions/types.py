from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class Session:
    """表示一段可恢复的对话会话。"""

    id: str
    user_id: str
    channel: str
    title: str
    summary: str
    created_at: str
    updated_at: str
    metadata: dict[str, Any]


@dataclass(slots=True)
class MessageRecord:
    """表示一条已持久化的对话消息。"""

    id: str
    session_id: str
    role: str
    content: str
    name: str | None
    tool_call_id: str | None
    token_count: int
    created_at: str
    metadata: dict[str, Any]

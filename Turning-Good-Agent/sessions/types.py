from dataclasses import dataclass
from typing import Any


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


@dataclass(slots=True)
class Session:
    """表示一段可恢复的对话会话。"""

    id: str
    user_id: str
    channel: str
    title: str
    summary: str
    uncompacted_history: list[MessageRecord]
    created_at: str
    updated_at: str


@dataclass(slots=True)
class ToolCallRecord:
    """表示一条已持久化的工具调用记录。"""

    turn_id: str
    tool_call_id: str
    tool_name: str
    args: dict[str, Any]
    content: str
    error: str | None
    duration_ms: float
    created_at: str

from dataclasses import dataclass
from typing import Any

from ..sessions.token_counter import count_content_tokens


@dataclass(slots=True)
class ContextAttachment:
    """表示仅对当前 AgentLoop 可见的工具上下文。"""

    source: str
    messages: list[dict[str, object]]
    token_count: int
    kind: str = "mcp"
    verified: bool = False


def validate_context_attachment(
    attachment: ContextAttachment | object | None,
    used_tokens: int,
    token_limit: int,
) -> str | None:
    """校验本轮上下文附件的格式和总预算。"""
    if attachment is None:
        return None
    if not isinstance(attachment, ContextAttachment):
        return "本轮上下文附件格式无效"
    if not isinstance(attachment.source, str) or not isinstance(attachment.messages, list):
        return "本轮上下文附件格式无效"
    if attachment.kind not in {"mcp", "skill"}:
        return "本轮上下文附件类型无效"
    if not isinstance(attachment.token_count, int) or attachment.token_count < 0:
        return "本轮上下文附件格式无效"
    allowed_roles = {"user", "assistant"}
    if attachment.kind == "skill" and attachment.verified and attachment.source.startswith("skill:"):
        allowed_roles.add("system")
    if any(
        not isinstance(message, dict)
        or message.get("role") not in allowed_roles
        or not isinstance(message.get("content"), str)
        for message in attachment.messages
    ):
        return "本轮上下文附件只允许 user 或 assistant 文本消息"
    actual_tokens = sum(count_content_tokens(message["content"]) for message in attachment.messages)
    if actual_tokens != attachment.token_count:
        return "本轮上下文附件 token 计数不一致"
    if used_tokens + attachment.token_count > token_limit:
        return f"本轮上下文附件总量超过 {token_limit} tokens 限制"
    return None

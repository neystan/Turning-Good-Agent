from typing import Any

from .system_prompt import SYSTEM_PROMPT
from ..sessions.types import MessageRecord


class ContextBuilder:
    """组装模型调用所需的消息列表。"""

    def build(
        self,
        summary: str,
        history: list[MessageRecord],
        user_content: str,
        profile_memory: str,
    ) -> list[dict[str, Any]]:
        """构建 system、摘要、历史和当前用户消息。"""
        messages: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}]
        if profile_memory:
            messages.append({"role": "system", "content": f"长期偏好：{profile_memory}"})
        if summary:
            messages.append({"role": "system", "content": f"会话摘要：{summary}"})
        for item in history:
            messages.append({"role": item.role, "content": item.content})
        messages.append({"role": "user", "content": user_content})
        return messages

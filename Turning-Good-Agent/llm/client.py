from typing import Any, Protocol

from .types import LLMResponse


class LLMProvider(Protocol):
    """定义 Runtime 需要的最小模型接口。"""

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        """根据消息和工具 schema 生成回复。"""

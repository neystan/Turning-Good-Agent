from collections.abc import AsyncIterator
from typing import Any, Protocol

from .types import LLMChunk, LLMResponse


class LLMProvider(Protocol):
    """定义 Runtime 需要的最小模型接口。"""

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        """根据消息和工具 schema 生成回复。"""

    def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[LLMChunk]:
        """根据消息和工具 schema 流式生成回复。"""

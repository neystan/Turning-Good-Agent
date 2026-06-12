from typing import Any
from uuid import uuid4

from .types import LLMResponse, ToolCall


class FakeLLM:
    """提供无需 API key 的本地开发模型。"""

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        """根据最后一条消息生成确定性回复。"""
        if not messages:
            return LLMResponse("收到。")

        last = messages[-1]
        content = str(last.get("content", ""))
        if last.get("role") == "tool":
            return LLMResponse(f"工具结果：{content}")
        if content.startswith("echo:"):
            text = content.split("echo:", 1)[1].strip()
            return LLMResponse("", [ToolCall(str(uuid4()), "echo", {"text": text})])
        if "time" in content.lower():
            return LLMResponse("", [ToolCall(str(uuid4()), "now", {})])
        return LLMResponse(f"收到：{content}")

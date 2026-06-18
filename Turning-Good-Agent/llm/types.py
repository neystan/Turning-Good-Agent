from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ToolCall:
    """表示模型请求的一次工具调用。"""

    id: str
    name: str
    args: dict[str, Any]


@dataclass(slots=True)
class LLMUsage:
    """表示一次或多次模型调用的真实 token 用量。"""

    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def add(self, other: "LLMUsage | None") -> "LLMUsage":
        """合并另一段模型用量。"""
        if other is None:
            return self
        return LLMUsage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


@dataclass(slots=True)
class LLMResponse:
    """表示模型返回的文本和工具调用。"""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: LLMUsage | None = None


@dataclass(slots=True)
class LLMChunk:
    """表示模型流式返回的增量。"""

    delta_text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: LLMUsage | None = None

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ToolCall:
    """表示模型请求的一次工具调用。"""

    id: str
    name: str
    args: dict[str, Any]


@dataclass(slots=True)
class LLMResponse:
    """表示模型返回的文本和工具调用。"""

    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)


@dataclass(slots=True)
class LLMChunk:
    """表示模型流式返回的增量。"""

    delta_text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None

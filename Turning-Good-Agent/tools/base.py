from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(slots=True)
class ToolResult:
    """保存工具执行结果。"""

    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseTool(Protocol):
    """定义工具必须提供的最小接口。"""

    name: str
    description: str
    input_schema: dict[str, Any]

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """执行工具并返回文本结果。"""

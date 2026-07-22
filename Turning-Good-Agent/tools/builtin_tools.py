from datetime import datetime
from typing import Any

from .base import ToolResult


class EchoTool:
    """回显输入文本。"""

    name = "echo"
    source = "builtin"
    discoverable = True
    parallel_safe = True
    description = "回显输入文本。"
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "回显文本"}},
        "required": ["text"],
    }

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """返回 text 参数。"""
        return ToolResult(str(args.get("text", "")))


class NowTool:
    """返回当前本地时间。"""

    name = "now"
    source = "builtin"
    discoverable = True
    parallel_safe = True
    description = "返回当前本地时间。"
    input_schema = {"type": "object", "properties": {}}

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """返回 ISO 格式时间。"""
        return ToolResult(datetime.now().isoformat(timespec="seconds"))

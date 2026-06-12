import time
from typing import Any

from .registry import ToolRegistry


class ToolExecutor:
    """执行工具并记录耗时。"""

    def __init__(self, registry: ToolRegistry) -> None:
        self.registry = registry

    async def run(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """执行单个工具，异常转为错误结果。"""
        started = time.perf_counter()
        try:
            result = await self.registry.get(tool_name).run(args)
            content = result.content
            error = None
        except Exception as exc:
            error = str(exc)
            content = f"工具 {tool_name} 执行失败：{error}"
        return {
            "tool_name": tool_name,
            "args": args,
            "content": content,
            "duration_ms": (time.perf_counter() - started) * 1000,
            "error": error,
        }

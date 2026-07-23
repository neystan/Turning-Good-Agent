import time
from pathlib import Path
from typing import Any

from . import security
from .base import BaseTool


class ToolExecutor:
    """执行工具并记录耗时。"""

    def precheck(self, tool: BaseTool, args: dict[str, Any]) -> str | None:
        """在策略判断或执行前检查硬安全规则。"""
        workspace = Path(getattr(tool, "workspace", Path.cwd())).resolve()
        return security.validate_tool_call(tool.name, args, workspace)

    async def run(self, tool: BaseTool, args: dict[str, Any]) -> dict[str, Any]:
        """执行已准备工具并再次检查硬安全规则。"""
        started = time.perf_counter()
        error = None
        context_attachment = None
        try:
            security_error = self.precheck(tool, args)
            if security_error:
                error = security_error
                content = f"工具 {tool.name} 安全检查失败：{security_error}"
            else:
                result = await tool.run(args)
                content = result.content if hasattr(result, "content") else str(result)
                context_attachment = getattr(result, "context_attachment", None)
        except Exception as exc:
            error = str(exc)
            content = f"工具 {tool.name} 执行失败：{error}"
        return {
            "tool_name": tool.name,
            "args": args,
            "content": content,
            "duration_ms": (time.perf_counter() - started) * 1000,
            "error": error,
            "context_attachment": context_attachment,
        }

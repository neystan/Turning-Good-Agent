from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..tools.base import ToolResult
from .types import McpCapability

if TYPE_CHECKING:
    from .manager import McpManager


class McpToolAdapter:
    """将一个远端 MCP Tool 适配为本地 BaseTool。"""

    parallel_safe = False
    approval_required = True

    def __init__(self, manager: McpManager, capability: McpCapability) -> None:
        """保存远端名称、schema 与展示元数据。"""
        self.manager = manager
        self.server_name = capability.server_name
        self.remote_name = capability.name
        self.name = f"mcp_{self.server_name}_{self.remote_name}"
        self.description = capability.description or f"MCP {self.server_name} 工具：{self.remote_name}"
        self.input_schema = dict(capability.metadata.get("input_schema", {"type": "object", "properties": {}}))
        self.metadata = {"annotations": dict(capability.metadata.get("annotations", {}))}

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """调用远端原始 Tool 名称。"""
        content = await self.manager.call_tool(self.server_name, self.remote_name, args)
        return ToolResult(content=content)

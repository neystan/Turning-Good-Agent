import json
from typing import Any

from ..tools.base import ToolResult
from .manager import McpManager


class SearchMcpCapabilitiesTool:
    """搜索已发现的 MCP 能力目录。"""

    name = "search_mcp_capabilities"
    description = "搜索已连接 MCP Server 的工具、资源和提示词描述。"
    parallel_safe = True
    approval_required = False
    input_schema = {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词"},
            "kinds": {"type": "array", "items": {"type": "string"}, "description": "能力类型过滤"},
            "limit": {"type": "integer", "minimum": 1, "maximum": 10, "description": "返回数量上限"},
        },
        "required": ["query"],
    }

    def __init__(self, manager: McpManager) -> None:
        """保存 MCP Catalog 管理器。"""
        self.manager = manager

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """返回内存 Catalog 的少量元数据。"""
        capabilities = await self.manager.search_capabilities(
            str(args["query"]), list(args.get("kinds", [])), int(args.get("limit", 5))
        )
        payload = [
            {
                "server_name": item.server_name,
                "kind": item.kind,
                "name": item.name,
                "description": item.description,
                "metadata": item.metadata,
            }
            for item in capabilities
        ]
        return ToolResult(json.dumps(payload, ensure_ascii=False))


class AttachMcpResourceTool:
    """读取获批 MCP Resource 并作为本轮附件返回。"""

    name = "attach_mcp_resource"
    description = "读取 MCP Resource 并仅附加到当前工具调用轮。"
    parallel_safe = False
    approval_required = True
    input_schema = {
        "type": "object",
        "properties": {
            "server_name": {"type": "string", "description": "MCP Server 名称"},
            "uri": {"type": "string", "description": "Resource URI"},
            "template_arguments": {"type": "object", "description": "Resource 模板参数"},
        },
        "required": ["server_name", "uri"],
    }

    def __init__(self, manager: McpManager) -> None:
        """保存 MCP Catalog 管理器。"""
        self.manager = manager

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """读取 Resource 并返回当前轮附件。"""
        attachment = await self.manager.attach_resource(
            str(args["server_name"]), str(args["uri"]), dict(args.get("template_arguments", {}))
        )
        return ToolResult("MCP Resource 已附加到当前轮上下文。", context_attachment=attachment)


class ApplyMcpPromptTool:
    """读取获批 MCP Prompt 并作为本轮附件返回。"""

    name = "apply_mcp_prompt"
    description = "读取 MCP Prompt 并仅附加到当前工具调用轮。"
    parallel_safe = False
    approval_required = True
    input_schema = {
        "type": "object",
        "properties": {
            "server_name": {"type": "string", "description": "MCP Server 名称"},
            "prompt_name": {"type": "string", "description": "Prompt 名称"},
            "arguments": {"type": "object", "description": "Prompt 参数"},
        },
        "required": ["server_name", "prompt_name"],
    }

    def __init__(self, manager: McpManager) -> None:
        """保存 MCP Catalog 管理器。"""
        self.manager = manager

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """读取 Prompt 并返回当前轮附件。"""
        attachment = await self.manager.apply_prompt(
            str(args["server_name"]), str(args["prompt_name"]), dict(args.get("arguments", {}))
        )
        return ToolResult("MCP Prompt 已附加到当前轮上下文。", context_attachment=attachment)


def register_mcp_control_tools(manager: McpManager, registry: Any) -> None:
    """注册固定的三个 MCP 控制 Tool。"""
    registry.register(SearchMcpCapabilitiesTool(manager))
    registry.register(AttachMcpResourceTool(manager))
    registry.register(ApplyMcpPromptTool(manager))

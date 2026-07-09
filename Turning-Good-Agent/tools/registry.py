from typing import Any

from .base import BaseTool, cast_args, validate_args


class ToolRegistry:
    """保存可被 AgentLoop 调用的工具。"""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}
        self._schemas_cache: list[dict[str, object]] | None = None
        self._openai_tools_cache: list[dict[str, object]] | None = None

    def register(self, tool: BaseTool) -> None:
        """注册一个工具。"""
        self._tools[tool.name] = tool
        self._schemas_cache = None
        self._openai_tools_cache = None

    def has(self, name: str) -> bool:
        """判断工具是否已注册。"""
        return name in self._tools

    def get(self, name: str) -> BaseTool:
        """按名称读取工具。"""
        return self._tools[name]

    @property
    def tool_names(self) -> list[str]:
        """返回已注册工具名。"""
        return sorted(self._tools)

    def _sorted_tools(self) -> list[BaseTool]:
        """按来源和名称稳定排序工具。"""
        return sorted(
            self._tools.values(),
            key=lambda tool: (1 if tool.name.startswith("mcp_") else 0, tool.name),
        )

    def prepare_call(self, name: str, args: Any) -> tuple[BaseTool | None, dict[str, Any], str | None]:
        """查找工具、归一化参数并返回错误文本。"""
        if not isinstance(args, dict):
            return None, {}, f"工具 {name} 参数必须是 object，实际是 {type(args).__name__}"
        tool = self._tools.get(name)
        if tool is None:
            return None, args, f"未知工具：{name}。可用工具：{', '.join(self.tool_names)}"

        schema = tool.input_schema or {"type": "object", "properties": {}}
        normalized_args = cast_args(args, schema)
        errors = validate_args(normalized_args, schema)
        if errors:
            return tool, normalized_args, "；".join(errors)
        return tool, normalized_args, None

    def schemas(self) -> list[dict[str, object]]:
        """返回模型可见的工具 schema。"""
        if self._schemas_cache is None:
            self._schemas_cache = [
                {
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                }
                for tool in self._sorted_tools()
            ]
        return self._schemas_cache

    def openai_tools(self) -> list[dict[str, object]]:
        """返回 OpenAI-compatible tool schema。"""
        if self._openai_tools_cache is None:
            self._openai_tools_cache = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": tool.input_schema,
                    },
                }
                for tool in self._sorted_tools()
            ]
        return self._openai_tools_cache

from .base import BaseTool


class ToolRegistry:
    """保存可被 AgentLoop 调用的工具。"""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """注册一个工具。"""
        self._tools[tool.name] = tool

    def get(self, name: str) -> BaseTool:
        """按名称读取工具。"""
        return self._tools[name]

    def schemas(self) -> list[dict[str, object]]:
        """返回模型可见的工具 schema。"""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            for tool in self._tools.values()
        ]

    def openai_tools(self) -> list[dict[str, object]]:
        """返回 OpenAI-compatible tool schema。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.input_schema,
                },
            }
            for tool in self._tools.values()
        ]

import asyncio
from importlib import import_module


McpToolAdapter = import_module("Turning-Good-Agent.mcp.adapter").McpToolAdapter
McpCapability = import_module("Turning-Good-Agent.mcp.types").McpCapability


def test_adapter_keeps_remote_name_and_exposes_wrapped_name():
    class Manager:
        async def call_tool(self, server_name, tool_name, arguments):
            assert (server_name, tool_name, arguments) == ("github", "list_issues", {"state": "open"})
            return "remote result"

    async def run_test():
        adapter = McpToolAdapter(
            Manager(),
            McpCapability(
                "github",
                "tool",
                "list_issues",
                "list issues",
                {"input_schema": {"type": "object", "properties": {}}, "annotations": {"readOnlyHint": True}},
            ),
        )
        return adapter, await adapter.run({"state": "open"})

    adapter, result = asyncio.run(run_test())

    assert adapter.name == "mcp_github_list_issues"
    assert adapter.metadata["annotations"] == {"readOnlyHint": True}
    assert adapter.approval_required is True
    assert result.content == "remote result"

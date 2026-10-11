import asyncio
from importlib import import_module

import pytest


AttachMcpResourceTool = import_module("Turning-Good-Agent.mcp.control_tools").AttachMcpResourceTool
ApplyMcpPromptTool = import_module("Turning-Good-Agent.mcp.control_tools").ApplyMcpPromptTool
SearchMcpCapabilitiesTool = import_module("Turning-Good-Agent.mcp.control_tools").SearchMcpCapabilitiesTool
McpCapability = import_module("Turning-Good-Agent.mcp.types").McpCapability
McpSettings = import_module("Turning-Good-Agent.config.settings").McpSettings


class Manager:
    async def search_capabilities(self, query, kinds, limit):
        return [McpCapability("demo", "resource", "issue", "issue details", {"uri": "issue://1"})]

    async def attach_resource(self, server_name, uri, arguments):
        return "attachment"

    async def apply_prompt(self, server_name, prompt_name, arguments):
        return "prompt attachment"


def test_search_returns_catalog_metadata_without_reading_content():
    async def run_test():
        tool = SearchMcpCapabilitiesTool(Manager())
        return await tool.run({"query": "issue"})

    result = asyncio.run(run_test())

    assert result.content == '[{"server_name": "demo", "kind": "resource", "name": "issue", "description": "issue details", "metadata": {"uri": "issue://1"}}]'
    assert result.context_attachment is None


def test_attach_and_prompt_controls_require_approval_metadata():
    assert AttachMcpResourceTool(Manager()).approval_required is True
    assert ApplyMcpPromptTool(Manager()).approval_required is True
    assert SearchMcpCapabilitiesTool(Manager()).approval_required is False

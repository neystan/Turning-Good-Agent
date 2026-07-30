from __future__ import annotations

import importlib


hooks = importlib.import_module("Turning-Good-Agent.hooks.tool_permission")
registry_module = importlib.import_module("Turning-Good-Agent.tools.registry")


def test_permission_validation_allows_configured_offline_mcp_tool() -> None:
    registry = registry_module.ToolRegistry()

    hooks.validate_tool_permission_tools(
        registry,
        ["mcp_offline_run"],
        allowed_unregistered_names={"mcp_offline_run"},
    )

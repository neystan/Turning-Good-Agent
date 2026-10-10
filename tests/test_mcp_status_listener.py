from __future__ import annotations

import asyncio
import importlib


mcp_module = importlib.import_module("Turning-Good-Agent.mcp.manager")
settings_module = importlib.import_module("Turning-Good-Agent.config.settings")

McpManager = mcp_module.McpManager
McpSettings = settings_module.McpSettings


def test_mcp_manager_notifies_status_listeners_for_terminal_failure() -> None:
    async def run() -> None:
        manager = McpManager(McpSettings())
        states: list[tuple[str, str]] = []

        def listener(status) -> None:
            states.append((status.name, status.state))

        manager.add_status_listener(listener)
        await manager.refresh_server("missing")
        manager.remove_status_listener(listener)

        assert states == [("missing", "failed")]

    asyncio.run(run())

from __future__ import annotations

import importlib
from types import SimpleNamespace


read_models = importlib.import_module("Turning-Good-Agent.web.backend.read_models")


def test_catalogs_expose_only_connected_mcp_tools() -> None:
    runtime = SimpleNamespace(
        skills=SimpleNamespace(
            list_skills=lambda: [SimpleNamespace(name="review", description="审查变更")]
        ),
        mcp=SimpleNamespace(
            statuses={
                "live": SimpleNamespace(name="live", connected=True, state="connected"),
                "offline": SimpleNamespace(name="offline", connected=False, state="failed"),
            }
        ),
        agent_loop=SimpleNamespace(
            tools=SimpleNamespace(
                tool_names=["exec", "mcp_live_search", "mcp_offline_hidden"],
                get=lambda name: SimpleNamespace(description=f"{name} description"),
            )
        ),
        settings=SimpleNamespace(
            tool_permissions=SimpleNamespace(
                approval_required_tools=["exec", "mcp_offline_hidden"], auto_approve_tools=False
            )
        ),
    )

    commands = read_models.build_command_catalog(runtime)["entries"]
    tools = read_models.build_tool_catalog(runtime, "sha256:active")

    assert {entry["id"] for entry in commands} == {
        "inspect.context",
        "inspect.tools",
        "skill.review",
        "mcp.live",
    }
    assert [item["name"] for item in tools["tools"]] == ["exec", "mcp_live_search"]
    assert tools["unavailable_approval_required"] == ["mcp_offline_hidden"]

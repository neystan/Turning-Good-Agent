from __future__ import annotations

import asyncio
import importlib
from datetime import UTC, datetime


builtin_tools = importlib.import_module("Turning-Good-Agent.tools.builtin_tools")
settings_module = importlib.import_module("Turning-Good-Agent.config.settings")

NowTool = builtin_tools.NowTool
Settings = settings_module.Settings


def test_now_tool_returns_configured_iana_time_with_offset() -> None:
    async def run() -> None:
        def fixed_clock(timezone):
            return datetime(2026, 8, 1, 12, 0, tzinfo=UTC).astimezone(timezone)

        result = await NowTool("Asia/Shanghai", clock=fixed_clock).run({})

        assert result.content == "2026-08-01T20:00:00+08:00（Asia/Shanghai）"
        assert result.metadata == {
            "timezone": "Asia/Shanghai",
            "iso_time": "2026-08-01T20:00:00+08:00",
        }

    asyncio.run(run())


def test_now_tool_create_binds_proactive_timezone() -> None:
    settings = Settings()
    settings.proactive.timezone = "America/New_York"

    tool = NowTool.create(settings)

    assert tool.timezone_name == "America/New_York"

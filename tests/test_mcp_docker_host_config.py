from __future__ import annotations

import json
import importlib
from pathlib import Path


settings_module = importlib.import_module("Turning-Good-Agent.config.settings")


def test_streamable_http_mcp_accepts_docker_host_alias(tmp_path: Path) -> None:
    config_path = tmp_path / "settings.local.json"
    config_path.write_text(
        json.dumps(
            {
                "mcp": {
                    "servers": {
                        "modular_rag": {
                            "enabled": True,
                            "transport": "streamable_http",
                            "url": "http://host.docker.internal:8010/mcp",
                            "enabled_tools": ["query_knowledge_hub"],
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    settings = settings_module.Settings.load(local_config_path=config_path)

    assert settings.mcp.servers["modular_rag"].url == "http://host.docker.internal:8010/mcp"

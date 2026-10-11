from importlib import import_module
import json

import pytest


Settings = import_module("Turning-Good-Agent.config.settings").Settings


def test_loads_named_mcp_servers_and_defaults_enabled_tools(tmp_path):
    config_path = tmp_path / "settings.local.json"
    config_path.write_text(
        json.dumps(
            {
                "mcp": {
                    "servers": {
                        "local": {
                            "enabled": True,
                            "transport": "stdio",
                            "command": "python",
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    settings = Settings.load(local_config_path=config_path)

    assert settings.mcp.servers["local"].command == "python"
    assert settings.mcp.servers["local"].enabled_tools == []
    assert settings.mcp.resource_context_token_limit == 8_000


def test_loads_mcp_server_connection_retry_settings(tmp_path):
    """验证每个 MCP Server 都可配置连接重试参数。"""
    config_path = tmp_path / "settings.local.json"
    config_path.write_text(
        json.dumps(
            {
                "mcp": {
                    "servers": {
                        "demo": {
                            "transport": "stdio",
                            "command": "python",
                            "connect_retry_attempts": 3,
                            "connect_retry_delay_seconds": 1,
                            "connect_retry_max_delay_seconds": 8,
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    server = Settings.load(local_config_path=config_path).mcp.servers["demo"]

    assert server.connect_retry_attempts == 3
    assert server.connect_retry_delay_seconds == 1.0
    assert server.connect_retry_max_delay_seconds == 8.0


@pytest.mark.parametrize(
    "server",
    [
        {"transport": "sse", "url": "https://example.com/mcp"},
        {"transport": "streamable_http", "url": "http://example.com/mcp"},
        {"transport": "stdio", "command": "python", "auto_approve_tools": ["*"]},
    ],
)
def test_rejects_unsafe_or_unsupported_mcp_server_config(tmp_path, server):
    config_path = tmp_path / "settings.local.json"
    config_path.write_text(json.dumps({"mcp": {"servers": {"demo": server}}}), encoding="utf-8")

    with pytest.raises(ValueError):
        Settings.load(local_config_path=config_path)


def test_rejects_removed_mcp_server_auto_approval_config(tmp_path):
    """验证 MCP Server 不能配置自动审批。"""
    config_path = tmp_path / "settings.local.json"
    config_path.write_text(
        json.dumps(
            {
                "mcp": {
                    "servers": {
                        "demo": {
                            "transport": "stdio",
                            "command": "python",
                            "auto_approve_tools": ["lookup"],
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=r"mcp\.servers\.demo\.auto_approve_tools 已不支持，请使用 /approve on。"):
        Settings.load(local_config_path=config_path)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("connect_retry_attempts", -1),
        ("connect_retry_delay_seconds", 0),
        ("connect_retry_max_delay_seconds", 0),
    ],
)
def test_rejects_invalid_mcp_server_connection_retry_settings(tmp_path, key, value):
    """验证连接重试参数必须在有效范围内。"""
    config_path = tmp_path / "settings.local.json"
    config_path.write_text(
        json.dumps({"mcp": {"servers": {"demo": {"transport": "stdio", "command": "python", key: value}}}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match=rf"mcp\.servers\.demo\.{key}"):
        Settings.load(local_config_path=config_path)

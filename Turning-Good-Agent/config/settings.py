from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse


@dataclass(slots=True)
class RuntimeSettings:
    """保存 Runtime 执行限制。"""

    max_tool_rounds: int = 5
    max_tool_calls_per_round: int = 8
    parallel_tool_calls_enabled: bool = True
    max_parallel_tool_calls: int = 4
    turn_timeout_seconds: int = 120
    max_context_tokens: int = 300_000
    max_tool_result_tokens: int = 8_000


@dataclass(slots=True)
class MemorySettings:
    """保存短期记忆压缩参数。"""

    compact_token_threshold: int = 200_000
    recent_window_token_limit: int = 20_000


@dataclass(slots=True)
class SessionSettings:
    """保存会话存储和生命周期配置。"""

    retention_days: int = 7


@dataclass(slots=True)
class ToolPermissionSettings:
    """保存审批类工具配置。"""

    approval_required_tools: list[str] = field(
        default_factory=lambda: ["write_file", "edit_file", "exec", "write_stdin"]
    )


@dataclass(slots=True)
class McpServerSettings:
    """保存一个 MCP Server 的本地连接配置。"""

    enabled: bool = False
    transport: str = "stdio"
    command: str | None = None
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None
    url: str | None = None
    headers: dict[str, str] = field(default_factory=dict)
    timeout_seconds: float = 30.0
    enabled_tools: list[str] = field(default_factory=list)


@dataclass(slots=True)
class McpSettings:
    """保存 MCP 的附件限制与 Server 配置。"""

    resource_context_token_limit: int = 8_000
    prompt_context_token_limit: int = 4_000
    attachment_context_token_limit: int = 12_000
    servers: dict[str, McpServerSettings] = field(default_factory=dict)


@dataclass(slots=True)
class LLMSettings:
    """保存 LLM Provider 配置。"""

    provider: str = "openai-compatible"
    api_key: str | None = None
    base_url: str = "https://api.openai.com/v1"
    model: str | None = None
    timeout_seconds: float = 60.0
    max_retries: int = 2
    retry_delay_seconds: float = 0.5
    streaming_enabled: bool = True


@dataclass(slots=True)
class Settings:
    """保存应用运行所需的集中配置。"""

    data_dir: Path = Path(".sessions")
    default_session_id: str = "default"
    user_id: str = "local-user"
    channel: str = "cli"
    runtime: RuntimeSettings = field(default_factory=RuntimeSettings)
    memory: MemorySettings = field(default_factory=MemorySettings)
    sessions: SessionSettings = field(default_factory=SessionSettings)
    tool_permissions: ToolPermissionSettings = field(default_factory=ToolPermissionSettings)
    mcp: McpSettings = field(default_factory=McpSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)

    @classmethod
    def load(
        cls,
        data_dir: Path | None = None,
        default_session_id: str | None = None,
        local_config_path: Path | None = None,
    ) -> "Settings":
        """从本地配置文件加载集中配置。"""
        settings = cls()
        config_path = local_config_path or Path.cwd() / "settings.local.json"
        if config_path.exists():
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            if "data_dir" in payload:
                settings.data_dir = config_path.parent / payload["data_dir"]
            if "user_id" in payload:
                settings.user_id = payload["user_id"]
            if "channel" in payload:
                settings.channel = payload["channel"]
            runtime = payload.get("runtime", {})
            for key in (
                "max_tool_rounds",
                "max_tool_calls_per_round",
                "parallel_tool_calls_enabled",
                "max_parallel_tool_calls",
                "turn_timeout_seconds",
                "max_context_tokens",
                "max_tool_result_tokens",
            ):
                if key in runtime:
                    setattr(settings.runtime, key, runtime[key])
            memory = payload.get("memory", {})
            for key in ("compact_token_threshold", "recent_window_token_limit"):
                if key in memory:
                    setattr(settings.memory, key, memory[key])
            sessions = payload.get("sessions", {})
            for key in ("retention_days",):
                if key in sessions:
                    setattr(settings.sessions, key, sessions[key])
            tool_permissions = payload.get("tool_permissions", {})
            if "approval_required_tools" in tool_permissions:
                settings.tool_permissions.approval_required_tools = tool_permissions["approval_required_tools"]
            settings.mcp = _load_mcp_settings(payload.get("mcp", {}))
            llm = payload.get("llm", {})
            for key in (
                "provider",
                "api_key",
                "base_url",
                "model",
                "timeout_seconds",
                "max_retries",
                "retry_delay_seconds",
                "streaming_enabled",
            ):
                if key in llm:
                    setattr(settings.llm, key, llm[key])
        if data_dir is not None:
            settings.data_dir = data_dir
        if default_session_id is not None:
            settings.default_session_id = default_session_id
        return settings


def _load_mcp_settings(payload: object) -> McpSettings:
    """解析并校验 MCP 本地配置。"""
    if not isinstance(payload, dict):
        raise ValueError("mcp 必须是 object")
    settings = McpSettings()
    for key in (
        "resource_context_token_limit",
        "prompt_context_token_limit",
        "attachment_context_token_limit",
    ):
        if key in payload:
            value = int(payload[key])
            if value <= 0:
                raise ValueError(f"mcp.{key} 必须大于 0")
            setattr(settings, key, value)
    servers = payload.get("servers", {})
    if not isinstance(servers, dict):
        raise ValueError("mcp.servers 必须是 object")
    settings.servers = {str(name): _load_mcp_server(str(name), value) for name, value in servers.items()}
    return settings


def _load_mcp_server(name: str, payload: object) -> McpServerSettings:
    """解析并校验单个 MCP Server。"""
    if not isinstance(payload, dict):
        raise ValueError(f"mcp.servers.{name} 必须是 object")
    if "auto_approve_tools" in payload:
        raise ValueError(f"mcp.servers.{name}.auto_approve_tools 已不支持，请使用 /approve on。")
    server = McpServerSettings(
        enabled=bool(payload.get("enabled", False)),
        transport=str(payload.get("transport", "stdio")),
        command=payload.get("command"),
        args=_string_list(payload.get("args", []), f"mcp.servers.{name}.args"),
        env=_string_mapping(payload.get("env", {}), f"mcp.servers.{name}.env"),
        cwd=payload.get("cwd"),
        url=payload.get("url"),
        headers=_string_mapping(payload.get("headers", {}), f"mcp.servers.{name}.headers"),
        timeout_seconds=float(payload.get("timeout_seconds", 30.0)),
        enabled_tools=_string_list(payload.get("enabled_tools", []), f"mcp.servers.{name}.enabled_tools"),
    )
    if server.transport not in {"stdio", "streamable_http"}:
        raise ValueError(f"mcp.servers.{name}.transport 仅支持 stdio 或 streamable_http")
    if server.timeout_seconds <= 0:
        raise ValueError(f"mcp.servers.{name}.timeout_seconds 必须大于 0")
    if server.transport == "stdio" and not isinstance(server.command, str):
        raise ValueError(f"mcp.servers.{name}.command 不能为空")
    if server.transport == "streamable_http":
        _validate_mcp_url(name, server.url)
    return server


def _validate_mcp_url(name: str, url: str | None) -> None:
    """限制远程 MCP Server 使用 HTTPS。"""
    if not isinstance(url, str):
        raise ValueError(f"mcp.servers.{name}.url 不能为空")
    parsed = urlparse(url)
    local_hosts = {"localhost", "127.0.0.1", "::1"}
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError(f"mcp.servers.{name}.url 必须是 HTTP URL")
    if parsed.scheme != "https" and parsed.hostname not in local_hosts:
        raise ValueError(f"mcp.servers.{name}.url 仅本地地址允许 HTTP")


def _string_list(value: object, label: str) -> list[str]:
    """校验配置中的字符串列表。"""
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} 必须是 string 数组")
    return list(value)


def _string_mapping(value: object, label: str) -> dict[str, str]:
    """校验配置中的字符串映射。"""
    if not isinstance(value, dict) or not all(isinstance(key, str) and isinstance(item, str) for key, item in value.items()):
        raise ValueError(f"{label} 必须是 string 映射")
    return dict(value)

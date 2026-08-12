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
class MultiAgentSettings:
    """保存 Phase 9 多 Agent 委派限制。"""

    enabled: bool = False
    run_timeout_seconds: int = 3_600
    worker_timeout_seconds: int = 900
    max_workers_per_run: int = 8
    max_concurrent_workers_per_run: int = 4
    max_concurrent_workers_global: int = 8
    worker_result_token_limit: int = 60_000
    parent_result_token_limit: int = 120_000


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
    auto_approve_tools: bool = False


@dataclass(slots=True)
class WebSettings:
    """保存本机 Web Host 的运行参数。"""

    host: str = "127.0.0.1"
    port: int = 8000
    max_concurrent_sessions: int = 6
    event_buffer_size: int = 512


@dataclass(slots=True)
class GatewaySettings:
    """保存唯一 Gateway 的本机监听和主体配置。"""

    host: str = "127.0.0.1"
    port: int = 8000
    principal_id: str = "local-user"
    auth_token: str | None = None


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
    connect_retry_attempts: int = 3
    connect_retry_delay_seconds: float = 1.0
    connect_retry_max_delay_seconds: float = 8.0
    enabled_tools: list[str] = field(default_factory=list)


@dataclass(slots=True)
class McpSettings:
    """保存 MCP 的附件限制与 Server 配置。"""

    resource_context_token_limit: int = 8_000
    prompt_context_token_limit: int = 4_000
    attachment_context_token_limit: int = 12_000
    servers: dict[str, McpServerSettings] = field(default_factory=dict)


@dataclass(slots=True)
class SkillsSettings:
    """保存本地 Skill Catalog 和当前轮加载限制。"""

    directory: str = ".skills"
    max_loaded_skills_per_turn: int = 3
    max_skill_tokens: int = 8_000
    max_loaded_skill_tokens_per_turn: int = 16_000


@dataclass(slots=True)
class ProactiveSettings:
    """保存 Phase 7 主动能力的集中配置。"""

    enabled: bool = True
    timezone: str = "Asia/Shanghai"
    review_provider: str | None = None
    review_api_key: str | None = None
    review_base_url: str | None = None
    review_model: str | None = None
    background_max_concurrency: int = 4
    breakbeat_refresh_minutes: int = 60
    dream_refresh_hours: int = 24
    review_window_token_limit: int = 100_000
    profile_total_token_limit: int = 16_000
    user_profile_token_limit: int = 12_000
    soul_profile_token_limit: int = 4_000
    skill_observation_turn_interval: int = 10
    skill_observation_token_limit: int = 160
    skill_evolution_batch_token_limit: int = 100_000
    skill_evolution_batches_per_kind: int = 3


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
    multi_agent: MultiAgentSettings = field(default_factory=MultiAgentSettings)
    memory: MemorySettings = field(default_factory=MemorySettings)
    sessions: SessionSettings = field(default_factory=SessionSettings)
    tool_permissions: ToolPermissionSettings = field(default_factory=ToolPermissionSettings)
    web: WebSettings = field(default_factory=WebSettings)
    gateway: GatewaySettings = field(default_factory=GatewaySettings)
    mcp: McpSettings = field(default_factory=McpSettings)
    skills: SkillsSettings = field(default_factory=SkillsSettings)
    proactive: ProactiveSettings = field(default_factory=ProactiveSettings)
    llm: LLMSettings = field(default_factory=LLMSettings)
    local_config_path: Path | None = field(default=None, repr=False)

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
        settings.local_config_path = config_path
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
            settings.multi_agent = _load_multi_agent_settings(payload.get("multi_agent", {}))
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
            if "auto_approve_tools" in tool_permissions:
                settings.tool_permissions.auto_approve_tools = bool(tool_permissions["auto_approve_tools"])
            settings.web = _load_web_settings(payload.get("web", {}))
            settings.gateway = _load_gateway_settings(payload.get("gateway", {}))
            settings.mcp = _load_mcp_settings(payload.get("mcp", {}))
            settings.skills = _load_skills_settings(payload.get("skills", {}))
            settings.proactive = _load_proactive_settings(payload.get("proactive", {}))
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
        from .validate import validate_settings

        validate_settings(settings)
        return settings

    def update_auto_approve_tools(self, enabled: bool) -> None:
        """更新并持久化全局工具自动审批开关。"""
        self.tool_permissions.auto_approve_tools = enabled
        path = self.local_config_path or Path.cwd() / "settings.local.json"
        payload = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        permissions = payload.setdefault("tool_permissions", {})
        if not isinstance(permissions, dict):
            raise ValueError("tool_permissions 必须是 object")
        permissions["auto_approve_tools"] = enabled
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


# 解析多 Agent 配置并忽略未声明字段。
def _load_multi_agent_settings(payload: object) -> MultiAgentSettings:
    """解析多 Agent 配置，只读取契约内的八个字段。"""
    if not isinstance(payload, dict):
        raise ValueError("multi_agent 必须是 object")
    settings = MultiAgentSettings()
    for key in (
        "enabled",
        "run_timeout_seconds",
        "worker_timeout_seconds",
        "max_workers_per_run",
        "max_concurrent_workers_per_run",
        "max_concurrent_workers_global",
        "worker_result_token_limit",
        "parent_result_token_limit",
    ):
        if key in payload:
            setattr(settings, key, payload[key])
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


def _load_web_settings(payload: object) -> WebSettings:
    """解析并校验本机 Web Host 配置。"""
    if not isinstance(payload, dict):
        raise ValueError("web 必须是 object")
    settings = WebSettings()
    if "host" in payload:
        host = str(payload["host"])
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("web.host 仅支持本机监听地址")
        settings.host = host
    for key in ("port", "max_concurrent_sessions", "event_buffer_size"):
        if key in payload:
            value = int(payload[key])
            if value <= 0:
                raise ValueError(f"web.{key} 必须大于 0")
            setattr(settings, key, value)
    return settings


def _load_gateway_settings(payload: object) -> GatewaySettings:
    """解析 Gateway 配置，字段级规则留给共享 validator。"""
    if not isinstance(payload, dict):
        raise ValueError("gateway 必须是 object")
    settings = GatewaySettings()
    for key in ("host", "port", "principal_id", "auth_token"):
        if key in payload:
            setattr(settings, key, payload[key])
    return settings


def _load_skills_settings(payload: object) -> SkillsSettings:
    """解析并校验本地 Skill 配置。"""
    if not isinstance(payload, dict):
        raise ValueError("skills 必须是 object")
    settings = SkillsSettings()
    for key in (
        "max_loaded_skills_per_turn",
        "max_skill_tokens",
        "max_loaded_skill_tokens_per_turn",
    ):
        if key in payload:
            value = int(payload[key])
            if value <= 0:
                raise ValueError(f"skills.{key} 必须大于 0")
            setattr(settings, key, value)
    if "directory" in payload:
        directory = str(payload["directory"])
        if directory != ".skills":
            raise ValueError("skills.directory 必须是项目根目录的 .skills")
        settings.directory = directory
    if settings.max_loaded_skill_tokens_per_turn < settings.max_skill_tokens:
        raise ValueError("skills.max_loaded_skill_tokens_per_turn 不能小于 skills.max_skill_tokens")
    return settings


def _load_proactive_settings(payload: object) -> ProactiveSettings:
    """解析 Phase 7 主动能力配置，全部字段规则留给共享 validator。"""
    if not isinstance(payload, dict):
        raise ValueError("proactive 必须是 object")
    settings = ProactiveSettings()
    settings.enabled = _proactive_value(payload, "enabled", settings.enabled)
    if "timezone" in payload:
        settings.timezone = payload["timezone"]
    for key in ("review_provider", "review_api_key", "review_base_url", "review_model"):
        setattr(settings, key, _proactive_value(payload, key, None))
    for key in (
        "background_max_concurrency",
        "breakbeat_refresh_minutes",
        "dream_refresh_hours",
        "review_window_token_limit",
        "profile_total_token_limit",
        "user_profile_token_limit",
        "soul_profile_token_limit",
        "skill_observation_turn_interval",
        "skill_observation_token_limit",
        "skill_evolution_batch_token_limit",
        "skill_evolution_batches_per_kind",
    ):
        setattr(settings, key, _proactive_value(payload, key, getattr(settings, key)))
    return settings


def _proactive_value(payload: dict[str, object], name: str, default: object) -> object:
    """保留原始主动配置值，由共享 validator 给出字段级错误。"""
    return payload.get(name, default)


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
        connect_retry_attempts=int(payload.get("connect_retry_attempts", 3)),
        connect_retry_delay_seconds=float(payload.get("connect_retry_delay_seconds", 1.0)),
        connect_retry_max_delay_seconds=float(payload.get("connect_retry_max_delay_seconds", 8.0)),
        enabled_tools=_string_list(payload.get("enabled_tools", []), f"mcp.servers.{name}.enabled_tools"),
    )
    if server.transport not in {"stdio", "streamable_http"}:
        raise ValueError(f"mcp.servers.{name}.transport 仅支持 stdio 或 streamable_http")
    if server.timeout_seconds <= 0:
        raise ValueError(f"mcp.servers.{name}.timeout_seconds 必须大于 0")
    if server.connect_retry_attempts < 0:
        raise ValueError(f"mcp.servers.{name}.connect_retry_attempts 不能小于 0")
    if server.connect_retry_delay_seconds <= 0:
        raise ValueError(f"mcp.servers.{name}.connect_retry_delay_seconds 必须大于 0")
    if server.connect_retry_max_delay_seconds <= 0:
        raise ValueError(f"mcp.servers.{name}.connect_retry_max_delay_seconds 必须大于 0")
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
    local_hosts = {"localhost", "127.0.0.1", "::1", "host.docker.internal"}
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

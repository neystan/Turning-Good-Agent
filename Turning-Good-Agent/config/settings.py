from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(slots=True)
class RuntimeSettings:
    """保存 Runtime 执行限制。"""

    max_tool_rounds: int = 5
    max_tool_calls_per_round: int = 8
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

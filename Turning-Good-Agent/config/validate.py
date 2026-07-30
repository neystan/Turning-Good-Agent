from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .settings import Settings


@dataclass(slots=True)
class SettingsValidationError(ValueError):
    """包含可直接映射到配置表单字段的校验错误。"""

    field_errors: dict[str, str]

    def __post_init__(self) -> None:
        ValueError.__init__(self, "；".join(f"{key}: {value}" for key, value in self.field_errors.items()))


def validate_settings(
    settings: Settings,
    *,
    available_tool_names: set[str] | None = None,
    unavailable_approval_names: set[str] | None = None,
) -> None:
    """校验所有配置值和跨字段约束，不执行任何 I/O。"""

    errors: dict[str, str] = {}
    _positive(errors, "runtime.max_tool_rounds", settings.runtime.max_tool_rounds)
    _positive(errors, "runtime.max_tool_calls_per_round", settings.runtime.max_tool_calls_per_round)
    _positive(errors, "runtime.max_parallel_tool_calls", settings.runtime.max_parallel_tool_calls)
    _positive(errors, "runtime.turn_timeout_seconds", settings.runtime.turn_timeout_seconds)
    _positive(errors, "runtime.max_context_tokens", settings.runtime.max_context_tokens)
    _positive(errors, "runtime.max_tool_result_tokens", settings.runtime.max_tool_result_tokens)
    _positive(errors, "memory.compact_token_threshold", settings.memory.compact_token_threshold)
    _positive(errors, "memory.recent_window_token_limit", settings.memory.recent_window_token_limit)
    _positive(errors, "sessions.retention_days", settings.sessions.retention_days)
    _positive(errors, "skills.max_loaded_skills_per_turn", settings.skills.max_loaded_skills_per_turn)
    _positive(errors, "skills.max_skill_tokens", settings.skills.max_skill_tokens)
    _positive(errors, "skills.max_loaded_skill_tokens_per_turn", settings.skills.max_loaded_skill_tokens_per_turn)
    _positive(errors, "llm.timeout_seconds", settings.llm.timeout_seconds)
    _non_negative(errors, "llm.max_retries", settings.llm.max_retries)
    _non_negative(errors, "llm.retry_delay_seconds", settings.llm.retry_delay_seconds)

    if settings.memory.recent_window_token_limit > settings.memory.compact_token_threshold:
        errors["memory.recent_window_token_limit"] = "不能大于 compact_token_threshold"
    if settings.skills.max_loaded_skill_tokens_per_turn < settings.skills.max_skill_tokens:
        errors["skills.max_loaded_skill_tokens_per_turn"] = "不能小于 max_skill_tokens"
    if settings.llm.provider != "openai-compatible":
        errors["llm.provider"] = "仅支持 openai-compatible"
    if settings.web.host not in {"127.0.0.1", "localhost", "::1"}:
        errors["web.host"] = "仅支持本机监听地址"

    _validate_approval_names(
        errors,
        settings.tool_permissions.approval_required_tools,
        available_tool_names,
        unavailable_approval_names or set(),
    )
    if errors:
        raise SettingsValidationError(errors)


def _positive(errors: dict[str, str], field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        errors[field] = "必须大于 0"


def _non_negative(errors: dict[str, str], field: str, value: object) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        errors[field] = "不能小于 0"


def _validate_approval_names(
    errors: dict[str, str],
    names: list[str],
    available_names: set[str] | None,
    unavailable_names: set[str],
) -> None:
    if len(names) != len(set(names)):
        errors["tool_permissions.approval_required_tools"] = "不能包含重复工具名"
        return
    if available_names is None:
        return
    unknown = set(names) - available_names - unavailable_names
    if unknown:
        errors["tool_permissions.approval_required_tools"] = f"包含未知工具：{', '.join(sorted(unknown))}"

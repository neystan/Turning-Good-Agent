from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

if TYPE_CHECKING:
    from .settings import Settings


@dataclass(slots=True)
class SettingsValidationError(ValueError):
    """包含可直接映射到配置表单字段的校验错误。"""

    field_errors: dict[str, str]

    def __post_init__(self) -> None:
        """初始化字段级配置错误。"""
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
    _positive(errors, "proactive.background_max_concurrency", settings.proactive.background_max_concurrency)
    _positive(errors, "proactive.breakbeat_refresh_minutes", settings.proactive.breakbeat_refresh_minutes)
    _positive(errors, "proactive.dream_refresh_hours", settings.proactive.dream_refresh_hours)
    _positive(errors, "proactive.review_window_token_limit", settings.proactive.review_window_token_limit)
    _positive(errors, "proactive.profile_total_token_limit", settings.proactive.profile_total_token_limit)
    _positive(errors, "proactive.user_profile_token_limit", settings.proactive.user_profile_token_limit)
    _positive(errors, "proactive.soul_profile_token_limit", settings.proactive.soul_profile_token_limit)
    _positive(errors, "proactive.skill_observation_turn_interval", settings.proactive.skill_observation_turn_interval)
    _positive(errors, "proactive.skill_observation_token_limit", settings.proactive.skill_observation_token_limit)
    _positive(errors, "proactive.skill_evolution_batch_token_limit", settings.proactive.skill_evolution_batch_token_limit)
    _positive(errors, "proactive.skill_evolution_batches_per_kind", settings.proactive.skill_evolution_batches_per_kind)

    if settings.memory.recent_window_token_limit > settings.memory.compact_token_threshold:
        errors["memory.recent_window_token_limit"] = "不能大于 compact_token_threshold"
    if settings.skills.max_loaded_skill_tokens_per_turn < settings.skills.max_skill_tokens:
        errors["skills.max_loaded_skill_tokens_per_turn"] = "不能小于 max_skill_tokens"
    if settings.llm.provider != "openai-compatible":
        errors["llm.provider"] = "仅支持 openai-compatible"
    if settings.web.host not in {"127.0.0.1", "localhost", "::1"}:
        errors["web.host"] = "仅支持本机监听地址"
    _validate_proactive_settings(errors, settings)

    _validate_approval_names(
        errors,
        settings.tool_permissions.approval_required_tools,
        available_tool_names,
        unavailable_approval_names or set(),
    )
    if errors:
        raise SettingsValidationError(errors)


def _positive(errors: dict[str, str], field: str, value: object) -> None:
    """校验正数配置项。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        errors[field] = "必须大于 0"


def _non_negative(errors: dict[str, str], field: str, value: object) -> None:
    """校验非负配置项。"""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        errors[field] = "不能小于 0"


def _validate_approval_names(
    errors: dict[str, str],
    names: list[str],
    available_names: set[str] | None,
    unavailable_names: set[str],
) -> None:
    """校验审批工具名称。"""
    if len(names) != len(set(names)):
        errors["tool_permissions.approval_required_tools"] = "不能包含重复工具名"
        return
    if available_names is None:
        return
    unknown = set(names) - available_names - unavailable_names
    if unknown:
        errors["tool_permissions.approval_required_tools"] = f"包含未知工具：{', '.join(sorted(unknown))}"


def _validate_proactive_settings(errors: dict[str, str], settings: Settings) -> None:
    """校验主动能力的时区、模型与固定画像预算。"""
    proactive = settings.proactive
    if not isinstance(proactive.enabled, bool):
        errors["proactive.enabled"] = "必须是布尔值"
    if not isinstance(proactive.timezone, str):
        errors["proactive.timezone"] = "不是有效时区"
    else:
        try:
            ZoneInfo(proactive.timezone)
        except ZoneInfoNotFoundError:
            errors["proactive.timezone"] = "不是有效时区"
    review_fields = {
        "review_provider": proactive.review_provider,
        "review_api_key": proactive.review_api_key,
        "review_base_url": proactive.review_base_url,
        "review_model": proactive.review_model,
    }
    for name, value in review_fields.items():
        if value is not None and (not isinstance(value, str) or not value.strip()):
            errors[f"proactive.{name}"] = "必须是非空字符串或 null"
    configured_review_fields = [value is not None for value in review_fields.values()]
    if any(configured_review_fields) and not all(configured_review_fields):
        errors["proactive.review_configuration"] = "provider、api_key、base_url、model 必须同时配置"
    elif all(configured_review_fields) and proactive.review_provider != "openai-compatible":
        errors["proactive.review_provider"] = "仅支持 openai-compatible"
    fixed_limits = {
        "proactive.profile_total_token_limit": (proactive.profile_total_token_limit, 16_000),
        "proactive.user_profile_token_limit": (proactive.user_profile_token_limit, 12_000),
        "proactive.soul_profile_token_limit": (proactive.soul_profile_token_limit, 4_000),
    }
    for field, (actual, expected) in fixed_limits.items():
        if actual != expected:
            errors[field] = f"第一版固定为 {expected}"

from __future__ import annotations

import errno
import hashlib
import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Callable, Mapping

from ...config.settings import Settings
from ...config.validate import SettingsValidationError, validate_settings
from ...memory.long_term import ProfileMemory, ProfileMemoryLimitError


_EDITABLE_FIELDS: dict[str, frozenset[str]] = {
    "llm": frozenset(
        {
            "api_key",
            "clear_api_key",
            "base_url",
            "model",
            "timeout_seconds",
            "max_retries",
            "retry_delay_seconds",
            "streaming_enabled",
        }
    ),
    "runtime": frozenset(
        {
            "max_tool_rounds",
            "max_tool_calls_per_round",
            "parallel_tool_calls_enabled",
            "max_parallel_tool_calls",
            "turn_timeout_seconds",
            "max_context_tokens",
            "max_tool_result_tokens",
        }
    ),
    "memory": frozenset({"compact_token_threshold", "recent_window_token_limit"}),
    "sessions": frozenset({"retention_days"}),
    "skills": frozenset(
        {"max_loaded_skills_per_turn", "max_skill_tokens", "max_loaded_skill_tokens_per_turn"}
    ),
    "proactive": frozenset(
        {
            "enabled",
            "timezone",
            "review_provider",
            "review_api_key",
            "clear_review_api_key",
            "review_base_url",
            "review_model",
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
        }
    ),
}


@dataclass(slots=True)
class ApprovalToolChanges:
    add: list[str] = field(default_factory=list)
    remove: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ConfigApplyRequest:
    changes: dict[str, dict[str, object]] = field(default_factory=dict)
    approval_required_tools: ApprovalToolChanges = field(default_factory=ApprovalToolChanges)


@dataclass(slots=True)
class DesiredConfigView:
    revision: str
    desired: dict[str, object]


class ConfigValidationError(ValueError):
    """将配置领域错误转换为 Web 字段错误。"""

    def __init__(self, field_errors: Mapping[str, str]) -> None:
        self.field_errors = dict(field_errors)
        super().__init__("；".join(f"{name}: {message}" for name, message in self.field_errors.items()))


class WebConfigControlService:
    """管理 Web 专有的配置合并、校验、脱敏和原子写入。"""

    def __init__(
        self,
        config_path: Path,
        *,
        native_tool_names: set[str],
        live_tool_names: Callable[[], set[str]],
        unavailable_approval_names: Callable[[], set[str]],
    ) -> None:
        self.config_path = config_path
        self.native_tool_names = set(native_tool_names)
        self._live_tool_names = live_tool_names
        self._unavailable_approval_names = unavailable_approval_names

    def read_desired(self) -> DesiredConfigView:
        raw = self._read_raw()
        settings = self._parse_and_validate(raw)
        return DesiredConfigView(self._revision(raw), self.redact_settings(settings))

    def apply(self, request: ConfigApplyRequest) -> DesiredConfigView:
        raw = self._read_raw()
        candidate = self._merge(raw, request)
        settings = self._parse_and_validate(candidate)
        self._atomic_write(candidate)
        return DesiredConfigView(self._revision(candidate), self.redact_settings(settings))

    def candidate_llm(self, changes: Mapping[str, object]) -> Settings:
        raw = self._read_raw()
        candidate = self._merge(raw, ConfigApplyRequest(changes={"llm": dict(changes)}))
        return self._parse_and_validate(candidate)

    def _merge(self, raw: dict[str, object], request: ConfigApplyRequest) -> dict[str, object]:
        candidate = json.loads(json.dumps(raw))
        for group, values in request.changes.items():
            allowed = _EDITABLE_FIELDS.get(group)
            if allowed is None or not isinstance(values, dict):
                raise ConfigValidationError({group: "不支持修改该配置组"})
            target = candidate.setdefault(group, {})
            if not isinstance(target, dict):
                raise ConfigValidationError({group: "配置组必须是 object"})
            for field_name, value in values.items():
                if field_name not in allowed:
                    raise ConfigValidationError({f"{group}.{field_name}": "不支持修改"})
                if (group, field_name) in {
                    ("llm", "clear_api_key"),
                    ("proactive", "clear_review_api_key"),
                }:
                    continue
                target[field_name] = value

        llm_changes = request.changes.get("llm", {})
        if "api_key" in llm_changes and "clear_api_key" in llm_changes:
            raise ConfigValidationError({"llm.api_key": "不能同时替换和清除 API Key"})
        if "api_key" in llm_changes and not str(llm_changes["api_key"]):
            raise ConfigValidationError({"llm.api_key": "不能为空；请使用 clear_api_key 明确清除"})
        if llm_changes.get("clear_api_key") is True:
            llm = candidate.setdefault("llm", {})
            assert isinstance(llm, dict)
            llm.pop("api_key", None)
        if isinstance(candidate.get("llm"), dict):
            candidate["llm"].pop("clear_api_key", None)

        proactive_changes = request.changes.get("proactive", {})
        if not isinstance(proactive_changes, dict):
            proactive_changes = {}
        if "review_api_key" in proactive_changes and "clear_review_api_key" in proactive_changes:
            raise ConfigValidationError({"proactive.review_api_key": "不能同时替换和清除 API Key"})
        if "review_api_key" in proactive_changes and not str(proactive_changes["review_api_key"]):
            raise ConfigValidationError({"proactive.review_api_key": "不能为空；请使用 clear_review_api_key 明确清除"})
        if proactive_changes.get("clear_review_api_key") is True:
            proactive = candidate.setdefault("proactive", {})
            assert isinstance(proactive, dict)
            proactive.pop("review_api_key", None)
        if isinstance(candidate.get("proactive"), dict):
            candidate["proactive"].pop("clear_review_api_key", None)

        changes = request.approval_required_tools
        if set(changes.add) & set(changes.remove):
            raise ConfigValidationError({"approval_required_tools": "同一工具不能同时添加和移除"})
        if len(changes.add) != len(set(changes.add)) or len(changes.remove) != len(set(changes.remove)):
            raise ConfigValidationError({"approval_required_tools": "不能包含重复工具名"})
        live_names = self._live_tool_names()
        invalid_adds = set(changes.add) - live_names
        if invalid_adds:
            raise ConfigValidationError(
                {"approval_required_tools.add": f"工具当前不可用：{', '.join(sorted(invalid_adds))}"}
            )
        permissions = candidate.setdefault("tool_permissions", {})
        if not isinstance(permissions, dict):
            raise ConfigValidationError({"tool_permissions": "配置组必须是 object"})
        previous = permissions.get("approval_required_tools", [])
        if not isinstance(previous, list) or not all(isinstance(name, str) for name in previous):
            raise ConfigValidationError({"tool_permissions.approval_required_tools": "必须是 string 数组"})
        next_names = (set(previous) - set(changes.remove)) | set(changes.add)
        permissions["approval_required_tools"] = sorted(next_names)
        return candidate

    def _parse_and_validate(self, raw: dict[str, object]) -> Settings:
        temporary_path: Path | None = None
        try:
            with NamedTemporaryFile(
                mode="w", encoding="utf-8", suffix=".json", dir=self.config_path.parent, delete=False
            ) as temporary:
                temporary.write(json.dumps(raw, ensure_ascii=False))
                temporary_path = Path(temporary.name)
            settings = Settings.load(local_config_path=temporary_path)
            validate_settings(
                settings,
                available_tool_names=self.native_tool_names | self._live_tool_names(),
                unavailable_approval_names=self._unavailable_approval_names(),
            )
        except SettingsValidationError as exc:
            raise ConfigValidationError(exc.field_errors) from exc
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ConfigValidationError({"config": str(exc)}) from exc
        finally:
            if temporary_path is not None:
                temporary_path.unlink(missing_ok=True)
        self._validate_existing_profiles(settings)
        return settings

    @staticmethod
    def _validate_existing_profiles(settings: Settings) -> None:
        """拒绝会截断既有长期画像的配额下调。"""
        try:
            ProfileMemory(settings.data_dir, settings.proactive).read()
        except ProfileMemoryLimitError as exc:
            message = str(exc)
            if message.startswith("USER.md"):
                field = "proactive.user_profile_token_limit"
                detail = "现有 USER.md 超过新的画像配额"
            elif message.startswith("SOUL.md"):
                field = "proactive.soul_profile_token_limit"
                detail = "现有 SOUL.md 超过新的画像配额"
            else:
                field = "proactive.profile_total_token_limit"
                detail = "现有长期记忆超过新的总画像配额"
            raise ConfigValidationError({field: detail}) from exc

    def _read_raw(self) -> dict[str, object]:
        if not self.config_path.exists():
            return {}
        try:
            payload = json.loads(self.config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConfigValidationError({"config": "settings.local.json 不是有效 JSON"}) from exc
        if not isinstance(payload, dict):
            raise ConfigValidationError({"config": "settings.local.json 顶层必须是 object"})
        return payload

    def _atomic_write(self, raw: dict[str, object]) -> None:
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json", dir=self.config_path.parent, delete=False
        ) as temporary:
            temporary.write(json.dumps(raw, ensure_ascii=False, indent=2) + "\n")
            temporary_path = Path(temporary.name)
        try:
            os.replace(temporary_path, self.config_path)
        except OSError as error:
            if error.errno != errno.EBUSY:
                raise
            with temporary_path.open("rb") as source, self.config_path.open("wb") as destination:
                destination.write(source.read())
                destination.flush()
                os.fsync(destination.fileno())
        finally:
            temporary_path.unlink(missing_ok=True)

    @staticmethod
    def _revision(raw: dict[str, object]) -> str:
        revision_input = dict(raw)
        gateway = revision_input.get("gateway")
        if isinstance(gateway, dict):
            retained_gateway = {key: value for key, value in gateway.items() if key != "auth_token"}
            if retained_gateway:
                revision_input["gateway"] = retained_gateway
            else:
                revision_input.pop("gateway")
        canonical = json.dumps(revision_input, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return f"sha256:{hashlib.sha256(canonical).hexdigest()}"

    @staticmethod
    def redact_settings(settings: Settings) -> dict[str, object]:
        return {
            "llm": {
                "provider": settings.llm.provider,
                "api_key_configured": bool(settings.llm.api_key),
                "base_url": settings.llm.base_url,
                "model": settings.llm.model,
                "timeout_seconds": settings.llm.timeout_seconds,
                "max_retries": settings.llm.max_retries,
                "retry_delay_seconds": settings.llm.retry_delay_seconds,
                "streaming_enabled": settings.llm.streaming_enabled,
            },
            "runtime": asdict(settings.runtime),
            "memory": asdict(settings.memory),
            "sessions": {"retention_days": settings.sessions.retention_days},
            "skills": {
                "max_loaded_skills_per_turn": settings.skills.max_loaded_skills_per_turn,
                "max_skill_tokens": settings.skills.max_skill_tokens,
                "max_loaded_skill_tokens_per_turn": settings.skills.max_loaded_skill_tokens_per_turn,
            },
            "proactive": {
                "enabled": settings.proactive.enabled,
                "timezone": settings.proactive.timezone,
                "review_provider": settings.proactive.review_provider,
                "review_api_key_configured": bool(settings.proactive.review_api_key),
                "review_base_url": settings.proactive.review_base_url,
                "review_model": settings.proactive.review_model,
                "background_max_concurrency": settings.proactive.background_max_concurrency,
                "breakbeat_refresh_minutes": settings.proactive.breakbeat_refresh_minutes,
                "dream_refresh_hours": settings.proactive.dream_refresh_hours,
                "review_window_token_limit": settings.proactive.review_window_token_limit,
                "profile_total_token_limit": settings.proactive.profile_total_token_limit,
                "user_profile_token_limit": settings.proactive.user_profile_token_limit,
                "soul_profile_token_limit": settings.proactive.soul_profile_token_limit,
                "skill_observation_turn_interval": settings.proactive.skill_observation_turn_interval,
                "skill_observation_token_limit": settings.proactive.skill_observation_token_limit,
                "skill_evolution_batch_token_limit": settings.proactive.skill_evolution_batch_token_limit,
                "skill_evolution_batches_per_kind": settings.proactive.skill_evolution_batches_per_kind,
            },
        }

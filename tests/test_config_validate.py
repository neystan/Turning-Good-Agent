from __future__ import annotations

import importlib
import json

import pytest


settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
validate_module = importlib.import_module("Turning-Good-Agent.config.validate")


def test_validate_settings_reports_cross_field_error() -> None:
    settings = settings_module.Settings()
    settings.memory.recent_window_token_limit = 200_001

    with pytest.raises(validate_module.SettingsValidationError) as raised:
        validate_module.validate_settings(
            settings,
            available_tool_names={"exec", "write_file", "edit_file", "write_stdin"},
            unavailable_approval_names=set(),
        )

    assert raised.value.field_errors == {
        "memory.recent_window_token_limit": "不能大于 compact_token_threshold"
    }


def test_settings_load_uses_static_validation(tmp_path) -> None:
    config_path = tmp_path / "settings.local.json"
    config_path.write_text(
        json.dumps(
            {"memory": {"compact_token_threshold": 10, "recent_window_token_limit": 11}}
        ),
        encoding="utf-8",
    )

    with pytest.raises(validate_module.SettingsValidationError):
        settings_module.Settings.load(local_config_path=config_path)


def test_validate_settings_allows_editable_profile_budget_split() -> None:
    settings = settings_module.Settings()
    settings.proactive.profile_total_token_limit = 10_000
    settings.proactive.user_profile_token_limit = 6_000
    settings.proactive.soul_profile_token_limit = 8_000

    validate_module.validate_settings(settings)


@pytest.mark.parametrize(
    ("total", "user", "soul", "runtime_limit", "message"),
    [
        (7_999, 8_000, 4_000, 300_000, "不能小于 USER 或 SOUL 画像配额"),
        (12_001, 8_000, 4_000, 300_000, "不能大于 USER 与 SOUL 画像配额之和"),
        (20_000, 12_000, 8_000, 19_999, "不能大于 runtime.max_context_tokens"),
    ],
)
def test_validate_settings_rejects_invalid_profile_budget_relation(
    total, user, soul, runtime_limit, message
) -> None:
    settings = settings_module.Settings()
    settings.proactive.profile_total_token_limit = total
    settings.proactive.user_profile_token_limit = user
    settings.proactive.soul_profile_token_limit = soul
    settings.runtime.max_context_tokens = runtime_limit

    with pytest.raises(validate_module.SettingsValidationError) as raised:
        validate_module.validate_settings(settings)

    assert raised.value.field_errors == {"proactive.profile_total_token_limit": message}

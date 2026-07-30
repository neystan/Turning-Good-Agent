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

from __future__ import annotations

import importlib
import json
from copy import deepcopy
from types import SimpleNamespace

import pytest


settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
validate_module = importlib.import_module("Turning-Good-Agent.config.validate")
service_module = importlib.import_module("Turning-Good-Agent.proactive.service")


def test_settings_loads_phase_7_proactive_defaults(tmp_path) -> None:
    config_path = tmp_path / "settings.local.json"
    config_path.write_text(json.dumps({"proactive": {}}), encoding="utf-8")

    settings = settings_module.Settings.load(local_config_path=config_path)

    assert settings.proactive.timezone == "Asia/Shanghai"
    assert settings.proactive.breakbeat_refresh_minutes == 60
    assert settings.proactive.dream_refresh_hours == 24
    assert settings.proactive.review_window_token_limit == 100_000


def test_proactive_settings_has_no_daily_draft_limit() -> None:
    assert not hasattr(settings_module.ProactiveSettings(), "skill_evolution_daily_draft_limit")


def test_validate_settings_rejects_unknown_proactive_timezone() -> None:
    settings = settings_module.Settings()
    settings.proactive.timezone = "Mars/Olympus_Mons"

    with pytest.raises(validate_module.SettingsValidationError) as raised:
        validate_module.validate_settings(settings)

    assert raised.value.field_errors == {"proactive.timezone": "不是有效时区"}


def test_validate_settings_requires_valid_profile_budget_split() -> None:
    settings = settings_module.Settings()
    settings.proactive.profile_total_token_limit = 16_001
    settings.proactive.user_profile_token_limit = 12_000
    settings.proactive.soul_profile_token_limit = 4_000

    with pytest.raises(validate_module.SettingsValidationError) as raised:
        validate_module.validate_settings(settings)

    assert raised.value.field_errors == {
        "proactive.profile_total_token_limit": "不能大于 USER 与 SOUL 画像配额之和"
    }


@pytest.mark.parametrize("value", ["false", 0, 1, None])
def test_proactive_enabled_rejects_non_boolean_json(value, tmp_path) -> None:
    config_path = tmp_path / "settings.local.json"
    config_path.write_text(json.dumps({"proactive": {"enabled": value}}), encoding="utf-8")

    with pytest.raises(validate_module.SettingsValidationError) as raised:
        settings_module.Settings.load(local_config_path=config_path)

    assert raised.value.field_errors == {"proactive.enabled": "必须是布尔值"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("background_max_concurrency", True),
        ("dream_refresh_hours", "24"),
        ("review_window_token_limit", 0),
    ],
)
def test_proactive_positive_integers_reject_invalid_json_types(field, value, tmp_path) -> None:
    config_path = tmp_path / "settings.local.json"
    config_path.write_text(json.dumps({"proactive": {field: value}}), encoding="utf-8")

    with pytest.raises(validate_module.SettingsValidationError) as raised:
        settings_module.Settings.load(local_config_path=config_path)

    assert raised.value.field_errors == {f"proactive.{field}": "必须是正整数"}


@pytest.mark.parametrize(
    "proactive",
    [
        {"review_model": "review-model"},
        {
            "review_provider": "openai-compatible",
            "review_api_key": "review-key",
            "review_base_url": "https://review.example/v1",
        },
    ],
)
def test_review_llm_configuration_must_be_complete(proactive, tmp_path) -> None:
    config_path = tmp_path / "settings.local.json"
    config_path.write_text(json.dumps({"proactive": proactive}), encoding="utf-8")

    with pytest.raises(validate_module.SettingsValidationError) as raised:
        settings_module.Settings.load(local_config_path=config_path)

    assert "proactive.review_configuration" in raised.value.field_errors


def test_review_provider_uses_complete_independent_llm_configuration(monkeypatch) -> None:
    settings = settings_module.Settings()
    settings.llm.provider = "openai-compatible"
    settings.llm.api_key = "main-key"
    settings.llm.base_url = "https://main.example/v1"
    settings.llm.model = "shared-name"
    settings.proactive.review_provider = "openai-compatible"
    settings.proactive.review_api_key = "review-key"
    settings.proactive.review_base_url = "https://review.example/v1"
    settings.proactive.review_model = "shared-name"
    captured = []
    provider = object()

    def capture_build_llm(candidate_settings):
        captured.append(deepcopy(candidate_settings.llm))
        return provider

    monkeypatch.setattr(service_module, "build_llm", capture_build_llm)
    service = object.__new__(service_module.ProactiveService)
    service.runtime = SimpleNamespace(
        settings=settings,
    )

    review_provider = service._review_provider()

    assert review_provider is provider
    assert len(captured) == 1
    assert captured[0].provider == "openai-compatible"
    assert captured[0].api_key == "review-key"
    assert captured[0].base_url == "https://review.example/v1"
    assert captured[0].model == "shared-name"

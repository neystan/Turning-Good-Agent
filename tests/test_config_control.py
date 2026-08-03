from __future__ import annotations

import errno
import importlib
import json

import pytest


control_module = importlib.import_module("Turning-Good-Agent.web.backend.config_control")


def test_apply_merges_changes_and_redacts_api_key(tmp_path) -> None:
    config_path = tmp_path / "settings.local.json"
    config_path.write_text(
        json.dumps(
            {
                "llm": {"api_key": "old-key", "model": "old-model"},
                "tool_permissions": {"approval_required_tools": ["exec"]},
            }
        ),
        encoding="utf-8",
    )
    service = control_module.WebConfigControlService(
        config_path,
        native_tool_names={"exec", "web_fetch"},
        live_tool_names=lambda: {"exec", "web_fetch"},
        unavailable_approval_names=lambda: set(),
    )

    view = service.apply(
        control_module.ConfigApplyRequest(
            changes={"llm": {"api_key": "new-key", "model": "new-model"}},
            approval_required_tools=control_module.ApprovalToolChanges(
                add=["web_fetch"], remove=["exec"]
            ),
        )
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["llm"]["api_key"] == "new-key"
    assert saved["llm"]["model"] == "new-model"
    assert saved["tool_permissions"]["approval_required_tools"] == ["web_fetch"]
    assert view.revision.startswith("sha256:")
    assert view.desired["llm"]["api_key_configured"] is True
    assert "api_key" not in view.desired["llm"]


def test_control_plane_rejects_global_auto_approval_changes(tmp_path) -> None:
    config_path = tmp_path / "settings.local.json"
    config_path.write_text(json.dumps({"tool_permissions": {"auto_approve_tools": False}}), encoding="utf-8")
    service = control_module.WebConfigControlService(
        config_path,
        native_tool_names={"exec"},
        live_tool_names=lambda: {"exec"},
        unavailable_approval_names=lambda: set(),
    )

    with pytest.raises(control_module.ConfigValidationError) as error:
        service.apply(control_module.ConfigApplyRequest(changes={"tool_permissions": {"auto_approve_tools": True}}))

    assert error.value.field_errors == {"tool_permissions": "不支持修改该配置组"}


def test_apply_persists_when_docker_file_mount_rejects_replace(tmp_path, monkeypatch) -> None:
    config_path = tmp_path / "settings.local.json"
    config_path.write_text(json.dumps({"llm": {"model": "old-model"}}), encoding="utf-8")
    service = control_module.WebConfigControlService(
        config_path,
        native_tool_names={"exec"},
        live_tool_names=lambda: {"exec"},
        unavailable_approval_names=lambda: set(),
    )

    def reject_replace(source, destination) -> None:
        raise OSError(errno.EBUSY, "Device or resource busy", str(destination))

    monkeypatch.setattr(control_module.os, "replace", reject_replace)

    view = service.apply(control_module.ConfigApplyRequest(changes={"llm": {"model": "new-model"}}))

    assert json.loads(config_path.read_text(encoding="utf-8"))["llm"]["model"] == "new-model"
    assert view.desired["llm"]["model"] == "new-model"


def test_apply_edits_all_proactive_fields_and_redacts_review_api_key(tmp_path) -> None:
    config_path = tmp_path / "settings.local.json"
    service = control_module.WebConfigControlService(
        config_path,
        native_tool_names={"exec"},
        live_tool_names=lambda: {"exec"},
        unavailable_approval_names=lambda: set(),
    )

    view = service.apply(
        control_module.ConfigApplyRequest(
            changes={
                "proactive": {
                    "enabled": False,
                    "timezone": "America/New_York",
                    "review_provider": "openai-compatible",
                    "review_api_key": "review-secret",
                    "review_base_url": "https://review.example/v1",
                    "review_model": "review-model",
                    "background_max_concurrency": 2,
                    "breakbeat_refresh_minutes": 30,
                    "dream_refresh_hours": 12,
                    "review_window_token_limit": 80_000,
                    "profile_total_token_limit": 15_000,
                    "user_profile_token_limit": 10_000,
                    "soul_profile_token_limit": 6_000,
                    "skill_observation_turn_interval": 5,
                    "skill_observation_token_limit": 120,
                    "skill_evolution_batch_token_limit": 80_000,
                    "skill_evolution_batches_per_kind": 2,
                }
            }
        )
    )

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert saved["proactive"]["review_api_key"] == "review-secret"
    assert view.desired["proactive"]["review_api_key_configured"] is True
    assert "review_api_key" not in view.desired["proactive"]
    assert view.desired["proactive"]["profile_total_token_limit"] == 15_000


def test_invalid_profile_budget_apply_preserves_config_and_profile_files(tmp_path) -> None:
    config_path = tmp_path / "settings.local.json"
    config_path.write_text(json.dumps({"data_dir": "state"}), encoding="utf-8")
    profile_path = tmp_path / "state" / "memory" / "USER.md"
    profile_path.parent.mkdir(parents=True)
    profile_path.write_text("memory " * 100, encoding="utf-8")
    original_config = config_path.read_bytes()
    original_profile = profile_path.read_bytes()
    service = control_module.WebConfigControlService(
        config_path,
        native_tool_names={"exec"},
        live_tool_names=lambda: {"exec"},
        unavailable_approval_names=lambda: set(),
    )

    with pytest.raises(control_module.ConfigValidationError) as raised:
        service.apply(
            control_module.ConfigApplyRequest(
                changes={
                    "proactive": {
                        "profile_total_token_limit": 2,
                        "user_profile_token_limit": 1,
                        "soul_profile_token_limit": 1,
                    }
                }
            )
        )

    assert raised.value.field_errors == {
        "proactive.user_profile_token_limit": "现有 USER.md 超过新的画像配额"
    }
    assert config_path.read_bytes() == original_config
    assert profile_path.read_bytes() == original_profile

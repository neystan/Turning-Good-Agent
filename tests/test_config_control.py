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

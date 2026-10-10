from __future__ import annotations

import importlib
import json

import pytest


settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
validate_module = importlib.import_module("Turning-Good-Agent.config.validate")
control_module = importlib.import_module("Turning-Good-Agent.web.backend.config_control")


def test_settings_loads_gateway_local_endpoint_and_identity(tmp_path) -> None:
    """Would fail if Gateway settings were ignored or lost during local config loading."""
    config_path = tmp_path / "settings.local.json"
    config_path.write_text(
        json.dumps(
            {
                "gateway": {
                    "host": "localhost",
                    "port": 9231,
                    "principal_id": "local-workspace",
                    "auth_token": "gateway-secret",
                }
            }
        ),
        encoding="utf-8",
    )

    settings = settings_module.Settings.load(local_config_path=config_path)

    assert settings.gateway.host == "localhost"
    assert settings.gateway.port == 9231
    assert settings.gateway.principal_id == "local-workspace"
    assert settings.gateway.auth_token == "gateway-secret"


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("host", "0.0.0.0", "仅支持本机监听地址"),
        ("port", 0, "必须是正整数"),
        ("principal_id", "", "必须是非空字符串"),
        ("auth_token", "", "必须是非空字符串或 null"),
    ],
)
def test_gateway_settings_reject_unsafe_or_invalid_values(field, value, message) -> None:
    """Would fail if a non-local or malformed Gateway endpoint could be accepted."""
    settings = settings_module.Settings()
    setattr(settings.gateway, field, value)

    with pytest.raises(validate_module.SettingsValidationError) as raised:
        validate_module.validate_settings(settings)

    assert raised.value.field_errors == {f"gateway.{field}": message}


def test_web_config_view_hides_gateway_and_apply_preserves_its_raw_object(tmp_path) -> None:
    """Would fail if Web control exposed or overwrote Gateway-only credentials."""
    config_path = tmp_path / "settings.local.json"
    gateway = {
        "host": "127.0.0.1",
        "port": 9231,
        "principal_id": "local-user",
        "auth_token": "gateway-secret",
    }
    config_path.write_text(
        json.dumps(
            {
                "gateway": gateway,
                "llm": {"model": "old"},
                "tool_permissions": {"approval_required_tools": ["exec"]},
            }
        ),
        encoding="utf-8",
    )
    service = control_module.WebConfigControlService(
        config_path,
        native_tool_names={"exec"},
        live_tool_names=lambda: {"exec"},
        unavailable_approval_names=lambda: set(),
    )

    before = service.read_desired()
    after = service.apply(control_module.ConfigApplyRequest(changes={"llm": {"model": "new"}}))

    saved = json.loads(config_path.read_text(encoding="utf-8"))
    assert "gateway" not in before.desired
    assert "gateway" not in after.desired
    assert saved["gateway"] == gateway

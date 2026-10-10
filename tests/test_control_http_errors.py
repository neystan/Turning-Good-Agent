from __future__ import annotations

import importlib
from pathlib import Path

from fastapi.testclient import TestClient


def _client(tmp_path: Path) -> TestClient:
    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
    host_module = importlib.import_module("Turning-Good-Agent.gateway.host")
    runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
    settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
    settings = settings_module.Settings(data_dir=tmp_path / "state")
    settings.local_config_path = tmp_path / "settings.local.json"
    settings.proactive.enabled = False
    runtime = runtime_module.AgentRuntime.create_default(settings, object())
    return TestClient(app_module.create_app(host_module.GatewayHost(settings, runtime=runtime)))


def test_malformed_channel_requests_never_echo_secret_or_owner_code(tmp_path: Path) -> None:
    secret = "super-secret-should-not-escape"
    owner_code = "123456"
    with _client(tmp_path) as client:
        malformed = client.post(
            "/api/control/channels/feishu",
            content=f'{{"app_id":"demo","app_secret":"{secret}"',
            headers={"content-type": "application/json"},
        )
        assert malformed.status_code == 422
        assert secret not in malformed.text

        invalid = client.post(
            "/api/control/channels/feishu/not-real/owner-code",
            json={"code": owner_code},
        )
        assert invalid.status_code == 404
        assert owner_code not in invalid.text
        assert secret not in invalid.text

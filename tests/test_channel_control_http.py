from __future__ import annotations

import importlib
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient


def _make_host(tmp_path: Path):
    host_module = importlib.import_module("Turning-Good-Agent.gateway.host")
    runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
    settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
    settings = settings_module.Settings(data_dir=tmp_path / "state")
    settings.local_config_path = tmp_path / "settings.local.json"
    settings.proactive.enabled = False
    runtime = runtime_module.AgentRuntime.create_default(settings, object())
    return host_module.GatewayHost(settings, runtime=runtime)


def test_owner_invitation_conflict_is_409(tmp_path: Path) -> None:
    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
    host = _make_host(tmp_path)
    host.weixin_transport.enable_account = lambda _account_id: None

    with TestClient(app_module.create_app(host)) as client:
        created = client.post(
            "/api/control/channels/weixin/invitations",
            json={"principal": "owner"},
        )
        response = client.post(
            "/api/control/channels/weixin/invitations",
            json={"principal": "owner"},
        )

    assert created.status_code == 200
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "owner_binding_exists"


def test_channel_view_exposes_role_without_candidate_state(tmp_path: Path) -> None:
    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
    host = _make_host(tmp_path)
    binding = host.channel_registry.create_weixin_invitation(principal_id=None)

    with TestClient(app_module.create_app(host)) as client:
        response = client.get(f"/api/control/channels/weixin/{binding.id}")

    assert response.status_code == 200
    assert response.json()["principal_kind"] == "independent"
    assert "rebind_state" not in response.json()
    assert "private" not in response.text


def test_delete_channel_binding_returns_gateway_result(tmp_path: Path) -> None:
    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
    host_module = importlib.import_module("Turning-Good-Agent.gateway.host")
    host = _make_host(tmp_path)
    binding = host.channel_registry.create_weixin_invitation(principal_id="owner")

    async def delete_binding(platform: str, account_id: str):
        return host_module.ChannelDeletionResult(
            platform=platform,
            account_id=account_id,
            principal_kind="owner",
            deleted_session_ids=[],
        )

    host.delete_channel_binding = delete_binding

    with TestClient(app_module.create_app(host)) as client:
        response = client.delete(f"/api/control/channels/weixin/{binding.id}")

    assert response.status_code == 200
    assert response.json() == {
        "deleted": True,
        "account_id": binding.id,
        "platform": "weixin",
    }


def test_busy_delete_returns_409_without_mutation(tmp_path: Path) -> None:
    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
    registry_module = importlib.import_module("Turning-Good-Agent.channels.registry")
    host = _make_host(tmp_path)
    binding = host.channel_registry.create_weixin_invitation(principal_id="owner")

    async def busy_delete(_platform: str, _account_id: str):
        raise registry_module.ChannelConflictError(
            "binding_busy",
            "该 Binding 正在处理消息或等待回复投递",
        )

    host.delete_channel_binding = busy_delete

    with TestClient(app_module.create_app(host)) as client:
        response = client.delete(f"/api/control/channels/weixin/{binding.id}")

    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "binding_busy",
            "message": "该 Binding 正在处理消息或等待回复投递",
        }
    }
    assert host.channel_registry.get("weixin", binding.id) is not None


def test_channel_control_http_never_returns_private_credentials(tmp_path: Path) -> None:
    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
    host_module = importlib.import_module("Turning-Good-Agent.gateway.host")
    runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
    settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
    settings = settings_module.Settings(data_dir=tmp_path / "state")
    settings.local_config_path = tmp_path / "settings.local.json"
    settings.proactive.enabled = False
    runtime = runtime_module.AgentRuntime.create_default(settings, object())
    host = host_module.GatewayHost(settings, runtime=runtime)
    async def reload_account(account_id: str, candidate=None) -> bool:
        del account_id, candidate
        return True
    host.feishu_transport.reload_account = reload_account
    with TestClient(app_module.create_app(host)) as client:
        created = client.post("/api/control/channels/feishu", json={"app_id": "cli_demo", "app_secret": "super-secret", "domain": "open.feishu.cn"})
        assert created.status_code == 200
        encoded = created.text
        assert "super-secret" not in encoded
        assert "cli_demo" not in encoded
        assert "app_secret" not in encoded
        listed = client.get("/api/control/channels")
        assert listed.status_code == 200
        assert "super-secret" not in listed.text
        assert client.get("/api/control/channels/other/not-real").status_code == 404


def test_weixin_rescan_endpoint_preserves_binding_identity(tmp_path: Path) -> None:
    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
    host_module = importlib.import_module("Turning-Good-Agent.gateway.host")
    runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
    settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
    settings = settings_module.Settings(data_dir=tmp_path / "state")
    settings.local_config_path = tmp_path / "settings.local.json"
    settings.proactive.enabled = False
    runtime = runtime_module.AgentRuntime.create_default(settings, object())
    host = host_module.GatewayHost(settings, runtime=runtime)
    binding = host.channel_registry.create_weixin_invitation(principal_id=None)
    record = host.channel_registry.get("weixin", binding.id)
    assert record is not None
    active = host.channel_registry.update(
        replace(
            record,
            status="awaiting_first_dm",
            private={
                **record.private,
                "credential_state": "active",
                "bot_token": "private-token",
                "ilink_bot_id": "private-bot",
                "from_user_id": "private-user",
                "context_token": "private-context",
                "cursor": "private-cursor",
                "connected": True,
            },
        )
    )
    active = host.channel_registry.update(replace(active, status="active"))
    rebound: list[str] = []

    async def start_rebind(account_id: str) -> None:
        rebound.append(account_id)

    host.weixin_transport.start_rebind = start_rebind

    with TestClient(app_module.create_app(host)) as client:
        response = client.post(f"/api/control/channels/weixin/{active.id}/rescan")

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == active.id
    assert payload["principal_id"] == active.principal_id
    assert payload["status"] == "active"
    assert rebound == [active.id]
    assert host.channel_registry.get("weixin", active.id) == active
    assert "private" not in payload


def test_weixin_qr_endpoint_exposes_only_short_lived_memory_qr(tmp_path: Path) -> None:
    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
    host_module = importlib.import_module("Turning-Good-Agent.gateway.host")
    runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
    settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
    settings = settings_module.Settings(data_dir=tmp_path / "state")
    settings.local_config_path = tmp_path / "settings.local.json"
    settings.proactive.enabled = False
    runtime = runtime_module.AgentRuntime.create_default(settings, object())
    host = host_module.GatewayHost(settings, runtime=runtime)

    with TestClient(app_module.create_app(host)) as client:
        binding = host.channel_registry.create_weixin_invitation(principal_id="owner")
        host.weixin_transport._qr_cache.put(binding.id, "data:image/png;base64,opaque-qr")
        response = client.get(f"/api/control/channels/weixin/{binding.id}/qr")

    assert response.status_code == 200
    payload = response.json()
    assert payload["binding_id"] == binding.id
    assert payload["status"] == "pending_qr"
    assert payload["qr_content"] == "data:image/png;base64,opaque-qr"
    assert payload["expires_at"] is not None
    assert "private" not in response.text
    assert response.headers["cache-control"] == "no-store"


def test_weixin_qr_endpoint_keeps_polling_during_candidate_regeneration(tmp_path: Path) -> None:
    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
    host = _make_host(tmp_path)
    binding = host.channel_registry.create_weixin_invitation(principal_id="owner")
    active = host.channel_registry.update(
        replace(
            binding,
            status="awaiting_first_dm",
            private={
                **binding.private,
                "bot_token": "old-token",
                "ilink_bot_id": "old-bot",
                "from_user_id": "old-user",
                "context_token": "old-context",
            },
        )
    )
    active = host.channel_registry.update(replace(active, status="active"))
    host.weixin_transport.get_qr = lambda _account_id: None
    host.weixin_transport.is_qr_pending = lambda account_id: account_id == active.id

    with TestClient(app_module.create_app(host)) as client:
        response = client.get(f"/api/control/channels/weixin/{active.id}/qr")

    assert response.status_code == 200
    assert response.json()["status"] == "pending_qr"
    assert response.json()["qr_content"] is None


def test_weixin_qr_endpoint_clears_after_disable(tmp_path: Path) -> None:
    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
    host_module = importlib.import_module("Turning-Good-Agent.gateway.host")
    runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
    settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
    settings = settings_module.Settings(data_dir=tmp_path / "state")
    settings.local_config_path = tmp_path / "settings.local.json"
    settings.proactive.enabled = False
    runtime = runtime_module.AgentRuntime.create_default(settings, object())
    host = host_module.GatewayHost(settings, runtime=runtime)

    with TestClient(app_module.create_app(host)) as client:
        binding = host.channel_registry.create_weixin_invitation(principal_id="owner")
        host.weixin_transport._qr_cache.put(binding.id, "data:image/png;base64,opaque-qr")
        response = client.post(f"/api/control/channels/weixin/{binding.id}/disable")
        qr_response = client.get(f"/api/control/channels/weixin/{binding.id}/qr")

    assert response.status_code == 200
    assert qr_response.status_code == 200
    payload = qr_response.json()
    assert payload["status"] == "disabled"
    assert payload["qr_content"] is None
    assert payload["expires_at"] is None

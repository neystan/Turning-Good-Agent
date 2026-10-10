from __future__ import annotations

import asyncio
import importlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

registry_module = importlib.import_module("Turning-Good-Agent.channels.registry")
control_module = importlib.import_module("Turning-Good-Agent.web.backend.channel_control")


def test_channel_control_views_are_redacted_and_lifecycle_keeps_identity(tmp_path: Path) -> None:
    class Transport:
        async def reload_account(self, account_id: str, candidate=None) -> bool:
            del account_id, candidate
            return True

        async def enable_account(self, account_id: str) -> None:
            del account_id

        async def disable_account(self, account_id: str) -> None:
            del account_id

    async def run() -> None:
        registry = registry_module.ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        service = control_module.ChannelControlService(
            registry,
            weixin_transport=Transport(),
            feishu_transport=Transport(),
            owner_principal_id="owner",
        )
        account = await service.register_feishu(app_id="app", app_secret="super-secret", domain="open.feishu.cn")
        view = service.get_view("feishu", account.id)
        assert "super-secret" not in repr(view)
        assert not hasattr(view, "private")
        disabled = await service.disable("feishu", account.id)
        assert disabled.enabled is False
        enabled = await service.enable("feishu", account.id)
        assert enabled.id == account.id
        assert enabled.principal_id == "owner"
        revoked = await service.revoke("feishu", account.id)
        assert revoked.status == "revoked"
        assert revoked.id == account.id

    asyncio.run(run())


def test_new_weixin_invitation_gets_non_owner_principal(tmp_path: Path) -> None:
    async def run() -> None:
        registry = registry_module.ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        service = control_module.ChannelControlService(registry, owner_principal_id="owner")
        view = await service.create_weixin_invitation("new")
        assert view.platform == "weixin"
        assert view.principal_id != "owner"
        assert view.credential_state == "pending"

    asyncio.run(run())


def test_safe_weixin_details_do_not_expose_candidate_state(tmp_path: Path) -> None:
    registry = registry_module.ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
    binding = registry.create_weixin_invitation(principal_id=None)
    service = control_module.ChannelControlService(
        registry,
        owner_principal_id="owner",
    )

    view = service.get_view("weixin", binding.id)

    assert view.principal_kind == "independent"
    assert service.safe_details(view) == {}


def test_delete_delegates_to_gateway_host_without_local_mutation(tmp_path: Path) -> None:
    async def run() -> None:
        registry = registry_module.ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        binding = registry.create_weixin_invitation(principal_id=None)
        calls: list[tuple[str, str]] = []

        async def delete_binding(platform: str, account_id: str):
            calls.append((platform, account_id))
            return SimpleNamespace(platform=platform, account_id=account_id)

        service = control_module.ChannelControlService(
            registry,
            delete_binding=delete_binding,
            owner_principal_id="owner",
        )

        result = await service.delete("weixin", binding.id)

        assert result.account_id == binding.id
        assert calls == [("weixin", binding.id)]
        assert registry.get("weixin", binding.id) is not None

    asyncio.run(run())


def test_reenabling_unscanned_weixin_binding_returns_to_qr_lifecycle(tmp_path: Path) -> None:
    class Transport:
        def __init__(self) -> None:
            self.enabled: list[str] = []

        async def enable_account(self, account_id: str) -> None:
            self.enabled.append(account_id)

        async def disable_account(self, _account_id: str) -> None:
            return None

    async def run() -> None:
        registry = registry_module.ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        transport = Transport()
        service = control_module.ChannelControlService(
            registry,
            weixin_transport=transport,
            owner_principal_id="owner",
        )

        invitation = await service.create_weixin_invitation("new")
        disabled = await service.disable("weixin", invitation.id)
        restored = await service.enable("weixin", invitation.id)

        assert disabled.status == "disabled"
        assert restored.status == "pending_qr"
        assert transport.enabled == [invitation.id, invitation.id]

    asyncio.run(run())


def test_rescan_delegates_to_candidate_flow_without_resetting_active_binding(tmp_path: Path) -> None:
    class Transport:
        def __init__(self) -> None:
            self.rebound: list[str] = []

        async def start_rebind(self, account_id: str) -> None:
            self.rebound.append(account_id)

    async def run() -> None:
        registry = registry_module.ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        transport = Transport()
        service = control_module.ChannelControlService(
            registry,
            weixin_transport=transport,
            owner_principal_id="owner",
        )
        pending = registry.create_weixin_invitation(principal_id=None)
        current = registry.update(
            replace(
                pending,
                status="awaiting_first_dm",
                subscribed=True,
                private={
                    **pending.private,
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
        current = registry.update(replace(current, status="active"))

        rebound = await service.rescan_weixin_binding(current.id)

        stored = registry.get("weixin", current.id)
        assert stored == current
        assert rebound.id == current.id
        assert rebound.status == "active"
        assert transport.rebound == [current.id]

    asyncio.run(run())


def test_weixin_disable_and_revoke_stop_the_transport_after_registry_update(tmp_path: Path) -> None:
    class Transport:
        def __init__(self) -> None:
            self.calls: list[str] = []

        async def disable_account(self, account_id: str) -> None:
            self.calls.append(account_id)

    async def run() -> None:
        registry = registry_module.ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        transport = Transport()
        service = control_module.ChannelControlService(
            registry,
            weixin_transport=transport,
            owner_principal_id="owner",
        )
        first = registry.create_weixin_invitation(principal_id=None)
        disabled = await service.disable("weixin", first.id)
        second = registry.create_weixin_invitation(principal_id=None)
        revoked = await service.revoke("weixin", second.id)

        assert disabled.status == "disabled"
        assert revoked.status == "revoked"
        assert transport.calls == [first.id, second.id]

    asyncio.run(run())


def test_control_updates_running_transports_and_exposes_explicit_account_controls(tmp_path: Path) -> None:
    class Transport:
        def __init__(self, registry) -> None:
            self.registry = registry
            self.calls: list[tuple[str, str]] = []
            self.owner_codes: list[tuple[str, str]] = []

        async def enable_account(self, account_id: str) -> None:
            self.calls.append(("enable", account_id))

        async def disable_account(self, account_id: str) -> None:
            self.calls.append(("disable", account_id))

        async def reload_account(self, account_id: str, candidate=None) -> bool:
            self.calls.append(("reload", account_id))
            if candidate is not None:
                self.registry.update(candidate)
            return True

        def issue_owner_code(self, account_id: str, *, code: str) -> str:
            self.owner_codes.append((account_id, code))
            return code

    async def run() -> None:
        registry = registry_module.ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        weixin = Transport(registry)
        feishu = Transport(registry)
        service = control_module.ChannelControlService(
            registry,
            weixin_transport=weixin,
            feishu_transport=feishu,
            owner_principal_id="owner",
        )

        invitation = await service.create_weixin_invitation("owner")
        assert ("enable", invitation.id) in weixin.calls

        registered = await service.register_feishu(
            app_id="app", app_secret="super-secret", domain="open.feishu.cn"
        )
        assert ("reload", registered.id) in feishu.calls
        assert (await service.subscribe("feishu", registered.id)).subscribed is True
        assert (await service.unsubscribe("feishu", registered.id)).subscribed is False
        assert (await service.set_feishu_owner_code(registered.id, "123456")).id == registered.id
        assert feishu.owner_codes == [(registered.id, "123456")]
        rotated = await service.update_feishu_credentials(
            registered.id,
            app_id="app",
            app_secret="new-secret",
            domain="open.feishu.cn",
        )
        assert rotated.id == registered.id
        assert (await service.revoke("feishu", registered.id)).status == "revoked"
        assert ("disable", registered.id) in feishu.calls

    asyncio.run(run())

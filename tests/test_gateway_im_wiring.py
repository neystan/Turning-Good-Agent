from __future__ import annotations

import importlib
from dataclasses import replace
from pathlib import Path


def test_host_owns_one_bus_manager_and_im_transports(tmp_path: Path) -> None:
    host_module = importlib.import_module("Turning-Good-Agent.gateway.host")
    runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
    settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
    weixin_module = importlib.import_module("Turning-Good-Agent.channels.weixin")
    settings = settings_module.Settings(data_dir=tmp_path / "state")
    settings.proactive.enabled = False
    runtime = runtime_module.AgentRuntime.create_default(settings, object())
    host = host_module.GatewayHost(settings, runtime=runtime)
    assert host.bus is host.turn_coordinator._bus
    assert host.weixin_transport.name == "weixin"
    assert host.feishu_transport.name == "feishu"
    assert host.channel_manager._transports["weixin"] is host.weixin_transport
    assert host.channel_manager._transports["feishu"] is host.feishu_transport
    presenter_type = getattr(weixin_module, "LocalIlinkQrPresenter", None)
    assert presenter_type is not None
    assert isinstance(host.weixin_transport._qr_presenter, presenter_type)


def test_guest_notification_subscriptions_never_include_owner_web_or_cli(tmp_path: Path) -> None:
    host_module = importlib.import_module("Turning-Good-Agent.gateway.host")
    runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
    settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
    settings = settings_module.Settings(data_dir=tmp_path / "state")
    settings.proactive.enabled = False
    runtime = runtime_module.AgentRuntime.create_default(settings, object())
    host = host_module.GatewayHost(settings, runtime=runtime)
    account = host.channel_registry.create_weixin_invitation(principal_id=None)
    account = host.channel_registry.update(replace(account, status="awaiting_first_dm"))
    host.channel_registry.update(
        replace(
            account,
            status="active",
            subscribed=True,
            private={**account.private, "conversation_id": account.id, "from_user_id": "private", "context_token": "private"},
        )
    )
    subscriptions = tuple(host._notification_subscriptions(account.principal_id))
    assert [(item.recipient.principal_id, item.recipient.channel) for item in subscriptions] == [(account.principal_id, "weixin")]

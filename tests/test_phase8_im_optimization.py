from __future__ import annotations

import asyncio
import importlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace


messages = importlib.import_module("Turning-Good-Agent.bus.messages")
registry_module = importlib.import_module("Turning-Good-Agent.channels.registry")
feishu_module = importlib.import_module("Turning-Good-Agent.channels.feishu")
feishu_ws_module = importlib.import_module("Turning-Good-Agent.channels.feishu_ws")

OutboundMessage = messages.OutboundMessage
Recipient = messages.Recipient


class FakeFeishuClient:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.card_calls = 0

    async def send_text(self, _account, _chat_id: str, content: str) -> bool:
        self.sent.append(content)
        return True

    async def update_card(self, *_args, **_kwargs) -> bool:
        self.card_calls += 1
        return True


def _transport(tmp_path: Path):
    registry = registry_module.ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
    account = registry.register_feishu_bot(
        app_id="app-1", app_secret="secret", domain="open.feishu.cn"
    )
    account = registry.update(
        replace(
            account,
            status="active",
            subscribed=True,
            private={**account.private, "owner_open_id": "owner", "connected": True},
        )
    )
    client = FakeFeishuClient()
    transport = feishu_module.FeishuTransport(
        registry,
        SimpleNamespace(accept=None),
        client,
    )
    transport._clients[account.id] = client
    transport._states[account.id] = SimpleNamespace(connected=True)
    return transport, account, client


def test_feishu_delta_is_ignored_and_terminal_reply_is_plain_text(tmp_path: Path) -> None:
    async def run() -> None:
        transport, account, client = _transport(tmp_path)
        recipient = Recipient("owner", "feishu", f"{account.id}:chat-1")

        assert await transport.send(
            OutboundMessage(
                "delta", "session", recipient=recipient, content="partial", event_type="response.delta"
            )
        ) is True
        assert client.sent == []
        assert client.card_calls == 0

        assert await transport.send(
            OutboundMessage(
                "complete", "session", recipient=recipient, content="final", event_type="response.completed"
            )
        ) is True
        assert client.sent == ["final"]
        assert client.card_calls == 0

    asyncio.run(run())


def test_feishu_client_has_no_cardkit_surface() -> None:
    client = feishu_ws_module.LarkFeishuClient()
    assert not hasattr(client, "update_card")
    assert not hasattr(feishu_ws_module, "_cardkit_supported")

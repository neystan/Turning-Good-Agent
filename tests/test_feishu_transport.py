from __future__ import annotations

import asyncio
import importlib
import inspect
import json
import logging
import queue
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

messages = importlib.import_module("Turning-Good-Agent.bus.messages")
registry_module = importlib.import_module("Turning-Good-Agent.channels.registry")
feishu_module = importlib.import_module("Turning-Good-Agent.channels.feishu")
ws_module = importlib.import_module("Turning-Good-Agent.channels.feishu_ws")
control_module = importlib.import_module("Turning-Good-Agent.web.backend.channel_control")

ChannelAccountRegistry = registry_module.ChannelAccountRegistry
OutboundMessage = messages.OutboundMessage
Recipient = messages.Recipient


def test_feishu_connection_state_exposes_only_connection_status() -> None:
    state = ws_module.FeishuConnectionState(True)
    assert state.connected is True
    with pytest.raises(TypeError):
        ws_module.FeishuConnectionState(True, False)


class FakeFeishu:
    def __init__(self) -> None:
        self.callbacks = {}
        self.states = {}
        self.sent_text: list[str] = []

    async def start_bot(self, account, on_event):
        self.callbacks[account.id] = on_event
        state = ws_module.FeishuConnectionState(True)
        self.states[account.id] = state
        return state

    async def stop_bot(self, account_id: str):
        self.callbacks.pop(account_id, None)

    async def send_text(self, account, chat_id: str, content: str) -> bool:
        self.sent_text.append(content)
        return True

    async def emit(
        self,
        account_id: str,
        sender: str,
        content: str,
        *,
        event_id: str = "e1",
        message_id: str | None = None,
        chat_type: str = "p2p",
        root_id: str | None = None,
        parent_id: str | None = None,
        thread_id: str | None = None,
        mention_count: int | None = None,
        raw_content: str | None = None,
    ):
        callback = self.callbacks[account_id]
        message_id = message_id or event_id
        message = {
            "chat_id": "chat-1",
            "chat_type": chat_type,
            "message_type": "text",
            "content": raw_content if raw_content is not None else '{"text": "' + content + '"}',
        }
        for key, value in {
            "root_id": root_id,
            "parent_id": parent_id,
            "thread_id": thread_id,
            "mention_count": mention_count,
        }.items():
            if value is not None:
                message[key] = value
        await callback(
            {
                "event_id": event_id,
                "message_id": message_id,
                "sender": {"open_id": sender},
                "message": message,
            }
        )


def _make(tmp_path: Path):
    registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
    account = registry.register_feishu_bot(app_id="app-" + str(tmp_path.name), app_secret="secret", domain="open.feishu.cn")
    fake = FakeFeishu()
    submitted: list[str] = []

    async def accept(message):
        submitted.append(message.content)
        return True

    transport = feishu_module.FeishuTransport(registry, type("C", (), {"accept": staticmethod(accept)})(), fake)
    return registry, account, fake, submitted, transport


def test_owner_code_locks_sender_and_only_locked_p2p_text_reaches_gateway(tmp_path: Path) -> None:
    async def run() -> None:
        registry, account, fake, submitted, transport = _make(tmp_path)
        code = transport.issue_owner_code(account.id, code="123456")
        await transport.start()
        await fake.emit(account.id, "wrong", "bad-code", event_id="bad")
        await fake.emit(account.id, "owner", "123456", event_id="lock")
        await fake.emit(account.id, "owner", "hello", event_id="ok")
        await fake.emit(account.id, "other", "intrude", event_id="drop")
        assert code == "123456"
        assert submitted == ["hello"]
        current = registry.get("feishu", account.id)
        assert current is not None
        assert current.private.get("owner_open_id") == "owner"
        await transport.close()

    asyncio.run(run())


def test_feishu_start_removes_legacy_cardkit_state(tmp_path: Path) -> None:
    async def run() -> None:
        registry, account, fake, _submitted, transport = _make(tmp_path)
        registry.update(replace(account, private={**account.private, "cardkit_enabled": True}))
        await transport.start()
        current = registry.get("feishu", account.id)
        assert current is not None
        assert "cardkit_enabled" not in current.private
        await transport.close()

    asyncio.run(run())


def test_reissued_owner_code_is_consumed_without_interrupting_active_owner(tmp_path: Path) -> None:
    async def run() -> None:
        registry, account, fake, submitted, transport = _make(tmp_path)
        transport.issue_owner_code(account.id, code="123456")
        await transport.start()
        await fake.emit(account.id, "owner", "123456", event_id="initial-lock")
        await fake.emit(account.id, "owner", "before", event_id="before")

        transport.issue_owner_code(account.id, code="654321")
        pending = registry.get("feishu", account.id)
        assert pending is not None
        assert pending.status == "active"
        assert pending.private.get("owner_open_id") == "owner"

        await fake.emit(account.id, "owner", "still active", event_id="during-reissue")
        await fake.emit(account.id, "other", "654321", event_id="attacker-code")
        await fake.emit(account.id, "owner", "000000", event_id="wrong-code")
        await fake.emit(account.id, "owner", "654321", event_id="fresh-code")
        await fake.emit(account.id, "owner", "after", event_id="after")

        assert submitted == ["before", "still active", "after"]
        current = registry.get("feishu", account.id)
        assert current is not None
        assert current.status == "active"
        assert current.private.get("owner_open_id") == "owner"
        assert "owner_code_hash" not in current.private
        assert "owner_code_expires_at" not in current.private
        await transport.close()

    asyncio.run(run())


def test_group_message_is_dropped(tmp_path: Path) -> None:
    async def run() -> None:
        registry, account, fake, submitted, transport = _make(tmp_path)
        transport.issue_owner_code(account.id, code="123456")
        await transport.start()
        await fake.emit(account.id, "owner", "123456", event_id="lock")
        await fake.emit(account.id, "owner", "hello", event_id="group", chat_type="group")
        assert submitted == []
        await transport.close()

    asyncio.run(run())


@pytest.mark.parametrize("chat_type", ["private", "direct"])
def test_non_p2p_private_aliases_are_dropped(tmp_path: Path, chat_type: str) -> None:
    async def run() -> None:
        registry, account, fake, submitted, transport = _make(tmp_path)
        transport.issue_owner_code(account.id, code="123456")
        await transport.start()
        await fake.emit(account.id, "owner", "123456", event_id="lock")
        await fake.emit(account.id, "owner", "alias", event_id=chat_type, chat_type=chat_type)
        assert submitted == []
        await transport.close()

    asyncio.run(run())


def test_malformed_text_payload_is_dropped(tmp_path: Path) -> None:
    async def run() -> None:
        registry, account, fake, submitted, transport = _make(tmp_path)
        transport.issue_owner_code(account.id, code="123456")
        await transport.start()
        await fake.emit(account.id, "owner", "123456", event_id="lock")
        await fake.emit(
            account.id,
            "owner",
            "ignored",
            event_id="malformed",
            raw_content="{not-json",
        )
        assert submitted == []
        await transport.close()

    asyncio.run(run())


def test_same_message_id_with_new_event_id_is_deduplicated(tmp_path: Path) -> None:
    async def run() -> None:
        registry, account, fake, submitted, transport = _make(tmp_path)
        transport.issue_owner_code(account.id, code="123456")
        await transport.start()
        await fake.emit(account.id, "owner", "123456", event_id="lock")
        await fake.emit(
            account.id,
            "owner",
            "hello",
            event_id="event-1",
            message_id="message-1",
        )
        await fake.emit(
            account.id,
            "owner",
            "duplicate",
            event_id="event-2",
            message_id="message-1",
        )

        assert submitted == ["hello"]
        await transport.close()

    asyncio.run(run())


def test_terminal_reply_uses_plain_text_without_card_streaming(tmp_path: Path) -> None:
    async def run() -> None:
        registry, account, fake, _submitted, transport = _make(tmp_path)
        current = registry.get("feishu", account.id)
        assert current is not None
        registry.update(
            registry_account := type(current)(
                id=current.id,
                platform=current.platform,
                principal_id=current.principal_id,
                status="active",
                enabled=True,
                subscribed=True,
                created_at=current.created_at,
                updated_at=current.updated_at,
                private={**current.private, "owner_open_id": "owner", "connected": True},
            )
        )
        del registry_account
        await transport.start()
        message = OutboundMessage(
            "reply-1",
            "opaque",
            recipient=Recipient("owner", "feishu", f"{account.id}:chat-1"),
            content="final",
            event_type="response.completed",
        )
        assert await transport.send(message) is True
        assert fake.sent_text == ["final"]
        assert not hasattr(fake, "update_card")
        await transport.close()

    asyncio.run(run())


def test_feishu_ignores_deltas_and_sends_terminal_text(tmp_path: Path) -> None:
    async def run() -> None:
        registry, account, fake, _submitted, transport = _make(tmp_path)
        current = registry.get("feishu", account.id)
        assert current is not None
        registry.update(
            replace(
                current,
                status="active",
                subscribed=True,
                private={
                    **current.private,
                    "owner_open_id": "owner",
                    "connected": True,
                },
            )
        )
        await transport.start()
        recipient = Recipient("owner", "feishu", f"{account.id}:chat-1")
        assert await transport.send(
            OutboundMessage(
                "delta",
                "opaque",
                recipient=recipient,
                content="partial",
                event_type="response.delta",
            )
        ) is True
        assert await transport.send(
            OutboundMessage(
                "completed",
                "opaque",
                recipient=recipient,
                content="partial final",
                event_type="response.completed",
            )
        ) is True

        assert not hasattr(fake, "update_card")
        assert fake.sent_text == ["partial final"]
        await transport.close()

    asyncio.run(run())


def test_proactive_notification_uses_final_text_without_cardkit(tmp_path: Path) -> None:
    async def run() -> None:
        registry, account, fake, _submitted, transport = _make(tmp_path)
        current = registry.get("feishu", account.id)
        assert current is not None
        registry.update(
            replace(
                current,
                status="active",
                subscribed=True,
                private={
                    **current.private,
                    "owner_open_id": "owner",
                    "connected": True,
                    "cardkit_enabled": True,
                },
            )
        )
        await transport.start()
        message = OutboundMessage(
            "proactive-1",
            "opaque",
            recipient=Recipient("owner", "feishu", f"{account.id}:chat-1"),
            content="scheduled result",
            event_type="proactive.cron.completed",
            disposition="proactive_notification",
        )

        assert await transport.send(message) is True
        assert fake.sent_text == ["scheduled result"]
        assert not hasattr(fake, "update_card")
        await transport.close()

    asyncio.run(run())


def test_proactive_stream_tool_and_status_events_are_suppressed(tmp_path: Path) -> None:
    async def run() -> None:
        registry, account, fake, _submitted, transport = _make(tmp_path)
        current = registry.get("feishu", account.id)
        assert current is not None
        registry.update(
            replace(
                current,
                status="active",
                subscribed=True,
                private={
                    **current.private,
                    "owner_open_id": "owner",
                    "connected": True,
                },
            )
        )
        await transport.start()
        recipient = Recipient("owner", "feishu", f"{account.id}:chat-1")
        for event_type in ("response.delta", "response.status", "tool.call"):
            message = OutboundMessage(
                f"proactive-{event_type}",
                "opaque",
                recipient=recipient,
                content="not-final",
                event_type=event_type,
                disposition="proactive_notification",
            )
            assert await transport.send(message) is False
        assert fake.sent_text == []
        assert not hasattr(fake, "update_card")
        await transport.close()

    asyncio.run(run())


def test_feishu_inbound_metadata_excludes_private_platform_identifiers(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        account = registry.register_feishu_bot(
            app_id="app",
            app_secret="secret",
            domain="open.feishu.cn",
        )
        fake = FakeFeishu()
        accepted = []

        async def accept(message) -> bool:
            accepted.append(message)
            return True

        transport = feishu_module.FeishuTransport(registry, SimpleNamespace(accept=accept), fake)
        transport.issue_owner_code(account.id, code="123456")
        await transport.start()
        await fake.emit(account.id, "owner", "123456", event_id="lock")
        await fake.emit(account.id, "owner", "hello", event_id="external-event")

        assert len(accepted) == 1
        assert accepted[0].metadata["event_id"] == "external-event"
        assert accepted[0].metadata["message_id"] == "external-event"
        assert accepted[0].metadata["instance_id"] == account.id
        assert "owner" not in repr(accepted[0].metadata)
        assert "secret" not in repr(accepted[0].metadata)
        await transport.close()

    asyncio.run(run())


def test_feishu_inbound_metadata_preserves_thread_summary_only(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        account = registry.register_feishu_bot(
            app_id="app",
            app_secret="secret",
            domain="open.feishu.cn",
        )
        fake = FakeFeishu()
        accepted = []

        async def accept(message) -> bool:
            accepted.append(message)
            return True

        transport = feishu_module.FeishuTransport(registry, SimpleNamespace(accept=accept), fake)
        transport.issue_owner_code(account.id, code="123456")
        await transport.start()
        await fake.emit(account.id, "owner", "123456", event_id="lock")
        await fake.emit(
            account.id,
            "owner",
            "threaded",
            event_id="event-2",
            message_id="message-2",
            root_id="root-1",
            parent_id="parent-1",
            thread_id="thread-1",
            mention_count=2,
        )

        assert len(accepted) == 1
        assert accepted[0].metadata == {
            "platform": "feishu",
            "instance_id": account.id,
            "session_type": "p2p",
            "event_id": "event-2",
            "message_id": "message-2",
            "root_id": "root-1",
            "parent_id": "parent-1",
            "thread_id": "thread-1",
            "mention_count": 2,
        }
        assert "owner" not in repr(accepted[0].metadata)
        await transport.close()

    asyncio.run(run())


class CredentialAwareFakeFeishu(FakeFeishu):
    async def start_bot(self, account, on_event):
        self.callbacks[account.id] = on_event
        connected = account.private.get("app_secret") != "bad-secret"
        state = ws_module.FeishuConnectionState(connected)
        self.states[account.id] = state
        return state


def test_failed_reload_with_shared_client_keeps_old_bot_running(tmp_path: Path) -> None:
    class SharedCredentialClient(CredentialAwareFakeFeishu):
        def __init__(self) -> None:
            super().__init__()
            self.stopped: list[str] = []

        async def stop_bot(self, account_id: str):
            self.stopped.append(account_id)
            await super().stop_bot(account_id)

    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        account = registry.register_feishu_bot(
            app_id="old-app",
            app_secret="old-secret",
            domain="open.feishu.cn",
        )
        client = SharedCredentialClient()
        transport = feishu_module.FeishuTransport(
            registry,
            SimpleNamespace(accept=lambda _message: True),
            client,
        )
        await transport.start()
        live_account = registry.get("feishu", account.id)
        assert live_account is not None
        candidate = replace(live_account, private={**live_account.private, "app_secret": "bad-secret"})

        assert await transport.reload_account(account.id, candidate) is False
        assert client.stopped == []
        assert transport._clients[account.id] is client
        assert registry.get("feishu", account.id) == live_account
        await transport.close()

    asyncio.run(run())


def test_failed_feishu_credential_reload_preserves_old_connection_and_registry(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        account = registry.register_feishu_bot(
            app_id="old-app",
            app_secret="old-secret",
            domain="open.feishu.cn",
        )
        account = registry.update(
            replace(
                account,
                status="active",
                private={**account.private, "owner_open_id": "owner", "conversation_id": f"{account.id}:chat-1"},
            )
        )
        clients: list[CredentialAwareFakeFeishu] = []

        def client_factory() -> CredentialAwareFakeFeishu:
            client = CredentialAwareFakeFeishu()
            clients.append(client)
            return client

        async def accept(_message):
            return True

        transport = feishu_module.FeishuTransport(
            registry,
            type("C", (), {"accept": staticmethod(accept)})(),
            client_factory=client_factory,
        )
        service = control_module.ChannelControlService(
            registry,
            feishu_transport=transport,
            owner_principal_id="owner",
        )
        await transport.start()
        old_client = clients[0]

        with pytest.raises(ValueError, match="旧连接仍保留"):
            await service.update_feishu_credentials(
                account.id,
                app_id="new-app",
                app_secret="bad-secret",
                domain="open.feishu.cn",
            )

        preserved = registry.get("feishu", account.id)
        assert preserved is not None
        assert preserved.private.get("app_id") == "old-app"
        assert preserved.private.get("app_secret") == "old-secret"
        message = OutboundMessage(
            "reply-after-failed-reload",
            "opaque",
            recipient=Recipient("owner", "feishu", f"{account.id}:chat-1"),
            content="still connected",
            event_type="response.completed",
        )
        assert await transport.send(message) is True
        assert old_client.sent_text == ["still connected"]
        await transport.close()

    asyncio.run(run())


def test_feishu_app_id_rotation_requires_new_owner_binding(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        account = registry.register_feishu_bot(
            app_id="old-app",
            app_secret="old-secret",
            domain="open.feishu.cn",
        )
        account = registry.update(
            replace(
                account,
                status="active",
                private={
                    **account.private,
                    "owner_open_id": "old-owner",
                    "conversation_id": f"{account.id}:old-chat",
                },
            )
        )
        clients: list[CredentialAwareFakeFeishu] = []

        def client_factory() -> CredentialAwareFakeFeishu:
            client = CredentialAwareFakeFeishu()
            clients.append(client)
            return client

        transport = feishu_module.FeishuTransport(
            registry,
            SimpleNamespace(accept=lambda _message: True),
            client_factory=client_factory,
        )
        service = control_module.ChannelControlService(
            registry,
            feishu_transport=transport,
            owner_principal_id="owner",
        )
        await transport.start()

        view = await service.update_feishu_credentials(
            account.id,
            app_id="new-app",
            app_secret="new-secret",
            domain="open.feishu.cn",
        )

        assert view.status == "awaiting_owner_code"
        current = registry.get("feishu", account.id)
        assert current is not None
        assert current.private["app_id"] == "new-app"
        assert "owner_open_id" not in current.private
        assert "conversation_id" not in current.private
        await transport.close()

    asyncio.run(run())


def test_failed_previous_stop_keeps_old_client_and_credentials(tmp_path: Path) -> None:
    class FailingStopClient(CredentialAwareFakeFeishu):
        def __init__(self) -> None:
            super().__init__()
            self.stop_attempts = 0

        async def stop_bot(self, account_id: str) -> None:
            self.stop_attempts += 1
            if self.stop_attempts == 1:
                raise RuntimeError("old connection did not stop")
            await super().stop_bot(account_id)

    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        account = registry.register_feishu_bot(
            app_id="old-app",
            app_secret="old-secret",
            domain="open.feishu.cn",
        )
        clients: list[CredentialAwareFakeFeishu] = []

        def client_factory() -> CredentialAwareFakeFeishu:
            client = FailingStopClient() if not clients else CredentialAwareFakeFeishu()
            clients.append(client)
            return client

        transport = feishu_module.FeishuTransport(
            registry,
            SimpleNamespace(accept=lambda _message: True),
            client_factory=client_factory,
        )
        await transport.start()
        live_account = registry.get("feishu", account.id)
        assert live_account is not None
        candidate = replace(
            live_account,
            private={**live_account.private, "app_secret": "new-secret"},
        )

        assert await transport.reload_account(account.id, candidate) is False
        assert transport._clients[account.id] is clients[0]
        assert clients[1].callbacks == {}
        current = registry.get("feishu", account.id)
        assert current is not None
        assert current.private["app_secret"] == "old-secret"

        await transport.close()
        assert clients[0].callbacks == {}

    asyncio.run(run())


def test_failed_registry_commit_keeps_old_feishu_connection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        account = registry.register_feishu_bot(
            app_id="old-app",
            app_secret="old-secret",
            domain="open.feishu.cn",
        )
        clients: list[CredentialAwareFakeFeishu] = []

        def client_factory() -> CredentialAwareFakeFeishu:
            client = CredentialAwareFakeFeishu()
            clients.append(client)
            return client

        transport = feishu_module.FeishuTransport(
            registry,
            SimpleNamespace(accept=lambda _message: True),
            client_factory=client_factory,
        )
        await transport.start()
        current = registry.get("feishu", account.id)
        assert current is not None
        original_update = registry.update

        def reject_candidate(updated, *, expected_updated_at=None):
            del expected_updated_at
            if updated.private.get("app_secret") == "new-secret":
                raise OSError("registry write failed")
            return original_update(updated)

        monkeypatch.setattr(registry, "update", reject_candidate)
        candidate = replace(current, private={**current.private, "app_secret": "new-secret"})

        assert await transport.reload_account(account.id, candidate) is False
        assert transport._clients[account.id] is clients[0]
        assert account.id in clients[0].callbacks
        assert clients[1].callbacks == {}
        assert registry.get("feishu", account.id) == current
        await transport.close()

    asyncio.run(run())


def test_feishu_reload_does_not_overwrite_concurrent_disable(tmp_path: Path) -> None:
    class BlockingCandidateClient(CredentialAwareFakeFeishu):
        def __init__(self, started: asyncio.Event, release: asyncio.Event) -> None:
            super().__init__()
            self.started = started
            self.release = release

        async def start_bot(self, account, on_event):
            await super().start_bot(account, on_event)
            self.started.set()
            await self.release.wait()
            return self.states[account.id]

    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        account = registry.register_feishu_bot(
            app_id="app",
            app_secret="old-secret",
            domain="open.feishu.cn",
        )
        candidate_started = asyncio.Event()
        release_candidate = asyncio.Event()
        clients: list[CredentialAwareFakeFeishu] = []

        def client_factory() -> CredentialAwareFakeFeishu:
            client: CredentialAwareFakeFeishu
            if clients:
                client = BlockingCandidateClient(candidate_started, release_candidate)
            else:
                client = CredentialAwareFakeFeishu()
            clients.append(client)
            return client

        transport = feishu_module.FeishuTransport(
            registry,
            SimpleNamespace(accept=lambda _message: True),
            client_factory=client_factory,
        )
        service = control_module.ChannelControlService(
            registry,
            feishu_transport=transport,
            owner_principal_id="owner",
        )
        await transport.start()

        reload_task = asyncio.create_task(
            service.update_feishu_credentials(
                account.id,
                app_id="app",
                app_secret="new-secret",
                domain="open.feishu.cn",
            )
        )
        await candidate_started.wait()
        disable_task = asyncio.create_task(service.disable("feishu", account.id))
        await asyncio.sleep(0)
        disabled = registry.get("feishu", account.id)
        assert disabled is not None
        assert disabled.status == "disabled"
        release_candidate.set()

        with pytest.raises(ValueError, match="旧连接仍保留"):
            await reload_task
        await disable_task

        current = registry.get("feishu", account.id)
        assert current is not None
        assert current.status == "disabled"
        assert current.private["app_secret"] == "old-secret"
        assert account.id not in transport._clients
        await transport.close()

    asyncio.run(run())


def test_feishu_close_waits_for_inflight_reload_and_stops_candidate(tmp_path: Path) -> None:
    class BlockingCandidateClient(CredentialAwareFakeFeishu):
        def __init__(self, started: asyncio.Event, release: asyncio.Event) -> None:
            super().__init__()
            self.started = started
            self.release = release

        async def start_bot(self, account, on_event):
            await super().start_bot(account, on_event)
            self.started.set()
            await self.release.wait()
            return self.states[account.id]

    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        account = registry.register_feishu_bot(
            app_id="app",
            app_secret="old-secret",
            domain="open.feishu.cn",
        )
        candidate_started = asyncio.Event()
        release_candidate = asyncio.Event()
        clients: list[CredentialAwareFakeFeishu] = []

        def client_factory() -> CredentialAwareFakeFeishu:
            client: CredentialAwareFakeFeishu
            if clients:
                client = BlockingCandidateClient(candidate_started, release_candidate)
            else:
                client = CredentialAwareFakeFeishu()
            clients.append(client)
            return client

        transport = feishu_module.FeishuTransport(
            registry,
            SimpleNamespace(accept=lambda _message: True),
            client_factory=client_factory,
        )
        await transport.start()
        current = registry.get("feishu", account.id)
        assert current is not None
        reload_task = asyncio.create_task(
            transport.reload_account(
                account.id,
                replace(current, private={**current.private, "app_secret": "new-secret"}),
            )
        )
        await candidate_started.wait()
        close_task = asyncio.create_task(transport.close())
        await asyncio.sleep(0)
        release_candidate.set()

        assert await reload_task is True
        await close_task

        assert account.id not in transport._clients
        assert clients[1].callbacks == {}

    asyncio.run(run())


def test_feishu_transport_close_retries_failed_bot_shutdown(tmp_path: Path) -> None:
    class FailingStopClient(FakeFeishu):
        def __init__(self) -> None:
            super().__init__()
            self.stop_attempts = 0

        async def stop_bot(self, account_id: str) -> None:
            self.stop_attempts += 1
            if self.stop_attempts == 1:
                raise RuntimeError("connection close failed")
            await super().stop_bot(account_id)

    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        account = registry.register_feishu_bot(
            app_id="first-app",
            app_secret="first-secret",
            domain="open.feishu.cn",
        )
        client = FailingStopClient()
        transport = feishu_module.FeishuTransport(
            registry,
            SimpleNamespace(accept=lambda _message: True),
            client,
        )
        await transport.start()

        await transport.close()
        assert account.id in client.callbacks
        await transport.close()

        assert client.stop_attempts == 2
        assert client.callbacks == {}

    asyncio.run(run())


def test_duplicate_app_id_is_rejected_before_candidate_bot_starts(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        first = registry.register_feishu_bot(
            app_id="first-app",
            app_secret="first-secret",
            domain="open.feishu.cn",
        )
        second = registry.register_feishu_bot(
            app_id="second-app",
            app_secret="second-secret",
            domain="open.feishu.cn",
        )
        clients: list[CredentialAwareFakeFeishu] = []

        def client_factory() -> CredentialAwareFakeFeishu:
            client = CredentialAwareFakeFeishu()
            clients.append(client)
            return client

        transport = feishu_module.FeishuTransport(
            registry,
            SimpleNamespace(accept=lambda _message: True),
            client_factory=client_factory,
        )
        service = control_module.ChannelControlService(
            registry,
            feishu_transport=transport,
            owner_principal_id="owner",
        )
        await transport.start()
        started_before_rotation = len(clients)

        with pytest.raises(ValueError, match="app_id"):
            await service.update_feishu_credentials(
                second.id,
                app_id=str(first.private["app_id"]),
                app_secret="candidate-secret",
                domain="open.feishu.cn",
            )

        assert len(clients) == started_before_rotation
        preserved = registry.get("feishu", second.id)
        assert preserved is not None
        assert preserved.private["app_id"] == "second-app"
        assert preserved.private["app_secret"] == "second-secret"
        await transport.close()

    asyncio.run(run())


class RecordingBuilder:
    def __init__(self, *, factory=None) -> None:
        self.values: dict[str, object] = {}
        self.factory = factory

    def __getattr__(self, name: str):
        def set_value(value):
            self.values[name] = value
            return self

        return set_value

    def build(self):
        if self.factory is not None:
            return self.factory(self.values)
        return SimpleNamespace(**self.values)


class RecordingModel:
    @staticmethod
    def builder() -> RecordingBuilder:
        return RecordingBuilder()


class RecordingRestClient:
    def __init__(self) -> None:
        self.auth_requests: list[object] = []
        self.message_requests: list[object] = []

        async def validate(request):
            self.auth_requests.append(request)
            return SimpleNamespace(code=0, success=lambda: True)

        async def create_message(request):
            self.message_requests.append(request)
            return SimpleNamespace(code=0, success=lambda: True)

        self.auth = SimpleNamespace(
            v3=SimpleNamespace(tenant_access_token=SimpleNamespace(ainternal=validate))
        )
        self.im = SimpleNamespace(
            v1=SimpleNamespace(message=SimpleNamespace(acreate=create_message))
        )


class RecordingSdk:
    def __init__(self) -> None:
        self.rest_client = RecordingRestClient()
        self.ws_clients: list[object] = []

        sdk = self

        class EventDispatcherHandler:
            @staticmethod
            def builder(_encrypt_key: str, _verification_token: str) -> RecordingBuilder:
                builder = RecordingBuilder()

                def register(handler):
                    builder.values["message_handler"] = handler
                    return builder

                builder.register_p2_im_message_receive_v1 = register
                return builder

        class WsClient:
            def __init__(self, app_id, app_secret, *, event_handler, domain) -> None:
                self.app_id = app_id
                self.app_secret = app_secret
                self.event_handler = event_handler
                self.domain = domain
                self.started = False
                sdk.ws_clients.append(self)

            def start(self) -> None:
                self.started = True

        class Client:
            @staticmethod
            def builder() -> RecordingBuilder:
                return RecordingBuilder(factory=lambda _values: sdk.rest_client)

        self.root = SimpleNamespace(
            EventDispatcherHandler=EventDispatcherHandler,
            ws=SimpleNamespace(Client=WsClient),
            Client=Client,
        )
        self.auth_v3 = SimpleNamespace(
            InternalTenantAccessTokenRequest=RecordingModel,
            InternalTenantAccessTokenRequestBody=RecordingModel,
        )
        self.im_v1 = SimpleNamespace(
            CreateMessageRequest=RecordingModel,
            CreateMessageRequestBody=RecordingModel,
        )


class RecordingWsConnection:
    def __init__(self, ws_client, *, fail_first_close: bool = False) -> None:
        self.ws_client = ws_client
        self.started = False
        self.closed = False
        self.close_attempts = 0
        self.fail_first_close = fail_first_close

    async def start(self, _on_event) -> bool:
        self.ws_client.start()
        self.started = True
        return True

    async def close(self) -> None:
        self.close_attempts += 1
        if self.fail_first_close and self.close_attempts == 1:
            raise RuntimeError("connection close failed")
        self.closed = True


class StateAwareRecordingWsConnection(RecordingWsConnection):
    def __init__(self, ws_client) -> None:
        super().__init__(ws_client)
        self.state_handler = None

    def set_state_change_handler(self, handler) -> None:
        self.state_handler = handler

    async def emit_state(self, state) -> None:
        assert self.state_handler is not None
        result = self.state_handler(state)
        if asyncio.iscoroutine(result):
            await result


def test_lark_client_propagates_connection_state_failure(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        account = registry.register_feishu_bot(
            app_id="state-app",
            app_secret="state-secret",
            domain="open.feishu.cn",
        )
        sdk = RecordingSdk()
        connections: list[StateAwareRecordingWsConnection] = []
        states: list[ws_module.FeishuConnectionState] = []

        def connection_factory(lark, app_id, app_secret, domain, dispatcher):
            connection = StateAwareRecordingWsConnection(
                lark.ws.Client(
                    app_id,
                    app_secret,
                    event_handler=dispatcher,
                    domain=domain,
                )
            )
            connections.append(connection)
            return connection

        client = ws_module.LarkFeishuClient(
            sdk_loader=lambda: (sdk.root, sdk.auth_v3, sdk.im_v1),
            connection_factory=connection_factory,
        )
        client.set_state_handler(account.id, lambda state: states.append(state))
        assert (await client.start_bot(account, lambda _event: asyncio.sleep(0))).connected is True
        await connections[0].emit_state(ws_module.FeishuConnectionState(False))

        assert states == [ws_module.FeishuConnectionState(False)]
        await client.stop_bot(account.id)

    asyncio.run(run())


def test_feishu_transport_marks_account_failed_after_connection_exhaustion(tmp_path: Path) -> None:
    class StateAwareFakeFeishu(FakeFeishu):
        def __init__(self) -> None:
            super().__init__()
            self.state_handler = None

        def set_state_handler(self, _account_id: str, handler) -> None:
            self.state_handler = handler

        async def emit_state(self, state) -> None:
            assert self.state_handler is not None
            result = self.state_handler(state)
            if asyncio.iscoroutine(result):
                await result

    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        account = registry.register_feishu_bot(
            app_id="state-app",
            app_secret="state-secret",
            domain="open.feishu.cn",
        )
        account = registry.update(
            replace(
                account,
                status="active",
                private={
                    **account.private,
                    "owner_open_id": "owner",
                    "conversation_id": f"{account.id}:chat",
                },
            )
        )
        client = StateAwareFakeFeishu()
        transport = feishu_module.FeishuTransport(
            registry,
            SimpleNamespace(accept=lambda _message: True),
            client,
        )
        await transport.start()
        assert transport._clients[account.id] is client
        assert client.state_handler is not None
        await client.emit_state(ws_module.FeishuConnectionState(False))

        current = registry.get("feishu", account.id)
        assert current is not None
        assert current.status == "failed"
        assert current.private["connected"] is False
        assert transport.is_deliverable(Recipient("owner", "feishu", f"{account.id}:chat")) is False
        await transport.close()

    asyncio.run(run())


def test_lark_client_uses_independent_stoppable_ws_connections(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        first = registry.register_feishu_bot(
            app_id="first-app",
            app_secret="first-secret",
            domain="open.feishu.cn",
        )
        second = registry.register_feishu_bot(
            app_id="second-app",
            app_secret="second-secret",
            domain="open.feishu.cn",
        )
        sdk = RecordingSdk()
        connections: list[RecordingWsConnection] = []

        def connection_factory(lark, app_id, app_secret, domain, dispatcher):
            connection = RecordingWsConnection(
                lark.ws.Client(
                    app_id,
                    app_secret,
                    event_handler=dispatcher,
                    domain=domain,
                )
            )
            connections.append(connection)
            return connection

        client = ws_module.LarkFeishuClient(
            sdk_loader=lambda: (sdk.root, sdk.auth_v3, sdk.im_v1),
            connection_factory=connection_factory,
        )

        assert (await client.start_bot(first, lambda _event: asyncio.sleep(0))).connected is True
        assert (await client.start_bot(second, lambda _event: asyncio.sleep(0))).connected is True
        assert len(connections) == 2
        assert all(connection.started for connection in connections)

        assert (await client.start_bot(first, lambda _event: asyncio.sleep(0))).connected is True
        assert len(connections) == 3
        assert connections[0].closed is True
        assert connections[1].closed is False
        assert connections[2].closed is False

        await client.stop_bot(first.id)
        assert connections[2].closed is True
        assert connections[1].closed is False
        await client.stop_bot(second.id)
        assert connections[1].closed is True

    asyncio.run(run())


def test_lark_client_keeps_previous_connection_reachable_if_replacement_stop_fails(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        account = registry.register_feishu_bot(
            app_id="first-app",
            app_secret="first-secret",
            domain="open.feishu.cn",
        )
        sdk = RecordingSdk()
        connections: list[RecordingWsConnection] = []

        def connection_factory(lark, app_id, app_secret, domain, dispatcher):
            connection = RecordingWsConnection(
                lark.ws.Client(
                    app_id,
                    app_secret,
                    event_handler=dispatcher,
                    domain=domain,
                ),
                fail_first_close=not connections,
            )
            connections.append(connection)
            return connection

        client = ws_module.LarkFeishuClient(
            sdk_loader=lambda: (sdk.root, sdk.auth_v3, sdk.im_v1),
            connection_factory=connection_factory,
        )

        assert (await client.start_bot(account, lambda _event: asyncio.sleep(0))).connected is True
        assert (await client.start_bot(account, lambda _event: asyncio.sleep(0))).connected is False
        assert connections[1].closed is True

        await client.stop_bot(account.id)

        assert connections[0].close_attempts == 2
        assert connections[0].closed is True

    asyncio.run(run())


def test_lark_client_keeps_connection_reachable_after_direct_stop_failure(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        account = registry.register_feishu_bot(
            app_id="first-app",
            app_secret="first-secret",
            domain="open.feishu.cn",
        )
        sdk = RecordingSdk()
        connections: list[RecordingWsConnection] = []

        def connection_factory(lark, app_id, app_secret, domain, dispatcher):
            connection = RecordingWsConnection(
                lark.ws.Client(
                    app_id,
                    app_secret,
                    event_handler=dispatcher,
                    domain=domain,
                ),
                fail_first_close=True,
            )
            connections.append(connection)
            return connection

        client = ws_module.LarkFeishuClient(
            sdk_loader=lambda: (sdk.root, sdk.auth_v3, sdk.im_v1),
            connection_factory=connection_factory,
        )
        assert (await client.start_bot(account, lambda _event: asyncio.sleep(0))).connected is True

        with pytest.raises(RuntimeError, match="connection close failed"):
            await client.stop_bot(account.id)
        await client.stop_bot(account.id)

        assert connections[0].close_attempts == 2
        assert connections[0].closed is True

    asyncio.run(run())


def test_lark_ws_process_reports_ready_and_failed_control_states(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Sender:
        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []
            self.closed = False

        def send(self, message: dict[str, object]) -> None:
            self.messages.append(message)

        def close(self) -> None:
            self.closed = True

    class DispatcherBuilder:
        def register_p2_im_message_receive_v1(self, _handler):
            return self

        def build(self):
            return object()

    class Dispatcher:
        @staticmethod
        def builder(_encrypt_key: str, _verification_token: str):
            return DispatcherBuilder()

    class FailingWsClient:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def start(self) -> None:
            raise RuntimeError("private websocket failure")

    fake_lark = SimpleNamespace(
        EventDispatcherHandler=Dispatcher,
        LogLevel=SimpleNamespace(CRITICAL=50),
        ws=SimpleNamespace(Client=FailingWsClient),
    )
    monkeypatch.setattr(ws_module, "_load_lark_sdk", lambda: (fake_lark, None, None, None))
    sender = Sender()

    ws_module._run_lark_ws_process("app", "secret", "https://domain", sender)

    assert sender.messages == [{"__tga_feishu_process__": "failed"}]
    assert sender.closed is True


def test_lark_ws_process_reports_ready_only_after_sdk_connection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Sender:
        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        def send(self, message: dict[str, object]) -> None:
            self.messages.append(message)

        def close(self) -> None:
            return None

    class DispatcherBuilder:
        def register_p2_im_message_receive_v1(self, _handler):
            return self

        def build(self):
            return object()

    class Dispatcher:
        @staticmethod
        def builder(_encrypt_key: str, _verification_token: str):
            return DispatcherBuilder()

    class ConnectingWsClient:
        def __init__(self, *_args, **_kwargs) -> None:
            self.connected = False

        def start(self) -> None:
            self.connected = True
            raise RuntimeError("connection rejected")

    fake_lark = SimpleNamespace(
        EventDispatcherHandler=Dispatcher,
        LogLevel=SimpleNamespace(CRITICAL=50),
        ws=SimpleNamespace(Client=ConnectingWsClient),
    )
    monkeypatch.setattr(ws_module, "_load_lark_sdk", lambda: (fake_lark, None, None, None))
    sender = Sender()

    ws_module._run_lark_ws_process("app", "secret", "https://domain", sender)

    assert sender.messages == [{"__tga_feishu_process__": "failed"}]


def test_lark_ws_process_forwards_sdk_reconnect_lifecycle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Sender:
        def __init__(self) -> None:
            self.messages: list[dict[str, object]] = []

        def send(self, message: dict[str, object]) -> None:
            self.messages.append(message)

        def close(self) -> None:
            return None

    class DispatcherBuilder:
        def register_p2_im_message_receive_v1(self, _handler):
            return self

        def build(self):
            return object()

    class Dispatcher:
        @staticmethod
        def builder(_encrypt_key: str, _verification_token: str):
            return DispatcherBuilder()

    class HookedWsClient:
        def __init__(self, *_args, **_kwargs) -> None:
            self._conn = None
            self.on_reconnecting = None
            self.on_reconnected = None

        async def _connect(self) -> None:
            self._conn = object()

        def start(self) -> None:
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(self._connect())
                self.on_reconnecting()
                self.on_reconnected()
            finally:
                loop.close()

    fake_lark = SimpleNamespace(
        EventDispatcherHandler=Dispatcher,
        LogLevel=SimpleNamespace(CRITICAL=50),
        ws=SimpleNamespace(Client=HookedWsClient),
    )
    monkeypatch.setattr(ws_module, "_load_lark_sdk", lambda: (fake_lark, None, None, None))
    sender = Sender()

    ws_module._run_lark_ws_process("app", "secret", "https://domain", sender)

    assert [message["__tga_feishu_process__"] for message in sender.messages] == [
        "ready",
        "disconnected",
        "ready",
        "stopped",
    ]


class ProcessPipeEndpoint:
    def __init__(self, messages: queue.Queue[dict[str, object]]) -> None:
        self.messages = messages
        self.closed = False
        self._received: dict[str, object] | None = None

    def poll(self, timeout: float) -> bool:
        if self._received is not None:
            return True
        try:
            self._received = self.messages.get(timeout=timeout)
        except queue.Empty:
            return False
        return True

    def recv(self) -> dict[str, object]:
        if self._received is None:
            raise EOFError
        received = self._received
        self._received = None
        return received

    def close(self) -> None:
        if self.closed:
            raise RuntimeError("pipe already closed")
        self.closed = True


class ProcessPipeSender:
    def __init__(self, messages: queue.Queue[dict[str, object]]) -> None:
        self.messages = messages
        self.closed = False

    def send(self, message: dict[str, object]) -> None:
        if self.closed:
            raise BrokenPipeError
        self.messages.put(message)

    def close(self) -> None:
        self.closed = True


class RecordingProcess:
    def __init__(self, sender: ProcessPipeSender, mode: str) -> None:
        self.sender = sender
        self.mode = mode
        self.alive = True
        self.terminated = False
        self.closed = False
        self.close_attempts = 0

    def start(self) -> None:
        status = "ready" if self.mode in {"ready-exit", "ready-no-close-failure"} else (
            "failed" if self.mode == "failed-close" else self.mode
        )
        self.sender.send({"__tga_feishu_process__": status})
        if self.mode in {"ready-exit", "failed-close"}:
            self.alive = False

    def is_alive(self) -> bool:
        return self.alive

    def terminate(self) -> None:
        self.terminated = True
        self.alive = False

    def join(self, _timeout: float) -> None:
        return None

    def kill(self) -> None:
        self.alive = False

    def close(self) -> None:
        self.close_attempts += 1
        if self.mode in {"ready", "failed-close"} and self.close_attempts == 1:
            raise RuntimeError("process handle close failed")
        self.closed = True


class RecordingProcessContext:
    def __init__(self, modes: list[str]) -> None:
        self.modes = modes
        self.processes: list[RecordingProcess] = []

    def Pipe(self, *, duplex: bool):
        del duplex
        messages: queue.Queue[dict[str, object]] = queue.Queue()
        return ProcessPipeEndpoint(messages), ProcessPipeSender(messages)

    def Process(self, *, target, args, daemon, name):
        del target, daemon, name
        process = RecordingProcess(args[-1], self.modes.pop(0))
        self.processes.append(process)
        return process


def test_process_ws_rejects_child_failed_handshake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        context = RecordingProcessContext(["failed"])
        monkeypatch.setattr(ws_module.multiprocessing, "get_context", lambda _name: context)
        connection = ws_module._ProcessWsConnection("account", "app", "secret", "domain")

        assert await connection.start(lambda _event: asyncio.sleep(0)) is False
        assert context.processes[0].terminated is True

    asyncio.run(run())


def test_process_ws_failed_handshake_close_failure_is_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        context = RecordingProcessContext(["failed-close"])
        monkeypatch.setattr(ws_module.multiprocessing, "get_context", lambda _name: context)
        connection = ws_module._ProcessWsConnection("account", "app", "secret", "domain")

        assert await connection.start(lambda _event: asyncio.sleep(0)) is False
        await connection.close()

        assert context.processes[0].close_attempts == 2
        assert context.processes[0].closed is True

    asyncio.run(run())


def test_process_ws_close_can_retry_after_process_handle_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        context = RecordingProcessContext(["ready"])
        monkeypatch.setattr(ws_module.multiprocessing, "get_context", lambda _name: context)
        connection = ws_module._ProcessWsConnection("account", "app", "secret", "domain")

        assert await connection.start(lambda _event: asyncio.sleep(0)) is True
        with pytest.raises(RuntimeError, match="process handle close failed"):
            await connection.close()
        await connection.close()

        assert context.processes[0].close_attempts == 2
        assert context.processes[0].closed is True

    asyncio.run(run())


def test_process_ws_reconnects_after_ready_child_exit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        context = RecordingProcessContext(["ready-exit", "ready-no-close-failure"])
        monkeypatch.setattr(ws_module.multiprocessing, "get_context", lambda _name: context)
        monkeypatch.setattr(ws_module, "_PROCESS_RECONNECT_DELAYS_SECONDS", (0.01,))
        connection = ws_module._ProcessWsConnection("account", "app", "secret", "domain")

        assert await connection.start(lambda _event: asyncio.sleep(0)) is True
        for _ in range(20):
            if len(context.processes) == 2:
                break
            await asyncio.sleep(0.02)

        assert len(context.processes) == 2
        await connection.close()

    asyncio.run(run())


def test_process_ws_reports_disconnect_and_reconnect_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        context = RecordingProcessContext(["ready-exit", "ready-no-close-failure"])
        monkeypatch.setattr(ws_module.multiprocessing, "get_context", lambda _name: context)
        monkeypatch.setattr(ws_module, "_PROCESS_RECONNECT_DELAYS_SECONDS", (0.01,))
        states: list[ws_module.FeishuConnectionState] = []

        async def on_state_change(state: ws_module.FeishuConnectionState) -> None:
            states.append(state)

        connection = ws_module._ProcessWsConnection(
            "account",
            "app",
            "secret",
            "domain",
            on_state_change=on_state_change,
        )

        assert await connection.start(lambda _event: asyncio.sleep(0)) is True
        for _ in range(20):
            if len(context.processes) == 2:
                break
            await asyncio.sleep(0.02)

        assert len(context.processes) == 2
        assert states[0] == ws_module.FeishuConnectionState(False)
        assert states[-1] == ws_module.FeishuConnectionState(True)
        await connection.close()

    asyncio.run(run())


def test_process_ws_explicit_close_does_not_reconnect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        context = RecordingProcessContext(["ready-no-close-failure"])
        monkeypatch.setattr(ws_module.multiprocessing, "get_context", lambda _name: context)
        monkeypatch.setattr(ws_module, "_PROCESS_RECONNECT_DELAYS_SECONDS", (0.01,))
        connection = ws_module._ProcessWsConnection("account", "app", "secret", "domain")

        assert await connection.start(lambda _event: asyncio.sleep(0)) is True
        await connection.close()
        await asyncio.sleep(0.04)

        assert len(context.processes) == 1

    asyncio.run(run())


def test_process_ws_reports_failed_after_reconnect_attempts_are_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        context = RecordingProcessContext(["ready-exit", "failed"])
        monkeypatch.setattr(ws_module.multiprocessing, "get_context", lambda _name: context)
        monkeypatch.setattr(ws_module, "_PROCESS_RECONNECT_DELAYS_SECONDS", (0.01,))
        states: list[ws_module.FeishuConnectionState] = []

        async def on_state_change(state: ws_module.FeishuConnectionState) -> None:
            states.append(state)

        connection = ws_module._ProcessWsConnection(
            "account",
            "app",
            "secret",
            "domain",
            on_state_change=on_state_change,
        )

        assert await connection.start(lambda _event: asyncio.sleep(0)) is True
        for _ in range(50):
            if len(context.processes) == 2:
                break
            await asyncio.sleep(0.01)

        assert len(context.processes) == 2
        assert states == [ws_module.FeishuConnectionState(False)]
        await connection.close()

    asyncio.run(run())


def test_process_ws_limits_reconnects_across_repeated_ready_child_exits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def run() -> None:
        context = RecordingProcessContext(["ready-exit", "ready-exit", "failed"])
        monkeypatch.setattr(ws_module.multiprocessing, "get_context", lambda _name: context)
        monkeypatch.setattr(ws_module, "_PROCESS_RECONNECT_DELAYS_SECONDS", (0.01, 0.01))
        states: list[ws_module.FeishuConnectionState] = []

        async def on_state_change(state: ws_module.FeishuConnectionState) -> None:
            states.append(state)

        connection = ws_module._ProcessWsConnection(
            "account",
            "app",
            "secret",
            "domain",
            on_state_change=on_state_change,
        )

        assert await connection.start(lambda _event: asyncio.sleep(0)) is True
        for _ in range(100):
            if len(context.processes) == 3:
                break
            await asyncio.sleep(0.01)

        assert len(context.processes) == 3
        assert states == [
            ws_module.FeishuConnectionState(False),
            ws_module.FeishuConnectionState(True),
            ws_module.FeishuConnectionState(False),
        ]
        await connection.close()

    asyncio.run(run())


def test_lark_client_suppresses_sdk_logs_before_connection_creation(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        account = registry.register_feishu_bot(
            app_id="official-app",
            app_secret="official-secret",
            domain="open.feishu.cn",
        )
        sdk = RecordingSdk()
        sdk_logger = logging.getLogger("Lark")
        original_disabled = sdk_logger.disabled
        original_propagate = sdk_logger.propagate
        sdk_logger.disabled = False
        sdk_logger.propagate = True

        def connection_factory(lark, app_id, app_secret, domain, dispatcher):
            sdk_logger.warning(
                "connected to wss://open.feishu.cn/ws?token=private-protocol-token"
            )
            return RecordingWsConnection(
                lark.ws.Client(
                    app_id,
                    app_secret,
                    event_handler=dispatcher,
                    domain=domain,
                )
            )

        try:
            client = ws_module.LarkFeishuClient(
                sdk_loader=lambda: (sdk.root, sdk.auth_v3, sdk.im_v1),
                connection_factory=connection_factory,
            )
            with caplog.at_level(logging.DEBUG):
                state = await client.start_bot(account, lambda _event: asyncio.sleep(0))
            assert state.connected is True
            assert "private-protocol-token" not in caplog.text
            await client.stop_bot(account.id)
        finally:
            sdk_logger.disabled = original_disabled
            sdk_logger.propagate = original_propagate

    asyncio.run(run())


def test_lark_client_reports_generic_start_failure_without_private_values(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        account = registry.register_feishu_bot(
            app_id="official-app",
            app_secret="official-secret",
            domain="open.feishu.cn",
        )
        sdk = RecordingSdk()

        def connection_factory(_lark, _app_id, _app_secret, _domain, _dispatcher):
            raise RuntimeError("wss://open.feishu.cn/ws?token=private-protocol-token")

        client = ws_module.LarkFeishuClient(
            sdk_loader=lambda: (sdk.root, sdk.auth_v3, sdk.im_v1),
            connection_factory=connection_factory,
        )
        with caplog.at_level(logging.WARNING, logger=ws_module.__name__):
            state = await client.start_bot(account, lambda _event: asyncio.sleep(0))

        assert state == ws_module.FeishuConnectionState(False)
        assert "Feishu SDK connection startup failed" in caplog.text
        assert "private-protocol-token" not in caplog.text

    asyncio.run(run())


def test_lark_client_uses_official_ws_dispatcher_and_rest_message_shape(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        account = registry.register_feishu_bot(
            app_id="official-app",
            app_secret="official-secret",
            domain="open.feishu.cn",
        )
        sdk = RecordingSdk()
        received: list[dict[str, object]] = []

        async def on_event(event: dict[str, object]) -> None:
            received.append(event)

        def connection_factory(lark, app_id, app_secret, domain, dispatcher):
            return RecordingWsConnection(
                lark.ws.Client(
                    app_id,
                    app_secret,
                    event_handler=dispatcher,
                    domain=domain,
                )
            )

        client = ws_module.LarkFeishuClient(
            sdk_loader=lambda: (sdk.root, sdk.auth_v3, sdk.im_v1),
            connection_factory=connection_factory,
        )

        state = await client.start_bot(account, on_event)
        ws_client = sdk.ws_clients[0]
        event = SimpleNamespace(
            header=SimpleNamespace(event_id="event-1"),
            event=SimpleNamespace(
                sender=SimpleNamespace(sender_id=SimpleNamespace(open_id="private-open-id")),
                message=SimpleNamespace(
                    message_id="message-1",
                    chat_id="chat-1",
                    chat_type="p2p",
                    message_type="text",
                    content='{"text":"hello"}',
                    root_id=None,
                    parent_id=None,
                    thread_id=None,
                ),
            ),
        )
        ws_client.event_handler.message_handler(event)
        await asyncio.sleep(0)

        assert state == ws_module.FeishuConnectionState(True)
        assert ws_client.app_id == "official-app"
        assert ws_client.app_secret == "official-secret"
        assert ws_client.domain == "https://open.feishu.cn"
        assert received == [
            {
                "event_id": "event-1",
                "message_id": "message-1",
                "sender": {"open_id": "private-open-id"},
                "message": {
                    "chat_id": "chat-1",
                    "chat_type": "p2p",
                    "message_type": "text",
                    "content": '{"text":"hello"}',
                },
            }
        ]
        assert await client.send_text(account, "chat-1", "final") is True
        request = sdk.rest_client.message_requests[0]
        assert request.receive_id_type == "chat_id"
        assert request.request_body.receive_id == "chat-1"
        assert request.request_body.msg_type == "text"
        assert json.loads(request.request_body.content) == {"text": "final"}
        assert not hasattr(client, "update_card")

    asyncio.run(run())


@pytest.mark.filterwarnings(
    "ignore:datetime.datetime.utcfromtimestamp.*:DeprecationWarning"
)
def test_declared_lark_sdk_exposes_the_used_official_shapes() -> None:
    async def run() -> None:
        lark, auth_v3, im_v1 = ws_module._load_lark_sdk()

        dispatcher = (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(lambda _event: None)
            .build()
        )
        rest_client = (
            lark.Client.builder()
            .app_id("app-id")
            .app_secret("app-secret")
            .domain("https://open.feishu.cn")
            .build()
        )
        auth_body = (
            auth_v3.InternalTenantAccessTokenRequestBody.builder()
            .app_id("app-id")
            .app_secret("app-secret")
            .build()
        )
        message_body = (
            im_v1.CreateMessageRequestBody.builder()
            .receive_id("chat-id")
            .msg_type("text")
            .content('{"text":"hello"}')
            .build()
        )
        ws_parameters = inspect.signature(lark.ws.Client).parameters
        assert {"app_id", "app_secret", "event_handler", "domain"} <= set(ws_parameters)
        assert callable(lark.ws.Client.start)
        assert dispatcher is not None
        assert callable(rest_client.auth.v3.tenant_access_token.ainternal)
        assert callable(rest_client.im.v1.message.acreate)
        assert auth_body.app_id == "app-id"
        assert auth_body.app_secret == "app-secret"
        assert message_body.receive_id == "chat-id"

    asyncio.run(run())

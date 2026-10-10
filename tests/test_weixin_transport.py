from __future__ import annotations

import asyncio
from dataclasses import replace
from types import SimpleNamespace
from pathlib import Path

import importlib
import pytest


messages = importlib.import_module("Turning-Good-Agent.bus.messages")
registry_module = importlib.import_module("Turning-Good-Agent.channels.registry")
weixin_module = importlib.import_module("Turning-Good-Agent.channels.weixin")
ilink_module = importlib.import_module("Turning-Good-Agent.channels.weixin_ilink")
InboundMessage = messages.InboundMessage
OutboundMessage = messages.OutboundMessage
Recipient = messages.Recipient
ChannelAccountRegistry = registry_module.ChannelAccountRegistry
WeixinTransport = weixin_module.WeixinTransport
split_utf8_chunks = weixin_module.split_utf8_chunks
IlinkPollResult = ilink_module.IlinkPollResult
IlinkLoginResult = ilink_module.IlinkLoginResult
HttpxIlinkClient = ilink_module.HttpxIlinkClient


class FakeIlink:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.sent: list[str] = []

    async def begin_login(self, binding):
        return type("Login", (), {"status": "awaiting_first_dm", "private_update": {}})()

    async def poll(self, binding):
        event = self.events.pop(0) if self.events else None
        return IlinkPollResult((event,) if event is not None else (), {})

    async def send_text(self, binding, content: str) -> bool:
        self.sent.append(content)
        return True


class FakeQrPresenter:
    def __init__(self) -> None:
        self.presented: list[tuple[str, str]] = []
        self.dismissed: list[str] = []
        self.closed = False

    async def present(self, binding_id: str, qr_content: str) -> bool:
        self.presented.append((binding_id, qr_content))
        return True

    async def dismiss(self, binding_id: str) -> None:
        self.dismissed.append(binding_id)

    async def close(self) -> None:
        self.closed = True


class LoginProgressIlink(FakeIlink):
    def __init__(self) -> None:
        super().__init__()
        self.login_polls = 0

    async def begin_login(self, binding):
        return IlinkLoginResult(
            "pending_qr",
            {"qrcode": "opaque-challenge"},
            qr_content="private-qr-content",
        )

    async def continue_login(self, binding):
        self.login_polls += 1
        return IlinkLoginResult(
            "awaiting_first_dm",
            {
                "bot_token": "private-token",
                "ilink_bot_id": "private-bot",
                "base_url": "https://ilink.example",
            },
        )


class RebindProgressIlink(FakeIlink):
    def __init__(self) -> None:
        super().__init__()
        self.begin_calls = 0
        self.continue_calls = 0

    async def begin_login(self, binding):
        self.begin_calls += 1
        assert binding.private.get("bot_token") is None
        assert binding.private.get("from_user_id") is None
        return IlinkLoginResult(
            "pending_qr",
            {"qrcode": "candidate-challenge"},
            qr_content="candidate-qr",
        )

    async def continue_login(self, binding):
        self.continue_calls += 1
        return IlinkLoginResult(
            "awaiting_first_dm",
            {
                "bot_token": "candidate-token",
                "ilink_bot_id": "candidate-bot",
                "base_url": "https://candidate.example",
            },
        )


class UnavailableQrPresenter(FakeQrPresenter):
    async def present(self, binding_id: str, qr_content: str) -> bool:
        self.presented.append((binding_id, qr_content))
        return False


class PersistentQrPresenter(FakeQrPresenter):
    def is_presenting(self, binding_id: str) -> bool:
        del binding_id
        return True


class FailingLoginIlink(FakeIlink):
    async def begin_login(self, binding):
        raise RuntimeError("iLink unavailable")


class MissingQrIlink(FakeIlink):
    async def begin_login(self, binding):
        return IlinkLoginResult("pending_qr", {"qrcode": "opaque-challenge"})


class ClosingQrPresenter(FakeQrPresenter):
    def __init__(self) -> None:
        super().__init__()
        self._on_closed = None

    def set_closed_callback(self, callback) -> None:
        self._on_closed = callback

    def is_presenting(self, _binding_id: str) -> bool:
        return True

    async def close_view(self, binding_id: str) -> None:
        assert self._on_closed is not None
        result = self._on_closed(binding_id)
        if asyncio.iscoroutine(result):
            await result


class GenerationQrPresenter(FakeQrPresenter):
    def __init__(self) -> None:
        super().__init__()
        self.generations: list[int | None] = []

    async def present(
        self,
        binding_id: str,
        qr_content: str,
        generation: int | None = None,
    ) -> bool:
        self.presented.append((binding_id, qr_content))
        self.generations.append(generation)
        return True

    def is_presenting(self, _binding_id: str) -> bool:
        return True


class FakeHttpResponse:
    def __init__(self, payload: dict[str, object], *, status_code: int = 200) -> None:
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, object]:
        return self.payload


class RecordingHttpClient:
    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = list(responses)
        self.requests: list[dict[str, object]] = []

    async def request(self, method: str, url: str, **kwargs):
        self.requests.append({"method": method, "url": url, **kwargs})
        return FakeHttpResponse(self.responses.pop(0))


class UnauthorizedHttpResponse(FakeHttpResponse):
    def __init__(self) -> None:
        super().__init__({}, status_code=401)

    def raise_for_status(self) -> None:
        raise RuntimeError("401 unauthorized")


def _event(message_id: str, sender: str, *, text: str | None = None, message_type: str = "text") -> dict[str, object]:
    item_list: list[dict[str, object]] = []
    if text is not None:
        item_list.append({"type": "text", "text": text})
    else:
        item_list.append({"type": message_type})
    return {
        "message_id": message_id,
        "from_user_id": sender,
        "chat_type": "p2p",
        "message_type": message_type,
        "item_list": item_list,
        "create_time_ms": 1,
        "context_token": f"ctx-{message_id}",
    }


def _make_transport(tmp_path: Path):
    registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
    binding = registry.create_weixin_invitation(principal_id=None)
    binding = registry.update(replace(binding, status="awaiting_first_dm"))
    client = FakeIlink()
    submitted: list[InboundMessage] = []

    async def accept(message: InboundMessage) -> bool:
        submitted.append(message)
        return True

    coordinator = SimpleNamespace(accept=accept)
    transport = WeixinTransport(registry, coordinator, client)
    return registry, binding, client, submitted, transport


def test_utf8_split_never_breaks_encoded_characters() -> None:
    parts = split_utf8_chunks("你好🙂世界", max_bytes=7)
    assert "".join(parts) == "你好🙂世界"
    assert all(len(part.encode("utf-8")) <= 7 for part in parts)


def test_first_text_dm_locks_binding_and_drops_second_sender(tmp_path: Path) -> None:
    async def run() -> None:
        registry, binding, client, submitted, transport = _make_transport(tmp_path)
        client.events.extend([_event("1", "first", text="hello"), _event("2", "other", text="no")])
        await transport.poll_once(binding.id)
        await transport.poll_once(binding.id)
        assert [item.id for item in submitted] == [f"weixin:{binding.id}:1"]
        current = registry.get("weixin", binding.id)
        assert current is not None
        assert current.private.get("from_user_id") == "first"
        assert current.private.get("context_token") == "ctx-1"

    asyncio.run(run())


def test_first_text_dm_without_chat_type_is_treated_as_private(tmp_path: Path) -> None:
    async def run() -> None:
        registry, binding, client, submitted, transport = _make_transport(tmp_path)
        event = _event("missing-chat-type", "first", text="hello")
        event.pop("chat_type")
        client.events.append(event)
        await transport.poll_once(binding.id)
        assert [item.id for item in submitted] == [f"weixin:{binding.id}:missing-chat-type"]

    asyncio.run(run())


def test_locked_binding_non_text_is_acknowledged_without_runtime(tmp_path: Path) -> None:
    async def run() -> None:
        registry, binding, client, submitted, transport = _make_transport(tmp_path)
        current = registry.get("weixin", binding.id)
        assert current is not None
        registry.update(
            replace(
                current,
                status="active",
                subscribed=True,
                private={**current.private, "from_user_id": "first", "context_token": "ctx"},
            )
        )
        client.events.append(_event("3", "first", text=None, message_type="image"))
        await transport.poll_once(binding.id)
        assert submitted == []
        assert client.sent == ["当前仅支持文本"]

    asyncio.run(run())


def test_terminal_send_chunks_text_and_requires_context(tmp_path: Path) -> None:
    async def run() -> None:
        registry, binding, client, _submitted, transport = _make_transport(tmp_path)
        current = registry.get("weixin", binding.id)
        assert current is not None
        registry.update(
            replace(
                current,
                status="active",
                subscribed=True,
                private={**current.private, "from_user_id": "first", "context_token": "ctx", "connected": True},
            )
        )
        message = OutboundMessage(
            "out-1",
            "opaque",
            recipient=Recipient(current.principal_id, "weixin", current.id),
            content="你好🙂世界",
            event_type="response.completed",
        )
        assert await transport.send(message) is True
        assert "".join(client.sent) == message.content

    asyncio.run(run())


def test_missing_context_marks_binding_offline(tmp_path: Path) -> None:
    async def run() -> None:
        registry, binding, client, _submitted, transport = _make_transport(tmp_path)
        current = registry.get("weixin", binding.id)
        assert current is not None
        registry.update(
            replace(
                current,
                status="active",
                subscribed=True,
                private={
                    **current.private,
                    "from_user_id": "first",
                    "context_token": None,
                    "connected": True,
                },
            )
        )
        message = OutboundMessage(
            "out-no-context",
            "opaque",
            recipient=Recipient(current.principal_id, "weixin", current.id),
            content="offline",
            event_type="response.completed",
        )

        assert await transport.send(message) is False
        updated = registry.get("weixin", binding.id)
        assert updated is not None
        assert updated.private.get("connected") is False
        assert client.sent == []

    asyncio.run(run())


def test_mixed_non_text_items_do_not_enter_runtime(tmp_path: Path) -> None:
    async def run() -> None:
        registry, binding, client, submitted, transport = _make_transport(tmp_path)
        current = registry.get("weixin", binding.id)
        assert current is not None
        registry.update(
            replace(
                current,
                status="active",
                subscribed=True,
                private={
                    **current.private,
                    "from_user_id": "first",
                    "context_token": "ctx",
                    "connected": True,
                },
            )
        )
        mixed = _event("mixed", "first", text="caption")
        mixed["item_list"] = [{"type": "image"}, {"type": "text", "text": "caption"}]
        client.events.append(mixed)

        await transport.poll_once(binding.id)
        assert submitted == []
        assert client.sent == ["当前仅支持文本"]

    asyncio.run(run())


def test_proactive_notification_sends_final_text_once(tmp_path: Path) -> None:
    async def run() -> None:
        registry, binding, client, _submitted, transport = _make_transport(tmp_path)
        current = registry.get("weixin", binding.id)
        assert current is not None
        registry.update(
            replace(
                current,
                status="active",
                subscribed=True,
                private={
                    **current.private,
                    "from_user_id": "first",
                    "context_token": "ctx",
                    "connected": True,
                },
            )
        )
        message = OutboundMessage(
            "proactive-1",
            "opaque",
            recipient=Recipient(current.principal_id, "weixin", current.id),
            content="scheduled result",
            event_type="proactive.cron.completed",
            disposition="proactive_notification",
        )

        assert await transport.send(message) is True
        assert client.sent == ["scheduled result"]

    asyncio.run(run())


def test_credential_rejection_expires_binding_and_forgets_private_credentials(tmp_path: Path) -> None:
    class ExpiredCredentialIlink(FakeIlink):
        async def send_text(self, binding, content: str) -> bool:
            del binding, content
            raise weixin_module.IlinkCredentialExpiredError()

    async def run() -> None:
        registry, binding, _client, _submitted, _transport = _make_transport(tmp_path)
        transport = WeixinTransport(
            registry,
            SimpleNamespace(accept=lambda _message: None),
            ExpiredCredentialIlink(),
        )
        current = registry.get("weixin", binding.id)
        assert current is not None
        current = registry.update(
            replace(
                current,
                status="active",
                subscribed=True,
                private={
                    **current.private,
                    "bot_token": "private-token",
                    "ilink_bot_id": "private-bot",
                    "from_user_id": "private-user",
                    "context_token": "private-context",
                    "cursor": "private-cursor",
                    "connected": True,
                },
            )
        )
        message = OutboundMessage(
            "expired-outbound",
            "opaque",
            recipient=Recipient(current.principal_id, "weixin", current.id),
            content="do not retry",
            event_type="response.completed",
        )

        assert await transport.send(message) is False

        expired = registry.get("weixin", binding.id)
        assert expired is not None
        assert expired.status == "expired"
        assert expired.private.get("credential_state") == "expired"
        assert expired.private.get("connected") is False
        assert not transport.is_deliverable(message.recipient)
        for field_name in ("bot_token", "ilink_bot_id", "from_user_id", "context_token", "cursor"):
            assert field_name not in expired.private

    asyncio.run(run())


def test_proactive_stream_tool_and_status_events_are_suppressed(tmp_path: Path) -> None:
    async def run() -> None:
        registry, binding, client, _submitted, transport = _make_transport(tmp_path)
        current = registry.get("weixin", binding.id)
        assert current is not None
        registry.update(
            replace(
                current,
                status="active",
                subscribed=True,
                private={
                    **current.private,
                    "from_user_id": "first",
                    "context_token": "ctx",
                    "connected": True,
                },
            )
        )
        recipient = Recipient(current.principal_id, "weixin", current.id)
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
        assert client.sent == []

    asyncio.run(run())


def test_pending_qr_progresses_without_persisting_presented_qr(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        binding = registry.create_weixin_invitation(principal_id=None)
        client = LoginProgressIlink()
        presenter = FakeQrPresenter()
        transport = WeixinTransport(
            registry,
            SimpleNamespace(accept=lambda _message: None),
            client,
            qr_presenter=presenter,
        )

        assert await transport.progress_login_once(binding.id) == "pending_qr"
        assert presenter.presented == [(binding.id, "private-qr-content")]
        pending = registry.get("weixin", binding.id)
        assert pending is not None
        assert "qr_content" not in pending.private
        assert "qrcode_img_content" not in pending.private

        assert await transport.progress_login_once(binding.id) == "awaiting_first_dm"
        ready = registry.get("weixin", binding.id)
        assert ready is not None
        assert ready.private.get("bot_token") == "private-token"
        assert client.login_polls == 1
        assert presenter.dismissed == [binding.id]

        await transport.close()
        assert presenter.closed is True

    asyncio.run(run())


def test_web_qr_cache_survives_headless_presentation_and_clears_after_login(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        binding = registry.create_weixin_invitation(principal_id=None)
        cache = weixin_module.InMemoryIlinkQrCache(ttl_seconds=300)
        transport = WeixinTransport(
            registry,
            SimpleNamespace(accept=lambda _message: None),
            LoginProgressIlink(),
            qr_presenter=UnavailableQrPresenter(),
            qr_cache=cache,
        )

        assert await transport.progress_login_once(binding.id) == "pending_qr"
        snapshot = transport.get_qr(binding.id)
        assert snapshot is not None
        assert snapshot.content == "private-qr-content"
        pending = registry.get("weixin", binding.id)
        assert pending is not None
        assert "qr_content" not in pending.private

        assert await transport.progress_login_once(binding.id) == "awaiting_first_dm"
        assert transport.get_qr(binding.id) is None

    asyncio.run(run())


def test_web_qr_cache_expires_without_touching_registry(tmp_path: Path) -> None:
    now = [100.0]
    cache = weixin_module.InMemoryIlinkQrCache(ttl_seconds=5, clock=lambda: now[0])
    assert cache.put("binding", "qr-content") is True
    snapshot = cache.get("binding")
    assert snapshot is not None
    assert snapshot.expires_at == 105.0
    now[0] = 105.0
    assert cache.get("binding") is None


def test_expired_web_qr_cache_requests_a_fresh_login_image(tmp_path: Path) -> None:
    class RefreshingIlink(FakeIlink):
        def __init__(self) -> None:
            super().__init__()
            self.begin_calls = 0
            self.continue_calls = 0

        async def begin_login(self, binding):
            del binding
            self.begin_calls += 1
            return IlinkLoginResult(
                "pending_qr",
                {"qrcode": "fresh-challenge"},
                qr_content="fresh-qr-content",
            )

        async def continue_login(self, binding):
            del binding
            self.continue_calls += 1
            raise AssertionError("expired web QR cache must request a fresh image")

    async def run() -> None:
        now = [100.0]
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        binding = registry.create_weixin_invitation(principal_id=None)
        binding = registry.update(replace(binding, private={**binding.private, "qrcode": "stale-challenge"}))
        cache = weixin_module.InMemoryIlinkQrCache(ttl_seconds=5, clock=lambda: now[0])
        cache.put(binding.id, "stale-qr-content")
        now[0] = 106.0
        client = RefreshingIlink()
        presenter = PersistentQrPresenter()
        transport = WeixinTransport(
            registry,
            SimpleNamespace(accept=lambda _message: None),
            client,
            qr_presenter=presenter,
            qr_cache=cache,
        )

        assert await transport.progress_login_once(binding.id) == "pending_qr"
        assert client.begin_calls == 1
        assert client.continue_calls == 0
        snapshot = transport.get_qr(binding.id)
        assert snapshot is not None
        assert snapshot.content == "fresh-qr-content"

    asyncio.run(run())


def test_pending_qr_fails_closed_when_local_presentation_is_unavailable(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        binding = registry.create_weixin_invitation(principal_id=None)
        presenter = UnavailableQrPresenter()
        transport = WeixinTransport(
            registry,
            SimpleNamespace(accept=lambda _message: None),
            LoginProgressIlink(),
            qr_presenter=presenter,
        )

        assert await transport.progress_login_once(binding.id) == "failed"
        current = registry.get("weixin", binding.id)
        assert current is not None
        assert current.status == "failed"
        assert current.private.get("credential_state") == "local_qr_unavailable"
        assert presenter.presented == [(binding.id, "private-qr-content")]

    asyncio.run(run())


def test_pending_qr_without_a_local_image_fails_closed_and_forgets_challenge(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        binding = registry.create_weixin_invitation(principal_id=None)
        transport = WeixinTransport(
            registry,
            SimpleNamespace(accept=lambda _message: None),
            MissingQrIlink(),
            qr_presenter=FakeQrPresenter(),
        )

        assert await transport.progress_login_once(binding.id) == "failed"
        current = registry.get("weixin", binding.id)
        assert current is not None
        assert current.private.get("credential_state") == "local_qr_unavailable"
        assert current.private.get("qrcode") is None

    asyncio.run(run())


def test_viewer_exit_fails_pending_binding_and_forgets_qr_challenge(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        binding = registry.create_weixin_invitation(principal_id=None)
        presenter = ClosingQrPresenter()
        transport = WeixinTransport(
            registry,
            SimpleNamespace(accept=lambda _message: None),
            LoginProgressIlink(),
            qr_presenter=presenter,
        )

        assert await transport.progress_login_once(binding.id) == "pending_qr"
        await presenter.close_view(binding.id)

        current = registry.get("weixin", binding.id)
        assert current is not None
        assert current.status == "failed"
        assert current.private.get("credential_state") == "local_qr_unavailable"
        assert current.private.get("qrcode") is None

    asyncio.run(run())


def test_disabling_weixin_binding_dismisses_pending_qr(tmp_path: Path) -> None:
    async def run() -> None:
        registry, binding, client, _submitted, transport = _make_transport(tmp_path)
        presenter = FakeQrPresenter()
        transport._qr_presenter = presenter

        await transport.disable_account(binding.id)

        assert client.sent == []
        assert presenter.dismissed == [binding.id]

    asyncio.run(run())


def test_permanent_detach_closes_owned_client_but_disable_retains_it(
    tmp_path: Path,
) -> None:
    class ClosableIlink(FakeIlink):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1

    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        binding = registry.create_weixin_invitation(principal_id=None)
        client = ClosableIlink()
        transport = WeixinTransport(
            registry,
            SimpleNamespace(accept=lambda _message: None),
            client_factory=lambda _account: client,
        )
        await transport._client_for(binding)

        await transport.disable_account(binding.id)
        assert transport._clients[binding.id] is client
        assert client.close_calls == 0

        await transport.detach_account(binding.id)
        assert binding.id not in transport._clients
        assert client.close_calls == 1

    asyncio.run(run())


def test_permanent_detach_never_closes_shared_injected_client(tmp_path: Path) -> None:
    class SharedIlink(FakeIlink):
        def __init__(self) -> None:
            super().__init__()
            self.close_calls = 0

        async def close(self) -> None:
            self.close_calls += 1

    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        first = registry.create_weixin_invitation(principal_id=None)
        second = registry.create_weixin_invitation(principal_id=None)
        client = SharedIlink()
        transport = WeixinTransport(
            registry,
            SimpleNamespace(accept=lambda _message: None),
            client,
        )
        await transport._client_for(first)
        await transport._client_for(second)

        await transport.detach_account(first.id)
        assert first.id not in transport._clients
        assert transport._clients[second.id] is client
        assert client.close_calls == 0

    asyncio.run(run())


def test_login_failure_dismisses_pending_qr(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        binding = registry.create_weixin_invitation(principal_id=None)
        presenter = FakeQrPresenter()
        transport = WeixinTransport(
            registry,
            SimpleNamespace(accept=lambda _message: None),
            FailingLoginIlink(),
            qr_presenter=presenter,
        )

        assert await transport.progress_login_once(binding.id) == "failed"
        assert presenter.dismissed == [binding.id]

    asyncio.run(run())


def test_http_client_uses_documented_qr_login_and_poll_protocol(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        binding = registry.create_weixin_invitation(principal_id=None)
        http = RecordingHttpClient(
            [
                {"qrcode": "opaque-challenge", "qrcode_img_content": "private-qr"},
                {
                    "status": "confirmed",
                    "bot_token": "private-token",
                    "ilink_bot_id": "private-bot",
                    "baseurl": "https://ilink.example",
                    "ilink_user_id": "private-user",
                },
            ]
        )
        client = HttpxIlinkClient(http_client=http)

        first = await client.begin_login(binding)
        binding = registry.update(replace(binding, status=first.status, private=first.private_update))
        second = await client.continue_login(binding)

        assert first.qr_content == "private-qr"
        assert second.status == "awaiting_first_dm"
        assert http.requests[0]["method"] == "GET"
        assert http.requests[0]["url"].endswith("/ilink/bot/get_bot_qrcode")
        assert http.requests[0]["params"] == {"bot_type": "3"}
        assert http.requests[1]["method"] == "GET"
        assert http.requests[1]["url"].endswith("/ilink/bot/get_qrcode_status")
        assert http.requests[1]["params"] == {"qrcode": "opaque-challenge"}

    asyncio.run(run())


def test_direct_rebind_switches_credentials_and_locks_the_new_sender(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        binding = registry.create_weixin_invitation(principal_id="owner")
        binding = registry.update(replace(binding, status="awaiting_first_dm"))
        binding = registry.update(
            replace(
                binding,
                status="active",
                subscribed=True,
                private={
                    **binding.private,
                    "bot_token": "old-token",
                    "ilink_bot_id": "old-bot",
                    "from_user_id": "old-user",
                    "context_token": "old-context",
                    "cursor": "old-cursor",
                    "connected": True,
                },
            )
        )
        client = RebindProgressIlink()
        submitted: list[InboundMessage] = []

        async def accept(message: InboundMessage) -> bool:
            submitted.append(message)
            return True

        transport = WeixinTransport(
            registry,
            SimpleNamespace(accept=accept),
            client,
            qr_cache=weixin_module.InMemoryIlinkQrCache(),
            poll_interval_seconds=0.01,
        )

        returned = await transport.start_rebind(binding.id)
        assert returned.id == binding.id
        current = registry.get("weixin", binding.id)
        assert current is not None and current.status == "active"
        assert current.private.get("from_user_id") == "old-user"
        snapshot = transport.get_qr(binding.id)
        assert snapshot is not None and snapshot.content == "candidate-qr"

        for _ in range(50):
            await asyncio.sleep(0.01)
            current = registry.get("weixin", binding.id)
            if current is not None and current.status == "awaiting_first_dm":
                break
        current = registry.get("weixin", binding.id)
        assert current is not None
        assert current.status == "awaiting_first_dm"
        assert current.private.get("bot_token") == "candidate-token"
        assert current.private.get("binding_epoch") == 1
        for field_name in ("from_user_id", "context_token", "cursor"):
            assert field_name not in current.private

        client.events.extend(
            [
                _event("new-first", "new-user", text="new hello"),
                _event("old-after", "old-user", text="must ignore"),
            ]
        )
        assert await transport.poll_once(binding.id) == 1
        assert [item.content for item in submitted] == ["new hello"]
        locked = registry.get("weixin", binding.id)
        assert locked is not None
        assert locked.private.get("from_user_id") == "new-user"
        stale_reply = OutboundMessage(
            "old-reply",
            "weixin-session",
            recipient=Recipient("owner", "weixin", binding.id),
            content="must not reach the new user",
            metadata={"binding_epoch": 0},
        )
        assert await transport.send(stale_reply) is False
        assert client.sent == []
        await transport.close()

    asyncio.run(run())


def test_rebind_failure_or_qr_close_preserves_the_current_binding(tmp_path: Path) -> None:
    class FailedCandidateIlink(FakeIlink):
        async def begin_login(self, binding):
            del binding
            return IlinkLoginResult("failed", {"credential_state": "invalid"})

    async def active_binding(registry: ChannelAccountRegistry):
        binding = registry.create_weixin_invitation(principal_id=None)
        binding = registry.update(replace(binding, status="awaiting_first_dm"))
        return registry.update(
            replace(
                binding,
                status="active",
                private={
                    **binding.private,
                    "bot_token": "old-token",
                    "ilink_bot_id": "old-bot",
                    "from_user_id": "old-user",
                    "context_token": "old-context",
                    "connected": True,
                },
            )
        )

    async def run() -> None:
        failed_registry = ChannelAccountRegistry(tmp_path / "failed", owner_principal_id="owner")
        failed_binding = await active_binding(failed_registry)
        failed = WeixinTransport(
            failed_registry,
            SimpleNamespace(accept=lambda _message: None),
            FailedCandidateIlink(),
            qr_cache=weixin_module.InMemoryIlinkQrCache(),
        )
        await failed.start_rebind(failed_binding.id)
        assert failed_registry.get("weixin", failed_binding.id) == failed_binding
        assert failed.get_qr(failed_binding.id) is None

        close_registry = ChannelAccountRegistry(tmp_path / "closed", owner_principal_id="owner")
        close_binding = await active_binding(close_registry)
        presenter = GenerationQrPresenter()
        closing = WeixinTransport(
            close_registry,
            SimpleNamespace(accept=lambda _message: None),
            RebindProgressIlink(),
            qr_presenter=presenter,
            poll_interval_seconds=60,
        )
        await closing.start_rebind(close_binding.id)
        generation = presenter.generations[-1]
        assert isinstance(generation, int)
        await closing._on_qr_presentation_closed(close_binding.id, generation)
        assert close_registry.get("weixin", close_binding.id) == close_binding
        await failed.close()
        await closing.close()

    asyncio.run(run())


def test_direct_rebind_can_replace_expired_or_revoked_credentials(tmp_path: Path) -> None:
    async def run_case(case_root: Path, status: str) -> None:
        registry = ChannelAccountRegistry(case_root, owner_principal_id="owner")
        binding = registry.create_weixin_invitation(principal_id=None)
        if status == "revoked":
            binding = registry.revoke("weixin", binding.id)
        else:
            binding = registry.update(replace(binding, status="expired", enabled=False))
        transport = WeixinTransport(
            registry,
            SimpleNamespace(accept=lambda _message: None),
            RebindProgressIlink(),
            qr_cache=weixin_module.InMemoryIlinkQrCache(),
            poll_interval_seconds=0.01,
        )
        await transport.start_rebind(binding.id)
        for _ in range(50):
            await asyncio.sleep(0.01)
            current = registry.get("weixin", binding.id)
            if current is not None and current.status == "awaiting_first_dm":
                break
        current = registry.get("weixin", binding.id)
        assert current is not None
        assert current.status == "awaiting_first_dm"
        assert current.enabled is True
        assert current.private.get("bot_token") == "candidate-token"
        await transport.close()

    async def run() -> None:
        await run_case(tmp_path / "expired", "expired")
        await run_case(tmp_path / "revoked", "revoked")

    asyncio.run(run())


def test_late_revoke_cancels_an_active_binding_candidate(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        binding = registry.create_weixin_invitation(principal_id=None)
        binding = registry.update(replace(binding, status="awaiting_first_dm"))
        binding = registry.update(
            replace(
                binding,
                status="active",
                private={
                    **binding.private,
                    "bot_token": "old-token",
                    "ilink_bot_id": "old-bot",
                    "from_user_id": "old-user",
                    "context_token": "old-context",
                },
            )
        )
        transport = WeixinTransport(
            registry,
            SimpleNamespace(accept=lambda _message: None),
            RebindProgressIlink(),
            qr_cache=weixin_module.InMemoryIlinkQrCache(),
            poll_interval_seconds=60,
        )
        await transport.start_rebind(binding.id)
        attempt = transport._pending_rebinds[binding.id].attempt_id
        registry.revoke("weixin", binding.id)
        async with transport._binding_lock(binding.id):
            await transport._progress_rebind_locked(binding.id, attempt)
        current = registry.get("weixin", binding.id)
        assert current is not None
        assert current.status == "revoked"
        assert current.enabled is False
        assert binding.id not in transport._pending_rebinds
        await transport.close()

    asyncio.run(run())


@pytest.mark.skip(reason="已由直接候选切换契约替代")
def test_active_rebind_keeps_old_user_and_credentials_until_idle_commit(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        binding = registry.create_weixin_invitation(principal_id="owner")
        binding = registry.update(replace(binding, status="awaiting_first_dm"))
        binding = registry.update(
            replace(
                binding,
                status="active",
                subscribed=True,
                private={
                    **binding.private,
                    "bot_token": "old-token",
                    "ilink_bot_id": "old-bot",
                    "from_user_id": "old-user",
                    "context_token": "old-context",
                    "cursor": "old-cursor",
                    "connected": True,
                },
            )
        )
        client = RebindProgressIlink()
        submitted: list[InboundMessage] = []

        async def accept(message: InboundMessage) -> bool:
            submitted.append(message)
            return True

        allow_commit = asyncio.Event()
        transport_holder: list[WeixinTransport] = []

        async def request_commit(binding_id: str) -> bool:
            if not allow_commit.is_set():
                return False
            return await transport_holder[0].try_commit_rebind(binding_id)

        cache = weixin_module.InMemoryIlinkQrCache(ttl_seconds=300)
        transport = WeixinTransport(
            registry,
            SimpleNamespace(accept=accept),
            client,
            qr_cache=cache,
            poll_interval_seconds=0.01,
            request_rebind_commit=request_commit,
        )
        transport_holder.append(transport)

        returned = await transport.start_rebind(binding.id)
        assert returned.id == binding.id
        current = registry.get("weixin", binding.id)
        assert current is not None
        assert current.status == "active"
        assert current.private.get("bot_token") == "old-token"
        assert current.private.get("from_user_id") == "old-user"
        snapshot = transport.get_qr(binding.id)
        assert snapshot is not None and snapshot.content == "candidate-qr"

        for _ in range(50):
            if transport.get_rebind_state(binding.id) == "waiting_for_idle":
                break
            await asyncio.sleep(0.01)
        assert transport.get_rebind_state(binding.id) == "waiting_for_idle"
        current = registry.get("weixin", binding.id)
        assert current is not None
        assert current.private.get("bot_token") == "old-token"
        assert current.private.get("from_user_id") == "old-user"

        client.events.append(_event("old-before", "old-user", text="still works"))
        assert await transport.poll_once(binding.id) == 1
        assert [item.content for item in submitted] == ["still works"]

        allow_commit.set()
        for _ in range(50):
            current = registry.get("weixin", binding.id)
            if current is not None and current.status == "awaiting_first_dm":
                break
            await asyncio.sleep(0.01)
        current = registry.get("weixin", binding.id)
        assert current is not None
        assert current.status == "awaiting_first_dm"
        assert current.subscribed is True
        assert current.private.get("bot_token") == "candidate-token"
        assert current.private.get("ilink_bot_id") == "candidate-bot"
        for field_name in ("from_user_id", "context_token", "cursor"):
            assert field_name not in current.private
        assert current.private.get("inbound_ids") == []
        assert transport.get_rebind_state(binding.id) is None
        assert transport.get_qr(binding.id) is None

        client.events.extend(
            [
                _event("new-first", "new-user", text="new hello"),
                _event("old-after", "old-user", text="must ignore"),
            ]
        )
        assert await transport.poll_once(binding.id) == 1
        assert await transport.poll_once(binding.id) == 0
        assert [item.content for item in submitted] == ["still works", "new hello"]
        locked = registry.get("weixin", binding.id)
        assert locked is not None
        assert locked.private.get("from_user_id") == "new-user"
        await transport.close()

    asyncio.run(run())


@pytest.mark.skip(reason="已由直接候选切换契约替代")
def test_rebind_failure_or_transport_close_never_mutates_old_binding(tmp_path: Path) -> None:
    class FailedCandidateIlink(FakeIlink):
        async def begin_login(self, binding):
            del binding
            return IlinkLoginResult("failed", {"credential_state": "invalid"})

    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        binding = registry.create_weixin_invitation(principal_id=None)
        binding = registry.update(replace(binding, status="awaiting_first_dm"))
        binding = registry.update(
            replace(
                binding,
                status="active",
                private={
                    **binding.private,
                    "bot_token": "old-token",
                    "ilink_bot_id": "old-bot",
                    "from_user_id": "old-user",
                    "context_token": "old-context",
                    "connected": True,
                },
            )
        )
        original = registry.get("weixin", binding.id)
        failed = WeixinTransport(
            registry,
            SimpleNamespace(accept=lambda _message: None),
            FailedCandidateIlink(),
            qr_cache=weixin_module.InMemoryIlinkQrCache(),
        )
        await failed.start_rebind(binding.id)
        assert transport_account(registry, binding.id) == original
        assert failed.get_rebind_state(binding.id) is None

        pending = WeixinTransport(
            registry,
            SimpleNamespace(accept=lambda _message: None),
            RebindProgressIlink(),
            qr_cache=weixin_module.InMemoryIlinkQrCache(),
            poll_interval_seconds=60,
        )
        await pending.start_rebind(binding.id)
        assert pending.get_rebind_state(binding.id) == "pending_qr"
        await pending.close()
        assert transport_account(registry, binding.id) == original
        assert pending.get_rebind_state(binding.id) is None
        assert pending.get_qr(binding.id) is None

    asyncio.run(run())


@pytest.mark.skip(reason="候选切换不再依赖 Host callback")
def test_candidate_commit_requires_host_callback_and_qr_expiry_discards_candidate(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        binding = registry.create_weixin_invitation(principal_id=None)
        binding = registry.update(replace(binding, status="awaiting_first_dm"))
        binding = registry.update(
            replace(
                binding,
                status="active",
                private={
                    **binding.private,
                    "bot_token": "old-token",
                    "ilink_bot_id": "old-bot",
                    "from_user_id": "old-user",
                    "context_token": "old-context",
                    "connected": True,
                },
            )
        )
        original = registry.get("weixin", binding.id)
        now = [100.0]
        cache = weixin_module.InMemoryIlinkQrCache(
            ttl_seconds=5,
            clock=lambda: now[0],
        )
        transport = WeixinTransport(
            registry,
            SimpleNamespace(accept=lambda _message: None),
            RebindProgressIlink(),
            qr_cache=cache,
            poll_interval_seconds=60,
        )

        await transport.start_rebind(binding.id)
        assert await transport.progress_login_once(binding.id) == "waiting_for_idle"
        assert await transport.try_commit_rebind(binding.id) is False
        assert transport_account(registry, binding.id) == original

        await transport.start_rebind(binding.id)
        assert len(transport._rebind_tasks) == 1
        now[0] = 106.0
        assert await transport.progress_login_once(binding.id) == "failed"
        assert transport.get_rebind_state(binding.id) is None
        assert transport_account(registry, binding.id) == original
        await transport.close()

    asyncio.run(run())


@pytest.mark.skip(reason="候选切换不再等待空闲")
def test_disable_and_late_revoke_win_over_waiting_candidate_commit(tmp_path: Path) -> None:
    async def make_waiting(binding_id: str, registry, client):
        entered = asyncio.Event()
        release = asyncio.Event()
        holder: list[WeixinTransport] = []

        async def request_commit(candidate_id: str) -> bool:
            entered.set()
            await release.wait()
            return await holder[0].try_commit_rebind(candidate_id)

        transport = WeixinTransport(
            registry,
            SimpleNamespace(accept=lambda _message: None),
            client,
            qr_cache=weixin_module.InMemoryIlinkQrCache(),
            poll_interval_seconds=0.01,
            request_rebind_commit=request_commit,
        )
        holder.append(transport)
        await transport.start_rebind(binding_id)
        await entered.wait()
        return transport, release

    async def active_binding(registry):
        binding = registry.create_weixin_invitation(principal_id=None)
        binding = registry.update(replace(binding, status="awaiting_first_dm"))
        return registry.update(
            replace(
                binding,
                status="active",
                private={
                    **binding.private,
                    "bot_token": "old-token",
                    "ilink_bot_id": "old-bot",
                    "from_user_id": "old-user",
                    "context_token": "old-context",
                    "connected": True,
                },
            )
        )

    async def run() -> None:
        disabled_registry = ChannelAccountRegistry(tmp_path / "disabled", owner_principal_id="owner")
        disabled_binding = await active_binding(disabled_registry)
        disabled_transport, disabled_release = await make_waiting(
            disabled_binding.id,
            disabled_registry,
            RebindProgressIlink(),
        )
        disabled = await disabled_transport.disable_binding(disabled_binding.id)
        disabled_release.set()
        assert disabled.status == "disabled"
        assert disabled.enabled is False
        assert disabled_transport.get_rebind_state(disabled_binding.id) is None
        await disabled_transport.close()

        revoked_registry = ChannelAccountRegistry(tmp_path / "revoked", owner_principal_id="owner")
        revoked_binding = await active_binding(revoked_registry)
        revoked_transport, revoked_release = await make_waiting(
            revoked_binding.id,
            revoked_registry,
            RebindProgressIlink(),
        )
        revoked_registry.revoke("weixin", revoked_binding.id)
        revoked_release.set()
        for _ in range(50):
            if revoked_transport.get_rebind_state(revoked_binding.id) is None:
                break
            await asyncio.sleep(0.01)
        revoked = revoked_registry.get("weixin", revoked_binding.id)
        assert revoked is not None
        assert revoked.status == "revoked"
        assert revoked.enabled is False
        assert revoked_transport.get_rebind_state(revoked_binding.id) is None
        await revoked_transport.close()

    asyncio.run(run())


@pytest.mark.skip(reason="已由候选二维码关闭的直接回归替代")
def test_late_qr_close_only_discards_matching_candidate_generation(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        binding = registry.create_weixin_invitation(principal_id=None)
        binding = registry.update(replace(binding, status="awaiting_first_dm"))
        binding = registry.update(
            replace(
                binding,
                status="active",
                private={
                    **binding.private,
                    "bot_token": "old-token",
                    "ilink_bot_id": "old-bot",
                    "from_user_id": "old-user",
                    "context_token": "old-context",
                    "connected": True,
                },
            )
        )
        presenter = GenerationQrPresenter()
        transport = WeixinTransport(
            registry,
            SimpleNamespace(accept=lambda _message: None),
            RebindProgressIlink(),
            qr_presenter=presenter,
            poll_interval_seconds=60,
        )

        await transport.start_rebind(binding.id)
        first_generation = presenter.generations[-1]
        await transport.start_rebind(binding.id)
        second_generation = presenter.generations[-1]
        assert isinstance(first_generation, int)
        assert isinstance(second_generation, int)
        assert first_generation != second_generation

        await transport._on_qr_presentation_closed(binding.id, first_generation)
        assert transport.get_rebind_state(binding.id) == "pending_qr"
        await transport._on_qr_presentation_closed(binding.id, None)
        assert transport.get_rebind_state(binding.id) == "pending_qr"
        await transport._on_qr_presentation_closed(binding.id, second_generation)
        assert transport.get_rebind_state(binding.id) is None
        current = registry.get("weixin", binding.id)
        assert current == binding
        await transport.close()

    asyncio.run(run())


@pytest.mark.skip(reason="已由控制面状态约束覆盖")
def test_rebind_rejects_persisted_initial_pending_qr(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        binding = registry.create_weixin_invitation(principal_id=None)
        transport = WeixinTransport(
            registry,
            SimpleNamespace(accept=lambda _message: None),
            RebindProgressIlink(),
            qr_cache=weixin_module.InMemoryIlinkQrCache(),
        )
        with pytest.raises(ValueError, match="状态不支持"):
            await transport.start_rebind(binding.id)
        assert transport.get_rebind_state(binding.id) is None

    asyncio.run(run())


@pytest.mark.skip(reason="候选切换不再依赖 Host callback")
def test_expired_and_revoked_bindings_can_complete_candidate_rebind(tmp_path: Path) -> None:
    async def run_case(case_root: Path, status: str) -> None:
        registry = ChannelAccountRegistry(case_root, owner_principal_id="owner")
        binding = registry.create_weixin_invitation(principal_id=None)
        if status == "revoked":
            binding = registry.revoke("weixin", binding.id)
        else:
            binding = registry.update(
                replace(binding, status="expired", enabled=False)
            )
        holder: list[WeixinTransport] = []

        async def request_commit(binding_id: str) -> bool:
            return await holder[0].try_commit_rebind(binding_id)

        transport = WeixinTransport(
            registry,
            SimpleNamespace(accept=lambda _message: None),
            RebindProgressIlink(),
            qr_cache=weixin_module.InMemoryIlinkQrCache(),
            poll_interval_seconds=0.01,
            request_rebind_commit=request_commit,
        )
        holder.append(transport)
        await transport.start_rebind(binding.id)
        for _ in range(50):
            current = registry.get("weixin", binding.id)
            if current is not None and current.status == "awaiting_first_dm":
                break
            await asyncio.sleep(0.01)
        current = registry.get("weixin", binding.id)
        assert current is not None
        assert current.status == "awaiting_first_dm"
        assert current.enabled is True
        assert current.private.get("bot_token") == "candidate-token"
        await transport.close()

    async def run() -> None:
        await run_case(tmp_path / "expired", "expired")
        await run_case(tmp_path / "revoked", "revoked")

    asyncio.run(run())


def transport_account(registry: ChannelAccountRegistry, binding_id: str):
    return registry.get("weixin", binding_id)


def test_http_client_uses_documented_updates_and_send_protocol(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        binding = registry.create_weixin_invitation(principal_id=None)
        binding = registry.update(replace(binding, status="awaiting_first_dm"))
        binding = registry.update(
            replace(
                binding,
                status="active",
                private={
                    **binding.private,
                    "bot_token": "private-token",
                    "ilink_bot_id": "private-bot",
                    "from_user_id": "private-user",
                    "context_token": "private-context",
                    "cursor": "private-cursor",
                },
            )
        )
        http = RecordingHttpClient(
            [
                {"ret": 0, "msgs": [], "get_updates_buf": "next-cursor"},
                {"ret": 0},
            ]
        )
        client = HttpxIlinkClient(http_client=http)

        polled = await client.poll(binding)
        assert await client.send_text(binding, "hello") is True

        assert polled.private_update == {"cursor": "next-cursor"}
        updates = http.requests[0]
        assert updates["url"].endswith("/ilink/bot/getupdates")
        assert updates["json"] == {
            "get_updates_buf": "private-cursor",
            "base_info": {"channel_version": "turning-good-agent"},
        }
        assert updates["headers"]["AuthorizationType"] == "ilink_bot_token"
        assert updates["headers"]["X-WECHAT-UIN"]
        sent = http.requests[1]
        assert sent["url"].endswith("/ilink/bot/sendmessage")
        assert sent["json"]["msg"] == {
            "from_user_id": "",
            "to_user_id": "private-user",
            "client_id": sent["json"]["msg"]["client_id"],
            "message_type": 2,
            "message_state": 2,
            "item_list": [{"type": 1, "text_item": {"text": "hello"}}],
            "context_token": "private-context",
        }

    asyncio.run(run())


def test_http_client_classifies_credential_rejection_as_expired(tmp_path: Path) -> None:
    async def run() -> None:
        registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
        binding = registry.create_weixin_invitation(principal_id=None)
        http = RecordingHttpClient([])
        http.responses = []

        async def request(_method: str, _url: str, **_kwargs):
            return UnauthorizedHttpResponse()

        http.request = request
        client = HttpxIlinkClient(http_client=http)

        try:
            await client.poll(binding)
        except ilink_module.IlinkCredentialExpiredError:
            pass
        else:
            raise AssertionError("credential rejection must be classified")

    asyncio.run(run())


def test_missing_required_weixin_message_fields_do_not_lock_or_submit(tmp_path: Path) -> None:
    async def run() -> None:
        registry, binding, client, submitted, transport = _make_transport(tmp_path / "message_type")
        event = _event("missing-message-type", "first", text="hello")
        event.pop("message_type")
        client.events.append(event)

        await transport.poll_once(binding.id)

        current = registry.get("weixin", binding.id)
        assert current is not None
        assert current.private.get("from_user_id") is None
        assert submitted == []

        registry, binding, client, submitted, transport = _make_transport(tmp_path / "chatroom")
        event = _event("missing-chat-type-group", "room@chatroom", text="hello")
        event.pop("chat_type")
        client.events.append(event)

        await transport.poll_once(binding.id)

        current = registry.get("weixin", binding.id)
        assert current is not None
        assert current.private.get("from_user_id") is None
        assert submitted == []

    asyncio.run(run())


def test_delta_and_wrong_recipient_are_not_sent(tmp_path: Path) -> None:
    async def run() -> None:
        registry, binding, client, _submitted, transport = _make_transport(tmp_path)
        current = registry.get("weixin", binding.id)
        assert current is not None
        registry.update(
            replace(current, status="active", private={**current.private, "context_token": "ctx", "connected": True})
        )
        delta = OutboundMessage(
            "out-2", "opaque", recipient=Recipient(current.principal_id, "weixin", current.id), content="x", event_type="response.delta"
        )
        wrong = OutboundMessage(
            "out-3", "opaque", recipient=Recipient("other", "weixin", current.id), content="x", event_type="response.completed"
        )
        assert await transport.send(delta) is False
        assert await transport.send(wrong) is False
        assert client.sent == []

    asyncio.run(run())

from __future__ import annotations

import asyncio
import inspect
import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from ..bus.messages import ChannelRoute, InboundMessage, OutboundMessage, Recipient
from ..gateway.routing import derive_session_id
from ..channels.base import ChannelCapabilities, ChannelTransport
from .im import ImGatewayCoordinator
from .registry import ChannelAccount, ChannelAccountRegistry
from .weixin_ilink import (
    HttpxIlinkClient,
    IlinkClient,
    IlinkCredentialExpiredError,
    IlinkLoginResult,
    IlinkPollResult,
)
from .weixin_qr import LocalIlinkQrPresenter


logger = logging.getLogger(__name__)
_DEFAULT_MAX_MESSAGE_BYTES = 2_048
_DEFAULT_POLL_INTERVAL_SECONDS = 1.0
_DEFAULT_QR_TTL_SECONDS = 300.0
_MAX_QR_CONTENT_LENGTH = 8 * 1024 * 1024
_TEXT_NOTICE = "当前仅支持文本"
_SUPPRESSED_OUTBOUND_EVENTS = {"response.delta", "response.status"}


def split_utf8_chunks(text: str, *, max_bytes: int = _DEFAULT_MAX_MESSAGE_BYTES) -> tuple[str, ...]:
    """按 UTF-8 字节边界分片，不切断多字节字符。"""
    if max_bytes <= 0:
        raise ValueError("max_bytes 必须大于 0")
    if not text:
        return ()
    chunks: list[str] = []
    current: list[str] = []
    current_bytes = 0
    for character in text:
        size = len(character.encode("utf-8"))
        if size > max_bytes:
            raise ValueError("max_bytes 小于单个 UTF-8 字符")
        if current and current_bytes + size > max_bytes:
            chunks.append("".join(current))
            current = []
            current_bytes = 0
        current.append(character)
        current_bytes += size
    if current:
        chunks.append("".join(current))
    return tuple(chunks)


ClientFactory = Callable[[ChannelAccount], IlinkClient]


class IlinkQrPresenter(Protocol):
    async def present(self, binding_id: str, qr_content: str) -> bool: ...

    async def dismiss(self, binding_id: str) -> None: ...

    async def close(self) -> None: ...

    def set_closed_callback(self, callback: Callable[[str], object]) -> None: ...

    def is_presenting(self, binding_id: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class IlinkQrSnapshot:
    """仅供本机控制面短时展示的二维码快照。"""

    content: str = field(repr=False)
    expires_at: float


class IlinkQrCache(Protocol):
    def put(self, binding_id: str, content: str) -> bool: ...

    def get(self, binding_id: str) -> IlinkQrSnapshot | None: ...

    def clear(self, binding_id: str) -> None: ...

    def clear_all(self) -> None: ...


class InMemoryIlinkQrCache:
    """保存二维码原文的短时内存缓存，不写 Registry 或其他持久化介质。"""

    def __init__(
        self,
        *,
        ttl_seconds: float = _DEFAULT_QR_TTL_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds 必须大于 0")
        self.ttl_seconds = float(ttl_seconds)
        self._clock = clock or time.time
        self._entries: dict[str, IlinkQrSnapshot] = {}

    def put(self, binding_id: str, content: str) -> bool:
        if not isinstance(binding_id, str) or not binding_id:
            return False
        if not isinstance(content, str):
            return False
        content = content.strip()
        if not content or len(content) > _MAX_QR_CONTENT_LENGTH:
            return False
        now = self._clock()
        self._entries[binding_id] = IlinkQrSnapshot(content, now + self.ttl_seconds)
        return True

    def get(self, binding_id: str) -> IlinkQrSnapshot | None:
        snapshot = self._entries.get(binding_id)
        if snapshot is None:
            return None
        if snapshot.expires_at <= self._clock():
            self._entries.pop(binding_id, None)
            return None
        return snapshot

    def clear(self, binding_id: str) -> None:
        self._entries.pop(binding_id, None)

    def clear_all(self) -> None:
        self._entries.clear()


class WeixinTransport(ChannelTransport):
    """个人微信 iLink Transport；所有 Binding 共享一个出站消费者。"""

    name = "weixin"
    capabilities = ChannelCapabilities()

    def __init__(
        self,
        registry: ChannelAccountRegistry,
        coordinator: ImGatewayCoordinator,
        client: IlinkClient | ClientFactory | None = None,
        *,
        client_factory: ClientFactory | None = None,
        qr_presenter: IlinkQrPresenter | None = None,
        qr_cache: IlinkQrCache | None = None,
        poll_interval_seconds: float = _DEFAULT_POLL_INTERVAL_SECONDS,
        max_message_bytes: int = _DEFAULT_MAX_MESSAGE_BYTES,
    ) -> None:
        if poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds 必须大于 0")
        if max_message_bytes <= 0:
            raise ValueError("max_message_bytes 必须大于 0")
        if client is not None and client_factory is not None:
            raise ValueError("client 与 client_factory 不能同时传入")
        self.registry = registry
        self.coordinator = coordinator
        self._client = client if client_factory is None else client_factory
        self._client_factory = client_factory
        if client is not None and callable(client) and not hasattr(client, "poll"):
            self._client_factory = client  # type: ignore[assignment]
            self._client = None
        self.poll_interval_seconds = poll_interval_seconds
        self.max_message_bytes = max_message_bytes
        self._qr_presenter = qr_presenter
        self._qr_cache = qr_cache
        self._configure_qr_presenter()
        self._clients: dict[str, IlinkClient] = {}
        self._poll_tasks: dict[str, asyncio.Task[None]] = {}
        self._started = False
        self._closed = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._closed = False
        for account in self.registry.list_accounts(platform="weixin"):
            if account.enabled and account.status not in {"revoked", "disabled", "expired"}:
                await self._prepare_binding(account)

    async def close(self) -> None:
        self._closed = True
        self._started = False
        tasks = tuple(self._poll_tasks.values())
        self._poll_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        for client in tuple(self._clients.values()):
            close = getattr(client, "close", None)
            if close is not None:
                result = close()
                if inspect.isawaitable(result):
                    await result
        self._clients.clear()
        await self._close_qr_presenter()
        self._clear_all_qr()

    def get_qr(self, binding_id: str) -> IlinkQrSnapshot | None:
        """返回仍处于待扫码生命周期的内存二维码，不读取 Registry 私有二维码字段。"""
        if self._qr_cache is None:
            return None
        account = self.registry.get("weixin", binding_id)
        if account is None or not account.enabled or account.status != "pending_qr":
            self._clear_qr(binding_id)
            return None
        return self._qr_cache.get(binding_id)

    async def enable_account(self, binding_id: str) -> None:
        account = self.registry.get("weixin", binding_id)
        if account is None:
            raise ValueError("微信 Binding 不存在")
        if self._started:
            await self._prepare_binding(account)

    async def disable_account(self, binding_id: str) -> None:
        await self._dismiss_qr(binding_id)
        task = self._poll_tasks.pop(binding_id, None)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    async def reload_account(self, binding_id: str) -> None:
        await self.disable_account(binding_id)
        account = self.registry.get("weixin", binding_id)
        if account is not None and account.enabled and self._started:
            await self._prepare_binding(account)

    async def send(self, message: OutboundMessage) -> bool:
        if message.recipient.channel != self.name:
            return False
        if (
            message.event_type in _SUPPRESSED_OUTBOUND_EVENTS
            or message.event_type.startswith("tool.")
        ):
            return False
        if message.disposition == "chat_reply" and message.event_type not in {
            "response.completed",
            "response.error",
        }:
            return False
        if message.disposition not in {"chat_reply", "proactive_notification"}:
            return False
        account = self.registry.get("weixin", message.recipient.conversation_id)
        if account is None or account.principal_id != message.recipient.principal_id:
            return False
        if (
            account.enabled
            and account.status == "active"
            and (
                not isinstance(account.private.get("context_token"), str)
                or not account.private.get("context_token")
            )
        ):
            await self._mark_disconnected(account)
            return False
        if not self._is_active_account(account):
            return False
        client = self._clients.get(account.id)
        if client is None:
            client = await self._client_for(account)
        for chunk in split_utf8_chunks(message.content, max_bytes=self.max_message_bytes):
            try:
                accepted = await client.send_text(account, chunk)
            except asyncio.CancelledError:
                raise
            except IlinkCredentialExpiredError:
                await self._expire_binding(account)
                return False
            except Exception:
                logger.warning("Weixin send failed for binding %s", account.id)
                await self._mark_disconnected(account)
                return False
            if accepted is False:
                await self._mark_disconnected(account)
                return False
        return True

    def is_deliverable(self, recipient: Recipient) -> bool:
        if recipient.channel != self.name:
            return False
        account = self.registry.get("weixin", recipient.conversation_id)
        return (
            account is not None
            and account.principal_id == recipient.principal_id
            and self._is_active_account(account)
            and account.id in self._clients
        )

    async def poll_once(self, binding_id: str) -> int:
        account = self.registry.get("weixin", binding_id)
        if account is not None and account.enabled and account.status == "pending_qr":
            await self.progress_login_once(binding_id)
            return 0
        if (
            account is None
            or not account.enabled
            or account.status not in {"awaiting_first_dm", "active"}
        ):
            return 0
        client = await self._client_for(account)
        try:
            result = await client.poll(account)
        except asyncio.CancelledError:
            raise
        except IlinkCredentialExpiredError:
            await self._expire_binding(account)
            return 0
        except Exception:
            logger.warning("Weixin poll failed for binding %s", account.id)
            await self._mark_disconnected(account)
            return 0
        account = self._apply_private_update(account, result.private_update)
        accepted = 0
        for event in result.events:
            if await self._handle_event(account, event):
                accepted += 1
            refreshed = self.registry.get("weixin", account.id)
            if refreshed is not None:
                account = refreshed
        return accepted

    async def progress_login_once(self, binding_id: str) -> str:
        account = self.registry.get("weixin", binding_id)
        if account is None:
            raise ValueError("微信 Binding 不存在")
        if not account.enabled or account.status != "pending_qr":
            return account.status
        client = await self._client_for(account)
        try:
            if self._qr_cache is None:
                qr_available = True
            else:
                qr_available = self.get_qr(account.id) is not None
            should_continue_login = bool(account.private.get("qrcode")) and qr_available
            if should_continue_login:
                result = await client.continue_login(account)
            else:
                result = await client.begin_login(account)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Weixin login failed for binding %s", account.id)
            failed = self._fail_pending_qr(account, credential_state="invalid")
            await self._dismiss_qr(account.id)
            return failed.status
        account = self._apply_login_result(account, result)
        qr_content = getattr(result, "qr_content", None)
        if isinstance(qr_content, str) and qr_content:
            cached = self._cache_qr(account.id, qr_content)
            presented = True
            if self._qr_presenter is not None:
                presented = await self._present_qr(account.id, qr_content)
            if not presented and not cached:
                failed = self._fail_pending_qr(account)
                await self._dismiss_qr(account.id)
                return failed.status
        elif account.status == "pending_qr":
            cached = self.get_qr(account.id)
            if self._qr_presenter is None:
                if cached is None:
                    return self._fail_pending_qr(account).status
            elif cached is None and not self._is_qr_presenting(account.id):
                return self._fail_pending_qr(account).status
        elif account.status != "pending_qr":
            await self._dismiss_qr(account.id)
        return account.status

    async def _prepare_binding(self, account: ChannelAccount) -> None:
        await self._client_for(account)
        if account.status == "pending_qr":
            await self.progress_login_once(account.id)
            account = self.registry.get("weixin", account.id) or account
        if account.status in {"pending_qr", "awaiting_first_dm", "active"} and account.id not in self._poll_tasks:
            self._poll_tasks[account.id] = asyncio.create_task(
                self._poll_loop(account.id), name=f"weixin-poll-{account.id}"
            )

    async def _poll_loop(self, binding_id: str) -> None:
        try:
            while not self._closed:
                await self.poll_once(binding_id)
                account = self.registry.get("weixin", binding_id)
                if account is None or account.status not in {
                    "pending_qr",
                    "awaiting_first_dm",
                    "active",
                }:
                    return
                await asyncio.sleep(self.poll_interval_seconds)
        finally:
            self._poll_tasks.pop(binding_id, None)

    async def _client_for(self, account: ChannelAccount) -> IlinkClient:
        client = self._clients.get(account.id)
        if client is not None:
            return client
        if self._client_factory is not None:
            client = self._client_factory(account)
        elif self._client is not None:
            client = self._client  # type: ignore[assignment]
        else:
            base_url = str(account.private.get("base_url", "https://ilinkai.weixin.qq.com"))
            client = HttpxIlinkClient(base_url)
        self._clients[account.id] = client
        return client

    async def _handle_event(self, account: ChannelAccount, event: dict[str, object]) -> bool:
        parsed = _parse_event(event)
        if parsed is None:
            return False
        message_key, sender, is_text, content, metadata = parsed
        inbound_id = f"weixin:{account.id}:{message_key}"
        metadata["binding_id"] = account.id
        if not _is_private_chat(event):
            return False
        if not self.registry.claim_inbound("weixin", account.id, inbound_id):
            return False
        locked_sender = account.private.get("from_user_id")
        if not isinstance(locked_sender, str) or not locked_sender:
            if not is_text or not content.strip():
                return False
            account = self._update_account(
                account,
                status="active",
                subscribed=account.subscribed,
                private={
                    "from_user_id": sender,
                    "conversation_id": account.id,
                    "context_token": event.get("context_token"),
                    "connected": True,
                },
            )
        elif sender != locked_sender:
            return False
        context_token = event.get("context_token")
        if isinstance(context_token, str) and context_token:
            account = self._update_account(
                account,
                private={"context_token": context_token, "connected": True},
            )
        if not is_text:
            await self._send_notice(account, _TEXT_NOTICE)
            return False
        content = content.strip()
        if not content:
            return False
        route = ChannelRoute(
            account.principal_id,
            self.name,
            account.id,
            derive_session_id(account.principal_id, self.name, account.id),
        )
        inbound = InboundMessage(
            inbound_id,
            route=route,
            content=content,
            attachments=[],
            metadata=metadata,
        )
        return await self.coordinator.accept(inbound)

    async def _send_notice(self, account: ChannelAccount, content: str) -> None:
        client = self._clients.get(account.id)
        if client is None:
            return
        try:
            accepted = await client.send_text(account, content)
        except IlinkCredentialExpiredError:
            await self._expire_binding(account)
            return
        except Exception:
            accepted = False
        if accepted is False:
            await self._mark_disconnected(account)

    def _apply_login_result(self, account: ChannelAccount, result: IlinkLoginResult) -> ChannelAccount:
        return self._update_account(account, status=result.status, private=result.private_update)

    def _apply_private_update(self, account: ChannelAccount, update: dict[str, object]) -> ChannelAccount:
        if not update:
            return account
        return self._update_account(account, private=update)

    def _update_account(
        self,
        account: ChannelAccount,
        *,
        status: str | None = None,
        subscribed: bool | None = None,
        private: dict[str, object] | None = None,
    ) -> ChannelAccount:
        current = self.registry.get("weixin", account.id) or account
        merged = dict(current.private)
        if private:
            merged.update(private)
        return self.registry.update(
            replace(
                current,
                status=status or current.status,
                subscribed=current.subscribed if subscribed is None else subscribed,
                private=merged,
            )
        )

    async def _mark_disconnected(self, account: ChannelAccount) -> None:
        current = self.registry.get("weixin", account.id)
        if current is None or current.status == "revoked":
            return
        try:
            self.registry.update(replace(current, private={**current.private, "connected": False}))
        except ValueError:
            logger.debug("Unable to mark Weixin binding %s disconnected", account.id)

    async def _expire_binding(self, account: ChannelAccount) -> None:
        await self._dismiss_qr(account.id)
        current = self.registry.get("weixin", account.id)
        if current is None or current.status == "revoked":
            return
        private = dict(current.private)
        for field_name in (
            "bot_token",
            "ilink_bot_id",
            "ilink_user_id",
            "from_user_id",
            "context_token",
            "cursor",
            "qrcode",
            "qr_status",
        ):
            private.pop(field_name, None)
        private.update({"credential_state": "expired", "connected": False})
        try:
            self.registry.replace_private_state(
                replace(current, status="expired", enabled=False, private=private)
            )
        except ValueError:
            logger.debug("Unable to expire Weixin binding %s", account.id)

    async def _present_qr(self, binding_id: str, qr_content: str) -> bool:
        if self._qr_presenter is None:
            return False
        try:
            return (await self._qr_presenter.present(binding_id, qr_content)) is not False
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Unable to present Weixin QR locally for binding %s", binding_id)
            return False

    async def _dismiss_qr(self, binding_id: str) -> None:
        self._clear_qr(binding_id)
        if self._qr_presenter is None:
            return
        dismiss = getattr(self._qr_presenter, "dismiss", None)
        if dismiss is None:
            return
        try:
            result = dismiss(binding_id)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Unable to dismiss Weixin QR locally for binding %s", binding_id)

    async def _close_qr_presenter(self) -> None:
        if self._qr_presenter is None:
            return
        close = getattr(self._qr_presenter, "close", None)
        if close is None:
            return
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Unable to close Weixin QR presenter")

    def _cache_qr(self, binding_id: str, content: str) -> bool:
        if self._qr_cache is None:
            return False
        try:
            return self._qr_cache.put(binding_id, content)
        except Exception:
            logger.warning("Unable to cache Weixin QR for binding %s", binding_id)
            return False

    def _clear_qr(self, binding_id: str) -> None:
        if self._qr_cache is None:
            return
        try:
            self._qr_cache.clear(binding_id)
        except Exception:
            logger.debug("Unable to clear Weixin QR for binding %s", binding_id)

    def _clear_all_qr(self) -> None:
        if self._qr_cache is None:
            return
        try:
            self._qr_cache.clear_all()
        except Exception:
            logger.debug("Unable to clear Weixin QR cache")

    def _configure_qr_presenter(self) -> None:
        if self._qr_presenter is None:
            return
        set_callback = getattr(self._qr_presenter, "set_closed_callback", None)
        if set_callback is None:
            return
        try:
            set_callback(self._on_qr_presentation_closed)
        except Exception:
            logger.warning("Unable to configure Weixin QR presenter")

    async def _on_qr_presentation_closed(self, binding_id: str) -> None:
        account = self.registry.get("weixin", binding_id)
        if account is None or account.status != "pending_qr":
            return
        self._fail_pending_qr(account)

    def _is_qr_presenting(self, binding_id: str) -> bool:
        if self._qr_presenter is None:
            return False
        is_presenting = getattr(self._qr_presenter, "is_presenting", None)
        if is_presenting is None:
            return False
        try:
            return bool(is_presenting(binding_id))
        except Exception:
            return False

    def _fail_pending_qr(
        self,
        account: ChannelAccount,
        *,
        credential_state: str = "local_qr_unavailable",
    ) -> ChannelAccount:
        self._clear_qr(account.id)
        current = self.registry.get("weixin", account.id) or account
        private = dict(current.private)
        private.update(
            {
                "credential_state": credential_state,
                "connected": False,
                "qrcode": None,
                "qr_status": None,
            }
        )
        return self.registry.update(replace(current, status="failed", private=private))

    @staticmethod
    def _is_active_account(account: ChannelAccount) -> bool:
        return bool(
            account.enabled
            and account.status == "active"
            and account.private.get("context_token")
            and account.private.get("from_user_id")
            and account.private.get("connected", True)
        )


def _parse_event(event: object) -> tuple[str, str, bool, str, dict[str, object]] | None:
    if not isinstance(event, dict):
        return None
    message_id = event.get("message_id", event.get("msg_id", event.get("seq")))
    sender = event.get("from_user_id", event.get("fromUserId"))
    if not isinstance(message_id, (str, int)) or not str(message_id):
        return None
    if not isinstance(sender, str) or not sender:
        return None
    message_type_value = event.get("message_type", event.get("msg_type"))
    if not isinstance(message_type_value, (str, int)) or str(message_type_value) == "":
        return None
    message_type = str(message_type_value).lower()
    is_text = message_type in {"text", "1", "txt"}
    content = ""
    has_non_text_item = False
    items = event.get("item_list", event.get("items", []))
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            item_type = str(item.get("type", item.get("item_type", message_type))).lower()
            if item_type in {"text", "1", "txt"}:
                text_item = item.get("text_item")
                nested_text = text_item.get("text") if isinstance(text_item, dict) else None
                value = item.get("text", item.get("content", nested_text or ""))
                if isinstance(value, str):
                    content += value
                    is_text = True
            elif item_type:
                has_non_text_item = True
    if has_non_text_item:
        is_text = False
    metadata: dict[str, object] = {
        "platform": "weixin",
        "binding_id": str(event.get("binding_id", "")),
        "message_id": str(message_id),
        "session_type": "p2p",
    }
    for key in ("seq", "create_time_ms"):
        value = event.get(key)
        if isinstance(value, (str, int, float)):
            metadata[key] = value
    metadata = {key: value for key, value in metadata.items() if value != ""}
    return str(message_id), sender, is_text, content, metadata


def _is_private_chat(event: dict[str, object]) -> bool:
    chat_type = event.get("chat_type", event.get("conversation_type"))
    if not isinstance(chat_type, (str, int)) or str(chat_type) == "":
        return False
    return str(chat_type).lower() in {"p2p", "private", "direct", "single", "1"}

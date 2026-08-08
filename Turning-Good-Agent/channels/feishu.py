from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from dataclasses import replace
from typing import Any

from ..bus.messages import ChannelRoute, InboundMessage, OutboundMessage, Recipient
from ..gateway.routing import derive_session_id
from .base import ChannelCapabilities, ChannelTransport
from .feishu_ws import FeishuClient, FeishuConnectionState, LarkFeishuClient
from .im import ImGatewayCoordinator
from .registry import ChannelAccount, ChannelAccountRegistry


logger = logging.getLogger(__name__)
_OWNER_CODE_TTL_SECONDS = 10 * 60
_OWNER_CODE_DOMAIN = b"tga-feishu-owner-code-v1\0"
_SUPPRESSED_OUTBOUND_EVENTS = {"response.status"}
_APP_SCOPED_PRIVATE_FIELDS = {
    "owner_open_id",
    "conversation_id",
    "owner_code_hash",
    "owner_code_expires_at",
}


class FeishuTransport(ChannelTransport):
    """官方飞书 Bot 多实例 Transport。"""

    name = "feishu"
    capabilities = ChannelCapabilities()

    def __init__(
        self,
        registry: ChannelAccountRegistry,
        coordinator: ImGatewayCoordinator,
        client: FeishuClient | None = None,
        *,
        client_factory: Callable[[], FeishuClient] | None = None,
        owner_principal_id: str | None = None,
    ) -> None:
        if client is not None and client_factory is not None:
            raise ValueError("client 与 client_factory 不能同时传入")
        self.registry = registry
        self.coordinator = coordinator
        self.owner_principal_id = owner_principal_id or registry.owner_principal_id
        self._client = client
        self._client_factory = client_factory
        self._started = False
        self._clients: dict[str, FeishuClient] = {}
        self._states: dict[str, FeishuConnectionState] = {}
        self._lock = asyncio.Lock()
        self._account_locks: dict[str, asyncio.Lock] = {}

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        for account in self.registry.list_accounts(platform="feishu"):
            if not account.enabled or account.status in {"disabled", "revoked"}:
                continue
            await self._start_account(account)

    async def close(self) -> None:
        self._started = False
        async with self._lock:
            account_ids = {account_id for account_id in self._clients}
        account_ids.update(
            account.id for account in self.registry.list_accounts(platform="feishu")
        )
        for account_id in sorted(account_ids):
            async with self._account_lifecycle_lock(account_id):
                async with self._lock:
                    client = self._clients.get(account_id)
                if client is None:
                    continue
                try:
                    await client.stop_bot(account_id)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.warning("Feishu Bot close failed for instance %s", account_id)
                    continue
                async with self._lock:
                    if self._clients.get(account_id) is client:
                        self._clients.pop(account_id, None)
                        self._states.pop(account_id, None)

    async def enable_account(self, account_id: str) -> None:
        async with self._account_lifecycle_lock(account_id):
            account = self.registry.get("feishu", account_id)
            if account is not None and self._started:
                await self._start_account_locked(account)

    async def disable_account(self, account_id: str) -> None:
        async with self._account_lifecycle_lock(account_id):
            async with self._lock:
                client = self._clients.get(account_id)
            if client is not None:
                await client.stop_bot(account_id)
            async with self._lock:
                if self._clients.get(account_id) is client:
                    self._clients.pop(account_id, None)
                    self._states.pop(account_id, None)

    async def reload_account(
        self,
        account_id: str,
        candidate: ChannelAccount | None = None,
    ) -> bool:
        async with self._account_lifecycle_lock(account_id):
            current = self.registry.get("feishu", account_id)
            if current is None or not self._started:
                return False
            selected = candidate or current
            if selected.id != current.id or selected.principal_id != current.principal_id:
                return False
            try:
                self.registry.validate_update(selected)
            except ValueError:
                return False
            replace_private = selected.private.get("app_id") != current.private.get("app_id")
            if replace_private:
                private = dict(selected.private)
                for field_name in _APP_SCOPED_PRIVATE_FIELDS:
                    private.pop(field_name, None)
                selected = replace(
                    selected,
                    status="awaiting_owner_code",
                    private=private,
                )
            return await self._start_account_locked(
                selected,
                replacing=True,
                replace_private=replace_private,
                expected_updated_at=current.updated_at,
            )

    def issue_owner_code(self, account_id: str, *, code: str | None = None) -> str:
        """生成一次性 Owner 验证码；明文只由本机控制调用方短暂持有。"""
        account = self.registry.get("feishu", account_id)
        if account is None:
            raise ValueError("飞书账号不存在")
        if account.principal_id != self.owner_principal_id:
            raise ValueError("飞书账号主体无效")
        selected = code or f"{secrets.randbelow(1_000_000):06d}"
        if not selected.isdigit() or len(selected) != 6:
            raise ValueError("验证码必须是六位数字")
        expires_at = datetime.now(UTC) + timedelta(seconds=_OWNER_CODE_TTL_SECONDS)
        private = dict(account.private)
        private["owner_code_hash"] = _hash_owner_code(selected)
        private["owner_code_expires_at"] = expires_at.isoformat()
        owner_locked = isinstance(private.get("owner_open_id"), str) and bool(
            private.get("owner_open_id")
        )
        updated = self.registry.update(
            replace(
                account,
                status=account.status if owner_locked else "awaiting_owner_code",
                enabled=account.enabled if owner_locked else True,
                private=private,
            )
        )
        del updated
        return selected

    async def send(self, message: OutboundMessage) -> bool:
        if message.recipient.channel != self.name:
            return False
        if (
            message.event_type in _SUPPRESSED_OUTBOUND_EVENTS
            or message.event_type.startswith("tool.")
        ):
            return False
        if (
            message.disposition == "proactive_notification"
            and message.event_type == "response.delta"
        ):
            return False
        if message.disposition == "chat_reply" and message.event_type not in {
            "response.delta",
            "response.completed",
            "response.error",
        }:
            return False
        if message.disposition not in {"chat_reply", "proactive_notification"}:
            return False
        account_id, chat_id = _split_conversation(message.recipient.conversation_id)
        account = self.registry.get("feishu", account_id)
        if account is None or account.principal_id != message.recipient.principal_id:
            return False
        if not self.is_deliverable(message.recipient):
            return False
        client = self._clients.get(account.id)
        state = self._states.get(account.id)
        if client is None or state is None:
            return False
        if message.disposition == "proactive_notification":
            return await self._send_final_text(client, account, chat_id, message.content)
        if message.event_type == "response.delta":
            if not state.cardkit_enabled:
                return True
            try:
                accepted = await client.update_card(
                    account,
                    chat_id,
                    message.content,
                    completed=False,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                accepted = False
            if accepted is False:
                self._disable_cardkit(account.id, state)
            return accepted is not False

        if state.cardkit_enabled:
            try:
                card_accepted = await client.update_card(
                    account,
                    chat_id,
                    message.content,
                    completed=True,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                card_accepted = False
            if card_accepted is not False:
                return True
            self._disable_cardkit(account.id, state)
        return await self._send_final_text(client, account, chat_id, message.content)

    def is_deliverable(self, recipient: Recipient) -> bool:
        if recipient.channel != self.name:
            return False
        account_id, _chat_id = _split_conversation(recipient.conversation_id)
        account = self.registry.get("feishu", account_id)
        state = self._states.get(account_id)
        return bool(
            account is not None
            and account.principal_id == recipient.principal_id
            and account.enabled
            and account.status == "active"
            and account.private.get("owner_open_id")
            and state is not None
            and state.connected
        )

    async def _send_final_text(
        self,
        client: FeishuClient,
        account: ChannelAccount,
        chat_id: str,
        content: str,
    ) -> bool:
        try:
            accepted = await client.send_text(account, chat_id, content)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Feishu send failed for instance %s", account.id)
            await self._mark_disconnected(account)
            return False
        if accepted is False:
            await self._mark_disconnected(account)
            return False
        return True

    async def _start_account(
        self,
        account: ChannelAccount,
        *,
        replacing: bool = False,
        replace_private: bool = False,
        expected_updated_at: str | None = None,
    ) -> bool:
        async with self._account_lifecycle_lock(account.id):
            return await self._start_account_locked(
                account,
                replacing=replacing,
                replace_private=replace_private,
                expected_updated_at=expected_updated_at,
            )

    async def _start_account_locked(
        self,
        account: ChannelAccount,
        *,
        replacing: bool = False,
        replace_private: bool = False,
        expected_updated_at: str | None = None,
    ) -> bool:
        if not self._started:
            return False
        previous_account = self.registry.get("feishu", account.id)
        if previous_account is None:
            return False
        expected_revision = expected_updated_at or previous_account.updated_at
        if previous_account.updated_at != expected_revision:
            return False
        previous_client = self._clients.get(account.id)
        client = self._client_factory() if self._client_factory is not None else self._client
        if client is None:
            client = LarkFeishuClient()
        self._set_client_state_handler(client, account.id)
        try:
            state = await client.start_bot(
                account,
                lambda event, account_id=account.id: self._on_event(account_id, event),
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            state = FeishuConnectionState(False, False)
        if not state.connected:
            if client is not previous_client:
                try:
                    await client.stop_bot(account.id)
                except Exception:
                    pass
            if not replacing:
                await self._mark_disconnected_by_id_locked(account.id)
            return False
        owner_locked = bool(account.private.get("owner_open_id"))
        status = "active" if state.connected and owner_locked else (
            "awaiting_owner_code" if state.connected else "failed"
        )
        try:
            updated_account = replace(
                account,
                status=status,
                private={
                    **account.private,
                    "connected": state.connected,
                    "cardkit_enabled": state.cardkit_enabled,
                    "instance_id": account.id,
                },
            )
            updated = (
                self.registry.replace_private_state(
                    updated_account,
                    expected_updated_at=expected_revision,
                )
                if replace_private
                else self.registry.update(
                    updated_account,
                    expected_updated_at=expected_revision,
                )
            )
        except (OSError, ValueError):
            logger.warning("Unable to update Feishu state for instance %s", account.id)
            await self._discard_candidate(
                client,
                previous_client,
                previous_account,
                account.id,
            )
            return False
        if previous_client is not None and previous_client is not client:
            try:
                await previous_client.stop_bot(account.id)
            except asyncio.CancelledError:
                await self._restore_registry(previous_account, updated)
                await self._discard_candidate(
                    client,
                    previous_client,
                    previous_account,
                    account.id,
                )
                raise
            except Exception:
                logger.warning("Feishu previous Bot close failed for instance %s", account.id)
                await self._restore_registry(previous_account, updated)
                await self._discard_candidate(
                    client,
                    previous_client,
                    previous_account,
                    account.id,
                )
                return False
        async with self._lock:
            self._clients[account.id] = client
            self._states[account.id] = state
        return True

    async def _restore_registry(
        self,
        previous_account: ChannelAccount,
        committed_account: ChannelAccount,
    ) -> None:
        try:
            self.registry.replace_private_state(
                previous_account,
                expected_updated_at=committed_account.updated_at,
            )
        except (OSError, ValueError):
            logger.warning(
                "Unable to restore Feishu state for instance %s",
                previous_account.id,
            )

    async def _discard_candidate(
        self,
        client: FeishuClient,
        previous_client: FeishuClient | None,
        previous_account: ChannelAccount,
        account_id: str,
    ) -> None:
        if client is previous_client:
            try:
                restored = await client.start_bot(
                    previous_account,
                    lambda event, current_account_id=account_id: self._on_event(
                        current_account_id,
                        event,
                    ),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.warning("Feishu previous Bot restore failed for instance %s", account_id)
                return
            if restored.connected:
                self._states[account_id] = restored
            return
        try:
            await client.stop_bot(account_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Feishu candidate Bot close failed for instance %s", account_id)

    def _account_lifecycle_lock(self, account_id: str) -> asyncio.Lock:
        return self._account_locks.setdefault(account_id, asyncio.Lock())

    def _set_client_state_handler(self, client: FeishuClient, account_id: str) -> None:
        setter = getattr(client, "set_state_handler", None)
        if not callable(setter):
            return

        async def on_state_change(state: FeishuConnectionState) -> None:
            async with self._lock:
                if self._clients.get(account_id) is not client:
                    return
                self._states[account_id] = state
            if state.connected:
                current = self.registry.get("feishu", account_id)
                if current is None or not current.enabled or current.status == "revoked":
                    return
                if current.status == "failed":
                    try:
                        self.registry.update(
                            replace(
                                current,
                                status="active" if current.private.get("owner_open_id") else "awaiting_owner_code",
                                private={
                                    **current.private,
                                    "connected": True,
                                    "cardkit_enabled": state.cardkit_enabled,
                                },
                            )
                        )
                    except (OSError, ValueError):
                        logger.debug("Unable to restore Feishu state for instance %s", account_id)
                return
            await self._mark_disconnected_by_id(account_id)

        setter(account_id, on_state_change)

    async def _on_event(self, account_id: str, event: dict[str, object]) -> None:
        try:
            await self._handle_event(account_id, event)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Invalid Feishu event for instance %s", account_id)
            await self._mark_disconnected_by_id(account_id)

    async def _handle_event(self, account_id: str, event: dict[str, object]) -> bool:
        account = self.registry.get("feishu", account_id)
        parsed = _parse_event(event)
        if account is None or parsed is None or not account.enabled:
            return False
        event_key, dedupe_keys, sender, chat_id, chat_type, is_text, content, metadata = parsed
        if chat_type != "p2p" or not is_text:
            return False
        inbound_id = f"feishu:{account.id}:{event_key}"
        inbound_ids = tuple(f"feishu:{account.id}:{key}" for key in dedupe_keys)
        if not self.registry.claim_inbounds("feishu", account.id, inbound_ids):
            return False
        owner_open_id = account.private.get("owner_open_id")
        if not isinstance(owner_open_id, str) or not owner_open_id:
            if not self._verify_owner_code(account, content):
                return False
            account = self._lock_owner(account, sender, chat_id)
            return False
        if sender != owner_open_id:
            return False
        if self._owner_reverification_pending(account):
            if self._verify_owner_code(account, content):
                self._complete_owner_reverification(account)
                return False
            if _looks_like_owner_code(content):
                return False
        content = content.strip()
        if not content:
            return False
        account = self._remember_conversation(account, chat_id)
        metadata.update(
            {
                "platform": "feishu",
                "instance_id": account.id,
                "session_type": "p2p",
            }
        )
        route = ChannelRoute(
            account.principal_id,
            self.name,
            f"{account.id}:{chat_id}",
            derive_session_id(account.principal_id, self.name, f"{account.id}:{chat_id}"),
        )
        return await self.coordinator.accept(
            InboundMessage(inbound_id, route=route, content=content, attachments=[], metadata=metadata)
        )

    def _verify_owner_code(self, account: ChannelAccount, content: str) -> bool:
        code_hash = account.private.get("owner_code_hash")
        expires_at = account.private.get("owner_code_expires_at")
        if not isinstance(code_hash, str) or not isinstance(expires_at, str):
            return False
        try:
            if datetime.fromisoformat(expires_at) < datetime.now(UTC):
                return False
        except ValueError:
            return False
        return secrets.compare_digest(code_hash, _hash_owner_code(content.strip()))

    def _lock_owner(self, account: ChannelAccount, sender: str, chat_id: str) -> ChannelAccount:
        private = dict(account.private)
        private["owner_open_id"] = sender
        private["conversation_id"] = f"{account.id}:{chat_id}"
        private.pop("owner_code_hash", None)
        private.pop("owner_code_expires_at", None)
        private["connected"] = True
        return self.registry.update(replace(account, status="active", private=private))

    @staticmethod
    def _owner_reverification_pending(account: ChannelAccount) -> bool:
        return isinstance(account.private.get("owner_code_hash"), str) and isinstance(
            account.private.get("owner_code_expires_at"), str
        )

    def _complete_owner_reverification(self, account: ChannelAccount) -> ChannelAccount:
        return self.registry.remove_private_fields(
            "feishu",
            account.id,
            {"owner_code_hash", "owner_code_expires_at"},
        )

    def _remember_conversation(self, account: ChannelAccount, chat_id: str) -> ChannelAccount:
        conversation_id = f"{account.id}:{chat_id}"
        if account.private.get("conversation_id") == conversation_id:
            return account
        return self.registry.update(
            replace(account, private={**account.private, "conversation_id": conversation_id})
        )

    async def _mark_disconnected(self, account: ChannelAccount) -> None:
        await self._mark_disconnected_by_id(account.id)

    def _disable_cardkit(self, account_id: str, state: FeishuConnectionState) -> None:
        self._states[account_id] = FeishuConnectionState(state.connected, False)
        current = self.registry.get("feishu", account_id)
        if current is None or current.status == "revoked":
            return
        try:
            self.registry.update(
                replace(
                    current,
                    private={**current.private, "cardkit_enabled": False},
                )
            )
        except ValueError:
            logger.debug("Unable to disable CardKit for instance %s", account_id)

    async def _mark_disconnected_by_id(self, account_id: str) -> None:
        async with self._account_lifecycle_lock(account_id):
            await self._mark_disconnected_by_id_locked(account_id)

    async def _mark_disconnected_by_id_locked(self, account_id: str) -> None:
        current = self.registry.get("feishu", account_id)
        if current is None or current.status == "revoked":
            return
        self._states[account_id] = FeishuConnectionState(False, False)
        try:
            self.registry.update(
                replace(
                    current,
                    status="failed" if current.status != "disabled" else current.status,
                    private={**current.private, "connected": False},
                )
            )
        except ValueError:
            logger.debug("Unable to mark Feishu instance %s disconnected", account_id)


def _hash_owner_code(code: str) -> str:
    return hashlib.sha256(_OWNER_CODE_DOMAIN + code.encode("utf-8")).hexdigest()


def _looks_like_owner_code(content: str) -> bool:
    candidate = content.strip()
    return candidate.isascii() and candidate.isdecimal() and len(candidate) == 6


def _split_conversation(conversation_id: str) -> tuple[str, str]:
    account_id, separator, chat_id = conversation_id.partition(":")
    if not separator or not account_id or not chat_id:
        return "", ""
    return account_id, chat_id


def _parse_event(
    event: object,
) -> tuple[str, tuple[str, ...], str, str, str, bool, str, dict[str, object]] | None:
    if not isinstance(event, dict):
        return None
    message = event.get("message") if isinstance(event.get("message"), dict) else event
    sender_payload = event.get("sender") if isinstance(event.get("sender"), dict) else {}
    sender = sender_payload.get("open_id")
    event_key = event.get("event_id")
    message_key = message.get("message_id", event.get("message_id"))
    chat_id = message.get("chat_id")
    chat_type = str(message.get("chat_type", ""))
    message_type = str(message.get("message_type", ""))
    if not isinstance(sender, str) or not sender:
        return None
    if not isinstance(event_key, (str, int)) and not isinstance(message_key, (str, int)):
        return None
    if not isinstance(chat_id, str) or not chat_id:
        return None
    content_value = message.get("content", "")
    content = ""
    if isinstance(content_value, str):
        try:
            decoded = json.loads(content_value)
        except (TypeError, ValueError):
            return None
        if not isinstance(decoded, dict) or not isinstance(decoded.get("text"), str):
            return None
        content = decoded["text"]
    elif isinstance(content_value, dict) and isinstance(content_value.get("text"), str):
        content = content_value["text"]
    else:
        return None
    event_id = str(event_key) if isinstance(event_key, (str, int)) else ""
    message_id = str(message_key) if isinstance(message_key, (str, int)) else ""
    primary_id = message_id or event_id
    dedupe_keys = tuple(dict.fromkeys(key for key in (message_id, event_id) if key))
    metadata: dict[str, object] = {
        "platform": "feishu",
        "event_id": event_id,
        "message_id": message_id,
        "session_type": "p2p",
    }
    for key in ("root_id", "parent_id", "thread_id"):
        value = message.get(key)
        if isinstance(value, str) and value:
            metadata[key] = value
    mention_count = message.get("mention_count")
    if isinstance(mention_count, int) and mention_count > 0:
        metadata["mention_count"] = mention_count
    for key in ("create_time", "create_time_ms"):
        value = message.get(key)
        if isinstance(value, (str, int, float)) and value != "":
            metadata[key] = value
    metadata = {key: value for key, value in metadata.items() if value != ""}
    return (
        primary_id,
        dedupe_keys,
        sender,
        chat_id,
        chat_type.lower(),
        message_type.lower() == "text",
        content,
        metadata,
    )


__all__ = ["FeishuTransport"]

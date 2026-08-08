from __future__ import annotations

import errno
import json
import os
import threading
from collections.abc import Iterable
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, cast
from uuid import UUID, uuid4

from ..bus.messages import Recipient


Platform = Literal["feishu", "weixin"]

_PLATFORMS = frozenset({"feishu", "weixin"})
_STATUSES = frozenset(
    {
        "pending_qr",
        "awaiting_first_dm",
        "awaiting_owner_code",
        "connecting",
        "active",
        "disabled",
        "expired",
        "failed",
        "revoked",
    }
)
_CREDENTIAL_STATES = frozenset({"pending", "configured", "active", "expired", "invalid", "revoked"})
_MAX_INBOUND_IDS = 2_048
_VERSION = 1
_RECORD_LOCK_GUARD = threading.Lock()
_RECORD_LOCKS: dict[str, threading.RLock] = {}
_DIRECTORY_LOCKS: dict[str, threading.RLock] = {}


@dataclass(frozen=True, slots=True)
class ChannelAccount:
    """保存一个平台账号的公开生命周期与私有协议状态。"""

    id: str
    platform: Platform
    principal_id: str
    status: str
    enabled: bool
    subscribed: bool
    created_at: str
    updated_at: str
    private: dict[str, object] = field(repr=False)


@dataclass(frozen=True, slots=True)
class ChannelAccountView:
    """供本机控制面使用的脱敏账号视图。"""

    id: str
    platform: Platform
    principal_id: str
    status: str
    enabled: bool
    subscribed: bool
    credential_state: str
    connected: bool


class ChannelAccountRegistry:
    """管理 Gateway 本地的 IM 账号和 Binding 私有状态。"""

    def __init__(self, data_dir: Path, *, owner_principal_id: str = "local-user") -> None:
        self.data_dir = Path(data_dir)
        self.root = self.data_dir / "channels"
        self.owner_principal_id = _validate_principal_id(owner_principal_id)

    def create_weixin_invitation(self, *, principal_id: str | None) -> ChannelAccount:
        """创建一条等待扫码的个人微信 Binding。"""
        resolved_principal = (
            _validate_principal_id(principal_id)
            if principal_id is not None
            else f"principal-{uuid4().hex}"
        )
        account = ChannelAccount(
            id=str(uuid4()),
            platform="weixin",
            principal_id=resolved_principal,
            status="pending_qr",
            enabled=True,
            subscribed=False,
            created_at=_utc_now(),
            updated_at=_utc_now(),
            private={"credential_state": "pending", "inbound_ids": []},
        )
        with self._directory_lock():
            self._write_account(account)
        return account

    def register_feishu_bot(self, *, app_id: str, app_secret: str, domain: str) -> ChannelAccount:
        """登记一个仅由 Owner 使用的飞书官方 Bot。"""
        _validate_protocol_value(app_id, field_name="app_id")
        _validate_protocol_value(app_secret, field_name="app_secret")
        _validate_protocol_value(domain, field_name="domain")
        with self._directory_lock():
            if any(
                account.platform == "feishu" and account.private.get("app_id") == app_id
                for account in self.list_accounts(platform="feishu")
            ):
                raise ValueError("feishu app_id 已登记")
            now = _utc_now()
            account = ChannelAccount(
                id=str(uuid4()),
                platform="feishu",
                principal_id=self.owner_principal_id,
                status="awaiting_owner_code",
                enabled=True,
                subscribed=False,
                created_at=now,
                updated_at=now,
                private={
                    "app_id": app_id,
                    "app_secret": app_secret,
                    "domain": domain,
                    "credential_state": "configured",
                    "inbound_ids": [],
                },
            )
            self._write_account(account)
        return account

    def get(self, platform: Platform, account_id: str) -> ChannelAccount | None:
        """读取指定账号，不存在时返回 None。"""
        platform = _validate_platform(platform)
        account_id = _validate_account_id(account_id)
        with self._record_lock(platform, account_id):
            return self._read_account(platform, account_id)

    def list_accounts(self, *, platform: Platform | None = None) -> tuple[ChannelAccount, ...]:
        """返回 Transport 使用的私有账号记录，不供控制面直接序列化。"""
        platforms = (
            (_validate_platform(platform),)
            if platform is not None
            else tuple(sorted(_PLATFORMS))
        )
        accounts: list[ChannelAccount] = []
        for item_platform in platforms:
            directory = self.root / item_platform
            if not directory.exists():
                continue
            for path in sorted(directory.glob("*.json")):
                try:
                    account_id = _validate_account_id(path.stem)
                    with self._record_lock(item_platform, account_id):
                        account = self._read_account(item_platform, account_id)
                except (OSError, ValueError, json.JSONDecodeError):
                    continue
                if account is not None:
                    accounts.append(account)
        return tuple(accounts)

    def list_views(self) -> tuple[ChannelAccountView, ...]:
        """返回不含凭据、二维码、cursor 或外部身份的账号视图。"""
        return tuple(self._view(account) for account in self.list_accounts())

    def update(
        self,
        account: ChannelAccount,
        *,
        expected_updated_at: str | None = None,
    ) -> ChannelAccount:
        """更新同一账号的状态与私有协议字段，并保留身份。"""
        normalized = self._validate_account(account)
        with self._directory_lock():
            with self._record_lock(normalized.platform, normalized.id):
                current = self._read_account(normalized.platform, normalized.id)
                if current is None:
                    raise ValueError("账号不存在")
                self._validate_expected_updated_at(current, expected_updated_at)
                private = self._validated_private_for_update(current, normalized)
                updated = ChannelAccount(
                    id=current.id,
                    platform=current.platform,
                    principal_id=current.principal_id,
                    status=normalized.status,
                    enabled=normalized.enabled,
                    subscribed=normalized.subscribed,
                    created_at=current.created_at,
                    updated_at=_utc_now(),
                    private=private,
                )
                self._write_account(updated)
                return updated

    def validate_update(self, account: ChannelAccount) -> None:
        """校验候选更新但不写入，供 Transport 启动前的生命周期预检使用。"""
        normalized = self._validate_account(account)
        with self._directory_lock():
            with self._record_lock(normalized.platform, normalized.id):
                current = self._read_account(normalized.platform, normalized.id)
                if current is None:
                    raise ValueError("账号不存在")
                self._validated_private_for_update(current, normalized)

    def remove_private_fields(
        self,
        platform: Platform,
        account_id: str,
        field_names: set[str],
    ) -> ChannelAccount:
        """原子移除私有协议字段，不影响账号其余生命周期状态。"""
        platform = _validate_platform(platform)
        account_id = _validate_account_id(account_id)
        if not field_names or any(not isinstance(name, str) or not name for name in field_names):
            raise ValueError("私有字段无效")
        with self._directory_lock():
            with self._record_lock(platform, account_id):
                current = self._read_account(platform, account_id)
                if current is None:
                    raise ValueError("账号不存在")
                private = dict(current.private)
                for field_name in field_names:
                    private.pop(field_name, None)
                private = _validate_private(private)
                updated = replace(current, updated_at=_utc_now(), private=private)
                self._write_account(updated)
                return updated

    def replace_private_state(
        self,
        account: ChannelAccount,
        *,
        expected_updated_at: str | None = None,
    ) -> ChannelAccount:
        """原子替换账号私有状态，不把已删除字段从旧记录合并回来。"""
        normalized = self._validate_account(account)
        with self._directory_lock():
            with self._record_lock(normalized.platform, normalized.id):
                current = self._read_account(normalized.platform, normalized.id)
                if current is None:
                    raise ValueError("账号不存在")
                self._validate_expected_updated_at(current, expected_updated_at)
                private = self._validated_private_for_update(
                    current,
                    normalized,
                    merge_private=False,
                )
                updated = ChannelAccount(
                    id=current.id,
                    platform=current.platform,
                    principal_id=current.principal_id,
                    status=normalized.status,
                    enabled=normalized.enabled,
                    subscribed=normalized.subscribed,
                    created_at=current.created_at,
                    updated_at=_utc_now(),
                    private=private,
                )
                self._write_account(updated)
                return updated

    def revoke(self, platform: Platform, account_id: str) -> ChannelAccount:
        """停止指定账号的收发，但不移除其稳定记录。"""
        platform = _validate_platform(platform)
        account_id = _validate_account_id(account_id)
        with self._record_lock(platform, account_id):
            account = self._read_account(platform, account_id)
            if account is None:
                raise ValueError("账号不存在")
            if account.status == "revoked":
                return account
            return self.update(
                replace(account, status="revoked", enabled=False, subscribed=False)
            )

    def claim_inbound(self, platform: Platform, account_id: str, inbound_id: str) -> bool:
        """持久化声明平台入站 ID，防止重复事件再次进入 Gateway。"""
        return self.claim_inbounds(platform, account_id, (inbound_id,))

    def claim_inbounds(
        self,
        platform: Platform,
        account_id: str,
        inbound_ids: Iterable[str],
    ) -> bool:
        """原子声明一组可能指向同一入站的稳定平台 ID。"""
        platform = _validate_platform(platform)
        account_id = _validate_account_id(account_id)
        validated_ids: list[str] = []
        for inbound_id in inbound_ids:
            inbound_id = _validate_inbound_id(inbound_id)
            if inbound_id not in validated_ids:
                validated_ids.append(inbound_id)
        if not validated_ids:
            raise ValueError("入站消息 ID 无效")
        with self._record_lock(platform, account_id):
            account = self._read_account(platform, account_id)
            if account is None:
                raise ValueError("账号不存在")
            inbound_ids = list(account.private.get("inbound_ids", []))
            if any(inbound_id in inbound_ids for inbound_id in validated_ids):
                return False
            inbound_ids.extend(validated_ids)
            private = dict(account.private)
            private["inbound_ids"] = inbound_ids[-_MAX_INBOUND_IDS:]
            updated = ChannelAccount(
                id=account.id,
                platform=account.platform,
                principal_id=account.principal_id,
                status=account.status,
                enabled=account.enabled,
                subscribed=account.subscribed,
                created_at=account.created_at,
                updated_at=_utc_now(),
                private=private,
            )
            self._write_account(updated)
            return True

    def subscribed_recipients(self, principal_id: str) -> tuple[Recipient, ...]:
        """返回当前主体可用于主动投递的已订阅 IM 收件人。"""
        principal_id = _validate_principal_id(principal_id)
        recipients: list[Recipient] = []
        for account in self.list_accounts():
            if (
                account.principal_id != principal_id
                or account.status != "active"
                or not account.enabled
                or not account.subscribed
            ):
                continue
            conversation_id = account.private.get("conversation_id")
            if isinstance(conversation_id, str) and conversation_id:
                recipients.append(Recipient(principal_id, account.platform, conversation_id))
        return tuple(recipients)

    def principal_ids(self) -> tuple[str, ...]:
        """返回拥有本地主动工作区的已知主体。"""
        principal_ids = {
            self.owner_principal_id,
            *(account.principal_id for account in self.list_accounts()),
        }
        return tuple(
            sorted(principal_ids)
        )

    def _read_account(self, platform: Platform, account_id: str) -> ChannelAccount | None:
        path = self._account_path(platform, account_id, create_directory=False)
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as error:
            raise ValueError("账号记录无效") from error
        return self._account_from_payload(payload, platform, account_id)

    def _write_account(self, account: ChannelAccount) -> None:
        path = self._account_path(account.platform, account.id, create_directory=True)
        payload = {
            "version": _VERSION,
            "id": account.id,
            "platform": account.platform,
            "principal_id": account.principal_id,
            "status": account.status,
            "enabled": account.enabled,
            "subscribed": account.subscribed,
            "created_at": account.created_at,
            "updated_at": account.updated_at,
            "private": account.private,
        }
        try:
            serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        except (TypeError, ValueError) as error:
            raise ValueError("账号私有状态无法序列化") from error
        temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
        _write_text(temporary, serialized)
        try:
            os.replace(temporary, path)
        except OSError as error:
            if not _replace_blocked(error):
                temporary.unlink(missing_ok=True)
                raise
            _write_text(path, serialized)
            temporary.unlink(missing_ok=True)

    def _account_from_payload(
        self, payload: object, expected_platform: Platform, expected_id: str
    ) -> ChannelAccount:
        if not isinstance(payload, dict) or payload.get("version") != _VERSION:
            raise ValueError("账号记录无效")
        try:
            account = ChannelAccount(
                id=_validate_account_id(payload["id"]),
                platform=_validate_platform(payload["platform"]),
                principal_id=_validate_principal_id(payload["principal_id"]),
                status=_validate_status(payload["status"]),
                enabled=_validate_bool(payload["enabled"]),
                subscribed=_validate_bool(payload["subscribed"]),
                created_at=_validate_timestamp(payload["created_at"]),
                updated_at=_validate_timestamp(payload["updated_at"]),
                private=_validate_private(payload["private"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("账号记录无效") from error
        if account.platform != expected_platform or account.id != expected_id:
            raise ValueError("账号记录无效")
        return account

    def _account_path(self, platform: Platform, account_id: str, *, create_directory: bool) -> Path:
        platform = _validate_platform(platform)
        account_id = _validate_account_id(account_id)
        directory = self.root / platform
        if create_directory:
            directory.mkdir(parents=True, exist_ok=True)
        return directory / f"{account_id}.json"

    def _validated_private_for_update(
        self,
        current: ChannelAccount,
        normalized: ChannelAccount,
        *,
        merge_private: bool = True,
    ) -> dict[str, object]:
        if (
            normalized.platform != current.platform
            or normalized.id != current.id
            or normalized.principal_id != current.principal_id
            or normalized.created_at != current.created_at
        ):
            raise ValueError("账号身份不可变")
        self._validate_transition(current.status, normalized.status)
        if normalized.status == "revoked" and (normalized.enabled or normalized.subscribed):
            raise ValueError("已撤销账号不能启用或订阅")
        if normalized.status == "disabled" and normalized.enabled:
            raise ValueError("已禁用账号不能启用")
        private = dict(current.private) if merge_private else {}
        private.update(normalized.private)
        private = _validate_private(private)
        if normalized.platform == "feishu":
            app_id = private.get("app_id")
            if isinstance(app_id, str) and any(
                other.id != current.id and other.private.get("app_id") == app_id
                for other in self.list_accounts(platform="feishu")
            ):
                raise ValueError("feishu app_id 已登记")
        return private

    @staticmethod
    def _validate_expected_updated_at(
        current: ChannelAccount,
        expected_updated_at: str | None,
    ) -> None:
        if expected_updated_at is None:
            return
        if _validate_timestamp(expected_updated_at) != current.updated_at:
            raise ValueError("账号状态已更新")

    def _record_lock(self, platform: Platform, account_id: str) -> threading.RLock:
        path = str(self._account_path(platform, account_id, create_directory=False).resolve())
        with _RECORD_LOCK_GUARD:
            return _RECORD_LOCKS.setdefault(path, threading.RLock())

    def _directory_lock(self) -> threading.RLock:
        path = str(self.root.resolve())
        with _RECORD_LOCK_GUARD:
            return _DIRECTORY_LOCKS.setdefault(path, threading.RLock())

    @staticmethod
    def _validate_account(account: ChannelAccount) -> ChannelAccount:
        if not isinstance(account, ChannelAccount):
            raise ValueError("账号记录无效")
        return ChannelAccount(
            id=_validate_account_id(account.id),
            platform=_validate_platform(account.platform),
            principal_id=_validate_principal_id(account.principal_id),
            status=_validate_status(account.status),
            enabled=_validate_bool(account.enabled),
            subscribed=_validate_bool(account.subscribed),
            created_at=_validate_timestamp(account.created_at),
            updated_at=_validate_timestamp(account.updated_at),
            private=_validate_private(account.private),
        )

    @staticmethod
    def _validate_transition(current: str, target: str) -> None:
        allowed = {
            "pending_qr": {
                "pending_qr", "awaiting_first_dm", "expired", "failed", "disabled", "revoked"
            },
            "awaiting_first_dm": {
                "awaiting_first_dm", "active", "expired", "failed", "disabled", "revoked"
            },
            "awaiting_owner_code": {
                "awaiting_owner_code", "connecting", "active", "failed", "disabled", "revoked"
            },
            "connecting": {
                "awaiting_owner_code", "connecting", "active", "failed", "disabled", "revoked"
            },
            "active": {
                "active",
                "connecting",
                "awaiting_owner_code",
                "awaiting_first_dm",
                "disabled",
                "expired",
                "failed",
                "revoked",
            },
            "disabled": {
                "pending_qr", "awaiting_first_dm", "awaiting_owner_code", "connecting", "active",
                "disabled", "expired", "failed", "revoked"
            },
            "expired": {
                "pending_qr", "awaiting_first_dm", "expired", "failed", "disabled", "revoked"
            },
            "failed": {
                "pending_qr", "awaiting_first_dm", "awaiting_owner_code", "connecting", "active",
                "disabled", "expired", "failed", "revoked"
            },
            "revoked": {"revoked", "pending_qr", "awaiting_owner_code"},
        }
        if target not in allowed[current]:
            raise ValueError("账号状态转换无效")

    @staticmethod
    def _view(account: ChannelAccount) -> ChannelAccountView:
        raw_credential_state = account.private.get("credential_state")
        credential_state = (
            raw_credential_state
            if isinstance(raw_credential_state, str) and raw_credential_state in _CREDENTIAL_STATES
            else "pending"
        )
        if account.status == "revoked":
            credential_state = "revoked"
        connected = (
            account.status == "active"
            and account.enabled
            and bool(account.private.get("connected", False))
            and (
                account.platform != "weixin"
                or bool(account.private.get("context_token"))
            )
        )
        return ChannelAccountView(
            id=account.id,
            platform=account.platform,
            principal_id=account.principal_id,
            status=account.status,
            enabled=account.enabled,
            subscribed=account.subscribed,
            credential_state=credential_state,
            connected=connected,
        )


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _validate_platform(value: object) -> Platform:
    if not isinstance(value, str) or value not in _PLATFORMS:
        raise ValueError("不支持的 platform")
    return cast(Platform, value)


def _validate_account_id(value: object) -> str:
    if not isinstance(value, str) or Path(value).name != value:
        raise ValueError("account id 无效")
    try:
        parsed = UUID(value)
    except (TypeError, ValueError, AttributeError) as error:
        raise ValueError("account id 无效") from error
    if str(parsed) != value:
        raise ValueError("account id 无效")
    return value


def _validate_principal_id(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 256:
        raise ValueError("principal id 无效")
    return value


def _validate_status(value: object) -> str:
    if not isinstance(value, str) or value not in _STATUSES:
        raise ValueError("账号状态无效")
    return value


def _validate_bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise ValueError("账号布尔状态无效")
    return value


def _validate_timestamp(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("账号时间无效")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise ValueError("账号时间无效") from error
    if parsed.tzinfo is None:
        raise ValueError("账号时间无效")
    return value


def _validate_private(value: object) -> dict[str, object]:
    if not isinstance(value, dict) or any(not isinstance(key, str) for key in value):
        raise ValueError("账号私有状态无效")
    private = dict(value)
    inbound_ids = private.get("inbound_ids", [])
    if not isinstance(inbound_ids, list) or any(
        not isinstance(inbound_id, str) or not inbound_id for inbound_id in inbound_ids
    ):
        raise ValueError("账号私有状态无效")
    if len(inbound_ids) > _MAX_INBOUND_IDS:
        raise ValueError("账号私有状态无效")
    return private


def _validate_protocol_value(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 4_096:
        raise ValueError(f"{field_name} 无效")
    return value


def _validate_inbound_id(value: object) -> str:
    if not isinstance(value, str) or not value or len(value) > 4_096:
        raise ValueError("入站消息 ID 无效")
    return value


def _write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(content)
        handle.flush()
        os.fsync(handle.fileno())


def _replace_blocked(error: OSError) -> bool:
    return isinstance(error, PermissionError) or error.errno in {
        errno.EACCES,
        errno.EBUSY,
        errno.EPERM,
    }

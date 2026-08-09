from __future__ import annotations

import inspect
from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import Any

from ...channels.registry import ChannelAccount, ChannelAccountRegistry, ChannelAccountView, Platform


class ChannelControlService:
    """Owner 本机控制面使用的 IM 账号生命周期端口。"""

    def __init__(
        self,
        registry: ChannelAccountRegistry,
        *,
        weixin_transport: Any | None = None,
        feishu_transport: Any | None = None,
        owner_principal_id: str | None = None,
        delete_binding: Callable[[Platform, str], Awaitable[Any]] | None = None,
    ) -> None:
        self.registry = registry
        self.weixin_transport = weixin_transport
        self.feishu_transport = feishu_transport
        self.owner_principal_id = owner_principal_id or registry.owner_principal_id
        self._delete_binding = delete_binding

    def list_views(self) -> tuple[ChannelAccountView, ...]:
        return self.registry.list_views()

    def get_view(self, platform: str, account_id: str) -> ChannelAccountView:
        account = self.registry.get(_platform(platform), account_id)
        if account is None:
            raise KeyError("账号不存在")
        return _view_for(account, self.registry)

    def safe_details(self, view: ChannelAccountView) -> dict[str, object]:
        """返回控制面允许展示的非秘密平台摘要。"""
        if view.platform != "feishu":
            return {}
        account = self.registry.get("feishu", view.id)
        if account is None:
            return {}
        app_id = account.private.get("app_id")
        return {
            "app_id_masked": _mask_identifier(app_id) if isinstance(app_id, str) else None,
        }

    async def get_weixin_qr(self, account_id: str) -> dict[str, object]:
        """返回当前 Binding 的短时内存二维码，不暴露 Registry 私有状态。"""
        account = self._account("weixin", account_id)
        transport = self.weixin_transport
        getter = getattr(transport, "get_qr", None) if transport is not None else None
        snapshot = getter(account.id) if getter is not None else None
        if inspect.isawaitable(snapshot):
            snapshot = await snapshot
        pending_getter = getattr(transport, "is_qr_pending", None) if transport is not None else None
        pending = pending_getter(account.id) if pending_getter is not None else snapshot is not None
        if inspect.isawaitable(pending):
            pending = await pending
        current = self.registry.get("weixin", account.id) or account
        content = getattr(snapshot, "content", None) if snapshot is not None else None
        expires_at = getattr(snapshot, "expires_at", None) if snapshot is not None else None
        return {
            "binding_id": current.id,
            # 重扫候选凭据只存在 Transport 内存中；Registry 仍保持旧 Binding
            # 的 active 状态。二维码端点用 pending_qr 表达这个短时展示状态，
            # 不能把候选状态写入持久化账号记录。
            "status": "pending_qr" if pending else current.status,
            "qr_content": content if isinstance(content, str) else None,
            "expires_at": expires_at if isinstance(expires_at, (int, float)) else None,
        }

    async def create_weixin_invitation(self, principal: str) -> ChannelAccountView:
        if principal == "owner":
            account = self.registry.create_weixin_invitation(
                principal_id=self.owner_principal_id
            )
        elif principal == "new":
            account = self.registry.create_weixin_invitation(principal_id=None)
        else:
            raise ValueError("principal 必须是 owner 或 new")
        try:
            await self._transport_call("weixin", "enable_account", account.id)
        except Exception as exc:
            raise ValueError("微信 Binding 初始化失败") from exc
        return _view_for(account, self.registry)

    async def register_feishu(self, *, app_id: str, app_secret: str, domain: str) -> ChannelAccountView:
        account = self.registry.register_feishu_bot(
            app_id=_required_text(app_id, "app_id"),
            app_secret=_required_text(app_secret, "app_secret"),
            domain=_required_text(domain, "domain"),
        )
        await self._reload_feishu(account.id)
        return _view_for(account, self.registry)

    async def set_feishu_owner_code(self, account_id: str, code: str) -> ChannelAccountView:
        account = self._account("feishu", account_id)
        if not code.isascii() or not code.isdecimal() or len(code) != 6:
            raise ValueError("验证码必须是六位数字")
        transport = self.feishu_transport
        issuer = getattr(transport, "issue_owner_code", None)
        if issuer is None:
            raise ValueError("飞书 Transport 尚未启动")
        result = issuer(account.id, code=code)
        if inspect.isawaitable(result):
            result = await result
        if not isinstance(result, str):
            raise ValueError("验证码生成失败")
        return _view_for(account, self.registry)

    async def subscribe(self, platform: str, account_id: str) -> ChannelAccountView:
        account = self._account(_platform(platform), account_id)
        if account.status == "revoked":
            raise ValueError("已撤销账号不能订阅")
        updated = self.registry.update(replace(account, subscribed=True))
        return _view_for(updated, self.registry)

    async def unsubscribe(self, platform: str, account_id: str) -> ChannelAccountView:
        account = self._account(_platform(platform), account_id)
        updated = self.registry.update(replace(account, subscribed=False))
        return _view_for(updated, self.registry)

    async def enable(self, platform: str, account_id: str) -> ChannelAccountView:
        item_platform = _platform(platform)
        account = self._account(item_platform, account_id)
        if account.status == "revoked":
            raise ValueError("已撤销账号不能直接启用")
        if account.status == "expired" and item_platform == "weixin":
            raise ValueError("微信 Binding 已过期，请重新扫码")
        if item_platform == "weixin":
            if account.private.get("from_user_id"):
                status = "active"
            elif account.private.get("bot_token") and account.private.get("ilink_bot_id"):
                status = "awaiting_first_dm"
            else:
                status = "pending_qr"
        else:
            status = "active" if account.private.get("owner_open_id") else "awaiting_owner_code"
        updated = self.registry.update(replace(account, enabled=True, status=status))
        await self._transport_call(item_platform, "enable_account", updated.id)
        return _view_for(updated, self.registry)

    async def rescan_weixin_binding(self, account_id: str) -> ChannelAccountView:
        account = self._account("weixin", account_id)
        if account.status not in {"active", "expired", "revoked"}:
            raise ValueError("当前微信 Binding 状态不支持重新扫码")
        await self._transport_call("weixin", "start_rebind", account.id)
        return _view_for(self._account("weixin", account.id), self.registry)

    async def delete(self, platform: str, account_id: str) -> Any:
        item_platform = _platform(platform)
        callback = self._delete_binding
        if callback is None:
            raise ValueError("永久删除当前不可用")
        return await callback(item_platform, account_id)

    async def disable(self, platform: str, account_id: str) -> ChannelAccountView:
        item_platform = _platform(platform)
        account = self._account(item_platform, account_id)
        if account.status == "revoked":
            return _view_for(account, self.registry)
        updated = self.registry.update(replace(account, enabled=False, status="disabled"))
        await self._transport_call(item_platform, "disable_account", updated.id)
        return _view_for(updated, self.registry)

    async def revoke(self, platform: str, account_id: str) -> ChannelAccountView:
        item_platform = _platform(platform)
        account = self._account(item_platform, account_id)
        updated = self.registry.revoke(item_platform, account.id)
        await self._transport_call(item_platform, "disable_account", updated.id)
        return _view_for(updated, self.registry)

    async def update_feishu_credentials(
        self,
        account_id: str,
        *,
        app_id: str,
        app_secret: str,
        domain: str,
    ) -> ChannelAccountView:
        account = self._account("feishu", account_id)
        next_app_id = _required_text(app_id, "app_id")
        next_app_secret = _required_text(app_secret, "app_secret")
        next_domain = _required_text(domain, "domain")
        private = dict(account.private)
        private.pop("cardkit_enabled", None)
        private.update(
            {
                "app_id": next_app_id,
                "app_secret": next_app_secret,
                "domain": next_domain,
                "credential_state": "configured",
            }
        )
        app_identity_changed = private.get("app_id") != account.private.get("app_id")
        if app_identity_changed:
            for field_name in (
                "owner_open_id",
                "conversation_id",
                "owner_code_hash",
                "owner_code_expires_at",
            ):
                private.pop(field_name, None)
            private["connected"] = False
        candidate = replace(
            account,
            status="awaiting_owner_code" if app_identity_changed else account.status,
            private=private,
        )
        self.registry.validate_update(candidate)
        await self._reload_feishu(account.id, candidate)
        updated = self._account("feishu", account.id)
        return _view_for(updated, self.registry)

    def _account(self, platform: Platform, account_id: str) -> ChannelAccount:
        account = self.registry.get(platform, account_id)
        if account is None:
            raise KeyError("账号不存在")
        if account.principal_id != self.owner_principal_id and platform == "feishu":
            raise KeyError("账号不存在")
        return account

    async def _transport_call(self, platform: Platform, method: str, account_id: str) -> None:
        transport = self.weixin_transport if platform == "weixin" else self.feishu_transport
        callback = getattr(transport, method, None) if transport is not None else None
        if callback is None:
            return
        result = callback(account_id)
        if inspect.isawaitable(result):
            await result

    async def _reload_feishu(
        self,
        account_id: str,
        candidate: ChannelAccount | None = None,
    ) -> None:
        transport = self.feishu_transport
        reload_account = getattr(transport, "reload_account", None) if transport is not None else None
        if reload_account is None:
            raise ValueError("飞书凭据验证失败，旧连接仍保留")
        try:
            result = reload_account(account_id, candidate)
            if inspect.isawaitable(result):
                result = await result
        except Exception as exc:
            raise ValueError("飞书凭据验证失败，旧连接仍保留") from exc
        if result is not True:
            raise ValueError("飞书凭据验证失败，旧连接仍保留")


def _platform(value: str) -> Platform:
    if value not in {"weixin", "feishu"}:
        raise KeyError("平台不存在")
    return value  # type: ignore[return-value]


def _required_text(value: object, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} 必须是非空字符串")
    return value.strip()


def _view_for(account: ChannelAccount, registry: ChannelAccountRegistry) -> ChannelAccountView:
    # Registry owns the only redaction implementation; never serialize ``private`` here.
    for view in registry.list_views():
        if view.platform == account.platform and view.id == account.id:
            return view
    raise KeyError("账号不存在")


def _mask_identifier(value: str) -> str:
    if len(value) <= 4:
        return "••••"
    return f"{value[:3]}••••{value[-2:]}"


__all__ = ["ChannelControlService"]

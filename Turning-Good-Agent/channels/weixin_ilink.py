from __future__ import annotations

import base64
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Literal, Protocol

from .registry import ChannelAccount


class IlinkCredentialExpiredError(RuntimeError):
    """iLink 明确拒绝当前 Bot 凭据。"""


@dataclass(frozen=True, slots=True)
class IlinkLoginResult:
    """最小化的 iLink 登录结果；二维码/凭据只留在 private_update。"""

    status: Literal["pending_qr", "awaiting_first_dm", "expired", "failed"]
    private_update: dict[str, object]
    qr_content: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class IlinkPollResult:
    """一次 long-poll 返回的事件与私有游标更新。"""

    events: tuple[dict[str, object], ...]
    private_update: dict[str, object]


class IlinkClient(Protocol):
    """微信 iLink 协议边界，业务 Transport 不依赖 HTTP 实现。"""

    async def begin_login(self, binding: ChannelAccount) -> IlinkLoginResult: ...

    async def continue_login(self, binding: ChannelAccount) -> IlinkLoginResult: ...

    async def poll(self, binding: ChannelAccount) -> IlinkPollResult: ...

    async def send_text(self, binding: ChannelAccount, content: str) -> bool: ...


class HttpxIlinkClient:
    """一个保守的 iLink HTTP 客户端实现。

    iLink 是实验性协议，所有平台字段都在这个模块内解析；Transport 只看到
    规范化结果。真实端点可通过 ``base_url`` 注入，测试使用 ``IlinkClient`` fake。
    """

    def __init__(
        self,
        base_url: str = "https://ilinkai.weixin.qq.com",
        *,
        timeout_seconds: float = 30.0,
        http_client: object | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._http_client = http_client
        self._owns_client = http_client is None

    async def __aenter__(self) -> "HttpxIlinkClient":
        await self._ensure_client()
        return self

    async def __aexit__(self, _type, _value, _traceback) -> None:
        await self.close()

    async def close(self) -> None:
        client = self._http_client
        if self._owns_client and client is not None:
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()
            self._http_client = None

    async def begin_login(self, binding: ChannelAccount) -> IlinkLoginResult:
        private = binding.private
        if private.get("bot_token") and private.get("ilink_bot_id"):
            return IlinkLoginResult("awaiting_first_dm", {"credential_state": "configured"})
        payload = await self._request_json(
            "GET",
            "/ilink/bot/get_bot_qrcode",
            params={"bot_type": "3"},
        )
        if not isinstance(payload, dict):
            return IlinkLoginResult("failed", {"credential_state": "invalid"})
        qrcode = payload.get("qrcode")
        qr_content = payload.get("qrcode_img_content")
        if not isinstance(qrcode, str) or not qrcode:
            return IlinkLoginResult("failed", {"credential_state": "invalid"})
        return IlinkLoginResult(
            "pending_qr",
            {"qrcode": qrcode, "credential_state": "pending"},
            qr_content=qr_content if isinstance(qr_content, str) and qr_content else None,
        )

    async def continue_login(self, binding: ChannelAccount) -> IlinkLoginResult:
        qrcode = binding.private.get("qrcode")
        if not isinstance(qrcode, str) or not qrcode:
            return IlinkLoginResult("failed", {"credential_state": "invalid"})
        payload = await self._request_json(
            "GET",
            "/ilink/bot/get_qrcode_status",
            params={"qrcode": qrcode},
            extra_headers={"iLink-App-ClientVersion": "1"},
        )
        if not isinstance(payload, dict):
            return IlinkLoginResult("failed", {"credential_state": "invalid"})
        status = payload.get("status")
        if status in {"wait", "scaned"}:
            return IlinkLoginResult("pending_qr", {"qr_status": status})
        if status == "expired":
            return IlinkLoginResult("expired", {"credential_state": "expired", "qr_status": status})
        if status != "confirmed":
            return IlinkLoginResult("failed", {"credential_state": "invalid"})
        bot_token = payload.get("bot_token")
        ilink_bot_id = payload.get("ilink_bot_id")
        base_url = payload.get("baseurl", self.base_url)
        if not all(isinstance(value, str) and value for value in (bot_token, ilink_bot_id, base_url)):
            return IlinkLoginResult("failed", {"credential_state": "invalid"})
        update: dict[str, object] = {
            "bot_token": bot_token,
            "ilink_bot_id": ilink_bot_id,
            "base_url": base_url,
            "credential_state": "configured",
            "qr_status": status,
        }
        ilink_user_id = payload.get("ilink_user_id")
        if isinstance(ilink_user_id, str) and ilink_user_id:
            update["ilink_user_id"] = ilink_user_id
        return IlinkLoginResult("awaiting_first_dm", update)

    async def poll(self, binding: ChannelAccount) -> IlinkPollResult:
        private = binding.private
        payload = await self._request_json(
            "POST",
            "/ilink/bot/getupdates",
            {
                "get_updates_buf": private.get("cursor", ""),
                "base_info": {"channel_version": "turning-good-agent"},
            },
            token=private.get("bot_token"),
            base_url=private.get("base_url"),
        )
        if not isinstance(payload, dict):
            return IlinkPollResult((), {})
        raw_events = payload.get("msgs", ())
        events = tuple(item for item in raw_events if isinstance(item, dict)) if isinstance(raw_events, list) else ()
        update: dict[str, object] = {}
        cursor = payload.get("get_updates_buf")
        if isinstance(cursor, str):
            update["cursor"] = cursor
        return IlinkPollResult(events, update)

    async def send_text(self, binding: ChannelAccount, content: str) -> bool:
        private = binding.private
        if not private.get("bot_token") or not private.get("context_token"):
            return False
        payload = await self._request_json(
            "POST",
            "/ilink/bot/sendmessage",
            {
                "msg": {
                    "from_user_id": "",
                    "to_user_id": private.get("from_user_id"),
                    "client_id": f"tga:{int(time.time() * 1000)}-{secrets.token_hex(4)}",
                    "message_type": 2,
                    "message_state": 2,
                    "item_list": [{"type": 1, "text_item": {"text": content}}],
                    "context_token": private.get("context_token"),
                },
                "base_info": {"channel_version": "turning-good-agent"},
            },
            token=private.get("bot_token"),
            base_url=private.get("base_url"),
        )
        if isinstance(payload, dict) and (
            payload.get("errcode") not in (None, 0, "0")
            or payload.get("ret") not in (None, 0, "0")
        ):
            return False
        return True

    async def _ensure_client(self):
        if self._http_client is None:
            try:
                import httpx
            except ImportError as exc:  # pragma: no cover - dependency metadata covers runtime
                raise RuntimeError("iLink Transport 需要 httpx") from exc
            self._http_client = httpx.AsyncClient(timeout=self.timeout_seconds)
        return self._http_client

    async def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, object] | None = None,
        *,
        params: dict[str, object] | None = None,
        token: object | None = None,
        base_url: object | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> object:
        client = await self._ensure_client()
        headers = dict(extra_headers or {})
        if payload is not None:
            headers["Content-Type"] = "application/json"
        if isinstance(token, str) and token:
            headers["AuthorizationType"] = "ilink_bot_token"
            headers["Authorization"] = f"Bearer {token}"
            random_uin = str(secrets.randbits(32)).encode("utf-8")
            headers["X-WECHAT-UIN"] = base64.b64encode(random_uin).decode("ascii")
        selected_base_url = base_url if isinstance(base_url, str) and base_url else self.base_url
        request_kwargs: dict[str, object] = {"headers": headers}
        if payload is not None:
            request_kwargs["json"] = payload
        if params is not None:
            request_kwargs["params"] = params
        response = await client.request(method, f"{selected_base_url.rstrip('/')}{path}", **request_kwargs)
        try:
            response.raise_for_status()
        except Exception as exc:
            if getattr(response, "status_code", None) in {401, 403}:
                raise IlinkCredentialExpiredError("iLink credentials expired") from exc
            raise
        try:
            payload = response.json()
        except (ValueError, json.JSONDecodeError):
            return {}
        if _is_credential_error_payload(payload):
            raise IlinkCredentialExpiredError("iLink credentials expired")
        return payload


def _is_credential_error_payload(payload: object) -> bool:
    if not isinstance(payload, dict):
        return False
    markers = (
        "invalid token",
        "token expired",
        "expired token",
        "invalid bot token",
        "unauthorized",
        "credential expired",
    )
    for field_name in ("errcode", "ret", "code", "error_code", "errmsg", "message"):
        value = payload.get(field_name)
        if isinstance(value, int) and value in {401, 403}:
            return True
        if isinstance(value, str):
            normalized = value.strip().lower().replace("_", " ")
            if normalized in {"401", "403", "invalid token", "token expired", "expired token"}:
                return True
            if any(marker in normalized for marker in markers):
                return True
    return False

from __future__ import annotations

import hashlib
import re


_SESSION_ID_PATTERN = re.compile(r"[0-9a-f]{64}")
_DOMAIN_SEPARATOR = b"tga-gateway-session-v1\0"


def derive_session_id(principal_id: str, channel: str, conversation_id: str) -> str:
    """从规范路由派生稳定且不泄露字段的会话 ID。"""
    parts = (principal_id, channel, conversation_id)
    if not all(isinstance(part, str) and part for part in parts):
        raise ValueError("principal_id、channel 和 conversation_id 必须是非空字符串")
    payload = _DOMAIN_SEPARATOR + b"".join(_encode_part(part) for part in parts)
    return hashlib.sha256(payload).hexdigest()


def is_opaque_session_id(value: object) -> bool:
    """仅检查派生 ID 的安全文本形状，不查询任何 Session Store。"""
    return isinstance(value, str) and _SESSION_ID_PATTERN.fullmatch(value) is not None


def _encode_part(value: str) -> bytes:
    encoded = value.encode("utf-8")
    return len(encoded).to_bytes(8, "big") + encoded

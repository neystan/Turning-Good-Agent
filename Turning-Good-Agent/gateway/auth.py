from __future__ import annotations

import errno
import json
import os
import secrets
from hmac import compare_digest
from pathlib import Path
from tempfile import NamedTemporaryFile


def load_or_create_gateway_token(config_path: Path) -> str:
    """读取或创建 Gateway 本机认证令牌，绝不输出令牌。"""
    settings = _read_settings(config_path)
    gateway = settings.get("gateway")
    if gateway is None:
        gateway = {}
        settings["gateway"] = gateway
    if not isinstance(gateway, dict):
        raise ValueError("gateway 必须是 object")

    token = gateway.get("auth_token")
    if isinstance(token, str) and token:
        return token

    token = secrets.token_urlsafe(32)
    gateway["auth_token"] = token
    _atomic_write_settings(config_path, settings)
    return token


def is_authorized_bearer(header: str | None, expected_token: str) -> bool:
    """以常数时间验证 HTTP Bearer 认证头。"""
    if not isinstance(header, str) or not expected_token:
        return False
    scheme, separator, presented_token = header.partition(" ")
    if separator != " " or scheme.lower() != "bearer" or not presented_token or " " in presented_token:
        return False
    return compare_digest(presented_token, expected_token)


def _read_settings(config_path: Path) -> dict[str, object]:
    if not config_path.exists():
        return {}
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("settings.local.json 顶层必须是 object")
    return payload


def _atomic_write_settings(config_path: Path, settings: dict[str, object]) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json", dir=config_path.parent, delete=False
        ) as temporary:
            temporary.write(json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
            temporary_path = Path(temporary.name)
        try:
            os.replace(temporary_path, config_path)
        except OSError as error:
            if error.errno != errno.EBUSY:
                raise
            with temporary_path.open("rb") as source, config_path.open("wb") as destination:
                destination.write(source.read())
                destination.flush()
                os.fsync(destination.fileno())
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)

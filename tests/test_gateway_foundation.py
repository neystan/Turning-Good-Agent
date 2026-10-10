from __future__ import annotations

import errno
import importlib
import json
import asyncio
from pathlib import Path

import pytest


def test_gateway_instance_lock_blocks_a_second_owner_until_closed(tmp_path: Path) -> None:
    """Would fail if a second Gateway could run schedulers for one data directory."""
    instance_lock = importlib.import_module("Turning-Good-Agent.gateway.instance_lock")
    data_dir = tmp_path / "data"

    owner = instance_lock.GatewayInstanceLock(data_dir)
    owner.acquire()
    try:
        contender = instance_lock.GatewayInstanceLock(data_dir)
        with pytest.raises(RuntimeError, match="Gateway.*already running"):
            contender.acquire()
    finally:
        owner.close()

    with instance_lock.GatewayInstanceLock(data_dir):
        pass


def test_gateway_auth_creates_and_reuses_a_local_token(tmp_path: Path) -> None:
    """Would fail if a first Gateway start did not persist one reusable local credential."""
    auth = importlib.import_module("Turning-Good-Agent.gateway.auth")
    config_path = tmp_path / "settings.local.json"

    first = auth.load_or_create_gateway_token(config_path)
    raw = json.loads(config_path.read_text(encoding="utf-8"))

    assert len(first) >= 32
    assert raw == {"gateway": {"auth_token": first}}
    assert auth.load_or_create_gateway_token(config_path) == first


def test_gateway_auth_keeps_existing_settings_and_accepts_only_its_bearer_token(tmp_path: Path) -> None:
    """Would fail if authentication accepted a wrong credential or overwrote unrelated settings."""
    auth = importlib.import_module("Turning-Good-Agent.gateway.auth")
    config_path = tmp_path / "settings.local.json"
    config_path.write_text(
        json.dumps({"llm": {"model": "keep-me"}, "gateway": {"auth_token": "known-token"}}),
        encoding="utf-8",
    )

    token = auth.load_or_create_gateway_token(config_path)

    assert token == "known-token"
    assert json.loads(config_path.read_text(encoding="utf-8"))["llm"] == {"model": "keep-me"}
    assert auth.is_authorized_bearer("Bearer known-token", token)
    assert not auth.is_authorized_bearer("Bearer wrong-token", token)
    assert not auth.is_authorized_bearer("known-token", token)
    assert not auth.is_authorized_bearer(None, token)


def test_gateway_auth_falls_back_to_direct_write_when_replace_is_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if a Windows bind-mounted settings file prevented token persistence."""
    auth = importlib.import_module("Turning-Good-Agent.gateway.auth")
    config_path = tmp_path / "settings.local.json"
    config_path.write_text("{}", encoding="utf-8")

    def replace_busy(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError(errno.EBUSY, "resource busy")

    monkeypatch.setattr(auth.os, "replace", replace_busy)

    token = auth.load_or_create_gateway_token(config_path)

    assert json.loads(config_path.read_text(encoding="utf-8")) == {"gateway": {"auth_token": token}}


def test_gateway_creates_a_token_only_after_acquiring_its_instance_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Would fail if a losing first-start contender could overwrite the winner's token."""
    gateway_module = importlib.import_module("Turning-Good-Agent.gateway.host")
    runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
    settings_module = importlib.import_module("Turning-Good-Agent.config.settings")

    async def run() -> None:
        data_dir = tmp_path / "data"
        config_path = tmp_path / "settings.local.json"
        generated: list[Path] = []

        def create_token(path: Path) -> str:
            generated.append(path)
            return "locked-token"

        monkeypatch.setattr(gateway_module, "load_or_create_gateway_token", create_token, raising=False)

        first_settings = settings_module.Settings(data_dir=data_dir)
        first_settings.local_config_path = config_path
        first_settings.proactive.enabled = False
        first = gateway_module.GatewayHost(
            first_settings,
            runtime=runtime_module.AgentRuntime.create_default(first_settings, object()),
        )
        await first.start()
        try:
            assert first_settings.gateway.auth_token == "locked-token"

            second_settings = settings_module.Settings(data_dir=data_dir)
            second_settings.local_config_path = config_path
            second_settings.proactive.enabled = False
            second = gateway_module.GatewayHost(
                second_settings,
                runtime=runtime_module.AgentRuntime.create_default(second_settings, object()),
            )
            with pytest.raises(RuntimeError, match="Gateway.*already running"):
                await second.start()

            assert generated == [config_path]
        finally:
            await first.close()

    asyncio.run(run())


def test_route_session_id_is_opaque_stable_and_separates_field_boundaries() -> None:
    """Would fail if route fields could collide or leak into a parser-facing session ID."""
    routing = importlib.import_module("Turning-Good-Agent.gateway.routing")

    first = routing.derive_session_id("alpha:beta", "cli", "conversation")
    same_route = routing.derive_session_id("alpha:beta", "cli", "conversation")
    boundary_variant = routing.derive_session_id("alpha", "beta:cli", "conversation")

    assert first == same_route
    assert first != boundary_variant
    assert routing.is_opaque_session_id(first)
    assert ":" not in first
    assert "alpha" not in first

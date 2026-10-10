from __future__ import annotations

import asyncio
import importlib
import json
from datetime import UTC, datetime, timedelta

import pytest


store_module = importlib.import_module("Turning-Good-Agent.sessions.store")
proactive_store_module = importlib.import_module("Turning-Good-Agent.proactive.store")
memory_module = importlib.import_module("Turning-Good-Agent.memory.long_term")
settings_module = importlib.import_module("Turning-Good-Agent.config.settings")

JsonlSessionStore = store_module.JsonlSessionStore
ProactiveStore = proactive_store_module.ProactiveStore
ProfileMemory = memory_module.ProfileMemory
ProfileMemorySnapshot = memory_module.ProfileMemorySnapshot
ProactiveSettings = settings_module.ProactiveSettings
messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
manager_module = importlib.import_module("Turning-Good-Agent.sessions.manager")

ChannelRoute = messages_module.ChannelRoute
SessionManager = manager_module.SessionManager


def test_save_message_accepts_existing_identity(tmp_path) -> None:
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        await store.create_session("session", "user", "cli")

        record = await store.save_message(
            "session",
            "user",
            "当前输入",
            message_id="inbound-1",
            created_at="2026-08-02T01:02:03+00:00",
        )

        assert record.id == "inbound-1"
        assert record.created_at == "2026-08-02T01:02:03+00:00"
        assert (await store.all_messages("session"))[0].id == "inbound-1"

    asyncio.run(run())


def test_cleanup_only_removes_expired_session_directories(tmp_path) -> None:
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        await store.create_session("expired", "user", "cli")
        await store.create_session("active", "user", "cli")
        expired_file = store._session_file("expired")
        active_file = store._session_file("active")

        for session_id, updated_at in (
            ("expired", datetime.now(UTC) - timedelta(days=30)),
            ("active", datetime.now(UTC) + timedelta(days=1)),
        ):
            session_file = store._session_file(session_id)
            payload = json.loads(session_file.read_text(encoding="utf-8"))
            payload["updated_at"] = updated_at.isoformat()
            session_file.write_text(json.dumps(payload), encoding="utf-8")

        protected_files = {
            tmp_path / "proactive" / "cron.json": b'{"jobs": [], "usage": {}}',
            tmp_path / "memory" / "USER.md": "用户画像".encode(),
            tmp_path / "unrelated" / "keep.txt": b"keep",
            tmp_path / "broken-session" / "session.json": b"not-json",
            tmp_path / "array-session" / "session.json": b"[]",
            tmp_path / "fake-session" / "session.json": (
                b'{"updated_at":"2000-01-01T00:00:00+00:00"}'
            ),
        }
        for path, content in protected_files.items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)

        removed = await store.cleanup_expired_sessions(retention_days=7)

        assert removed == 1
        assert not expired_file.exists()
        assert active_file.exists()
        for path, content in protected_files.items():
            assert path.read_bytes() == content

        await store.create_session("after-cleanup", "user", "cli")
        loaded = await store.load_session("after-cleanup")
        assert loaded is not None and loaded.id == "after-cleanup"
        assert [session.id for session in await store.list_sessions()] == [
            "active",
            "after-cleanup",
        ]

    asyncio.run(run())


def test_new_session_cleanup_preserves_all_proactive_and_profile_state(tmp_path) -> None:
    async def run() -> None:
        proactive = ProactiveStore(tmp_path)
        snapshots = {
            "cron.json": {"jobs": [], "usage": {}},
            "breakbeat.json": {"items": [], "cursors": {}, "next_run_at": None, "usage": {}},
            "dream.json": {"cursors": {}, "next_run_at": None, "usage": {}},
            "skill.json": {"observations": [], "next_run_at": None, "usage": {}},
            "incidents.json": {"incidents": []},
        }
        for name, payload in snapshots.items():
            proactive.write_json(name, payload)
        legacy_outbox = tmp_path / "proactive" / "pending_deliveries.json"
        legacy_outbox_bytes = b'{"legacy": "preserve-without-reading"}'
        legacy_outbox.write_bytes(legacy_outbox_bytes)
        memory = ProfileMemory(tmp_path, ProactiveSettings())
        snapshot = ProfileMemorySnapshot(user="用户偏好中文。", soul="先核对事实。")
        memory.write(snapshot)

        sessions = JsonlSessionStore(tmp_path)
        await sessions.create_session("new-session", "user", "cli")
        await sessions.cleanup_expired_sessions(retention_days=7)

        for name, payload in snapshots.items():
            assert proactive.read_json(name, None) == payload
        assert legacy_outbox.read_bytes() == legacy_outbox_bytes
        assert memory.read() == snapshot

    asyncio.run(run())


def test_session_route_is_persisted_filtered_and_rejects_existing_route_mismatch(tmp_path) -> None:
    """Would fail if separate channels could reopen the same session's chat history."""
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        manager = SessionManager(store)
        cli_route = ChannelRoute("principal-1", "cli", "conversation-1", "session-cli")
        web_route = ChannelRoute("principal-1", "web", "conversation-1", "session-web")

        cli_session = await manager.load_or_create(cli_route)
        await manager.load_or_create(web_route)

        payload = json.loads(store._session_file(cli_session.id).read_text(encoding="utf-8"))
        assert payload["principal_id"] == "principal-1"
        assert payload["conversation_id"] == "conversation-1"
        assert [item.id for item in await manager.list_sessions(channel="cli")] == ["session-cli"]
        assert {item.id for item in await manager.list_sessions(principal_id="principal-1")} == {
            "session-cli",
            "session-web",
        }

        conflicting = ChannelRoute("principal-1", "web", "conversation-1", "session-cli")
        with pytest.raises(ValueError, match="route"):
            await manager.load_or_create(conflicting)

    asyncio.run(run())

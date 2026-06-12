import asyncio
import json
from datetime import UTC, datetime, timedelta
from importlib import import_module

InboundMessage = import_module("Turning-Good-Agent.bus.messages").InboundMessage
SessionManager = import_module("Turning-Good-Agent.sessions.manager").SessionManager
JsonlSessionStore = import_module("Turning-Good-Agent.sessions.store").JsonlSessionStore


def test_session_manager_saves_and_loads_messages(tmp_path):
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        manager = SessionManager(store)
        session = await manager.load_or_create("demo", "user-1", "cli")

        await manager.save_user_message(session.id, "hello")
        await manager.save_assistant_message(session.id, "hi")

        history = await manager.recent_messages(session.id, limit=10)
        assert [m.role for m in history] == ["user", "assistant"]
        assert [m.content for m in history] == ["hello", "hi"]
        session_dirs = list((tmp_path / "sessions").iterdir())
        assert len(session_dirs) == 1
        session_dir = session_dirs[0]
        assert (session_dir / "session.json").exists()
        assert (session_dir / "messages.jsonl").exists()

    asyncio.run(run())


def test_session_directory_name_contains_created_time(tmp_path):
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        manager = SessionManager(store)

        await manager.load_or_create("demo", "user-1", "cli")

        session_dirs = list((tmp_path / "sessions").iterdir())
        assert len(session_dirs) == 1
        assert session_dirs[0].name.endswith("_demo")
        assert session_dirs[0].name.startswith("20")

    asyncio.run(run())


def test_history_command_returns_recent_messages(tmp_path):
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        manager = SessionManager(store)
        session = await manager.load_or_create("demo", "user-1", "cli")
        await manager.save_user_message(session.id, "hello")

        msg = InboundMessage.new("/history", "demo", "user-1", "cli")
        response = await manager.handle_command(session, msg)

        assert response is not None
        assert "hello" in response

    asyncio.run(run())


def test_history_command_returns_all_messages(tmp_path):
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        manager = SessionManager(store)
        session = await manager.load_or_create("demo", "user-1", "cli")
        for index in range(12):
            await manager.save_user_message(session.id, f"msg-{index}")

        msg = InboundMessage.new("/history", "demo", "user-1", "cli")
        response = await manager.handle_command(session, msg)

        assert response is not None
        assert "msg-0" in response
        assert "msg-11" in response

    asyncio.run(run())


def test_unknown_command_returns_available_command_help(tmp_path):
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        manager = SessionManager(store)
        session = await manager.load_or_create("demo", "user-1", "cli")

        response = await manager.handle_command(
            session,
            InboundMessage.new("/bad", "demo", "user-1", "cli"),
        )

        assert response is not None
        assert "未知命令：/bad" in response
        assert "/history" in response
        assert "查看当前会话的完整历史消息" in response
        assert "/clear" in response
        assert "/new" in response

    asyncio.run(run())


def test_clear_command_clears_current_session(tmp_path):
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        manager = SessionManager(store)
        session = await manager.load_or_create("demo", "user-1", "cli")
        await manager.save_user_message(session.id, "hello")
        await manager.save_assistant_message(session.id, "hi")
        session_dir = next((tmp_path / "sessions").iterdir())

        msg = InboundMessage.new("/clear", "demo", "user-1", "cli")
        response = await manager.handle_command(session, msg)

        assert response is not None
        assert "已清空" in response
        assert await manager.recent_messages("demo", limit=10) == []
        assert not session_dir.exists()

    asyncio.run(run())


def test_clear_command_on_new_empty_session_leaves_no_directory(tmp_path):
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        manager = SessionManager(store)

        response = await manager.handle_inbound_command("demo", InboundMessage.new("/clear", "demo", "user-1", "cli"))

        assert response == "当前会话已清空。"
        assert list((tmp_path / "sessions").iterdir()) == []

    asyncio.run(run())


def test_new_command_does_not_clear_current_session(tmp_path):
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        manager = SessionManager(store)
        session = await manager.load_or_create("demo", "user-1", "cli")
        await manager.save_user_message(session.id, "hello")

        msg = InboundMessage.new("/new", "demo", "user-1", "cli")
        response = await manager.handle_command(session, msg)

        assert response is not None
        assert "新会话" in response
        history = await manager.recent_messages("demo", limit=10)
        assert [item.content for item in history] == ["hello"]

    asyncio.run(run())


def test_cleanup_expired_sessions_removes_sessions_older_than_retention_days(tmp_path):
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        manager = SessionManager(store)

        old_session = await manager.load_or_create("old", "user-1", "cli")
        recent_session = await manager.load_or_create("recent", "user-1", "cli")
        old_dir = store._session_dir(old_session.id)
        recent_dir = store._session_dir(recent_session.id)

        old_payload = {
            "id": old_session.id,
            "user_id": old_session.user_id,
            "channel": old_session.channel,
            "title": old_session.title,
            "summary": old_session.summary,
            "created_at": (datetime.now(UTC) - timedelta(days=8)).isoformat(),
            "updated_at": (datetime.now(UTC) - timedelta(days=8)).isoformat(),
            "metadata": old_session.metadata,
        }
        recent_payload = {
            "id": recent_session.id,
            "user_id": recent_session.user_id,
            "channel": recent_session.channel,
            "title": recent_session.title,
            "summary": recent_session.summary,
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "metadata": recent_session.metadata,
        }
        (old_dir / "session.json").write_text(json.dumps(old_payload, ensure_ascii=False), encoding="utf-8")
        (recent_dir / "session.json").write_text(
            json.dumps(recent_payload, ensure_ascii=False),
            encoding="utf-8",
        )

        removed = await manager.cleanup_expired_sessions(7)

        assert removed == 1
        assert not old_dir.exists()
        assert recent_dir.exists()

    asyncio.run(run())

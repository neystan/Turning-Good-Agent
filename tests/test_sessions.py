import asyncio
import json
from datetime import UTC, datetime, timedelta
from importlib import import_module

InboundMessage = import_module("Turning-Good-Agent.bus.messages").InboundMessage
SessionManager = import_module("Turning-Good-Agent.sessions.manager").SessionManager
JsonlSessionStore = import_module("Turning-Good-Agent.sessions.store").JsonlSessionStore
Settings = import_module("Turning-Good-Agent.config.settings").Settings


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
        session_dirs = [path for path in tmp_path.iterdir() if path.is_dir()]
        assert len(session_dirs) == 1
        session_dir = session_dirs[0]
        assert (session_dir / "session.json").exists()
        assert (session_dir / "messages.jsonl").exists()

    asyncio.run(run())


def test_new_session_initializes_web_metadata(tmp_path):
    """验证新会话默认具有 Web 管理元数据。"""

    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        session = await SessionManager(store).load_or_create("demo", "user-1", "cli")

        assert session.pinned is False
        assert session.archived is False

    asyncio.run(run())


def test_session_reloads_pinned_and_archived_metadata(tmp_path):
    """验证 Web 会话元数据会写入并在重载后保留。"""

    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        manager = SessionManager(store)
        await manager.load_or_create("demo", "user-1", "cli")

        await store.update_session_metadata("demo", pinned=True, archived=True)
        session = await store.load_session("demo")

        assert session is not None
        assert session.pinned is True
        assert session.archived is True
        session_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
        payload = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
        assert payload["pinned"] is True
        assert payload["archived"] is True

    asyncio.run(run())


def test_legacy_session_without_web_metadata_uses_defaults(tmp_path):
    """验证旧会话缺少 Web 元数据时使用明确默认值。"""

    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        session = await store.create_session("demo", "user-1", "cli")
        session_dir = store._session_dir(session.id)
        payload = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
        payload.pop("pinned")
        payload.pop("archived")
        (session_dir / "session.json").write_text(json.dumps(payload), encoding="utf-8")

        reloaded = await store.load_session("demo")

        assert reloaded is not None
        assert reloaded.pinned is False
        assert reloaded.archived is False

    asyncio.run(run())


def test_session_directory_name_contains_created_time(tmp_path):
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        manager = SessionManager(store)

        await manager.load_or_create("demo", "user-1", "cli")

        session_dirs = [path for path in tmp_path.iterdir() if path.is_dir()]
        assert len(session_dirs) == 1
        assert session_dirs[0].name.endswith("_demo")
        assert session_dirs[0].name.startswith("20")
        assert "_" in session_dirs[0].name
        assert "T" not in session_dirs[0].name

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


def test_context_command_returns_summary_and_uncompacted_messages(tmp_path):
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        manager = SessionManager(store)
        session = await manager.load_or_create("demo", "user-1", "cli")
        await store.update_summary("demo", "旧摘要")
        await manager.save_user_message(session.id, "old")
        await manager.save_assistant_message(session.id, "recent")
        history = await manager.all_messages("demo")
        await store.update_uncompacted_history("demo", history[1:])

        response = await manager.handle_inbound_command("demo", InboundMessage.new("/context", "demo", "user-1", "cli"))

        assert response is not None
        assert "会话摘要：旧摘要" in response
        assert "未压缩历史消息数：1" in response
        assert "assistant: recent" in response
        assert "user: old" not in response

    asyncio.run(run())


def test_tools_command_returns_empty_message_when_no_tool_calls(tmp_path):
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        manager = SessionManager(store)

        response = await manager.handle_inbound_command("demo", InboundMessage.new("/tools", "demo", "user-1", "cli"))

        assert response == "暂无工具调用记录。"

    asyncio.run(run())


def test_approve_commands_update_global_setting_without_creating_session(tmp_path):
    """验证审批命令只修改全局本地设置。"""

    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        settings = Settings(data_dir=tmp_path)
        settings.local_config_path = tmp_path / "settings.local.json"
        manager = SessionManager(store, settings)
        msg = lambda content: InboundMessage.new(content, "demo", "user-1", "cli")

        assert await manager.handle_inbound_command("demo", msg("/approve")) == "全局工具自动审批：已关闭。"
        assert "已关闭" in await manager.handle_inbound_command("demo", msg("/approve off"))
        assert [path for path in tmp_path.iterdir() if path.is_dir()] == []

        enabled = await manager.handle_inbound_command("demo", msg("/approve on"))
        assert enabled is not None
        assert "已开启" in enabled
        assert await store.load_session("demo") is None
        assert settings.tool_permissions.auto_approve_tools is True
        assert await manager.handle_inbound_command("demo", msg("/approve")) == "全局工具自动审批：已开启。"

        disabled = await manager.handle_inbound_command("demo", msg("/approve off"))
        assert disabled is not None
        assert "已关闭" in disabled
        assert settings.tool_permissions.auto_approve_tools is False

    asyncio.run(run())


def test_approve_command_rejects_unknown_argument(tmp_path):
    """验证未知审批参数返回明确用法。"""

    async def run() -> None:
        manager = SessionManager(JsonlSessionStore(tmp_path))

        response = await manager.handle_inbound_command(
            "demo",
            InboundMessage.new("/approve later", "demo", "user-1", "cli"),
        )

        assert response == "用法：/approve [on|off]"

    asyncio.run(run())


def test_tools_command_returns_saved_tool_calls(tmp_path):
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        manager = SessionManager(store)
        await manager.load_or_create("demo", "user-1", "cli")
        await store.save_tool_calls(
            "turn-1",
            "demo",
            [
                {
                    "tool_call_id": "call-1",
                    "tool_name": "now",
                    "args": {},
                    "content": "2026-06-25T12:00:00",
                    "error": None,
                    "duration_ms": 12.5,
                }
            ],
        )

        response = await manager.handle_inbound_command("demo", InboundMessage.new("/tools", "demo", "user-1", "cli"))

        assert response is not None
        assert "tool=now" in response
        assert "tool_call_id=call-1" in response
        assert "result: 2026-06-25T12:00:00" in response

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
        assert "/context" in response
        assert "/tools" in response
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
        session_dir = next(path for path in tmp_path.iterdir() if path.is_dir())

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
        assert [path for path in tmp_path.iterdir() if path.is_dir()] == []

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
            "uncompacted_history": [],
            "created_at": (datetime.now(UTC) - timedelta(days=8)).isoformat(),
            "updated_at": (datetime.now(UTC) - timedelta(days=8)).isoformat(),
        }
        recent_payload = {
            "id": recent_session.id,
            "user_id": recent_session.user_id,
            "channel": recent_session.channel,
            "title": recent_session.title,
            "summary": recent_session.summary,
            "uncompacted_history": [],
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
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

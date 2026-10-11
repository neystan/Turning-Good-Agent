"""验证 Web Host 的 WebSocket 动作协议。"""

import asyncio
from importlib import import_module

from fastapi.testclient import TestClient


Settings = import_module("Turning-Good-Agent.config.settings").Settings
AgentRuntime = import_module("Turning-Good-Agent.runtime.runtime").AgentRuntime
AsyncMessageBus = import_module("Turning-Good-Agent.bus.queue").AsyncMessageBus
create_app = import_module("Turning-Good-Agent.web.backend.app").create_app
WebSessionCoordinator = import_module("Turning-Good-Agent.web.backend.coordinator").WebSessionCoordinator
LLMResponse = import_module("Turning-Good-Agent.llm.types").LLMResponse
LLMUsage = import_module("Turning-Good-Agent.llm.types").LLMUsage
StateTrace = import_module("Turning-Good-Agent.observability.trace").StateTrace


class FakeLLM:
    """提供不依赖外部服务的最小 Web Host 模型。"""

    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        """返回固定回答及真实 usage。"""
        del messages, tools
        return LLMResponse("Web 测试回复。", usage=LLMUsage(3, 2, 5))


def test_static_public_assets_are_served(tmp_path):
    """验证前端公共品牌资源可由 Web Host 直接访问。"""
    settings = Settings(data_dir=tmp_path / ".sessions")
    runtime = AgentRuntime.create_default(settings, FakeLLM())

    with TestClient(create_app(settings, runtime)) as client:
        for path in ("/static/tga-brand.png", "/static/tga-wordmark.png"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["content-type"] == "image/png"


def test_message_action_error_keeps_websocket_usable(tmp_path):
    """验证动作错误回显关联 ID，且连接可继续接收后续消息。"""
    settings = Settings(data_dir=tmp_path / ".sessions")
    runtime = AgentRuntime.create_default(settings, FakeLLM())

    with TestClient(create_app(settings, runtime)) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(
                {"type": "message.send", "content": "", "client_action_id": "empty-message"}
            )
            error = websocket.receive_json()
            assert error == {
                "type": "error",
                "message": "消息不能为空",
                "client_action_id": "empty-message",
            }

            websocket.send_json(
                {"type": "message.send", "content": "继续发送", "client_action_id": "next-message"}
            )
            accepted = websocket.receive_json()

    assert accepted["type"] == "message.accepted"
    assert accepted["client_action_id"] == "next-message"
    assert accepted["session_id"].startswith("web-")
    assert accepted["request_id"]


def test_archived_session_rejection_keeps_websocket_usable(tmp_path):
    """验证归档会话被拒绝后仍可发送草稿首条消息。"""
    settings = Settings(data_dir=tmp_path / ".sessions")
    runtime = AgentRuntime.create_default(settings, FakeLLM())

    async def create_archived_session() -> None:
        """创建供 WebSocket 拒绝路径使用的归档会话。"""
        await runtime.sessions.store.create_session("archived-web", settings.user_id, "web")
        await runtime.sessions.store.update_session_metadata("archived-web", archived=True)

    asyncio.run(create_archived_session())
    with TestClient(create_app(settings, runtime)) as client:
        with client.websocket_connect("/ws") as websocket:
            websocket.send_json(
                {
                    "type": "message.send",
                    "session_id": "archived-web",
                    "content": "不应发送",
                    "client_action_id": "archived-message",
                }
            )
            error = websocket.receive_json()
            assert error == {
                "type": "error",
                "message": "请先恢复已归档会话",
                "client_action_id": "archived-message",
            }

            websocket.send_json(
                {"type": "message.send", "content": "草稿消息", "client_action_id": "draft-message"}
            )
            accepted = websocket.receive_json()

    assert accepted["type"] == "message.accepted"
    assert accepted["client_action_id"] == "draft-message"


def test_replayed_message_action_is_enqueued_once(tmp_path):
    """验证重试复用动作标识时不会重复创建 Runtime 任务。"""
    settings = Settings(data_dir=tmp_path / ".sessions")
    runtime = AgentRuntime.create_default(settings, FakeLLM())

    async def replay_action() -> None:
        """重复提交相同动作标识，并检查队列只保留一条消息。"""
        coordinator = WebSessionCoordinator(runtime, AsyncMessageBus(), 1, 8)
        first = await coordinator.send("web-retry", "继续处理", settings.user_id, "retry-action")
        second = await coordinator.send("web-retry", "继续处理", settings.user_id, "retry-action")

        assert first == second
        assert coordinator.bus.inbound.qsize() == 1
        assert not coordinator.controls["web-retry"].guidance

    asyncio.run(replay_action())


def test_context_window_returns_latest_saved_context_and_runtime_limit(tmp_path):
    """验证上下文窗口只返回最近一次 SAVE 的真实读数。"""
    settings = Settings(data_dir=tmp_path / ".sessions")
    settings.runtime.max_context_tokens = 300_000
    runtime = AgentRuntime.create_default(settings, FakeLLM())

    async def seed_context() -> None:
        """写入会话与两条不同阶段的上下文 trace。"""
        await runtime.sessions.store.create_session("context-web", settings.user_id, "web")
        await runtime.sessions.store.save_trace(
            StateTrace("turn-old", "context-web", "SAVE", 1, "saved", metadata={"current_context_tokens": 1_200})
        )
        await runtime.sessions.store.save_trace(
            StateTrace("turn-new", "context-web", "SAVE", 1, "saved", metadata={"current_context_tokens": 2_617})
        )

    asyncio.run(seed_context())
    with TestClient(create_app(settings, runtime)) as client:
        response = client.get("/api/sessions/context-web/context-window")

    assert response.status_code == 200
    assert response.json() == {"current_context_tokens": 2_617, "max_context_tokens": 300_000}

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest


def test_web_channel_capabilities_allow_guidance() -> None:
    """Would fail if server-side Web guidance were no longer an explicit capability."""
    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")

    assert app_module.WEB_CHANNEL_CAPABILITIES.supports_guidance is True


def test_cli_websocket_authenticates_binds_a_route_and_forwards_controls() -> None:
    """Would fail if `/ws/cli` created a local Runtime or lost its bound CLI route."""
    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
    transport_module = importlib.import_module("Turning-Good-Agent.channels.cli_gateway")

    class CliCoordinator:
        def __init__(self) -> None:
            self.sent = []
            self.stopped = []
            self.approvals = []
            self.disconnected = []

        async def send(self, route, request_id, content):
            self.sent.append((route, request_id, content))
            return request_id

        async def stop(self, route):
            self.stopped.append(route)

        async def resolve_approval(self, route, approval_id, approved):
            self.approvals.append((route, approval_id, approved))
            return True

        async def disconnect(self, route):
            self.disconnected.append(route)

    class Gateway:
        def __init__(self) -> None:
            self.settings = SimpleNamespace(
                gateway=SimpleNamespace(principal_id="stan", auth_token="local-token")
            )
            self.web_coordinator = object()
            self.runtime_supervisor = object()
            self.config_control = object()
            self.proactive_control = object()
            self.proactive_events = object()
            self.proactive_owner_state = object()
            self.cli_transport = transport_module.CliGatewayTransport()
            self.cli_coordinator = CliCoordinator()

        async def start(self):
            return None

        async def close(self):
            return None

    gateway = Gateway()
    app = app_module.create_app(gateway)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/cli", headers={"Authorization": "Bearer local-token"}) as websocket:
            websocket.send_json(
                {"type": "cli.connect", "connection_id": "connection-1", "conversation_id": "research"}
            )
            ready = websocket.receive_json()
            assert ready["type"] == "cli.ready"
            assert ready["conversation_id"] == "research"
            assert ready["session_id"]

            websocket.send_json({"type": "message.send", "request_id": "request-1", "content": "开始"})
            assert websocket.receive_json() == {
                "type": "message.accepted",
                "session_id": ready["session_id"],
                "request_id": "request-1",
            }
            websocket.send_json({"type": "guidance.send", "content": "优先结论"})
            assert websocket.receive_json() == {
                "type": "error",
                "message": "CLI Channel 不支持运行中引导",
            }
            websocket.send_json({"type": "task.stop"})
            assert websocket.receive_json() == {"type": "task.stopping", "session_id": ready["session_id"]}
            websocket.send_json({"type": "approval.resolve", "approval_id": "approval-1", "approved": True})
            assert websocket.receive_json() == {
                "type": "approval.resolved",
                "session_id": ready["session_id"],
                "approval_id": "approval-1",
                "approved": True,
            }

    route, request_id, content = gateway.cli_coordinator.sent[0]
    assert (route.principal_id, route.channel, route.conversation_id) == ("stan", "cli", "research")
    assert request_id == "request-1"
    assert content == "开始"
    assert gateway.cli_coordinator.stopped == [route]
    assert gateway.cli_coordinator.approvals == [(route, "approval-1", True)]
    assert gateway.cli_coordinator.disconnected == [route]


def test_duplicate_cli_connection_id_does_not_remove_the_existing_connection() -> None:
    """Would fail if a rejected reconnect ran `finally` against the live CLI connection."""

    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
    transport_module = importlib.import_module("Turning-Good-Agent.channels.cli_gateway")

    class CliCoordinator:
        async def disconnect(self, route) -> None:
            del route

    class Gateway:
        def __init__(self) -> None:
            self.settings = SimpleNamespace(
                gateway=SimpleNamespace(principal_id="stan", auth_token="local-token")
            )
            self.web_coordinator = object()
            self.runtime_supervisor = object()
            self.config_control = object()
            self.proactive_control = object()
            self.proactive_events = object()
            self.proactive_owner_state = object()
            self.cli_transport = transport_module.CliGatewayTransport()
            self.cli_coordinator = CliCoordinator()

        async def start(self) -> None:
            return None

        async def close(self) -> None:
            return None

    gateway = Gateway()
    app = app_module.create_app(gateway)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/cli", headers={"Authorization": "Bearer local-token"}) as original:
            original.send_json(
                {"type": "cli.connect", "connection_id": "connection-1", "conversation_id": "research"}
            )
            ready = original.receive_json()

            with client.websocket_connect("/ws/cli", headers={"Authorization": "Bearer local-token"}) as duplicate:
                duplicate.send_json(
                    {"type": "cli.connect", "connection_id": "connection-1", "conversation_id": "planning"}
                )
                assert duplicate.receive_json() == {
                    "type": "error",
                    "message": "CLI connection_id is already registered",
                }

            assert gateway.cli_transport.active_route("stan").session_id == ready["session_id"]


def test_cli_websocket_rejects_non_boolean_approval_values() -> None:
    """Would fail if JSON text such as `"false"` were treated as approval."""

    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
    transport_module = importlib.import_module("Turning-Good-Agent.channels.cli_gateway")

    class CliCoordinator:
        def __init__(self) -> None:
            self.approvals = []

        async def resolve_approval(self, route, approval_id, approved):
            self.approvals.append((route, approval_id, approved))
            return True

        async def disconnect(self, route) -> None:
            del route

    class Gateway:
        def __init__(self) -> None:
            self.settings = SimpleNamespace(
                gateway=SimpleNamespace(principal_id="stan", auth_token="local-token")
            )
            self.web_coordinator = object()
            self.runtime_supervisor = object()
            self.config_control = object()
            self.proactive_control = object()
            self.proactive_events = object()
            self.proactive_owner_state = object()
            self.cli_transport = transport_module.CliGatewayTransport()
            self.cli_coordinator = CliCoordinator()

        async def start(self) -> None:
            return None

        async def close(self) -> None:
            return None

    gateway = Gateway()
    app = app_module.create_app(gateway)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/cli", headers={"Authorization": "Bearer local-token"}) as websocket:
            websocket.send_json(
                {"type": "cli.connect", "connection_id": "connection-1", "conversation_id": "research"}
            )
            websocket.receive_json()
            for action in (
                {"type": "approval.resolve", "approval_id": "approval-1", "approved": "false"},
                {"type": "approval.resolve", "approval_id": "approval-1", "approved": 0},
                {"type": "approval.resolve", "approval_id": "approval-1"},
            ):
                websocket.send_json(action)
                assert websocket.receive_json() == {
                    "type": "error",
                    "message": "approved 必须是布尔值",
                }

            assert gateway.cli_coordinator.approvals == []
            websocket.send_json(
                {"type": "approval.resolve", "approval_id": "approval-1", "approved": False}
            )
            resolved = websocket.receive_json()
            assert resolved["type"] == "approval.resolved"
            assert resolved["approved"] is False

    assert gateway.cli_coordinator.approvals[0][1:] == ("approval-1", False)


# 验证无效模式在 CLI 入站消息提交前被拒绝。
def test_cli_rejects_invalid_multi_agent_mode_before_turn_submission() -> None:
    # 构造只记录已提交入站消息的协调器依赖。
    async def run() -> None:
        gateway_module = importlib.import_module("Turning-Good-Agent.channels.cli_gateway")
        messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
        queue_module = importlib.import_module("Turning-Good-Agent.bus.queue")
        submitted = []

        # 记录合法入站消息，验证非法模式不会到达此处。
        async def submit(message, *, dispatch):
            submitted.append(message)
            await dispatch(message)
            return True

        # 提供不可达的正常终态回调。
        async def complete_route_turn(route, request_id):
            del route, request_id

        # 提供不可达的断线清理回调。
        async def discard_pending_route(route):
            del route

        route = messages_module.ChannelRoute("stan", "cli", "research", "opaque-session")
        coordinator = gateway_module.CliGatewayCoordinator(
            queue_module.AsyncMessageBus(),
            submit=submit,
            complete_route_turn=complete_route_turn,
            discard_pending_route=discard_pending_route,
        )

        for mode in ("enabled", "on"):
            with pytest.raises(ValueError, match="multi_agent_mode 必须是 auto 或 off"):
                await coordinator.send(route, "request-1", "分析项目", multi_agent_mode=mode)
        assert submitted == []

    asyncio.run(run())


# 验证已移除的 CLI Run 读取动作被统一拒绝。
def test_cli_rejects_removed_multi_agent_read_actions() -> None:
    # 构造不提供 Multi-Agent 历史读取器的最小 Gateway。
    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
    transport_module = importlib.import_module("Turning-Good-Agent.channels.cli_gateway")

    class CliCoordinator:
        # 任何普通消息提交都应使测试失败。
        async def send(self, *args, **kwargs):
            del args, kwargs
            raise AssertionError("旧读取动作不得创建 turn")

        # 断线清理不属于读取动作。
        async def disconnect(self, route):
            del route

    class Gateway:
        # 初始化最小 CLI 测试 Gateway。
        def __init__(self) -> None:
            self.settings = SimpleNamespace(
                gateway=SimpleNamespace(principal_id="stan", auth_token="local-token")
            )
            self.web_coordinator = object()
            self.runtime_supervisor = object()
            self.config_control = object()
            self.proactive_control = object()
            self.proactive_events = object()
            self.proactive_owner_state = object()
            self.cli_transport = transport_module.CliGatewayTransport()
            self.cli_coordinator = CliCoordinator()
            self.current_runtime = object()

        # 保持 TestClient 生命周期可用。
        async def start(self):
            return None

        # 保持 TestClient 生命周期可用。
        async def close(self):
            return None

    app = app_module.create_app(Gateway())
    with TestClient(app) as client:
        with client.websocket_connect("/ws/cli", headers={"Authorization": "Bearer local-token"}) as websocket:
            websocket.send_json(
                {"type": "cli.connect", "connection_id": "connection-1", "conversation_id": "research"}
            )
            ready = websocket.receive_json()
            for action in (
                {"type": "multi_agent.status"},
                {"type": "multi_agent.show", "run_id": "run-1"},
            ):
                websocket.send_json(action)
                assert websocket.receive_json() == {
                    "type": "error",
                    "message": "不支持的 CLI WebSocket 动作",
                }

from __future__ import annotations

import asyncio
import importlib
import json
from types import SimpleNamespace

import pytest


class RecordingSocket:
    def __init__(self, incoming: list[dict[str, object]]) -> None:
        self._incoming = [json.dumps(item) for item in incoming]
        self.sent: list[dict[str, object]] = []
        self.closed = False

    async def send(self, payload: str) -> None:
        self.sent.append(json.loads(payload))

    async def recv(self) -> str:
        return self._incoming.pop(0)

    async def close(self) -> None:
        self.closed = True


def test_gateway_cli_client_authenticates_connects_and_preserves_event_payload() -> None:
    """Would fail if `tga chat` bypassed the authenticated Gateway CLI protocol."""

    async def run() -> None:
        cli = importlib.import_module("Turning-Good-Agent.cli")
        socket = RecordingSocket(
            [
                {
                    "type": "cli.ready",
                    "conversation_id": "research",
                    "session_id": "opaque-session",
                },
                {
                    "type": "response.delta",
                    "session_id": "opaque-session",
                    "request_id": "request-1",
                    "content": "流式内容",
                    "tool_name": "read_file",
                    "args": {"path": "note.txt"},
                },
            ]
        )
        connector_calls: list[tuple[str, dict[str, str]]] = []

        async def connector(url: str, headers: dict[str, str]) -> RecordingSocket:
            connector_calls.append((url, headers))
            return socket

        settings = SimpleNamespace(
            gateway=SimpleNamespace(host="127.0.0.1", port=9231, auth_token="local-token")
        )
        client = cli.GatewayCliClient.from_settings(
            settings,
            connector=connector,
            connection_id="connection-1",
        )

        assert await client.connect("research") == "opaque-session"
        assert await client.send_message("你好", request_id="request-1") == "request-1"
        assert await client.receive_event() == {
            "type": "response.delta",
            "session_id": "opaque-session",
            "request_id": "request-1",
            "content": "流式内容",
            "tool_name": "read_file",
            "args": {"path": "note.txt"},
        }
        await client.close()

        assert connector_calls == [
            ("ws://127.0.0.1:9231/ws/cli", {"Authorization": "Bearer local-token"})
        ]
        assert socket.sent == [
            {
                "type": "cli.connect",
                "connection_id": "connection-1",
                "conversation_id": "research",
            },
            {"type": "message.send", "request_id": "request-1", "content": "你好"},
        ]
        assert socket.closed is True

    asyncio.run(run())


def test_chat_uses_the_gateway_client_and_closes_it_on_exit() -> None:
    """Would fail if the interactive command still constructed a local Host or Runtime."""

    class RecordingClient:
        def __init__(self) -> None:
            self.connected: list[str] = []
            self.sent: list[str] = []
            self.closed = False
            self._events = asyncio.Event()

        async def connect(self, conversation_id: str) -> str:
            self.connected.append(conversation_id)
            return "opaque-session"

        async def send_message(self, content: str) -> str:
            self.sent.append(content)
            return f"request-{len(self.sent)}"

        async def receive_event(self) -> dict[str, object]:
            await self._events.wait()
            raise AssertionError("receiver should be cancelled on /exit")

        async def close(self) -> None:
            self.closed = True

    async def run() -> None:
        cli = importlib.import_module("Turning-Good-Agent.cli")
        client = RecordingClient()
        inputs = iter(("你好", "/exit"))

        async def read_content() -> str:
            return next(inputs)

        await cli.chat(
            "research",
            settings_loader=lambda: SimpleNamespace(),
            client_factory=lambda _: client,
            read_content=read_content,
            render=lambda *_: None,
        )

        assert client.connected == ["research"]
        assert client.sent == ["你好"]
        assert client.closed is True

    asyncio.run(run())


def test_gateway_cli_client_sends_only_session_scoped_stop_and_approval_controls() -> None:
    """Would fail if CLI stop or approval bypassed its bound Gateway session."""

    async def run() -> None:
        cli = importlib.import_module("Turning-Good-Agent.cli")
        socket = RecordingSocket(
            [{"type": "cli.ready", "conversation_id": "research", "session_id": "opaque-session"}]
        )

        async def connector(_: str, __: dict[str, str]) -> RecordingSocket:
            return socket

        settings = SimpleNamespace(
            gateway=SimpleNamespace(host="127.0.0.1", port=9231, auth_token="local-token")
        )
        client = cli.GatewayCliClient.from_settings(
            settings,
            connector=connector,
            connection_id="connection-1",
        )
        await client.connect("research")

        await client.request_stop()
        await client.resolve_approval("approval-1", approved=True)

        assert socket.sent[1:] == [
            {"type": "task.stop", "session_id": "opaque-session"},
            {
                "type": "approval.resolve",
                "session_id": "opaque-session",
                "approval_id": "approval-1",
                "approved": True,
            },
        ]

    asyncio.run(run())


def test_gateway_cli_client_carries_one_shot_multi_agent_mode() -> None:
    """CLI 模式只附着到下一条 message.send，发送后立即清除。"""

    async def run() -> None:
        cli = importlib.import_module("Turning-Good-Agent.cli")
        socket = RecordingSocket(
            [{"type": "cli.ready", "conversation_id": "research", "session_id": "opaque-session"}]
        )

        async def connector(_: str, __: dict[str, str]) -> RecordingSocket:
            return socket

        settings = SimpleNamespace(
            gateway=SimpleNamespace(host="127.0.0.1", port=9231, auth_token="local-token")
        )
        client = cli.GatewayCliClient.from_settings(settings, connector=connector, connection_id="connection-1")
        await client.connect("research")
        client.set_multi_agent_mode("auto")
        await client.send_message("first")
        await client.send_message("second")
        with pytest.raises(ValueError, match="auto 或 off"):
            client.set_multi_agent_mode("on")
        with pytest.raises(ValueError, match="auto 或 off"):
            client.set_multi_agent_mode("invalid")
        assert socket.sent[1:] == [
            {"type": "message.send", "request_id": socket.sent[1]["request_id"], "content": "first", "multi_agent_mode": "auto"},
            {"type": "message.send", "request_id": socket.sent[2]["request_id"], "content": "second"},
        ]

    asyncio.run(run())


# 验证 CLI 继续渲染实时 Multi-Agent 生命周期事件。
def test_cli_renderer_displays_multi_agent_lifecycle_event() -> None:
    cli = importlib.import_module("Turning-Good-Agent.cli")
    rendered: list[str] = []
    cli._render_event(
        {
            "type": "multi_agent.node.updated",
            "run_id": "run-1",
            "node_id": "risk",
            "task_label": "风险审查",
            "strategy": "review",
            "status": "failed",
            "duration_ms": 10.0,
            "usage": {"turn_total_tokens": 5},
            "error_code": "worker_failed",
            "error": "Worker 执行失败",
        },
        lambda text, _: rendered.append(text),
        set(),
    )

    assert any("耗时: 10.0ms" in text and "Token: 5" in text for text in rendered)
    assert any("失败: worker_failed Worker 执行失败" in text for text in rendered)


def test_chat_turns_a_prompt_answer_into_an_approval_decision() -> None:
    """Would fail if a remote approval no longer reached the terminal prompt path."""

    class ApprovalClient:
        def __init__(self) -> None:
            self.approvals: list[tuple[str, bool]] = []
            self.closed = False
            self._emitted = False
            self.approval_visible = asyncio.Event()

        async def connect(self, _: str) -> str:
            return "opaque-session"

        async def send_message(self, _: str) -> str:
            raise AssertionError("approval answer must not become a chat message")

        async def receive_event(self) -> dict[str, object]:
            if not self._emitted:
                self._emitted = True
                return {
                    "type": "approval.requested",
                    "session_id": "opaque-session",
                    "approval_id": "approval-1",
                    "tool_name": "write_file",
                    "args": "{\"path\": \"note.txt\"}",
                }
            await asyncio.Event().wait()
            raise AssertionError("receiver should be cancelled on /exit")

        async def resolve_approval(self, approval_id: str, approved: bool) -> None:
            self.approvals.append((approval_id, approved))

        async def close(self) -> None:
            self.closed = True

    async def run() -> None:
        cli = importlib.import_module("Turning-Good-Agent.cli")
        client = ApprovalClient()
        inputs = iter(("Y", "/exit"))

        async def read_content() -> str:
            await client.approval_visible.wait()
            return next(inputs)

        def render(content: str, _: bool) -> None:
            if content.startswith("[审批]"):
                client.approval_visible.set()

        await cli.chat(
            "research",
            settings_loader=lambda: SimpleNamespace(),
            client_factory=lambda _: client,
            read_content=read_content,
            render=render,
        )

        assert client.approvals == [("approval-1", True)]
        assert client.closed is True

    asyncio.run(run())


def test_chat_parser_defaults_the_conversation_and_preserves_an_explicit_one() -> None:
    """Would fail if `tga chat` did not connect to the stable default conversation."""
    cli = importlib.import_module("Turning-Good-Agent.cli")
    parser = cli.build_parser()

    default_args = parser.parse_args(["chat"])
    explicit_args = parser.parse_args(["chat", "--session", "research"])

    assert default_args.session == "default"
    assert explicit_args.session == "research"
    with pytest.raises(SystemExit):
        parser.parse_args(["chat", "--session", "research", "--api-key", "secret"])

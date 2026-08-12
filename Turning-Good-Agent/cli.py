from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections.abc import Awaitable, Callable
from contextlib import suppress
from typing import Any, Protocol
from uuid import uuid4

from .config.settings import Settings


class GatewaySocket(Protocol):
    """CLI 所需的最小异步 WebSocket 接口。"""

    async def send(self, payload: str) -> None:
        """发送一段 JSON 文本。"""

    async def recv(self) -> str | bytes:
        """接收一段 JSON 文本。"""

    async def close(self) -> None:
        """关闭连接。"""


GatewayConnector = Callable[[str, dict[str, str]], Awaitable[GatewaySocket]]
ContentReader = Callable[[], Awaitable[str]]
Renderer = Callable[[str, bool], None]


class GatewayCliClient:
    """只通过认证 WebSocket 与唯一 Gateway 交互的 CLI Client。"""

    def __init__(
        self,
        url: str,
        auth_token: str,
        *,
        connector: GatewayConnector | None = None,
        connection_id: str | None = None,
    ) -> None:
        self._url = url
        self._auth_token = auth_token
        self._connector = connector or _connect_websocket
        self._connection_id = connection_id or str(uuid4())
        self._socket: GatewaySocket | None = None
        self._session_id: str | None = None
        self._pending_multi_agent_mode: str | None = None

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        *,
        connector: GatewayConnector | None = None,
        connection_id: str | None = None,
    ) -> "GatewayCliClient":
        """从本地 Gateway 配置构建 Client，不读取 LLM 或 Runtime 配置。"""
        gateway = settings.gateway
        token = getattr(gateway, "auth_token", None)
        if not isinstance(token, str) or not token:
            raise RuntimeError("缺少 gateway.auth_token；请先启动 tga gateway 以创建本机凭据")
        host = str(getattr(gateway, "host", "127.0.0.1"))
        port = int(getattr(gateway, "port", 8000))
        return cls(
            _gateway_url(host, port),
            token,
            connector=connector,
            connection_id=connection_id,
        )

    @property
    def session_id(self) -> str | None:
        """返回 Gateway 在握手后确认的 opaque session ID。"""
        return self._session_id

    async def connect(self, conversation_id: str) -> str:
        """认证并把当前终端绑定到一个 CLI conversation。"""
        if self._socket is not None:
            raise RuntimeError("CLI Gateway Client 已连接")
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("session 必须是非空字符串")
        self._socket = await self._connector(
            self._url,
            {"Authorization": f"Bearer {self._auth_token}"},
        )
        try:
            await self._send(
                {
                    "type": "cli.connect",
                    "connection_id": self._connection_id,
                    "conversation_id": conversation_id.strip(),
                }
            )
            ready = await self.receive_event()
            if ready.get("type") == "error":
                raise RuntimeError(str(ready.get("message", "Gateway 拒绝 CLI 连接")))
            if ready.get("type") != "cli.ready":
                raise RuntimeError("Gateway 未确认 CLI 会话")
            session_id = ready.get("session_id")
            if not isinstance(session_id, str) or not session_id:
                raise RuntimeError("Gateway 返回了无效 CLI session")
            if ready.get("conversation_id") != conversation_id.strip():
                raise RuntimeError("Gateway 返回了不匹配的 CLI conversation")
            self._session_id = session_id
            return session_id
        except BaseException:
            await self.close()
            raise

    async def send_message(self, content: str, *, request_id: str | None = None) -> str:
        """提交一条普通聊天输入到当前 Gateway 绑定路由。"""
        text = content.strip()
        if not text:
            raise ValueError("消息不能为空")
        identifier = request_id or str(uuid4())
        payload: dict[str, object] = {"type": "message.send", "request_id": identifier, "content": text}
        if self._pending_multi_agent_mode is not None:
            payload["multi_agent_mode"] = self._pending_multi_agent_mode
        await self._send(payload)
        self._pending_multi_agent_mode = None
        return identifier

    # 暂存只作用于下一条普通消息的 Multi-Agent 模式。
    def set_multi_agent_mode(self, value: str) -> None:
        from .multi_agent.types import parse_multi_agent_mode

        self._pending_multi_agent_mode = parse_multi_agent_mode(value).value

    async def request_stop(self) -> None:
        """请求当前 CLI turn 在下一个安全检查点停止。"""
        await self._send({"type": "task.stop", "session_id": self._require_session()})

    async def resolve_approval(self, approval_id: str, *, approved: bool) -> None:
        """提交当前终端针对一个工具审批的选择。"""
        if not isinstance(approval_id, str) or not approval_id:
            raise ValueError("approval_id 必须是非空字符串")
        await self._send(
            {
                "type": "approval.resolve",
                "session_id": self._require_session(),
                "approval_id": approval_id,
                "approved": approved,
            }
        )

    async def receive_event(self) -> dict[str, object]:
        """读取并验证一条 Gateway JSON 事件。"""
        socket = self._require_socket()
        payload = await socket.recv()
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8")
        try:
            event = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise RuntimeError("Gateway 返回了无效 JSON") from exc
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            raise RuntimeError("Gateway 返回了无效事件")
        return event

    async def close(self) -> None:
        """断开瞬态 CLI 连接，不请求重放或创建 Outbox。"""
        socket, self._socket = self._socket, None
        self._session_id = None
        if socket is not None:
            await socket.close()

    async def _send(self, payload: dict[str, object]) -> None:
        await self._require_socket().send(json.dumps(payload, ensure_ascii=False))

    def _require_socket(self) -> GatewaySocket:
        if self._socket is None:
            raise RuntimeError("CLI Gateway Client 尚未连接")
        return self._socket

    def _require_session(self) -> str:
        if self._session_id is None:
            raise RuntimeError("CLI Gateway Client 尚未完成会话握手")
        return self._session_id


def supports_prompt_toolkit(input_stream: Any = None, output_stream: Any = None) -> bool:
    """仅在真实交互终端启用 prompt_toolkit。"""
    source = sys.stdin if input_stream is None else input_stream
    target = sys.stdout if output_stream is None else output_stream
    try:
        return bool(source.isatty() and target.isatty())
    except (AttributeError, OSError):
        return False


def build_parser() -> argparse.ArgumentParser:
    """创建只暴露 Gateway Client 所需参数的命令行解析器。"""
    parser = argparse.ArgumentParser(prog="tga")
    subcommands = parser.add_subparsers(dest="command")
    chat = subcommands.add_parser("chat", help="连接本机 Gateway 的交互式 CLI 会话")
    chat.add_argument("--session", default="default", help="CLI conversation 名称")
    gateway = subcommands.add_parser("gateway", help="启动唯一 Gateway 与 Web 服务")
    gateway.add_argument("--host", help="仅覆盖本次 Gateway 监听地址")
    gateway.add_argument("--port", type=int, help="仅覆盖本次 Gateway 监听端口")
    return parser


async def chat(
    session_id: str,
    *,
    settings_loader: Callable[[], Any] = Settings.load,
    client_factory: Callable[[Any], GatewayCliClient] = GatewayCliClient.from_settings,
    read_content: ContentReader | None = None,
    render: Renderer | None = None,
) -> None:
    """运行纯 Gateway Client 的终端输入、流式输出和交互控制循环。"""
    settings = settings_loader()
    client = client_factory(settings)
    default_reader, default_renderer = _terminal_io()
    reader = read_content or default_reader
    renderer = render or default_renderer
    ui_events: asyncio.Queue[tuple[str, object]] = asyncio.Queue()
    input_consumed = asyncio.Event()
    approvals: list[dict[str, object]] = []
    streamed: set[str] = set()
    receiver: asyncio.Task[None] | None = None
    input_task: asyncio.Task[None] | None = None
    try:
        await client.connect(session_id)
        await ui_events.put(
            ("notice", "Turning Good Agent。输入 /exit 退出；/stop 停止；/multi auto|off。")
        )
        receiver = asyncio.create_task(
            _receive_cli_events(client, ui_events),
            name="cli-gateway-receiver",
        )
        input_task = asyncio.create_task(
            _receive_terminal_input(reader, ui_events, input_consumed),
            name="cli-terminal-input",
        )
        while True:
            event_type, payload = await ui_events.get()
            if event_type == "notice":
                renderer(str(payload), True)
                continue
            if event_type == "gateway":
                event = payload
                assert isinstance(event, dict)
                if event["type"] == "approval.requested":
                    approval_id = event.get("approval_id")
                    if isinstance(approval_id, str) and approval_id:
                        approvals.append(event)
                        renderer(_approval_prompt(event), True)
                        continue
                _render_event(event, renderer, streamed)
                continue
            if event_type == "gateway.error":
                renderer(f"[错误] {payload}", True)
                return
            if event_type == "input.closed":
                return
            assert event_type == "input"
            content = str(payload).strip()
            input_consumed.set()
            if content == "/exit":
                return
            if approvals:
                approval = approvals.pop(0)
                await client.resolve_approval(
                    str(approval["approval_id"]),
                    approved=_is_approval(content),
                )
                continue
            if not content:
                continue
            if content == "/stop":
                await client.request_stop()
                continue
            if content.startswith("/multi"):
                parts = content.split(maxsplit=2)
                mode = parts[1] if len(parts) > 1 else ""
                if mode in {"auto", "off"} and len(parts) == 2:
                    client.set_multi_agent_mode(mode)
                    label = {"auto": "自动", "off": "关闭"}[mode]
                    renderer(f"[Multi-Agent] 下一条消息模式：{label}", True)
                    continue
                renderer("[错误] 用法：/multi auto|off", True)
                continue
            await client.send_message(content)
    finally:
        for task in (receiver, input_task):
            if task is not None:
                task.cancel()
        if receiver is not None or input_task is not None:
            with suppress(asyncio.CancelledError):
                await asyncio.gather(
                    *(task for task in (receiver, input_task) if task is not None),
                )
        await client.close()


async def _receive_cli_events(
    client: GatewayCliClient,
    ui_events: asyncio.Queue[tuple[str, object]],
) -> None:
    try:
        while True:
            await ui_events.put(("gateway", await client.receive_event()))
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        await ui_events.put(("gateway.error", str(exc)))


async def _receive_terminal_input(
    reader: ContentReader,
    ui_events: asyncio.Queue[tuple[str, object]],
    input_consumed: asyncio.Event,
) -> None:
    try:
        while True:
            await ui_events.put(("input", await reader()))
            await input_consumed.wait()
            input_consumed.clear()
    except asyncio.CancelledError:
        raise
    except (EOFError, KeyboardInterrupt):
        await ui_events.put(("input.closed", None))


def _render_event(event: dict[str, object], render: Renderer, streamed: set[str]) -> None:
    event_type = str(event["type"])
    content = str(event.get("content", ""))
    request_id = str(event.get("request_id", ""))
    if event_type == "response.delta":
        render(content, False)
        if request_id:
            streamed.add(request_id)
        return
    if event_type in {"response.completed", "response.error"}:
        if request_id in streamed:
            render("", True)
            streamed.discard(request_id)
        else:
            render(content, True)
        return
    if event_type == "tool.started":
        tool_name = str(event.get("tool_name", "未知工具"))
        render(f"[工具] 开始 {tool_name} {_argument_summary(event.get('args'))}", True)
        return
    if event_type == "tool.finished":
        tool_name = str(event.get("tool_name", "未知工具"))
        state = "失败" if event.get("failed") is True else "完成"
        render(f"[工具] {state} {tool_name}", True)
        return
    if event_type.startswith("proactive."):
        render(f"[主动提醒] {content}", True)
        return
    if event_type.startswith("multi_agent."):
        run_id = str(event.get("run_id", ""))
        node_id = event.get("node_id")
        status = str(event.get("status", ""))
        strategy = str(event.get("strategy", ""))
        label = str(event.get("task_label", ""))
        prefix = f"[Multi-Agent {run_id[:8]}]"
        metrics = _multi_agent_metrics(event)
        failure = _multi_agent_failure_summary(event)
        if node_id:
            render(f"{prefix}  └─ {strategy} {label or node_id}: {status} {metrics}{failure}", True)
        else:
            render(f"{prefix} {strategy} {status} {metrics}{failure}", True)
        return
    if event_type == "error":
        render(f"[错误] {event.get('message', content)}", True)
        return
    if event_type in {"message.accepted", "task.stopping", "approval.resolved"}:
        return
    render(f"[系统] {content}", True)


# 格式化安全 Multi-Agent 观测指标。
def _multi_agent_metrics(value: dict[str, object]) -> str:
    duration = value.get("duration_ms")
    duration_text = f"{float(duration):.1f}ms" if isinstance(duration, (int, float)) else "-"
    usage = value.get("usage")
    token_total = None
    if isinstance(usage, dict):
        total = usage.get("total")
        source = total if isinstance(total, dict) else usage
        candidate = source.get("turn_total_tokens")
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            token_total = candidate
    return f"耗时: {duration_text} Token: {token_total if token_total is not None else '-'}"


# 格式化安全 Multi-Agent 失败摘要。
def _multi_agent_failure_summary(value: dict[str, object]) -> str:
    error_code = value.get("error_code")
    error = value.get("error")
    details = " ".join(str(item) for item in (error_code, error) if isinstance(item, str) and item)
    return f" 失败: {details}" if details else ""


def _approval_prompt(event: dict[str, object]) -> str:
    tool_name = str(event.get("tool_name", "未知工具"))
    return f"[审批] 允许执行 {tool_name} {_argument_summary(event.get('args'))}？[y/N]"


def _argument_summary(arguments: object, *, limit: int = 180) -> str:
    try:
        rendered = json.dumps(arguments if arguments is not None else {}, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        rendered = "{}"
    return rendered if len(rendered) <= limit else f"{rendered[: limit - 1]}…"


def _is_approval(content: str) -> bool:
    return content.strip() in {"y", "Y"}


def _terminal_io() -> tuple[ContentReader, Renderer]:
    interactive = supports_prompt_toolkit()
    if interactive:
        from prompt_toolkit import PromptSession
        from prompt_toolkit.patch_stdout import patch_stdout
        from prompt_toolkit import print_formatted_text

        prompt = PromptSession()

        async def read_content() -> str:
            with patch_stdout(raw=True):
                return await prompt.prompt_async("> ")

        def render(content: str, newline: bool) -> None:
            print_formatted_text(content, end="\n" if newline else "")

        return read_content, render

    async def read_content() -> str:
        return await asyncio.to_thread(input, "> ")

    def render(content: str, newline: bool) -> None:
        print(content, end="\n" if newline else "", flush=True)

    return read_content, render


async def _connect_websocket(url: str, headers: dict[str, str]) -> GatewaySocket:
    try:
        from websockets.asyncio.client import connect
    except ModuleNotFoundError as exc:
        raise RuntimeError("缺少 websockets 依赖；请重新安装 Turning Good Agent") from exc
    return await connect(url, additional_headers=headers)


def _gateway_url(host: str, port: int) -> str:
    rendered_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    return f"ws://{rendered_host}:{port}/ws/cli"


def gateway(*, host: str | None = None, port: int | None = None) -> None:
    """启动唯一 Gateway；Runtime、Bus 和主动服务只在此入口创建。"""
    import uvicorn

    from .gateway.host import GatewayHost
    from .web.backend.app import create_app

    settings = Settings.load()
    listen_host = settings.gateway.host if host is None else host
    gateway_host = GatewayHost(settings)
    uvicorn.run(
        create_app(gateway_host),
        host=listen_host,
        port=settings.gateway.port if port is None else port,
    )


def main() -> None:
    """执行 Gateway 或纯 Client CLI 入口。"""
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "chat":
        asyncio.run(chat(args.session))
        return
    if args.command == "gateway":
        gateway(host=args.host, port=args.port)
        return
    parser.print_help()

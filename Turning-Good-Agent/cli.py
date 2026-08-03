import argparse
import asyncio
import sys
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

from .bus.messages import InboundMessage, OutboundMessage
from .bus.queue import AsyncMessageBus
from .channels.cli import CliChannelAdapter
from .config.settings import Settings
from .llm.factory import OPENAI_COMPATIBLE_PROVIDER, build_llm
from .proactive.cli_lifecycle import CliProactiveLifecycle
from .runtime.runtime import AgentRuntime


class CliBusDispatcher:
    """顺序消费 CLI 入站队列，并把 Runtime 终态重新写回 outbound。"""

    def __init__(self, bus: AsyncMessageBus, runtime: AgentRuntime) -> None:
        """初始化对象状态。"""
        self._bus = bus
        self._runtime = runtime
        self._task: asyncio.Task[None] | None = None
        self._closed = False
        self._pending: dict[str, asyncio.Future[OutboundMessage]] = {}

    async def start(self) -> None:
        """启动当前服务。"""
        if self._task is None:
            self._task = asyncio.create_task(self._dispatch(), name="cli-inbound-dispatcher")

    async def close(self) -> None:
        """关闭当前资源。"""
        self._closed = True
        if self._task is not None:
            self._task.cancel()
            with suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        for future in self._pending.values():
            if not future.done():
                future.cancel()
        self._pending.clear()

    async def send_and_wait(self, message: InboundMessage) -> OutboundMessage:
        """发送输入并等待回复。"""
        if self._closed:
            raise RuntimeError("CLI dispatcher 已关闭")
        if message.id in self._pending:
            raise RuntimeError("重复的 CLI 请求 ID")
        future: asyncio.Future[OutboundMessage] = asyncio.get_running_loop().create_future()
        self._pending[message.id] = future
        try:
            await self._bus.publish_inbound(message)
            return await future
        finally:
            self._pending.pop(message.id, None)

    async def _dispatch(self) -> None:
        """分发入站消息并运行 Runtime。"""
        while not self._closed:
            message = await self._bus.consume_inbound()
            try:
                outbound = await self._runtime.run_turn(message)
            except Exception as exc:
                outbound = OutboundMessage.error(message.session_id, message.channel, f"请求失败：{exc}")
            outbound.metadata["request_id"] = message.id
            await self._bus.publish_outbound(outbound)
            future = self._pending.get(message.id)
            if future is not None and not future.done():
                future.set_result(outbound)


def supports_prompt_toolkit(input_stream: Any = None, output_stream: Any = None) -> bool:
    """仅在真实交互终端启用 prompt_toolkit 的 Win32 console 输出。"""
    source = sys.stdin if input_stream is None else input_stream
    target = sys.stdout if output_stream is None else output_stream
    try:
        return bool(source.isatty() and target.isatty())
    except (AttributeError, OSError):
        return False

def build_parser() -> argparse.ArgumentParser:
    """创建命令行参数解析器。"""
    parser = argparse.ArgumentParser(prog="tga")
    subcommands = parser.add_subparsers(dest="command")
    chat = subcommands.add_parser("chat", help="启动交互式 CLI 会话")
    chat.add_argument("--session", help="会话 ID，不传则每次启动创建临时会话")
    chat.add_argument("--data-dir", help="本地数据目录，未传时读取 settings.local.json 或默认值")
    chat.add_argument(
        "--llm",
        choices=["openai-compatible"],
        help="LLM 接入类型",
    )
    chat.add_argument("--api-key", help="真实 LLM API Key")
    chat.add_argument("--base-url", help="OpenAI-compatible API Base URL")
    chat.add_argument("--model", help="真实 LLM 模型名")
    web = subcommands.add_parser("web", help="启动本机 Web 工作台")
    web.add_argument("--host", help="本机监听地址")
    web.add_argument("--port", type=int, help="本机监听端口")
    return parser


def configure_readline_for_unicode_input() -> None:
    """配置 readline，避免中文输入退格删除异常。"""
    try:
        import readline

        readline.parse_and_bind("set bind-tty-special-chars off")
        readline.parse_and_bind("set input-meta on")
        readline.parse_and_bind("set output-meta on")
        readline.parse_and_bind("set convert-meta off")
    except ImportError:
        readline = None


def resolve_cli_session_id(session_id: str | None) -> str:
    """解析 CLI 会话 ID，默认创建临时会话。"""
    return session_id or f"cli-{uuid4()}"


def resolve_provider(provider: str) -> str:
    """将 provider 名称归一化到当前支持的实现类型。"""
    if provider == OPENAI_COMPATIBLE_PROVIDER:
        return OPENAI_COMPATIBLE_PROVIDER
    return provider


async def chat(
    session_id: str | None,
    data_dir: str | None,
    provider: str | None,
    api_key: str | None,
    base_url: str | None,
    model: str | None,
) -> None:
    """运行交互式聊天循环。"""
    active_session_id = resolve_cli_session_id(session_id)
    active_inbound_message: InboundMessage | None = None
    root = Path(data_dir) if data_dir is not None else None
    settings = Settings.load(data_dir=root, default_session_id=active_session_id)
    if provider is not None:
        settings.llm.provider = provider
    if api_key is not None:
        settings.llm.api_key = api_key
    if base_url is not None:
        settings.llm.base_url = base_url
    if model is not None:
        settings.llm.model = model
    runtime = AgentRuntime.create_default(settings, build_llm(settings))
    bus = AsyncMessageBus()
    interactive_terminal = supports_prompt_toolkit()
    prompt = None
    if interactive_terminal:
        from prompt_toolkit import PromptSession

        prompt = PromptSession()

    runtime.channel_router.register(
        settings.channel,
        lambda message: CliChannelAdapter(
            bus,
            message.session_id,
            prompt=prompt,
            request_id=message.id,
        ),
    )
    dispatcher = CliBusDispatcher(bus, runtime)
    proactive_lifecycle = CliProactiveLifecycle(
        runtime,
        bus,
        target_channel=settings.channel,
        active_session_id=lambda: active_session_id,
        active_inbound_message=lambda: active_inbound_message,
    )
    renderer = asyncio.create_task(
        _render_cli_outbound(bus, interactive=interactive_terminal),
        name="cli-outbound-renderer",
    )
    await dispatcher.start()
    await runtime.start()
    await proactive_lifecycle.start()
    await bus.publish_outbound(OutboundMessage.new(active_session_id, settings.channel, "Turning Good Agent。输入 /exit 退出。"))

    async def read_content() -> str:
        """读取一条用户输入。"""
        if prompt is not None:
            return await prompt.prompt_async("> ")
        return await asyncio.to_thread(input, "> ")

    async def run_input_loop() -> None:
        """循环读取并分发用户输入。"""
        nonlocal active_inbound_message, active_session_id
        while True:
            try:
                content = (await read_content()).strip()
            except (EOFError, KeyboardInterrupt):
                return
            if not content:
                continue
            msg = InboundMessage.new(content, active_session_id, settings.user_id, settings.channel)
            active_inbound_message = msg
            try:
                await dispatcher.send_and_wait(msg)
            finally:
                active_inbound_message = None
            if content == "/exit":
                return
            if content == "/new":
                active_session_id = resolve_cli_session_id(None)

    try:
        if prompt is None:
            await run_input_loop()
        else:
            from prompt_toolkit.patch_stdout import patch_stdout

            with patch_stdout(raw=True):
                await run_input_loop()
    finally:
        await proactive_lifecycle.stop()
        await dispatcher.close()
        renderer.cancel()
        with suppress(asyncio.CancelledError):
            await renderer
        close = getattr(runtime, "close", None)
        if callable(close):
            await close()


async def _render_cli_outbound(bus: AsyncMessageBus, *, interactive: bool) -> None:
    """由唯一 CLI 消费者显示普通回复、状态与后台主动通知。"""
    if interactive:
        from prompt_toolkit import print_formatted_text

        def render(content: str, *, end: str = "\n") -> None:
            """渲染文本到终端。"""
            print_formatted_text(content, end=end)

    else:

        def render(content: str, *, end: str = "\n") -> None:
            """渲染文本到终端。"""
            print(content, end=end, flush=True)

    streamed: set[str] = set()
    while True:
        outbound = await bus.consume_outbound()
        if outbound.target_channel != "cli":
            continue
        request_id = str(outbound.metadata.get("request_id", ""))
        if outbound.event_type == "response.delta":
            render(outbound.content, end="")
            if request_id:
                streamed.add(request_id)
            continue
        if outbound.event_type in {"response.completed", "response.error"}:
            if request_id in streamed:
                render("")
                streamed.discard(request_id)
            else:
                render(outbound.content)
            continue
        if outbound.event_type.startswith("proactive."):
            render(f"[主动提醒] {outbound.content}")
            continue
        render(f"[系统] {outbound.content}")


def web(host: str | None, port: int | None) -> None:
    """启动复用现有 Runtime 的本机 Web Host。"""
    import uvicorn

    from .web.backend.app import create_app

    settings = Settings.load()
    if host is not None:
        settings.web.host = host
    if port is not None:
        settings.web.port = port
    runtime = AgentRuntime.create_default(settings, build_llm(settings))
    uvicorn.run(create_app(settings, runtime), host=settings.web.host, port=settings.web.port)


def main() -> None:
    """执行 CLI 入口。"""
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "chat":
        asyncio.run(chat(args.session, args.data_dir, args.llm, args.api_key, args.base_url, args.model))
        return
    if args.command == "web":
        web(args.host, args.port)
        return
    parser.print_help()

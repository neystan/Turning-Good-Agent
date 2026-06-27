import argparse
import asyncio
from pathlib import Path
from uuid import uuid4

from .bus.messages import InboundMessage
from .config.settings import Settings
from .llm.client import LLMProvider
from .llm.openai_compatible import OpenAICompatibleLLM
from .runtime.runtime import AgentRuntime

OPENAI_COMPATIBLE_PROVIDER = "openai-compatible"


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


def build_llm(settings: Settings) -> LLMProvider:
    """根据集中配置创建模型 Provider。"""
    provider = resolve_provider(settings.llm.provider)
    if provider != OPENAI_COMPATIBLE_PROVIDER:
        raise ValueError(f"不支持的 LLM Provider：{settings.llm.provider}")

    if not settings.llm.api_key:
        raise ValueError("使用 openai-compatible 时必须在 settings.local.json 或命令行中设置 api_key")
    if not settings.llm.model:
        raise ValueError("使用 openai-compatible 时必须在 settings.local.json 或命令行中设置 model")
    return OpenAICompatibleLLM(
        api_key=settings.llm.api_key,
        base_url=settings.llm.base_url,
        model=settings.llm.model,
        timeout_seconds=settings.llm.timeout_seconds,
        max_retries=settings.llm.max_retries,
        retry_delay_seconds=settings.llm.retry_delay_seconds,
    )


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
    configure_readline_for_unicode_input()
    print("Turning Good Agent MVP。输入 /exit 退出。")
    while True:
        try:
            content = input("> ").strip()
        except EOFError:
            break
        if not content:
            continue
        msg = InboundMessage.new(content, active_session_id, settings.user_id, settings.channel)
        streamed = False
        response_line_open = False

        def print_delta(delta: str) -> None:
            """立即打印流式文本片段。"""
            nonlocal streamed, response_line_open
            streamed = True
            response_line_open = True
            print(delta, end="", flush=True)

        outbound = await runtime.run_turn(
            msg,
            print_delta if settings.llm.streaming_enabled else None,
        )
        if response_line_open:
            print()
        elif not streamed:
            print(outbound.content)
        if content == "/exit":
            break
        if content == "/new":
            active_session_id = resolve_cli_session_id(None)


def main() -> None:
    """执行 CLI 入口。"""
    parser = build_parser()
    args = parser.parse_args()
    if args.command == "chat":
        asyncio.run(chat(args.session, args.data_dir, args.llm, args.api_key, args.base_url, args.model))
        return
    parser.print_help()

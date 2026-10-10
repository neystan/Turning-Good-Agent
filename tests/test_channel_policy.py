from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest


def test_im_policy_hides_configured_and_declared_approval_tools() -> None:
    """Would fail if IM schemas retain a configured or tool-declared approval tool."""

    policy_module = importlib.import_module("Turning-Good-Agent.channels.policy")
    messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
    settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
    registry_module = importlib.import_module("Turning-Good-Agent.tools.registry")
    tool_base_module = importlib.import_module("Turning-Good-Agent.tools.base")

    class Tool:
        description = "test tool"
        input_schema = {"type": "object", "properties": {}}
        parallel_safe = False

        def __init__(self, name: str, *, approval_required: bool = False) -> None:
            self.name = name
            self.approval_required = approval_required

        async def run(self, args: dict[str, object]) -> object:
            del args
            return tool_base_module.ToolResult("ok")

    settings = settings_module.Settings()
    settings.tool_permissions.auto_approve_tools = True
    settings.tool_permissions.approval_required_tools = ["write_file"]
    tools = registry_module.ToolRegistry()
    tools.register(Tool("safe_tool"))
    tools.register(Tool("write_file"))
    tools.register(Tool("declared_approval_tool", approval_required=True))

    allowed = policy_module.DefaultChannelToolPolicy().allowed_tool_names(
        messages_module.ChannelRoute("principal", "weixin", "binding", "session"),
        tools,
        settings,
    )

    assert allowed == frozenset({"safe_tool"})


@pytest.mark.parametrize(
    ("channel", "command", "expected"),
    [
        (
            "weixin",
            "/new",
            "当前 IM 渠道不支持 /new。如需清空当前话题，请发送 /clear。",
        ),
        ("feishu", "/exit", "当前 IM 渠道不支持 /exit。"),
        (
            "weixin",
            "/approve on",
            "当前 IM 渠道不支持工具审批。请在 Web 或 CLI 中操作。",
        ),
        (
            "feishu",
            "/approve off",
            "当前 IM 渠道不支持工具审批。请在 Web 或 CLI 中操作。",
        ),
        (
            "weixin",
            "/approve unexpected",
            "当前 IM 渠道不支持工具审批。请在 Web 或 CLI 中操作。",
        ),
        ("weixin", "/multi auto", "当前 IM 渠道不支持 /multi 命令。请在 Web 或 CLI 中操作。"),
        ("feishu", "/multi off", "当前 IM 渠道不支持 /multi 命令。请在 Web 或 CLI 中操作。"),
    ],
)
def test_runtime_blocks_unsupported_im_commands_before_session_handling(
    channel: str,
    command: str,
    expected: str,
) -> None:
    """Unsupported IM commands must not reach SessionManager side effects."""

    async def run() -> None:
        state_module = importlib.import_module("Turning-Good-Agent.runtime.state")
        messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")

        class ExplodingSessions:
            async def handle_inbound_command(self, session_id: str, message: object) -> str:
                del session_id, message
                raise AssertionError("blocked IM command reached SessionManager")

        route = messages_module.ChannelRoute(
            "owner",
            channel,
            "binding",
            "session",
        )
        context = SimpleNamespace(
            inbound=messages_module.InboundMessage.new(command, route),
            shortcut_response=None,
            final_content=None,
            principal_context=None,
        )
        runtime = SimpleNamespace(sessions=ExplodingSessions())

        assert await state_module.command(runtime, context) == "shortcut"
        assert context.shortcut_response == expected
        assert context.final_content == expected

    asyncio.run(run())


def test_runtime_keeps_non_im_commands_on_existing_path() -> None:
    async def run() -> None:
        state_module = importlib.import_module("Turning-Good-Agent.runtime.state")
        messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")

        class Sessions:
            async def handle_inbound_command(self, session_id: str, message: object) -> str:
                assert session_id == "session"
                assert message.content == "/new"
                return "existing command response"

        route = messages_module.ChannelRoute("owner", "cli", "conversation", "session")
        context = SimpleNamespace(
            inbound=messages_module.InboundMessage.new("/new", route),
            shortcut_response=None,
            final_content=None,
            principal_context=None,
        )

        assert await state_module.command(SimpleNamespace(sessions=Sessions()), context) == "shortcut"
        assert context.shortcut_response == "existing command response"
        assert context.final_content == "existing command response"

    asyncio.run(run())


def test_runtime_keeps_clear_available_for_im() -> None:
    async def run() -> None:
        state_module = importlib.import_module("Turning-Good-Agent.runtime.state")
        messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")

        class Sessions:
            async def handle_inbound_command(self, session_id: str, message: object) -> str:
                assert session_id == "session"
                assert message.content == "/clear"
                return "当前会话已清空。"

        route = messages_module.ChannelRoute("owner", "weixin", "binding", "session")
        context = SimpleNamespace(
            inbound=messages_module.InboundMessage.new("/clear", route),
            shortcut_response=None,
            final_content=None,
            principal_context=None,
        )

        assert await state_module.command(SimpleNamespace(sessions=Sessions()), context) == "shortcut"
        assert context.shortcut_response == "当前会话已清空。"
        assert context.final_content == "当前会话已清空。"

    asyncio.run(run())

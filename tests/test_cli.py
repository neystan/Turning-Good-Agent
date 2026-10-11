import sys
import asyncio
from importlib import import_module


CliChannelAdapter = import_module("Turning-Good-Agent.channels.cli").CliChannelAdapter
ToolCall = import_module("Turning-Good-Agent.llm.types").ToolCall


def test_configure_readline_for_unicode_input_binds_expected_options(monkeypatch):
    """验证 CLI 会配置中文输入删除相关的 readline 选项。"""
    cli = import_module("Turning-Good-Agent.cli")
    calls: list[str] = []

    class FakeReadline:
        """记录 readline 绑定命令。"""

        def parse_and_bind(self, command: str) -> None:
            """保存每条绑定命令。"""
            calls.append(command)

    monkeypatch.setitem(sys.modules, "readline", FakeReadline())

    cli.configure_readline_for_unicode_input()

    assert calls == [
        "set bind-tty-special-chars off",
        "set input-meta on",
        "set output-meta on",
        "set convert-meta off",
    ]


def test_resolve_cli_session_id_is_ephemeral_without_explicit_session():
    """验证默认 CLI 会话不会跨进程复用固定 default。"""
    cli = import_module("Turning-Good-Agent.cli")

    first = cli.resolve_cli_session_id(None)
    second = cli.resolve_cli_session_id(None)

    assert first != second
    assert first.startswith("cli-")
    assert second.startswith("cli-")


def test_resolve_cli_session_id_preserves_explicit_session():
    """验证显式 session ID 可以继续恢复旧会话。"""
    cli = import_module("Turning-Good-Agent.cli")

    assert cli.resolve_cli_session_id("demo") == "demo"


def test_cli_channel_adapter_prints_complete_content_without_delta(capsys):
    """验证未流式输出时直接打印完整回复。"""
    output = CliChannelAdapter()

    asyncio.run(output.on_completed("完整回复"))
    asyncio.run(output.on_completed("下一轮回复"))

    assert capsys.readouterr().out == "完整回复\n下一轮回复\n"


def test_cli_channel_adapter_avoids_duplicate_content_and_keeps_status_on_own_line(capsys):
    """验证流式回复不重复打印且状态独占一行。"""
    output = CliChannelAdapter()

    async def run() -> None:
        await output.on_delta("正在回答")
        await output.on_status("正在压缩")
        await output.on_completed("正在回答")

    asyncio.run(run())

    assert capsys.readouterr().out == "正在回答\n[系统] 正在压缩\n"


def test_cli_channel_adapter_ends_error_line_and_resets_for_next_turn(capsys):
    """验证错误结束流式行并重置下一轮状态。"""
    output = CliChannelAdapter()

    async def run() -> None:
        await output.on_delta("部分回复")
        await output.on_error("请求失败：超时")
        await output.on_completed("下一轮回复")

    asyncio.run(run())

    assert capsys.readouterr().out == "部分回复\n请求失败：超时\n下一轮回复\n"


def test_cli_channel_adapter_renders_parallel_tools_without_stale_characters(capsys):
    """验证同名并行工具独立显示且完成时清除旧状态行。"""
    output = CliChannelAdapter()

    async def run() -> None:
        await output.on_tool_started("call-1", "web_fetch")
        await output.on_tool_started("call-2", "web_fetch")
        await asyncio.sleep(0)
        await output.on_tool_finished("call-1", "web_fetch", False)
        await output.on_tool_finished("call-2", "web_fetch", False)

    asyncio.run(run())

    rendered = capsys.readouterr().out
    assert "\x1b[2K" in rendered
    assert rendered.count("[系统] 工具完成：web_fetch\n") == 2
    assert "web_fetchetch" not in rendered


def test_cli_channel_adapter_ends_delta_line_before_tool_status(capsys):
    """验证工具状态不会覆盖已输出的模型文本。"""
    output = CliChannelAdapter()

    async def run() -> None:
        await output.on_delta("准备查询")
        await output.on_tool_started("call-1", "web_search")
        await asyncio.sleep(0)
        await output.on_tool_finished("call-1", "web_search", False)
        await output.on_completed("准备查询")

    asyncio.run(run())

    assert capsys.readouterr().out.startswith("准备查询\n")


def test_cli_channel_adapter_approves_expected_inputs(monkeypatch):
    """验证 CLI 接受审批输入。"""

    async def run() -> None:
        adapter = CliChannelAdapter()
        call = ToolCall("call-1", "exec", {"command": "pwd"})

        for answer in ("y", "yes", "允许"):
            monkeypatch.setattr("builtins.input", lambda _: answer)
            assert await adapter.request_tool_approval(call) is None

    asyncio.run(run())


def test_cli_channel_adapter_rejects_empty_or_negative_approval(monkeypatch):
    """验证 CLI 空输入和否定输入会拒绝工具。"""

    async def run() -> None:
        adapter = CliChannelAdapter()
        call = ToolCall("call-1", "exec", {"command": "pwd"})

        for answer in ("", "n"):
            monkeypatch.setattr("builtins.input", lambda _: answer)
            assert await adapter.request_tool_approval(call) == "用户拒绝执行工具"

    asyncio.run(run())


def test_chat_uses_renderer_for_streaming_and_error(monkeypatch, capsys, tmp_path):
    """验证 CLI 将流式回调和错误回复交给 Renderer。"""
    cli = import_module("Turning-Good-Agent.cli")
    messages = import_module("Turning-Good-Agent.bus.messages")
    settings_module = import_module("Turning-Good-Agent.config.settings")

    class FakeChannelRouter:
        """记录 CLI 注册的输出工厂。"""

        def __init__(self):
            """初始化适配器工厂与实例记录。"""
            self.factories = {}
            self.instances = []

        def register(self, channel, factory):
            """保存 Channel 适配器工厂。"""
            self.factories[channel] = factory

        def create(self, channel):
            """创建并记录本轮适配器。"""
            output = self.factories[channel]()
            self.instances.append(output)
            return output

    class FakeRuntime:
        """模拟 Runtime 的流式错误响应。"""

        def __init__(self):
            """初始化 Channel 路由。"""
            self.channel_router = FakeChannelRouter()
            self.started = False

        async def start(self):
            """记录 CLI 已启动 Runtime 生命周期。"""
            self.started = True

        async def run_turn(self, msg):
            """模拟 Runtime 通过 Channel 适配器输出错误回复。"""
            output = self.channel_router.create(msg.channel)
            await output.on_delta("部分回复")
            await output.on_error("请求失败：模拟错误")
            return messages.OutboundMessage.error("demo", "cli", "请求失败：模拟错误")

    async def run() -> None:
        settings = settings_module.Settings(data_dir=tmp_path)
        settings.llm.streaming_enabled = True
        runtime = FakeRuntime()
        monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda **_: settings))
        monkeypatch.setattr(cli, "build_llm", lambda _: object())
        monkeypatch.setattr(cli.AgentRuntime, "create_default", staticmethod(lambda *_: runtime))
        monkeypatch.setattr("builtins.input", lambda _: "/exit")

        await cli.chat(None, None, None, None, None, None)

        assert list(runtime.channel_router.factories) == ["cli"]
        assert runtime.started is True

    asyncio.run(run())

    output = capsys.readouterr().out
    assert "部分回复\n请求失败：模拟错误\n" in output


def test_chat_uses_channel_output_for_completed_response(monkeypatch, capsys, tmp_path):
    """验证 CLI 将成功回复交给 Channel 输出。"""
    cli = import_module("Turning-Good-Agent.cli")
    messages = import_module("Turning-Good-Agent.bus.messages")
    settings_module = import_module("Turning-Good-Agent.config.settings")

    class FakeChannelRouter:
        """保存 CLI 适配器工厂。"""

        def __init__(self):
            """初始化适配器工厂表。"""
            self.factories = {}

        def register(self, channel, factory):
            """保存 Channel 适配器工厂。"""
            self.factories[channel] = factory

    class FakeRuntime:
        """模拟非流式成功响应。"""

        def __init__(self):
            """初始化 Channel 路由。"""
            self.channel_router = FakeChannelRouter()
            self.started = False

        async def start(self):
            """记录 CLI 已启动 Runtime 生命周期。"""
            self.started = True

        async def run_turn(self, msg):
            """模拟 Runtime 通过 Channel 适配器输出成功回复。"""
            output = self.channel_router.factories[msg.channel]()
            await output.on_completed("完整回复")
            return messages.OutboundMessage.completed("demo", "cli", "完整回复")

    async def run() -> None:
        settings = settings_module.Settings(data_dir=tmp_path)
        runtime = FakeRuntime()
        monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda **_: settings))
        monkeypatch.setattr(cli, "build_llm", lambda _: object())
        monkeypatch.setattr(cli.AgentRuntime, "create_default", staticmethod(lambda *_: runtime))
        monkeypatch.setattr("builtins.input", lambda _: "/exit")

        await cli.chat(None, None, None, None, None, None)

        assert list(runtime.channel_router.factories) == ["cli"]
        assert runtime.started is True

    asyncio.run(run())

    assert "完整回复\n" in capsys.readouterr().out


def test_chat_closes_runtime_on_eof(monkeypatch, tmp_path):
    """验证 EOF 退出也会关闭 Runtime。"""
    cli = import_module("Turning-Good-Agent.cli")
    settings_module = import_module("Turning-Good-Agent.config.settings")

    class Runtime:
        """记录最终关闭调用。"""

        def __init__(self) -> None:
            """初始化 Channel 路由与关闭状态。"""
            self.channel_router = type("Router", (), {"register": lambda *_: None})()
            self.closed = False
            self.started = False

        async def start(self) -> None:
            """记录 Runtime 已启动。"""
            self.started = True

        async def close(self) -> None:
            """记录 Runtime 已关闭。"""
            self.closed = True

    async def run() -> Runtime:
        runtime = Runtime()
        settings = settings_module.Settings(data_dir=tmp_path)
        monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda **_: settings))
        monkeypatch.setattr(cli, "build_llm", lambda _: object())
        monkeypatch.setattr(cli.AgentRuntime, "create_default", staticmethod(lambda *_: runtime))
        monkeypatch.setattr("builtins.input", lambda _: (_ for _ in ()).throw(EOFError()))
        await cli.chat(None, None, None, None, None, None)
        return runtime

    runtime = asyncio.run(run())

    assert runtime.started is True
    assert runtime.closed is True

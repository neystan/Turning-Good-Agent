import asyncio
from importlib import import_module
from types import SimpleNamespace


ChannelRouter = import_module("Turning-Good-Agent.channels.base").ChannelRouter
ChannelStatusHook = import_module("Turning-Good-Agent.hooks.channel_status").ChannelStatusHook
ToolCall = import_module("Turning-Good-Agent.llm.types").ToolCall
LLMResponse = import_module("Turning-Good-Agent.llm.types").LLMResponse
LLMUsage = import_module("Turning-Good-Agent.llm.types").LLMUsage
Settings = import_module("Turning-Good-Agent.config.settings").Settings
InboundMessage = import_module("Turning-Good-Agent.bus.messages").InboundMessage
AgentRuntime = import_module("Turning-Good-Agent.runtime.runtime").AgentRuntime


class RecordingOutput:
    """记录 Channel 收到的输出。"""

    def __init__(self):
        """初始化事件记录。"""
        self.events = []

    async def on_delta(self, text: str) -> None:
        """记录流式文本。"""
        self.events.append(("delta", text))

    async def on_status(self, text: str) -> None:
        """记录状态文本。"""
        self.events.append(("status", text))

    async def on_tool_started(self, tool_call_id: str, tool_name: str) -> None:
        """记录工具开始事件。"""
        self.events.append(("tool_started", tool_call_id, tool_name))

    async def on_tool_finished(self, tool_call_id: str, tool_name: str, failed: bool) -> None:
        """记录工具结束事件。"""
        self.events.append(("tool_finished", tool_call_id, tool_name, failed))

    async def on_completed(self, content: str) -> None:
        """记录成功回复。"""
        self.events.append(("completed", content))

    async def on_error(self, content: str) -> None:
        """记录错误回复。"""
        self.events.append(("error", content))

    async def request_tool_approval(self, call):
        """默认批准测试工具。"""
        del call
        return None


def test_channel_router_uses_registered_channel_and_silences_unknown_channel():
    """验证路由按 Channel 创建输出，未知 Channel 静默。"""

    async def run() -> list[tuple[str, str]]:
        router = ChannelRouter()
        router.register("web", RecordingOutput)
        output = router.create("web")
        await output.on_delta("回答片段")
        await output.on_completed("完整回答")
        silent = router.create("feishu")
        await silent.on_status("正在调用工具：web_search")
        return output.events

    assert asyncio.run(run()) == [("delta", "回答片段"), ("completed", "完整回答")]


def test_channel_status_hook_sends_tool_and_compaction_status_to_current_output():
    """验证通用状态 Hook 不依赖具体 Channel。"""

    async def run() -> list[tuple[str, str]]:
        output = RecordingOutput()
        hook = ChannelStatusHook()
        call = ToolCall("call-1", "web_search", {"query": "天气"})
        await hook.on_tool_started(call, output)
        record = await hook.after_tool_call(call, {"tool_name": "web_search", "error": None})
        assert record["tool_name"] == "web_search"
        ctx = SimpleNamespace(
            channel_adapter=output,
            compact_stats={
                "compacted_message_count": 4,
                "compacted_token_count": 120,
                "raw_window_token_count": 20,
            },
        )
        await hook.before_compact(ctx)
        await hook.after_compact(ctx)
        return output.events

    assert asyncio.run(run()) == [
        ("tool_started", "call-1", "web_search"),
        ("tool_finished", "call-1", "web_search", False),
        ("status", "正在压缩会话上下文..."),
        ("status", "压缩完成：已压缩 4 条消息，压缩 120 tokens，保留最近 20 tokens 原文。"),
    ]


def test_runtime_routes_tool_status_and_completion_to_registered_channel_output(tmp_path):
    """验证 Runtime 按入站 Channel 输出工具状态与最终回复。"""

    class ToolLLM:
        """模拟一次工具调用后的最终回复。"""

        async def complete(self, messages, tools):
            """返回工具调用或最终回复。"""
            del tools
            if messages[-1]["role"] == "tool":
                return LLMResponse("当前时间已查询。", usage=LLMUsage(20, 4, 24))
            return LLMResponse("", [ToolCall("call-1", "now", {})], LLMUsage(10, 2, 12))

    async def run() -> list[tuple[str, str]]:
        outputs = []
        runtime = AgentRuntime.create_default(Settings(data_dir=tmp_path), ToolLLM())
        runtime.channel_router.register("web", lambda: outputs.append(RecordingOutput()) or outputs[-1])
        outbound = await runtime.run_turn(InboundMessage.new("现在几点", "demo", "user-1", "web"))
        assert outbound.content == "当前时间已查询。"
        return outputs[0].events

    assert asyncio.run(run()) == [
        ("tool_started", "call-1", "now"),
        ("tool_finished", "call-1", "now", False),
        ("completed", "当前时间已查询。"),
    ]

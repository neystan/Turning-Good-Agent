import asyncio
import json
from dataclasses import dataclass
from importlib import import_module

Settings = import_module("Turning-Good-Agent.config.settings").Settings
ToolResultTruncationHook = import_module(
    "Turning-Good-Agent.hooks.tool_result_truncation"
).ToolResultTruncationHook
count_content_tokens = import_module("Turning-Good-Agent.sessions.token_counter").count_content_tokens
InboundMessage = import_module("Turning-Good-Agent.bus.messages").InboundMessage
AgentRuntime = import_module("Turning-Good-Agent.runtime.runtime").AgentRuntime
LLMResponse = import_module("Turning-Good-Agent.llm.types").LLMResponse
LLMUsage = import_module("Turning-Good-Agent.llm.types").LLMUsage
ToolCall = import_module("Turning-Good-Agent.llm.types").ToolCall
ToolResult = import_module("Turning-Good-Agent.tools.base").ToolResult


def _call(tool_name, args=None):
    """创建指定工具的标准化调用。"""
    return ToolCall("call-1", tool_name, args or {})


def test_tool_result_hook_truncates_head_tail_with_token_limit():
    """验证文件与网页正文按 token 上限保留头尾。"""

    async def run() -> None:
        hook = ToolResultTruncationHook(80)
        content = "开头标记\n" + "中间内容 " * 200 + "\n结尾标记"

        record = await hook.after_tool_call(_call("read_file"), {"content": content, "error": None})

        assert count_content_tokens(record["content"]) <= 80
        assert "开头标记" in record["content"]
        assert "结尾标记" in record["content"]
        assert "已省略" in record["content"]
        assert "更精确" in record["content"]

    asyncio.run(run())


def test_tool_result_hook_preserves_exec_tail():
    """验证命令结果优先保留 stderr 和退出码所在末尾。"""

    async def run() -> None:
        hook = ToolResultTruncationHook(70)
        content = ("普通输出\n" * 200) + "STDERR:\n关键错误\nExit code: 2"

        record = await hook.after_tool_call(_call("exec"), {"content": content, "error": "failed"})

        assert count_content_tokens(record["content"]) <= 70
        assert "关键错误" in record["content"]
        assert "Exit code: 2" in record["content"]
        assert "已省略" in record["content"]

    asyncio.run(run())


def test_tool_result_hook_preserves_web_fetch_source_url():
    """验证网页正文截断结果保留原始来源 URL。"""

    async def run() -> None:
        hook = ToolResultTruncationHook(80)
        call = _call("web_fetch", {"url": "https://example.com/article"})
        content = "网页开头\n" + "正文 " * 200 + "\n网页结尾"

        record = await hook.after_tool_call(call, {"content": content, "error": None})

        assert count_content_tokens(record["content"]) <= 80
        assert "https://example.com/article" in record["content"]
        assert "网页结尾" in record["content"]

    asyncio.run(run())


def test_tool_result_hook_reports_omitted_list_entries():
    """验证列表类工具保留前部并说明省略条数。"""

    async def run() -> None:
        hook = ToolResultTruncationHook(60)
        content = "\n".join(f"file-{index}.txt" for index in range(100))

        record = await hook.after_tool_call(_call("list_dir"), {"content": content, "error": None})

        assert count_content_tokens(record["content"]) <= 60
        assert "file-0.txt" in record["content"]
        assert "条结果" in record["content"]
        assert "更精确" in record["content"]

    asyncio.run(run())


def test_tool_result_token_limit_loads_from_central_settings(tmp_path):
    """验证模型侧工具结果上限从集中配置读取。"""
    config_path = tmp_path / "settings.local.json"
    config_path.write_text(json.dumps({"runtime": {"max_tool_result_tokens": 1234}}), encoding="utf-8")

    settings = Settings.load(local_config_path=config_path)

    assert settings.runtime.max_tool_result_tokens == 1234
    assert Settings().runtime.max_tool_result_tokens == 8_000


def test_runtime_uses_same_truncated_record_for_llm_and_jsonl(tmp_path):
    """验证截断结果同时用于模型上下文和工具记录落盘。"""

    class LargeTool:
        """返回超长文本的测试工具。"""

        name = "large_result"
        description = "返回超长结果。"
        input_schema = {"type": "object", "properties": {}}

        async def run(self, args):
            """返回超长工具结果。"""
            del args
            return ToolResult("large content " * 500)

    @dataclass
    class RecordingLLM:
        """记录第二次模型调用的 working messages。"""

        calls: list = None

        def __post_init__(self):
            """初始化模型调用记录。"""
            self.calls = []

        async def complete(self, messages, tools):
            """先请求工具，再返回最终文本。"""
            del tools
            self.calls.append(list(messages))
            if len(self.calls) == 1:
                return LLMResponse("", [ToolCall("call-1", "large_result", {})], LLMUsage(10, 2, 12))
            return LLMResponse("完成", usage=LLMUsage(20, 4, 24))

    async def run() -> None:
        llm = RecordingLLM()
        settings = Settings(data_dir=tmp_path)
        settings.runtime.max_tool_result_tokens = 80
        runtime = AgentRuntime.create_default(settings, llm)
        runtime.agent_loop.tools.register(LargeTool())

        await runtime.run_turn(InboundMessage.new("run", "demo", "user-1", "cli"))

        model_content = next(item["content"] for item in llm.calls[1] if item["role"] == "tool")
        session_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
        row = json.loads((session_dir / "tool_calls.jsonl").read_text(encoding="utf-8").splitlines()[0])
        assert row["content"] == model_content
        assert count_content_tokens(row["content"]) <= 80
        assert "已省略" in row["content"]

    asyncio.run(run())

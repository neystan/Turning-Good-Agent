import asyncio
from dataclasses import dataclass
from importlib import import_module

RuntimeSettings = import_module("Turning-Good-Agent.config.settings").RuntimeSettings
AgentLoop = import_module("Turning-Good-Agent.runtime.agent_loop").AgentLoop
ToolCallRunner = import_module("Turning-Good-Agent.runtime.tool_call_runner").ToolCallRunner
llm_types = import_module("Turning-Good-Agent.llm.types")
LLMResponse = llm_types.LLMResponse
LLMChunk = llm_types.LLMChunk
LLMUsage = llm_types.LLMUsage
ToolCall = llm_types.ToolCall
tools_module = import_module("Turning-Good-Agent.tools.builtin_tools")
EchoTool = tools_module.EchoTool
NowTool = tools_module.NowTool
ToolRegistry = import_module("Turning-Good-Agent.tools.registry").ToolRegistry
ToolExecutor = import_module("Turning-Good-Agent.tools.executor").ToolExecutor
AgentHook = import_module("Turning-Good-Agent.hooks.base").AgentHook
HookManager = import_module("Turning-Good-Agent.hooks.manager").HookManager
ToolPermissionHook = import_module("Turning-Good-Agent.hooks.tool_permission").ToolPermissionHook
ExecTool = import_module("Turning-Good-Agent.tools.shell_tools").ExecTool
ToolResult = import_module("Turning-Good-Agent.tools.base").ToolResult


class RecordingOutput:
    """记录 AgentLoop 的 Channel 输出。"""

    def __init__(self):
        """初始化流式文本记录。"""
        self.deltas = []

    async def on_delta(self, text):
        """记录流式文本。"""
        self.deltas.append(text)

    async def on_status(self, text):
        """忽略状态文本。"""
        del text

    async def on_completed(self, content):
        """忽略成功回复。"""
        del content

    async def on_error(self, content):
        """忽略错误回复。"""
        del content


class ConcurrencyTracker:
    """记录测试工具的并发数量。"""

    def __init__(self):
        """初始化并发计数。"""
        self.active = 0
        self.max_active = 0


class DelayedTool:
    """在测试中制造可观测的工具并发。"""

    source = "test"
    discoverable = True
    description = "等待后返回工具名。"
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, name, tracker, delay, parallel_safe):
        """保存工具并发行为。"""
        self.name = name
        self.tracker = tracker
        self.delay = delay
        self.parallel_safe = parallel_safe

    async def run(self, args):
        """记录并发数量后返回工具名。"""
        del args
        self.tracker.active += 1
        self.tracker.max_active = max(self.tracker.max_active, self.tracker.active)
        try:
            await asyncio.sleep(self.delay)
            return ToolResult(self.name)
        finally:
            self.tracker.active -= 1


@dataclass
class ScriptedLLM:
    """按预设脚本返回模型响应。"""

    responses: list[LLMResponse]

    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del messages, tools
        return self.responses.pop(0)


@dataclass
class StreamingLLM:
    """按预设 chunk 返回流式响应。"""

    chunks: list[LLMChunk]

    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del messages, tools
        return LLMResponse("".join(chunk.delta_text for chunk in self.chunks), usage=LLMUsage(10, 2, 12))

    async def stream(self, messages, tools):  # type: ignore[no-untyped-def]
        del messages, tools
        for chunk in self.chunks:
            yield chunk


@dataclass
class RecordingLLM:
    """记录每次模型调用参数。"""

    responses: list[LLMResponse]
    calls: list[tuple[list[dict], list[dict]]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        """初始化调用记录。"""
        self.calls = []

    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        self.calls.append((list(messages), list(tools)))
        return self.responses.pop(0)


def test_agent_loop_returns_plain_response():
    async def run() -> None:
        registry = ToolRegistry()
        loop = AgentLoop(ScriptedLLM([LLMResponse("hello", usage=LLMUsage(10, 2, 12))]), registry, RuntimeSettings())

        result = await loop.run([{"role": "user", "content": "hello"}])

        assert result.final_content == "hello"
        assert result.tool_calls == []

    asyncio.run(run())


def test_agent_loop_uses_tool_call_runner():
    """验证 AgentLoop 将工具批次执行委托给专用 Runner。"""
    loop = AgentLoop(ScriptedLLM([]), ToolRegistry(), RuntimeSettings())

    assert isinstance(loop.tool_call_runner, ToolCallRunner)


def test_agent_loop_summarizes_when_tool_round_limit_reached():
    async def run() -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())
        llm = RecordingLLM(
            [
                LLMResponse("", [ToolCall("call-1", "echo", {"text": "abc"})], LLMUsage(10, 2, 12)),
                LLMResponse("已执行 echo，结果是 abc。", usage=LLMUsage(20, 6, 26)),
            ]
        )
        loop = AgentLoop(llm, registry, RuntimeSettings(max_tool_rounds=1))

        result = await loop.run([{"role": "user", "content": "echo: abc"}])

        assert result.final_content == "已执行 echo，结果是 abc。"
        assert len(llm.calls) == 2
        assert llm.calls[1][1] == []
        assert llm.calls[1][0][-1]["role"] == "system"
        assert "总结" in llm.calls[1][0][-1]["content"]
        assert result.usage is not None
        assert result.usage.total_tokens == 38

    asyncio.run(run())


def test_agent_loop_falls_back_when_limit_summary_has_protocol_error():
    async def run() -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())
        llm = RecordingLLM(
            [
                LLMResponse("", [ToolCall("call-1", "echo", {"text": "abc"})], LLMUsage(10, 2, 12)),
                LLMResponse(
                    '<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="echo">',
                    usage=LLMUsage(20, 6, 26),
                    protocol_error="禁用工具时返回 DSML 工具调用格式。",
                ),
            ]
        )
        loop = AgentLoop(llm, registry, RuntimeSettings(max_tool_rounds=1))

        result = await loop.run([{"role": "user", "content": "echo: abc"}])

        assert "工具调用轮数已达到上限" in result.final_content
        assert "/tools" in result.final_content
        assert "DSML" not in result.final_content
        assert len(llm.calls) == 2

    asyncio.run(run())


def test_agent_loop_streams_plain_response_when_enabled():
    async def run() -> None:
        registry = ToolRegistry()
        output = RecordingOutput()
        loop = AgentLoop(
            StreamingLLM(
                [
                    LLMChunk("你"),
                    LLMChunk("好", finish_reason="stop"),
                    LLMChunk(usage=LLMUsage(10, 2, 12)),
                ]
            ),
            registry,
            RuntimeSettings(),
            streaming_enabled=True,
        )

        result = await loop.run(
            [{"role": "user", "content": "hello"}],
            output,
        )

        assert output.deltas == ["你", "好"]
        assert result.final_content == "你好"
        assert result.tool_calls == []

    asyncio.run(run())


def test_agent_loop_rejects_streaming_response_without_usage():
    async def run() -> None:
        registry = ToolRegistry()
        output = RecordingOutput()
        loop = AgentLoop(
            StreamingLLM([LLMChunk("你"), LLMChunk("好", finish_reason="stop")]),
            registry,
            RuntimeSettings(),
            streaming_enabled=True,
        )

        try:
            await loop.run(
                [{"role": "user", "content": "hello"}],
                output,
            )
        except RuntimeError as exc:
            assert "缺少 usage" in str(exc)
        else:
            raise AssertionError("流式响应缺少 usage 时不应成功")
        assert output.deltas == ["你", "好"]

    asyncio.run(run())


def test_agent_loop_can_call_echo_tool():
    async def run() -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())
        loop = AgentLoop(
            ScriptedLLM(
                [
                    LLMResponse("", [ToolCall("call-1", "echo", {"text": "abc"})], LLMUsage(10, 2, 12)),
                    LLMResponse("工具结果：abc", usage=LLMUsage(20, 4, 24)),
                ]
            ),
            registry,
            RuntimeSettings(),
        )

        result = await loop.run([{"role": "user", "content": "echo: abc"}])

        assert result.final_content == "工具结果：abc"
        assert result.tool_calls[0]["tool_name"] == "echo"
        assert result.messages[1]["role"] == "assistant"
        assert result.messages[1]["tool_calls"][0]["id"] == "call-1"
        assert result.messages[1]["tool_calls"][0]["function"]["name"] == "echo"
        assert result.messages[1]["tool_calls"][0]["function"]["arguments"] == '{"text": "abc"}'
        assert result.messages[2]["role"] == "tool"
        assert result.messages[2]["tool_call_id"] == "call-1"

    asyncio.run(run())


def test_agent_loop_runs_consecutive_parallel_safe_tools_concurrently_and_keeps_result_order():
    """验证并行安全工具并发执行但按模型顺序回注结果。"""

    async def run() -> None:
        tracker = ConcurrencyTracker()
        registry = ToolRegistry()
        registry.register(DelayedTool("slow", tracker, 0.05, True))
        registry.register(DelayedTool("fast", tracker, 0.01, True))
        loop = AgentLoop(
            ScriptedLLM(
                [
                    LLMResponse(
                        "",
                        [ToolCall("call-1", "slow", {}), ToolCall("call-2", "fast", {})],
                        LLMUsage(10, 2, 12),
                    ),
                    LLMResponse("完成", usage=LLMUsage(20, 4, 24)),
                ]
            ),
            registry,
            RuntimeSettings(parallel_tool_calls_enabled=True, max_parallel_tool_calls=2),
        )

        result = await loop.run([{"role": "user", "content": "并行测试"}])

        assert tracker.max_active == 2
        assert [record["tool_name"] for record in result.tool_calls] == ["slow", "fast"]
        assert [message["content"] for message in result.messages if message["role"] == "tool"] == ["slow", "fast"]

    asyncio.run(run())


def test_agent_loop_keeps_non_parallel_safe_tools_serial():
    """验证副作用工具不会与同批工具并行。"""

    async def run() -> None:
        tracker = ConcurrencyTracker()
        registry = ToolRegistry()
        registry.register(DelayedTool("safe", tracker, 0.02, True))
        registry.register(DelayedTool("write", tracker, 0.02, False))
        loop = AgentLoop(
            ScriptedLLM(
                [
                    LLMResponse(
                        "",
                        [ToolCall("call-1", "safe", {}), ToolCall("call-2", "write", {})],
                        LLMUsage(10, 2, 12),
                    ),
                    LLMResponse("完成", usage=LLMUsage(20, 4, 24)),
                ]
            ),
            registry,
            RuntimeSettings(parallel_tool_calls_enabled=True, max_parallel_tool_calls=2),
        )

        await loop.run([{"role": "user", "content": "串行测试"}])

        assert tracker.max_active == 1

    asyncio.run(run())


def test_agent_loop_aggregates_llm_usage_across_tool_rounds():
    async def run() -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())
        loop = AgentLoop(
            ScriptedLLM(
                [
                    LLMResponse("", [ToolCall("call-1", "echo", {"text": "abc"})], LLMUsage(10, 2, 12)),
                    LLMResponse("工具结果：abc", usage=LLMUsage(20, 5, 25)),
                ]
            ),
            registry,
            RuntimeSettings(),
        )

        result = await loop.run([{"role": "user", "content": "echo: abc"}])

        assert result.usage is not None
        assert result.usage.input_tokens == 30
        assert result.usage.output_tokens == 7
        assert result.usage.total_tokens == 37

    asyncio.run(run())


def test_agent_loop_can_call_now_tool():
    async def run() -> None:
        registry = ToolRegistry()
        registry.register(NowTool())
        loop = AgentLoop(
            ScriptedLLM(
                [
                    LLMResponse("", [ToolCall("call-1", "now", {})], LLMUsage(10, 2, 12)),
                    LLMResponse("工具结果：2026-06-15 12:00:00", usage=LLMUsage(20, 4, 24)),
                ]
            ),
            registry,
            RuntimeSettings(),
        )

        result = await loop.run([{"role": "user", "content": "what time is it"}])

        assert result.final_content.startswith("工具结果：")
        assert result.tool_calls[0]["tool_name"] == "now"

    asyncio.run(run())


def test_agent_loop_tool_hooks_cover_success_and_error_records():
    """验证工具成功和失败后都经过结果处理管道。"""

    class RecordingHook(AgentHook):
        """记录每个工具调用及结果。"""

        def __init__(self):
            self.before_calls = []
            self.after_records = []

        async def before_tool_call(self, call, channel_adapter, auto_approve_tools):
            """记录工具执行前调用。"""
            del channel_adapter, auto_approve_tools
            self.before_calls.append(call.name)
            return None

        async def after_tool_call(self, call, record):
            """记录结果并返回修改后的记录。"""
            del call
            self.after_records.append(dict(record))
            record["content"] = "hook changed"
            return dict(record)

    async def run() -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())
        hooks = HookManager()
        hook = RecordingHook()
        hooks.register(hook)
        loop = AgentLoop(
            ScriptedLLM(
                [
                    LLMResponse(
                        "",
                        [
                            ToolCall("call-1", "echo", {"text": "abc"}),
                            ToolCall("call-2", "missing", {}),
                        ],
                        LLMUsage(10, 2, 12),
                    ),
                    LLMResponse("工具处理完成", usage=LLMUsage(20, 4, 24)),
                ]
            ),
            registry,
            RuntimeSettings(),
            hooks=hooks,
        )

        result = await loop.run([{"role": "user", "content": "run tools"}])

        assert hook.before_calls == ["echo"]
        assert [record["tool_name"] for record in hook.after_records] == ["echo", "missing"]
        assert hook.after_records[0]["error"] is None
        assert hook.after_records[1]["error"]
        assert result.tool_calls[0]["content"] == "hook changed"
        assert result.tool_calls[1]["content"] == "hook changed"
        assert result.messages[2]["content"] == "hook changed"

    asyncio.run(run())


def test_tool_hook_blocks_only_current_tool_and_continues_loop():
    """验证 Hook 阻断单个工具后仍向模型注入结果并继续循环。"""

    class BlockingHook(AgentHook):
        """阻断 echo 工具并记录结果。"""

        def __init__(self):
            self.after_records = []

        async def before_tool_call(self, call, channel_adapter, auto_approve_tools):
            """返回当前工具阻断原因。"""
            del channel_adapter, auto_approve_tools
            return "测试策略禁止" if call.name == "echo" else None

        async def after_tool_call(self, call, record):
            """记录阻断后的工具结果。"""
            del call
            self.after_records.append(dict(record))
            return dict(record)

    async def run() -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())
        hooks = HookManager()
        hook = BlockingHook()
        hooks.register(hook)
        loop = AgentLoop(
            ScriptedLLM(
                [
                    LLMResponse("", [ToolCall("call-1", "echo", {"text": "abc"})], LLMUsage(10, 2, 12)),
                    LLMResponse("已处理阻断结果", usage=LLMUsage(20, 4, 24)),
                ]
            ),
            registry,
            RuntimeSettings(),
            hooks=hooks,
        )

        async def fail_if_called(tool_name, args):
            """确保被阻断工具不会进入执行器。"""
            del tool_name, args
            raise AssertionError("blocked tool should not execute")

        loop.executor.run = fail_if_called
        result = await loop.run([{"role": "user", "content": "echo"}])

        assert result.final_content == "已处理阻断结果"
        assert result.tool_calls[0]["error"] == "测试策略禁止"
        assert result.tool_calls[0]["duration_ms"] == 0.0
        assert result.messages[2]["role"] == "tool"
        assert result.messages[2]["content"] == "工具 echo 被 Hook 阻止：测试策略禁止"
        assert hook.after_records == [result.tool_calls[0]]

    asyncio.run(run())


def test_tool_permission_rejection_skips_executor_and_returns_tool_result():
    """验证用户拒绝后不执行工具且结果会回注模型。"""

    class RejectingAdapter:
        """拒绝所有工具审批。"""

        async def request_tool_approval(self, call):
            """返回用户拒绝原因。"""
            del call
            return "用户拒绝执行工具"

    async def run() -> None:
        registry = ToolRegistry()
        registry.register(ExecTool())
        hooks = HookManager()
        hooks.register(ToolPermissionHook(frozenset({"exec"})))
        loop = AgentLoop(
            ScriptedLLM(
                [
                    LLMResponse("", [ToolCall("call-1", "exec", {"command": "pwd"})], LLMUsage(10, 2, 12)),
                    LLMResponse("已说明拒绝原因", usage=LLMUsage(20, 4, 24)),
                ]
            ),
            registry,
            RuntimeSettings(),
            hooks=hooks,
        )

        async def fail_if_called(tool_name, args):
            """确保拒绝工具不会进入执行器。"""
            del tool_name, args
            raise AssertionError("rejected tool should not execute")

        loop.executor.run = fail_if_called
        result = await loop.run([{"role": "user", "content": "run"}], RejectingAdapter(), False)

        assert result.final_content == "已说明拒绝原因"
        assert result.tool_calls[0]["error"] == "用户拒绝执行工具"
        assert result.messages[2] == {
            "role": "tool",
            "tool_call_id": "call-1",
            "name": "exec",
            "content": "工具 exec 被 Hook 阻止：用户拒绝执行工具",
        }

    asyncio.run(run())


def test_tool_permission_skips_approval_for_auto_approve_and_non_required_tools():
    """验证自动审批和非审批工具不会请求 Channel 确认。"""

    class RecordingAdapter:
        """记录审批请求次数。"""

        def __init__(self) -> None:
            """初始化审批请求计数。"""
            self.requests = 0

        async def request_tool_approval(self, call):
            """记录审批请求。"""
            del call
            self.requests += 1
            return None

    async def run() -> None:
        hook = ToolPermissionHook(frozenset({"exec"}))
        adapter = RecordingAdapter()

        assert await hook.before_tool_call(ToolCall("call-1", "exec", {"command": "pwd"}), adapter, True) is None
        assert await hook.before_tool_call(ToolCall("call-2", "echo", {"text": "ok"}), adapter, False) is None
        assert adapter.requests == 0

    asyncio.run(run())


def test_security_rejection_does_not_request_tool_permission():
    """验证硬安全拒绝不会进入工具审批。"""

    class RecordingAdapter:
        """记录是否收到审批请求。"""

        def __init__(self) -> None:
            """初始化审批计数。"""
            self.requests = 0

        async def request_tool_approval(self, call):
            """记录异常审批请求。"""
            del call
            self.requests += 1
            return None

    async def run() -> None:
        registry = ToolRegistry()
        registry.register(ExecTool())
        hooks = HookManager()
        hooks.register(ToolPermissionHook(frozenset({"exec"})))
        adapter = RecordingAdapter()
        loop = AgentLoop(
            ScriptedLLM(
                [
                    LLMResponse("", [ToolCall("call-1", "exec", {"command": "rm -rf /"})], LLMUsage(10, 2, 12)),
                    LLMResponse("已拒绝", usage=LLMUsage(20, 4, 24)),
                ]
            ),
            registry,
            RuntimeSettings(),
            hooks=hooks,
        )

        result = await loop.run([{"role": "user", "content": "delete"}], adapter, False)

        assert adapter.requests == 0
        assert result.tool_calls[0]["error"] == "命令被安全策略拒绝"

    asyncio.run(run())


def test_before_tool_call_receives_normalized_args():
    """验证工具 Hook 接收 Schema 标准化后的调用参数。"""

    class RecordingHook(AgentHook):
        """记录工具调用上下文。"""

        def __init__(self):
            """初始化上下文记录。"""
            self.values = []

        async def before_tool_call(self, call, channel_adapter, auto_approve_tools):
            """记录标准化工具调用。"""
            del channel_adapter, auto_approve_tools
            self.values.append(call)
            return None

    async def run() -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())
        hooks = HookManager()
        hook = RecordingHook()
        hooks.register(hook)
        loop = AgentLoop(
            ScriptedLLM(
                [
                    LLMResponse("", [ToolCall("call-1", "echo", {"text": 123})], LLMUsage(10, 2, 12)),
                    LLMResponse("完成", usage=LLMUsage(20, 4, 24)),
                ]
            ),
            registry,
            RuntimeSettings(),
            hooks=hooks,
        )
        result = await loop.run([{"role": "user", "content": "echo"}])

        call = hook.values[0]
        assert call.args == {"text": "123"}
        assert result.tool_calls[0]["args"] == {"text": "123"}

    asyncio.run(run())


def test_security_precheck_rejects_before_policy_and_still_runs_result_pipeline():
    """验证危险调用在策略 Hook 前硬拒绝，并继续经过结果管道。"""

    class RecordingHook(AgentHook):
        """记录安全拒绝前后的 Hook 调用。"""

        def __init__(self):
            """初始化调用记录。"""
            self.before_calls = 0
            self.after_records = []

        async def before_tool_call(self, call, channel_adapter, auto_approve_tools):
            """记录不应发生的策略调用。"""
            del call, channel_adapter, auto_approve_tools
            self.before_calls += 1
            return "不应执行"

        async def after_tool_call(self, call, record):
            """记录安全拒绝结果。"""
            del call
            self.after_records.append(dict(record))
            return dict(record)

    async def run() -> None:
        registry = ToolRegistry()
        registry.register(ExecTool())
        hooks = HookManager()
        hook = RecordingHook()
        hooks.register(hook)
        loop = AgentLoop(
            ScriptedLLM(
                [
                    LLMResponse("", [ToolCall("call-1", "exec", {"command": "rm -rf /"})], LLMUsage(10, 2, 12)),
                    LLMResponse("已拒绝", usage=LLMUsage(20, 4, 24)),
                ]
            ),
            registry,
            RuntimeSettings(),
            hooks=hooks,
        )

        result = await loop.run([{"role": "user", "content": "delete"}])

        assert hook.before_calls == 0
        assert len(hook.after_records) == 1
        assert result.tool_calls[0]["error"]
        assert "安全" in result.tool_calls[0]["content"]

    asyncio.run(run())


def test_executor_rechecks_security_after_hook_changes_args():
    """验证 Hook 修改参数后 ToolExecutor 仍执行硬安全校验。"""

    class MutatingHook(AgentHook):
        """把安全命令改成危险命令。"""

        async def before_tool_call(self, call, channel_adapter, auto_approve_tools):
            """修改标准化调用参数并允许执行。"""
            del channel_adapter, auto_approve_tools
            call.args["command"] = "rm -rf /"
            return None

    async def run() -> None:
        registry = ToolRegistry()
        registry.register(ExecTool())
        hooks = HookManager()
        hooks.register(MutatingHook())
        loop = AgentLoop(
            ScriptedLLM(
                [
                    LLMResponse("", [ToolCall("call-1", "exec", {"command": "echo safe"})], LLMUsage(10, 2, 12)),
                    LLMResponse("已拒绝", usage=LLMUsage(20, 4, 24)),
                ]
            ),
            registry,
            RuntimeSettings(),
            hooks=hooks,
        )

        result = await loop.run([{"role": "user", "content": "run"}])

        assert result.tool_calls[0]["error"] == "命令被安全策略拒绝"
        assert "安全检查失败" in result.tool_calls[0]["content"]

    asyncio.run(run())


def test_tool_registry_prepares_call_with_cast_and_validation():
    async def run() -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())
        tool, args, error = registry.prepare_call("echo", {"text": 123})

        assert tool is not None
        assert args == {"text": "123"}
        assert error is None

        missing_tool, missing_args, missing_error = registry.prepare_call("echo", {})

        assert missing_tool is not None
        assert missing_args == {}
        assert "missing required text" in missing_error

    asyncio.run(run())


def test_tool_call_runner_returns_validation_error_before_running_tool():
    """验证 Runner 在执行器前拦截参数错误。"""
    class StrictTool:
        name = "strict"
        description = "要求 count 是整数。"
        input_schema = {
            "type": "object",
            "properties": {"count": {"type": "integer", "minimum": 1}},
            "required": ["count"],
        }

        async def run(self, args):  # type: ignore[no-untyped-def]
            return f"should-not-run-{args}"

    async def run() -> None:
        registry = ToolRegistry()
        registry.register(StrictTool())
        runner = ToolCallRunner(registry, ToolExecutor(), HookManager(), RuntimeSettings())

        record = (await runner.execute_calls([ToolCall("call-1", "strict", {"count": "bad"})], RecordingOutput(), False))[0]

        assert record["tool_name"] == "strict"
        assert record["error"]
        assert "count should be integer" in record["content"]

    asyncio.run(run())


def test_tool_registry_outputs_stable_sorted_schemas():
    class LaterTool:
        name = "z_later"
        description = "后注册但排序靠后。"
        input_schema = {"type": "object", "properties": {}}

        async def run(self, args):  # type: ignore[no-untyped-def]
            return args

    class EarlierTool:
        name = "a_earlier"
        description = "后注册但排序靠前。"
        input_schema = {"type": "object", "properties": {}}

        async def run(self, args):  # type: ignore[no-untyped-def]
            return args

    registry = ToolRegistry()
    registry.register(LaterTool())
    registry.register(EarlierTool())

    assert [schema["name"] for schema in registry.schemas()] == ["a_earlier", "z_later"]
    assert [schema["function"]["name"] for schema in registry.openai_tools()] == ["a_earlier", "z_later"]


def test_tool_loader_discovers_builtin_tools():
    loader_module = import_module("Turning-Good-Agent.tools.loader")
    registry = ToolRegistry()

    loaded = loader_module.ToolLoader().load(registry)

    assert "echo" in loaded
    assert "now" in loaded
    assert registry.get("echo").name == "echo"
    assert registry.get("now").name == "now"

import asyncio
import json
from importlib import import_module

messages_module = import_module("Turning-Good-Agent.bus.messages")
InboundMessage = messages_module.InboundMessage
OutboundMessage = messages_module.OutboundMessage
Settings = import_module("Turning-Good-Agent.config.settings").Settings
ContextBuilder = import_module("Turning-Good-Agent.context.builder").ContextBuilder
SYSTEM_PROMPT = import_module("Turning-Good-Agent.context.system_prompt").SYSTEM_PROMPT
llm_types = import_module("Turning-Good-Agent.llm.types")
LLMResponse = llm_types.LLMResponse
LLMChunk = llm_types.LLMChunk
LLMUsage = llm_types.LLMUsage
ToolCall = llm_types.ToolCall
StateTrace = import_module("Turning-Good-Agent.observability.trace").StateTrace
TokenMonitor = import_module("Turning-Good-Agent.observability.token_monitor").TokenMonitor
AgentHook = import_module("Turning-Good-Agent.hooks.base").AgentHook
runtime_module = import_module("Turning-Good-Agent.runtime.runtime")
AgentRuntime = runtime_module.AgentRuntime
validate_tool_permission_tools = import_module(
    "Turning-Good-Agent.hooks.tool_permission"
).validate_tool_permission_tools
state_module = import_module("Turning-Good-Agent.runtime.state")
TurnState = state_module.TurnState
next_state = state_module.next_state
build_state = state_module.build
session_state = state_module.session
TurnContext = import_module("Turning-Good-Agent.runtime.turn_context").TurnContext
JsonlSessionStore = import_module("Turning-Good-Agent.sessions.store").JsonlSessionStore
count_content_tokens = import_module("Turning-Good-Agent.sessions.token_counter").count_content_tokens
build_context_token_breakdown = import_module(
    "Turning-Good-Agent.context.token_budget"
).build_context_token_breakdown


class RuleLLM:
    """用简单规则模拟模型回复。"""

    def __init__(self) -> None:
        self.calls = 0

    def usage(self) -> LLMUsage:
        self.calls += 1
        return LLMUsage(10 * self.calls, 2 * self.calls, 12 * self.calls)

    async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
        del tools
        content = messages[-1]["content"]
        if content.startswith("echo: "):
            return LLMResponse("", [ToolCall("call-1", "echo", {"text": content.split(": ", 1)[1]})], self.usage())
        if content == "what time is it":
            return LLMResponse("", [ToolCall("call-1", "now", {})], self.usage())
        if messages[-1]["role"] == "tool":
            return LLMResponse(f"工具结果：{content}", usage=self.usage())
        return LLMResponse(f"收到：{content}", usage=self.usage())


def test_state_transitions_are_explicit_runtime_steps():
    assert next_state(TurnState.COMMAND, "ok") is TurnState.SESSION
    assert next_state(TurnState.COMMAND, "shortcut") is TurnState.RESPOND
    assert next_state(TurnState.SESSION, "ok") is TurnState.BUILD
    assert next_state(TurnState.BUILD, "ok") is TurnState.RUN
    assert next_state(TurnState.BUILD, "rejected") is TurnState.RESPOND
    assert next_state(TurnState.RUN, "ok") is TurnState.COMPACT
    assert next_state(TurnState.COMPACT, "ok") is TurnState.SAVE
    assert next_state(TurnState.SAVE, "ok") is TurnState.RESPOND
    assert next_state(TurnState.RESPOND, "ok") is None


def test_inbound_message_has_runtime_defaults():
    msg = InboundMessage.new("hello", "demo", "user-1", "cli")
    assert msg.content == "hello"
    assert msg.session_id == "demo"
    assert msg.channel == "cli"
    assert msg.id
    assert msg.created_at


def test_outbound_message_supports_response_events():
    completed = OutboundMessage.completed("demo", "cli", "done")
    started = OutboundMessage.started("demo", "cli")
    delta = OutboundMessage.delta("demo", "cli", "片段")
    error = OutboundMessage.error("demo", "cli", "failed")

    assert completed.event_type == "response.completed"
    assert completed.content == "done"
    assert started.event_type == "response.started"
    assert started.content == ""
    assert delta.event_type == "response.delta"
    assert delta.content == "片段"
    assert error.event_type == "response.error"
    assert error.content == "failed"


def test_context_builder_uses_summary_history_and_user_message():
    """验证 ContextBuilder 只构建真实消息，不重复注入工具 schema。"""
    builder = ContextBuilder()
    messages = builder.build(
        summary="用户喜欢简洁回答。",
        history=[],
        user_content="你好",
        profile_memory="",
    )
    assert messages[0]["role"] == "system"
    assert "用户喜欢简洁回答" in messages[1]["content"]
    assert all("可用工具：" not in item["content"] for item in messages)
    assert messages[-1]["content"] == "你好"


def test_context_token_budget_counts_openai_tools_once():
    """验证预算只统计一次实际发送的 OpenAI Tool schema。"""
    openai_tools = [
        {
            "type": "function",
            "function": {
                "name": "echo",
                "description": "echo",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    breakdown = build_context_token_breakdown(
        summary="摘要",
        history=[],
        current_input="问题",
        output="回答",
        profile_memory="偏好",
        openai_tools=openai_tools,
        include_current_turn=True,
    )

    assert breakdown["tool_schema_tokens"] == count_content_tokens(
        json.dumps(openai_tools, ensure_ascii=False, sort_keys=True)
    )
    assert breakdown["current_context_tokens"] == sum(
        breakdown[key]
        for key in (
            "system_tokens",
            "profile_memory_tokens",
            "summary_tokens",
            "history_tokens",
            "current_input_tokens",
            "output_tokens",
            "tool_schema_tokens",
        )
    )


def test_context_token_budget_ignores_empty_openai_tools():
    """验证未发送 tools 参数时不计 schema token。"""
    breakdown = build_context_token_breakdown(
        summary="",
        history=[],
        current_input="问题",
        output="",
        profile_memory="",
        openai_tools=[],
        include_current_turn=True,
    )

    assert breakdown["tool_schema_tokens"] == 0


def test_turn_context_only_keeps_uncompacted_history():
    """验证跨状态上下文不再保存重复历史字段。"""
    ctx = TurnContext(inbound=InboundMessage.new("hi", "demo", "user-1", "cli"))

    assert hasattr(ctx, "uncompacted_history")
    assert not hasattr(ctx, "full_history")
    assert not hasattr(ctx, "history")


def test_state_trace_records_state_name():
    trace = StateTrace("turn-1", "demo", "SESSION", 1.5, "ok")
    assert trace.state == "SESSION"


def test_token_monitor_returns_complete_usage_record():
    monitor = TokenMonitor()

    usage = monitor.record_llm_usage(
        LLMUsage(100, 30, 130),
        previous_total_tokens=20,
        compacted=True,
    )

    assert usage == {
        "input_tokens": 100,
        "output_tokens": 30,
        "turn_total_tokens": 130,
        "total_tokens": 150,
        "compacted": 1,
    }


def test_store_saves_turn_traces_in_one_call(tmp_path):
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        await store.create_session("demo", "user-1", "cli")

        await store.save_turn_traces(
            [
                StateTrace("turn-1", "demo", "SESSION", 1.0, "ok"),
                StateTrace("turn-1", "demo", "RUN", 2.0, "ok", metadata={"tool_call_count": 0}),
            ]
        )

        session_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
        rows = [
            json.loads(line)
            for line in (session_dir / "turn_traces.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert [row["state"] for row in rows] == ["SESSION", "RUN"]
        assert rows[1]["metadata"] == {"tool_call_count": 0}
        assert all("id" not in row for row in rows)

    asyncio.run(run())


def test_runtime_processes_plain_message(tmp_path):
    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        runtime = AgentRuntime.create_default(settings, RuleLLM())
        msg = InboundMessage.new("hello", "demo", "user-1", "cli")

        outbound = await runtime.run_turn(msg)

        assert outbound.content == "收到：hello"
        assert [item.state for item in runtime.last_trace] == [
            "COMMAND",
            "SESSION",
            "BUILD",
            "RUN",
            "COMPACT",
            "SAVE",
            "RESPOND",
        ]
        assert await runtime.sessions.store.count_rows("true_token_usage") == 1
        assert await runtime.sessions.store.count_rows("turn_traces") == 7
        session_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
        assert (session_dir / "true_token_usage.jsonl").exists()
        assert (session_dir / "turn_traces.jsonl").exists()
        trace_rows = [
            json.loads(line)
            for line in (session_dir / "turn_traces.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        save_metadata = next(row["metadata"] for row in trace_rows if row["state"] == "SAVE")
        assert "message_count" not in save_metadata
        assert save_metadata["tool_count"] == 0
        assert save_metadata["output_tokens"] == count_content_tokens("收到：hello")
        assert save_metadata["current_context_tokens"] > save_metadata["current_input_tokens"]

    asyncio.run(run())


def test_runtime_writes_turn_monitor_fields_only_to_respond_trace(tmp_path):
    """验证终态监控字段只写入 RESPOND trace。"""

    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        runtime = AgentRuntime.create_default(settings, RuleLLM())

        await runtime.run_turn(InboundMessage.new("hello", "demo", "user-1", "cli"))

        traces = {trace.state: trace.metadata for trace in runtime.last_trace}
        assert set(traces["RESPOND"]) == {
            "outcome",
            "turn_duration_ms",
            "session_lock_wait_ms",
            "tool_failure_count",
        }
        assert traces["RESPOND"]["outcome"] == "completed"
        assert traces["RESPOND"]["tool_failure_count"] == 0
        for state in ("RUN", "COMPACT", "SAVE"):
            assert not set(traces[state]).intersection(traces["RESPOND"])

    asyncio.run(run())


def test_runtime_turn_monitor_marks_build_rejection(tmp_path):
    """验证上下文拒绝会写入 rejected 终态。"""

    class UnusedLLM:
        """确保拒绝路径不调用模型。"""

        async def complete(self, messages, tools):
            """拒绝意外模型调用。"""
            del messages, tools
            raise AssertionError("context rejection should skip llm")

    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        settings.runtime.max_context_tokens = 1
        runtime = AgentRuntime.create_default(settings, UnusedLLM())

        await runtime.run_turn(InboundMessage.new("hello", "demo", "user-1", "cli"))

        respond = runtime.last_trace[-1]
        assert respond.state == "RESPOND"
        assert respond.metadata["outcome"] == "rejected"

    asyncio.run(run())


def test_runtime_turn_monitor_marks_llm_failure(tmp_path):
    """验证模型异常会写入 failed 终态。"""

    class FailingLLM:
        """模拟模型异常。"""

        async def complete(self, messages, tools):
            """抛出模型异常。"""
            del messages, tools
            raise RuntimeError("模拟模型请求失败")

    async def run() -> None:
        runtime = AgentRuntime.create_default(Settings(data_dir=tmp_path), FailingLLM())

        await runtime.run_turn(InboundMessage.new("hello", "demo", "user-1", "cli"))

        assert runtime.last_trace[-1].metadata["outcome"] == "failed"

    asyncio.run(run())


def test_runtime_shortcuts_skip_turn_monitor_without_session(tmp_path):
    """验证无会话快捷命令不触发终态监控。"""

    class RecordingHook(AgentHook):
        """记录终态 Hook 调用次数。"""

        def __init__(self) -> None:
            """初始化调用计数。"""
            self.calls = 0

        async def after_turn(self, ctx, turn_duration_ms, session_lock_wait_ms):
            """记录终态 Hook 调用。"""
            del ctx, turn_duration_ms, session_lock_wait_ms
            self.calls += 1
            return {}

    async def run() -> None:
        runtime = AgentRuntime.create_default(Settings(data_dir=tmp_path), RuleLLM())
        hook = RecordingHook()
        runtime.hooks.register(hook)

        await runtime.run_turn(InboundMessage.new("/clear", "demo", "user-1", "cli"))
        await runtime.run_turn(InboundMessage.new("/approve", "demo", "user-1", "cli"))

        assert hook.calls == 0
        assert [path for path in tmp_path.iterdir() if path.is_dir()] == []
        assert not list(tmp_path.rglob("turn_traces.jsonl"))

    asyncio.run(run())


def test_runtime_turn_monitor_records_lock_wait(tmp_path):
    """验证同会话锁竞争会写入正数等待耗时。"""

    class BlockingLLM:
        """阻塞首个模型调用以制造锁竞争。"""

        def __init__(self, started, release) -> None:
            """保存并发同步事件。"""
            self.started = started
            self.release = release
            self.calls = 0

        async def complete(self, messages, tools):
            """阻塞第一轮并正常返回后续调用。"""
            del messages, tools
            self.calls += 1
            if self.calls == 1:
                self.started.set()
                await self.release.wait()
            return LLMResponse("ok", usage=LLMUsage(10, 2, 12))

    class RecordingLock:
        """在第二个协程等待锁时通知测试。"""

        def __init__(self, second_waiting) -> None:
            """初始化互斥锁和等待事件。"""
            self.lock = asyncio.Lock()
            self.second_waiting = second_waiting
            self.entries = 0

        async def __aenter__(self):
            """获取锁并标记第二次进入。"""
            self.entries += 1
            if self.entries == 2:
                self.second_waiting.set()
            await self.lock.acquire()
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            """释放互斥锁。"""
            del exc_type, exc, traceback
            self.lock.release()

    class RecordingLocks:
        """为指定会话返回同一把记录锁。"""

        def __init__(self, lock) -> None:
            """保存记录锁。"""
            self.lock = lock

        def lock_for(self, session_id):
            """返回共享锁。"""
            del session_id
            return self.lock

    async def run() -> None:
        started = asyncio.Event()
        release = asyncio.Event()
        second_waiting = asyncio.Event()
        runtime = AgentRuntime.create_default(Settings(data_dir=tmp_path), BlockingLLM(started, release))
        runtime.sessions.locks = RecordingLocks(RecordingLock(second_waiting))

        first = asyncio.create_task(runtime.run_turn(InboundMessage.new("first", "demo", "user-1", "cli")))
        await started.wait()
        second = asyncio.create_task(runtime.run_turn(InboundMessage.new("second", "demo", "user-1", "cli")))
        await second_waiting.wait()
        release.set()
        await asyncio.gather(first, second)

        session_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
        rows = [
            json.loads(line)
            for line in (session_dir / "turn_traces.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        responses = [row for row in rows if row["state"] == "RESPOND"]
        assert len(responses) == 2
        assert responses[-1]["metadata"]["session_lock_wait_ms"] > 0

    asyncio.run(run())


def test_build_state_does_not_persist_context_observability(tmp_path):
    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        runtime = AgentRuntime.create_default(settings, RuleLLM())
        ctx = TurnContext(inbound=InboundMessage.new("hello", "demo", "user-1", "cli"))

        await session_state(runtime, ctx)
        event = await build_state(runtime, ctx)

        assert event == "ok"
        assert ctx.model_messages
        assert ctx.context_tokens == {}

    asyncio.run(run())


def test_runtime_streams_plain_message_when_enabled(tmp_path):
    class StreamingLLM:
        async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            del messages, tools
            return LLMResponse("fallback", usage=LLMUsage(10, 2, 12))

        async def stream(self, messages, tools):  # type: ignore[no-untyped-def]
            del messages, tools
            yield LLMChunk("你")
            yield LLMChunk("好", finish_reason="stop")
            yield LLMChunk(usage=LLMUsage(10, 2, 12))

    async def run() -> None:
        class RecordingOutput:
            """记录 Runtime 的 Channel 输出。"""

            def __init__(self) -> None:
                """初始化流式文本记录。"""
                self.deltas = []

            async def on_delta(self, text):
                """记录流式文本。"""
                self.deltas.append(text)

            async def on_status(self, text):
                """忽略状态文本。"""
                del text

            async def on_completed(self, content):
                """忽略最终回复。"""
                del content

            async def on_error(self, content):
                """忽略错误回复。"""
                del content

        settings = Settings(data_dir=tmp_path)
        settings.llm.streaming_enabled = True
        runtime = AgentRuntime.create_default(settings, StreamingLLM())
        output = RecordingOutput()
        runtime.channel_router.register("cli", lambda: output)

        outbound = await runtime.run_turn(InboundMessage.new("hello", "demo", "user-1", "cli"))

        assert output.deltas == ["你", "好"]
        assert outbound.content == "你好"

    asyncio.run(run())


def test_runtime_history_command_skips_llm(tmp_path):
    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        runtime = AgentRuntime.create_default(settings, RuleLLM())

        await runtime.run_turn(InboundMessage.new("hello", "demo", "user-1", "cli"))
        outbound = await runtime.run_turn(InboundMessage.new("/history", "demo", "user-1", "cli"))

        assert "user: hello" in outbound.content
        assert "assistant: 收到：hello" in outbound.content

    asyncio.run(run())


def test_runtime_tools_command_shows_saved_tool_calls(tmp_path):
    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        runtime = AgentRuntime.create_default(settings, RuleLLM())

        await runtime.run_turn(InboundMessage.new("what time is it", "demo", "user-1", "cli"))
        outbound = await runtime.run_turn(InboundMessage.new("/tools", "demo", "user-1", "cli"))

        assert "tool=now" in outbound.content
        assert "tool_call_id=call-1" in outbound.content
        assert "result:" in outbound.content

    asyncio.run(run())


def test_runtime_context_command_shows_summary_and_uncompacted_history(tmp_path):
    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        runtime = AgentRuntime.create_default(settings, RuleLLM())
        session = await runtime.sessions.load_or_create("demo", "user-1", "cli")
        await runtime.sessions.store.update_summary("demo", "旧会话摘要")
        await runtime.sessions.store.save_message("demo", "user", "old-user", token_count=5)
        await runtime.sessions.store.save_message("demo", "assistant", "old-assistant", token_count=5)
        await runtime.sessions.store.save_message("demo", "user", "recent-user", token_count=5)
        history = await runtime.sessions.all_messages("demo")
        await runtime.sessions.store.update_uncompacted_history("demo", history[2:])

        outbound = await runtime.run_turn(InboundMessage.new("/context", "demo", "user-1", "cli"))

        assert "会话摘要：旧会话摘要" in outbound.content
        assert "未压缩历史消息数：1" in outbound.content
        assert "user: recent-user" in outbound.content
        assert "old-user" not in outbound.content
        assert not any(path.name == "true_token_usage.jsonl" for path in tmp_path.rglob("true_token_usage.jsonl"))

    asyncio.run(run())


def test_runtime_clear_command_removes_session_directory_without_recreating_it(tmp_path):
    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        runtime = AgentRuntime.create_default(settings, RuleLLM())
        await runtime.run_turn(InboundMessage.new("hello", "demo", "user-1", "cli"))

        outbound = await runtime.run_turn(InboundMessage.new("/clear", "demo", "user-1", "cli"))

        assert "已清空" in outbound.content
        assert not any(path.is_dir() for path in tmp_path.iterdir())

    asyncio.run(run())


def test_runtime_saves_tool_calls_to_dedicated_jsonl(tmp_path):
    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        runtime = AgentRuntime.create_default(settings, RuleLLM())

        await runtime.run_turn(InboundMessage.new("what time is it", "demo", "user-1", "cli"))

        session_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
        rows = [
            json.loads(line)
            for line in (session_dir / "tool_calls.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert len(rows) == 1
        assert rows[0]["tool_name"] == "now"
        assert rows[0]["tool_call_id"] == "call-1"
        assert rows[0]["args"] == {}
        assert "session_id" not in rows[0]
        assert "id" not in rows[0]
        assert await runtime.sessions.store.count_rows("tool_calls") == 1

    asyncio.run(run())


def test_runtime_clear_command_on_new_empty_session_does_not_create_directory(tmp_path):
    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        runtime = AgentRuntime.create_default(settings, RuleLLM())

        outbound = await runtime.run_turn(InboundMessage.new("/clear", "demo", "user-1", "cli"))

        assert "已清空" in outbound.content
        assert [path for path in tmp_path.iterdir() if path.is_dir()] == []

    asyncio.run(run())


def test_runtime_can_call_echo_tool(tmp_path):
    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        runtime = AgentRuntime.create_default(settings, RuleLLM())

        outbound = await runtime.run_turn(InboundMessage.new("echo: abc", "demo", "user-1", "cli"))

        assert outbound.content == "工具结果：abc"
        session_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
        token_rows = [
            json.loads(line)
            for line in (session_dir / "true_token_usage.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        trace_rows = [
            json.loads(line)
            for line in (session_dir / "turn_traces.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        session_payload = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
        assert "id" not in token_rows[0]
        assert all("id" not in row for row in trace_rows)
        assert "metadata" not in session_payload
        assert "tool_call_count" not in token_rows[0]
        assert "tool_names" not in token_rows[0]
        run_trace = next(row for row in trace_rows if row["state"] == "RUN")
        assert run_trace["metadata"]["tool_call_count"] == 1
        assert run_trace["metadata"]["tool_names"] == ["echo"]

    asyncio.run(run())


def test_runtime_records_cumulative_session_tokens(tmp_path):
    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        runtime = AgentRuntime.create_default(settings, RuleLLM())

        await runtime.run_turn(InboundMessage.new("hello", "demo", "user-1", "cli"))
        await runtime.run_turn(InboundMessage.new("hello again", "demo", "user-1", "cli"))

        session_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
        rows = [
            json.loads(line)
            for line in (session_dir / "true_token_usage.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert rows[0]["turn_total_tokens"] > 0
        assert rows[1]["turn_total_tokens"] > 0
        assert rows[1]["total_tokens"] > rows[0]["total_tokens"]
        assert "compacted_message_count" not in rows[0]
        assert "compacted_token_count" not in rows[0]
        history = await runtime.sessions.all_messages("demo")
        assert history[0].token_count == 1
        assert history[1].token_count == count_content_tokens("收到：hello")
        assert history[0].token_count != rows[0]["input_tokens"]

    asyncio.run(run())


def test_runtime_true_token_usage_uses_llm_usage_for_messages_and_totals(tmp_path):
    class UsageLLM:
        async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            del messages, tools
            return LLMResponse("ok", usage=LLMUsage(100, 30, 130))

    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        runtime = AgentRuntime.create_default(settings, UsageLLM())

        await runtime.run_turn(InboundMessage.new("hi", "demo", "user-1", "cli"))

        session_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
        token_rows = [
            json.loads(line)
            for line in (session_dir / "true_token_usage.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        messages = await runtime.sessions.all_messages("demo")
        assert token_rows[0]["input_tokens"] == 100
        assert token_rows[0]["output_tokens"] == 30
        assert token_rows[0]["turn_total_tokens"] == 130
        assert token_rows[0]["total_tokens"] == 130
        assert messages[0].token_count == 1
        assert messages[1].token_count == 1
        assert messages[0].token_count != token_rows[0]["input_tokens"]

    asyncio.run(run())


def test_shortcut_command_does_not_require_llm_usage(tmp_path):
    class UnusedLLM:
        async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            raise AssertionError("shortcut command should not call llm")

    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        runtime = AgentRuntime.create_default(settings, UnusedLLM())

        outbound = await runtime.run_turn(InboundMessage.new("/clear", "demo", "user-1", "cli"))

        assert outbound.event_type == "response.completed"
        assert outbound.content == "当前会话已清空。"
        assert [trace.state for trace in runtime.last_trace] == ["COMMAND", "RESPOND"]
        assert not any(path.name == "true_token_usage.jsonl" for path in tmp_path.rglob("true_token_usage.jsonl"))

    asyncio.run(run())


def test_runtime_returns_error_when_llm_usage_is_missing(tmp_path):
    class MissingUsageLLM:
        async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            del messages, tools
            return LLMResponse("ok")

    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        runtime = AgentRuntime.create_default(settings, MissingUsageLLM())

        outbound = await runtime.run_turn(InboundMessage.new("hi", "demo", "user-1", "cli"))

        assert outbound.event_type == "response.error"
        assert "缺少有效 total_tokens" in outbound.content
        session_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
        assert not (session_dir / "true_token_usage.jsonl").exists()

    asyncio.run(run())


def test_runtime_returns_error_text_instead_of_empty_response(tmp_path):
    class FailingLLM:
        async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            del messages, tools
            raise RuntimeError("模拟模型请求失败")

    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        runtime = AgentRuntime.create_default(settings, FailingLLM())

        outbound = await runtime.run_turn(InboundMessage.new("hello", "demo", "user-1", "cli"))

        assert "模拟模型请求失败" in outbound.content
        assert outbound.event_type == "response.error"
        assert runtime.last_trace[-1].state == "RESPOND"
        assert runtime.last_trace[-2].event == "error"

    asyncio.run(run())


def test_runtime_rejects_turn_when_build_context_exceeds_limit(tmp_path):
    class UnusedLLM:
        async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            raise AssertionError("context too large should skip llm")

    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        settings.runtime.max_context_tokens = 10
        runtime = AgentRuntime.create_default(settings, UnusedLLM())
        await runtime.sessions.load_or_create("demo", "user-1", "cli")
        await runtime.sessions.store.save_message("demo", "user", "huge-history", token_count=20)

        outbound = await runtime.run_turn(InboundMessage.new("hi", "demo", "user-1", "cli"))

        assert outbound.event_type == "response.error"
        assert "上下文过大" in outbound.content
        assert [trace.state for trace in runtime.last_trace] == ["COMMAND", "SESSION", "BUILD", "RESPOND"]
        assert not any(path.name == "true_token_usage.jsonl" for path in tmp_path.rglob("true_token_usage.jsonl"))

    asyncio.run(run())


def test_runtime_rejects_turn_when_system_prompt_pushes_context_over_limit(tmp_path):
    class UnusedLLM:
        async def complete(self, messages, tools):  # type: ignore[no-untyped-def]
            raise AssertionError("context too large should skip llm")

    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        user_input = "hi"
        settings.runtime.max_context_tokens = count_content_tokens(user_input)
        runtime = AgentRuntime.create_default(settings, UnusedLLM())

        outbound = await runtime.run_turn(InboundMessage.new(user_input, "demo", "user-1", "cli"))

        assert count_content_tokens(SYSTEM_PROMPT) > 0
        assert outbound.event_type == "response.error"
        assert "上下文过大" in outbound.content
        assert [trace.state for trace in runtime.last_trace] == ["COMMAND", "SESSION", "BUILD", "RESPOND"]

    asyncio.run(run())


def test_runtime_compacts_after_save_and_applies_summary_on_next_turn(tmp_path):
    class RecordingLLM:
        """记录 Runtime 实际传入模型的回答和摘要消息。"""

        def __init__(self) -> None:
            self.answer_calls: list[list[dict[str, str]]] = []
            self.summary_calls: list[list[dict[str, str]]] = []

        async def complete(self, messages, tools):
            """按提示类型返回回答或摘要。"""
            del tools
            if "待压缩会话片段" in messages[-1]["content"]:
                self.summary_calls.append(messages)
                return LLMResponse("LLM摘要", usage=LLMUsage(7, 3, 10))
            self.answer_calls.append(messages)
            return LLMResponse("ok", usage=LLMUsage(20, 4, 24))

    async def run() -> None:
        llm = RecordingLLM()
        settings = Settings(data_dir=tmp_path)
        settings.memory.compact_token_threshold = 20
        settings.memory.recent_window_token_limit = 10
        runtime = AgentRuntime.create_default(settings, llm)
        await runtime.sessions.load_or_create("demo", "user-1", "cli")

        for index in range(3):
            await runtime.sessions.store.save_message("demo", "user", f"old-user-{index}", token_count=5)
            await runtime.sessions.store.save_message(
                "demo",
                "assistant",
                f"old-assistant-{index}",
                token_count=5,
            )

        outbound = await runtime.run_turn(InboundMessage.new("current", "demo", "user-1", "cli"))
        second = await runtime.run_turn(InboundMessage.new("next", "demo", "user-1", "cli"))

        assert outbound.content == "ok"
        assert second.content == "ok"
        first_call_contents = [item["content"] for item in llm.answer_calls[0]]
        second_call_contents = [item["content"] for item in llm.answer_calls[1]]
        assert "old-user-0" in first_call_contents
        assert "old-assistant-0" in first_call_contents
        assert "old-user-1" in first_call_contents
        assert "old-assistant-1" in first_call_contents
        assert "old-user-0" not in second_call_contents
        assert "old-assistant-0" not in second_call_contents
        assert any("会话摘要：LLM摘要" == item for item in second_call_contents)
        assert llm.summary_calls
        summary_prompt = llm.summary_calls[0][-1]["content"]
        assert "old-user-0" in summary_prompt
        assert "old-assistant-0" in summary_prompt
        session = await runtime.sessions.store.load_session("demo")
        assert session is not None
        assert session.summary == "LLM摘要"
        assert sum(item.token_count for item in session.uncompacted_history) <= 10
        assert all(item.role in {"user", "assistant"} for item in session.uncompacted_history)
        session_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
        session_payload = json.loads((session_dir / "session.json").read_text(encoding="utf-8"))
        assert "metadata" not in session_payload
        assert "compacted_message_count" not in session_payload
        assert all(set(item) == {"role", "content"} for item in session_payload["uncompacted_history"])
        token_rows = [
            json.loads(line)
            for line in (session_dir / "true_token_usage.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        trace_rows = [
            json.loads(line)
            for line in (session_dir / "turn_traces.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        compacted_rows = [row for row in token_rows if row["compacted"] == 1]
        compact_traces = [row for row in trace_rows if row["state"] == "COMPACT"]
        compacted_traces = [row for row in compact_traces if row["metadata"]["compacted"] == 1]
        assert token_rows[0]["compacted"] == 1
        assert token_rows[0]["input_tokens"] == 27
        assert token_rows[0]["output_tokens"] == 7
        assert token_rows[0]["turn_total_tokens"] == 34
        assert "compacted_message_count" not in token_rows[0]
        assert "compacted_token_count" not in token_rows[0]
        assert "raw_window_message_count" not in token_rows[0]
        assert "raw_window_token_count" not in token_rows[0]
        assert compacted_rows
        assert compact_traces
        assert compacted_traces
        assert compact_traces[0]["event"] == "ok"
        assert set(compact_traces[0]["metadata"]) == {
            "compacted",
            "compacted_message_count",
            "compacted_token_count",
            "raw_window_message_count",
            "raw_window_token_count",
        }
        assert compacted_traces[-1]["metadata"]["compacted_message_count"] > 0
        assert compacted_traces[-1]["metadata"]["compacted_token_count"] > 0
        assert compacted_traces[-1]["metadata"]["raw_window_message_count"] >= 0
        assert compacted_traces[-1]["metadata"]["raw_window_token_count"] <= 10

    asyncio.run(run())


def test_save_context_tokens_exclude_current_turn_when_compacted_out(tmp_path):
    class SummaryLLM:
        """模拟本轮压缩后 recent_window 为空的模型。"""

        async def complete(self, messages, tools):
            """普通回答和摘要调用都返回 usage。"""
            del tools
            if "待压缩会话片段" in messages[-1]["content"]:
                return LLMResponse("只保留摘要", usage=LLMUsage(6, 2, 8))
            return LLMResponse("very long assistant reply", usage=LLMUsage(20, 4, 24))

    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        settings.memory.compact_token_threshold = 1
        settings.memory.recent_window_token_limit = 1
        runtime = AgentRuntime.create_default(settings, SummaryLLM())

        outbound = await runtime.run_turn(
            InboundMessage.new("very long user message", "demo", "user-1", "cli")
        )

        assert outbound.content == "very long assistant reply"
        session = await runtime.sessions.store.load_session("demo")
        assert session is not None
        assert session.summary == "只保留摘要"
        assert session.uncompacted_history == []
        session_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
        trace_rows = [
            json.loads(line)
            for line in (session_dir / "turn_traces.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        save_metadata = next(row["metadata"] for row in trace_rows if row["state"] == "SAVE")
        assert save_metadata["current_input_tokens"] > 0
        assert save_metadata["output_tokens"] > 0
        assert save_metadata["history_tokens"] == 0
        assert save_metadata["current_context_tokens"] == (
            save_metadata["system_tokens"]
            + save_metadata["profile_memory_tokens"]
            + save_metadata["summary_tokens"]
            + save_metadata["history_tokens"]
            + save_metadata["tool_schema_tokens"]
        )

    asyncio.run(run())


def test_runtime_rejects_compaction_when_summary_usage_is_missing(tmp_path):
    class MissingSummaryUsageLLM:
        """模拟摘要调用没有返回 usage 的异常 provider。"""

        async def complete(self, messages, tools):
            """普通回答有 usage，摘要回答缺 usage。"""
            del tools
            if "待压缩会话片段" in messages[-1]["content"]:
                return LLMResponse("摘要", usage=None)
            return LLMResponse("ok", usage=LLMUsage(20, 4, 24))

    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        settings.memory.compact_token_threshold = 10
        settings.memory.recent_window_token_limit = 5
        runtime = AgentRuntime.create_default(settings, MissingSummaryUsageLLM())
        await runtime.sessions.load_or_create("demo", "user-1", "cli")
        await runtime.sessions.store.save_message("demo", "user", "old-user", token_count=6)
        await runtime.sessions.store.save_message("demo", "assistant", "old-assistant", token_count=6)

        outbound = await runtime.run_turn(InboundMessage.new("current", "demo", "user-1", "cli"))

        assert outbound.event_type == "response.error"
        assert "摘要 LLM 响应缺少 usage" in outbound.content
        session_dir = next(path for path in tmp_path.iterdir() if path.is_dir())
        assert not (session_dir / "true_token_usage.jsonl").exists()
        assert [trace.state for trace in runtime.last_trace] == ["COMMAND", "SESSION", "BUILD", "RUN", "COMPACT", "RESPOND"]
        assert runtime.last_trace[-2].event == "error"

    asyncio.run(run())


def test_validate_tool_permission_tools_rejects_unknown_or_parallel_safe_tools(tmp_path):
    """验证审批工具配置会在启动前被拒绝。"""
    unknown = Settings(data_dir=tmp_path / "unknown")
    unknown.tool_permissions.approval_required_tools = ["missing"]
    unknown_tools = import_module("Turning-Good-Agent.tools.registry").ToolRegistry()
    try:
        validate_tool_permission_tools(unknown_tools, unknown.tool_permissions.approval_required_tools)
    except ValueError as exc:
        assert str(exc) == "审批工具未注册：missing"
    else:
        raise AssertionError("未知审批工具应导致启动前校验失败")

    parallel_safe = Settings(data_dir=tmp_path / "parallel")
    parallel_safe.tool_permissions.approval_required_tools = ["now"]
    parallel_safe_tools = import_module("Turning-Good-Agent.tools.registry").ToolRegistry()
    ToolLoader = import_module("Turning-Good-Agent.tools.loader").ToolLoader
    ToolLoader().load(parallel_safe_tools, parallel_safe)
    try:
        validate_tool_permission_tools(parallel_safe_tools, parallel_safe.tool_permissions.approval_required_tools)
    except ValueError as exc:
        assert str(exc) == "审批工具不能设置 parallel_safe=true：now"
    else:
        raise AssertionError("并行安全审批工具应导致 Runtime 创建失败")


def test_settings_loads_tool_permission_configuration(tmp_path):
    """验证本地配置可覆盖审批类工具列表。"""
    config_path = tmp_path / "settings.local.json"
    config_path.write_text(
        json.dumps({"tool_permissions": {"approval_required_tools": ["exec"]}}),
        encoding="utf-8",
    )

    settings = Settings.load(local_config_path=config_path)

    assert settings.tool_permissions.approval_required_tools == ["exec"]

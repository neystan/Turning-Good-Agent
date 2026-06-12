import asyncio
import json
from importlib import import_module

InboundMessage = import_module("Turning-Good-Agent.bus.messages").InboundMessage
Settings = import_module("Turning-Good-Agent.config.settings").Settings
estimate_tokens = import_module("Turning-Good-Agent.context.budget").estimate_tokens
ContextBuilder = import_module("Turning-Good-Agent.context.builder").ContextBuilder
FakeLLM = import_module("Turning-Good-Agent.llm.fake").FakeLLM
LLMResponse = import_module("Turning-Good-Agent.llm.types").LLMResponse
StateTrace = import_module("Turning-Good-Agent.observability.trace").StateTrace
AgentRuntime = import_module("Turning-Good-Agent.runtime.runtime").AgentRuntime
state_module = import_module("Turning-Good-Agent.runtime.state")
TurnState = state_module.TurnState
next_state = state_module.next_state


def test_state_transitions_are_six_state_runtime():
    assert next_state(TurnState.PREPARE, "ok") is TurnState.RUN
    assert next_state(TurnState.RUN, "ok") is TurnState.SAVE
    assert next_state(TurnState.SAVE, "ok") is TurnState.COMPACT
    assert next_state(TurnState.COMPACT, "ok") is TurnState.RESPOND
    assert next_state(TurnState.RESPOND, "ok") is TurnState.DONE


def test_inbound_message_has_runtime_defaults():
    msg = InboundMessage.new("hello", "demo", "user-1", "cli")
    assert msg.content == "hello"
    assert msg.session_id == "demo"
    assert msg.channel == "cli"
    assert msg.id
    assert msg.created_at


def test_context_builder_uses_summary_history_and_user_message():
    builder = ContextBuilder()
    messages = builder.build(
        summary="用户喜欢简洁回答。",
        history=[],
        user_content="你好",
        tool_schemas=[{"name": "echo"}],
        profile_memory="",
    )
    assert messages[0]["role"] == "system"
    assert "用户喜欢简洁回答" in messages[1]["content"]
    assert messages[-1]["content"] == "你好"


def test_token_estimate_is_stable_and_nonzero():
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("你好世界") >= 4
    assert estimate_tokens("hello world") == 2
    assert estimate_tokens("") == 1


def test_state_trace_records_state_name():
    trace = StateTrace("turn-1", "demo", "PREPARE", 1.5, "ok")
    assert trace.state == "PREPARE"


def test_runtime_processes_plain_message(tmp_path):
    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        runtime = AgentRuntime.create_default(settings, FakeLLM())
        msg = InboundMessage.new("hello", "demo", "user-1", "cli")

        outbound = await runtime.run_turn(msg)

        assert outbound.content == "收到：hello"
        assert [item.state for item in runtime.last_trace] == ["PREPARE", "RUN", "SAVE", "COMPACT", "RESPOND"]
        assert await runtime.sessions.store.count_rows("token_usage") == 1
        assert await runtime.sessions.store.count_rows("turn_traces") == 5
        session_dir = next((tmp_path / "sessions").iterdir())
        assert (session_dir / "token_usage.jsonl").exists()
        assert (session_dir / "turn_traces.jsonl").exists()

    asyncio.run(run())


def test_runtime_history_command_skips_llm(tmp_path):
    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        runtime = AgentRuntime.create_default(settings, FakeLLM())

        await runtime.run_turn(InboundMessage.new("hello", "demo", "user-1", "cli"))
        outbound = await runtime.run_turn(InboundMessage.new("/history", "demo", "user-1", "cli"))

        assert "user: hello" in outbound.content
        assert "assistant: 收到：hello" in outbound.content

    asyncio.run(run())


def test_runtime_clear_command_removes_session_directory_without_recreating_it(tmp_path):
    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        runtime = AgentRuntime.create_default(settings, FakeLLM())
        await runtime.run_turn(InboundMessage.new("hello", "demo", "user-1", "cli"))

        outbound = await runtime.run_turn(InboundMessage.new("/clear", "demo", "user-1", "cli"))

        assert "已清空" in outbound.content
        assert not (tmp_path / "sessions" / "demo").exists()

    asyncio.run(run())


def test_runtime_clear_command_on_new_empty_session_does_not_create_directory(tmp_path):
    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        runtime = AgentRuntime.create_default(settings, FakeLLM())

        outbound = await runtime.run_turn(InboundMessage.new("/clear", "demo", "user-1", "cli"))

        assert "已清空" in outbound.content
        assert list((tmp_path / "sessions").iterdir()) == []

    asyncio.run(run())


def test_runtime_can_call_echo_tool(tmp_path):
    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        runtime = AgentRuntime.create_default(settings, FakeLLM())

        outbound = await runtime.run_turn(InboundMessage.new("echo: abc", "demo", "user-1", "cli"))

        assert outbound.content == "工具结果：abc"

    asyncio.run(run())


def test_runtime_records_cumulative_session_tokens(tmp_path):
    async def run() -> None:
        settings = Settings(data_dir=tmp_path)
        runtime = AgentRuntime.create_default(settings, FakeLLM())

        await runtime.run_turn(InboundMessage.new("hello", "demo", "user-1", "cli"))
        await runtime.run_turn(InboundMessage.new("hello again", "demo", "user-1", "cli"))

        session_dir = next((tmp_path / "sessions").iterdir())
        rows = [
            json.loads(line)
            for line in (session_dir / "token_usage.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        assert rows[0]["turn_total_tokens"] > 0
        assert rows[1]["turn_total_tokens"] > 0
        assert rows[1]["total_tokens"] > rows[0]["total_tokens"]
        assert rows[0]["compacted_message_count"] == 0
        assert rows[0]["compacted_token_count"] == 0
        history = await runtime.sessions.all_messages("demo")
        assert all(item.token_count > 0 for item in history)

    asyncio.run(run())


def test_runtime_compacts_after_save_and_applies_summary_on_next_turn(tmp_path):
    class RecordingLLM:
        """记录 Runtime 实际传入模型的消息。"""

        def __init__(self) -> None:
            self.calls: list[list[dict[str, str]]] = []

        async def complete(self, messages, tools):
            """保存消息并返回固定文本。"""
            self.calls.append(messages)
            return LLMResponse("ok")

    async def run() -> None:
        llm = RecordingLLM()
        settings = Settings(data_dir=tmp_path)
        settings.memory.compact_token_threshold = 20
        settings.memory.raw_window_token_limit = 10
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
        first_call_contents = [item["content"] for item in llm.calls[0]]
        second_call_contents = [item["content"] for item in llm.calls[1]]
        assert "old-user-0" in first_call_contents
        assert "old-assistant-0" in first_call_contents
        assert "old-user-1" in first_call_contents
        assert "old-assistant-1" in first_call_contents
        assert "old-user-0" not in second_call_contents
        assert "old-assistant-0" not in second_call_contents
        assert any("会话摘要" in item for item in second_call_contents)
        session = await runtime.sessions.store.load_session("demo")
        assert session is not None
        assert session.summary
        assert session.metadata["compacted_message_count"] >= 4
        assert "last_compaction_at" not in session.metadata
        assert "compacted_until_created_at" not in session.metadata
        assert "last_compacted_message_count" not in session.metadata
        assert "last_compacted_token_count" not in session.metadata
        assert "last_raw_window_message_count" not in session.metadata
        assert "last_raw_window_token_count" not in session.metadata
        assert "raw_window_token_limit" not in session.metadata
        session_dir = next((tmp_path / "sessions").iterdir())
        token_rows = [
            json.loads(line)
            for line in (session_dir / "token_usage.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        trace_rows = [
            json.loads(line)
            for line in (session_dir / "turn_traces.jsonl").read_text(encoding="utf-8").splitlines()
        ]
        compacted_rows = [row for row in token_rows if row["compacted"] == 1]
        compact_traces = [row for row in trace_rows if row["state"] == "COMPACT"]
        compacted_traces = [row for row in compact_traces if row["metadata"]["compacted"] == 1]
        assert token_rows[0]["compacted"] == 1
        assert token_rows[0]["compacted_message_count"] > 0
        assert token_rows[0]["compacted_token_count"] > 0
        assert token_rows[0]["raw_window_message_count"] >= 0
        assert token_rows[0]["raw_window_token_count"] <= 10
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
        assert compacted_traces[-1]["metadata"]["compacted_message_count"] == compacted_rows[-1]["compacted_message_count"]
        assert compacted_traces[-1]["metadata"]["compacted_token_count"] == compacted_rows[-1]["compacted_token_count"]
        assert compacted_traces[-1]["metadata"]["raw_window_message_count"] == compacted_rows[-1]["raw_window_message_count"]
        assert compacted_traces[-1]["metadata"]["raw_window_token_count"] == compacted_rows[-1]["raw_window_token_count"]

    asyncio.run(run())

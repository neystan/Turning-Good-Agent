import asyncio
from importlib import import_module

InboundMessage = import_module("Turning-Good-Agent.bus.messages").InboundMessage
Settings = import_module("Turning-Good-Agent.config.settings").Settings
AgentHook = import_module("Turning-Good-Agent.hooks.base").AgentHook
LLMResponse = import_module("Turning-Good-Agent.llm.types").LLMResponse
LLMUsage = import_module("Turning-Good-Agent.llm.types").LLMUsage
AgentRuntime = import_module("Turning-Good-Agent.runtime.runtime").AgentRuntime
ShortTermMemory = import_module("Turning-Good-Agent.memory.short_term").ShortTermMemory
MessageRecord = import_module("Turning-Good-Agent.sessions.types").MessageRecord


def _message(index: int, role: str, content: str, token_count: int) -> object:
    """构造短期记忆测试消息。"""
    return MessageRecord(
        id=str(index),
        session_id="demo",
        role=role,
        content=content,
        name=None,
        tool_call_id=None,
        token_count=token_count,
        created_at="2026-07-23T00:00:00+00:00",
        metadata={},
    )


def test_compaction_plan_keeps_full_history_below_threshold():
    """验证未达到阈值时计划保留完整未压缩历史。"""
    messages = [_message(1, "user", "u1", 3), _message(2, "assistant", "a1", 3)]

    plan = ShortTermMemory(10, 6).plan_compaction(messages)

    assert not plan.should_compact
    assert plan.compact_source == []
    assert plan.recent_window == messages
    assert plan.raw_window_message_count == 2
    assert plan.raw_window_token_count == 6


def test_compaction_plan_only_compacts_complete_turns_outside_recent_window():
    """验证压缩计划只压缩 recent window 外的完整 user/assistant 对话。"""
    messages = [
        _message(1, "user", "u1", 3),
        _message(2, "assistant", "a1", 3),
        _message(3, "user", "u2", 3),
        _message(4, "assistant", "a2", 3),
    ]

    plan = ShortTermMemory(6, 6).plan_compaction(messages)

    assert plan.should_compact
    assert [item.content for item in plan.compact_source] == ["u1", "a1"]
    assert [item.content for item in plan.recent_window] == ["u2", "a2"]
    assert plan.compacted_message_count == 2
    assert plan.compacted_token_count == 6


def test_forced_compaction_never_keeps_incomplete_turn():
    """验证强制压缩时 recent window 仍只保留完整对话。"""
    messages = [
        _message(1, "user", "u1", 3),
        _message(2, "assistant", "a1", 3),
        _message(3, "user", "u2", 3),
    ]

    plan = ShortTermMemory(100, 6).plan_compaction(messages, force=True)

    assert plan.should_compact
    assert plan.recent_window == messages[:2]
    assert plan.compact_source == messages[2:]


class CompactRecordingHook(AgentHook):
    """记录压缩 Hook 的触发顺序。"""

    def __init__(self, events):
        self.events = events

    async def before_compact(self, ctx):
        """记录真实压缩开始。"""
        del ctx
        self.events.append("before")

    async def after_compact(self, ctx):
        """记录真实压缩完成。"""
        assert ctx.compact_stats["compacted_message_count"] > 0
        assert ctx.true_token_usage["compacted"] == 1
        self.events.append("after")


class CompactLLM:
    """分别返回普通回答和压缩摘要。"""

    def __init__(self, events):
        self.events = events

    async def complete(self, messages, tools):
        """根据最后一条提示返回回答或摘要。"""
        del tools
        if "待压缩会话片段" in messages[-1]["content"]:
            self.events.append("summary")
            return LLMResponse("压缩摘要", usage=LLMUsage(8, 2, 10))
        return LLMResponse("ok", usage=LLMUsage(20, 4, 24))


def test_compact_hooks_run_only_around_real_compaction(tmp_path, capsys):
    """验证真实压缩按 before、summary、after 顺序触发且 Runtime 不打印。"""

    async def run() -> None:
        events = []
        settings = Settings(data_dir=tmp_path)
        settings.memory.compact_token_threshold = 10
        settings.memory.recent_window_token_limit = 5
        runtime = AgentRuntime.create_default(settings, CompactLLM(events))
        runtime.hooks.register(CompactRecordingHook(events))
        await runtime.sessions.load_or_create("demo", "user-1", "cli")
        await runtime.sessions.store.save_message("demo", "user", "old-user", token_count=6)
        await runtime.sessions.store.save_message("demo", "assistant", "old-assistant", token_count=6)

        outbound = await runtime.run_turn(InboundMessage.new("current", "demo", "user-1", "cli"))

        assert outbound.content == "ok"
        assert events == ["before", "summary", "after"]
        assert capsys.readouterr().out == ""

    asyncio.run(run())


def test_compact_hooks_skip_when_threshold_is_not_reached(tmp_path):
    """验证未达到压缩阈值时不触发 Compact Hook。"""

    async def run() -> None:
        events = []
        settings = Settings(data_dir=tmp_path)
        settings.memory.compact_token_threshold = 100_000
        runtime = AgentRuntime.create_default(settings, CompactLLM(events))
        runtime.hooks.register(CompactRecordingHook(events))

        await runtime.run_turn(InboundMessage.new("current", "demo", "user-1", "cli"))

        assert events == []

    asyncio.run(run())


def test_compact_hooks_skip_when_compact_source_is_empty(tmp_path):
    """验证 recent window 保留全部消息时不触发 Compact Hook。"""

    async def run() -> None:
        events = []
        settings = Settings(data_dir=tmp_path)
        settings.memory.compact_token_threshold = 1
        settings.memory.recent_window_token_limit = 100_000
        runtime = AgentRuntime.create_default(settings, CompactLLM(events))
        runtime.hooks.register(CompactRecordingHook(events))

        await runtime.run_turn(InboundMessage.new("current", "demo", "user-1", "cli"))

        assert events == []

    asyncio.run(run())

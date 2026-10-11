import asyncio
from importlib import import_module


AgentHook = import_module("Turning-Good-Agent.hooks.base").AgentHook
HookManager = import_module("Turning-Good-Agent.hooks.manager").HookManager
ToolCall = import_module("Turning-Good-Agent.llm.types").ToolCall
SilentChannelAdapter = import_module("Turning-Good-Agent.channels.base").SilentChannelAdapter
InboundMessage = import_module("Turning-Good-Agent.bus.messages").InboundMessage
StateTrace = import_module("Turning-Good-Agent.observability.trace").StateTrace
TurnContext = import_module("Turning-Good-Agent.runtime.turn_context").TurnContext
TurnMonitorHook = import_module("Turning-Good-Agent.hooks.turn_monitor").TurnMonitorHook


def test_after_turn_hook_returns_empty_mapping_by_default():
    """验证默认终态 Hook 不产生监控字段。"""

    async def run() -> None:
        result = await AgentHook().after_turn(object(), 12.0, 3.0)

        assert result == {}

    asyncio.run(run())


def test_after_turn_hooks_merge_results_in_registration_order():
    """验证终态 Hook 按注册顺序合并结果。"""

    class MetadataHook(AgentHook):
        """返回固定终态字段。"""

        def __init__(self, result):
            """保存待返回字段。"""
            self.result = result

        async def after_turn(self, ctx, turn_duration_ms, session_lock_wait_ms):
            """返回固定终态字段。"""
            del ctx, turn_duration_ms, session_lock_wait_ms
            return self.result

    async def run() -> None:
        manager = HookManager()
        manager.register(MetadataHook({"outcome": "completed", "tool_failure_count": 0}))
        manager.register(MetadataHook({"outcome": "failed", "turn_duration_ms": 12.0}))

        result = await manager.run_after_turn(object(), 12.0, 3.0)

        assert result == {"outcome": "failed", "tool_failure_count": 0, "turn_duration_ms": 12.0}

    asyncio.run(run())


def test_after_turn_hook_error_does_not_block_later_hook():
    """验证终态 Hook 异常不会阻断后续 Hook。"""

    class FailingHook(AgentHook):
        """模拟终态 Hook 异常。"""

        async def after_turn(self, ctx, turn_duration_ms, session_lock_wait_ms):
            """抛出模拟异常。"""
            del ctx, turn_duration_ms, session_lock_wait_ms
            raise RuntimeError("hook failed")

    class RecordingHook(AgentHook):
        """记录异常后的终态调用。"""

        async def after_turn(self, ctx, turn_duration_ms, session_lock_wait_ms):
            """返回后续 Hook 字段。"""
            del ctx, turn_duration_ms, session_lock_wait_ms
            return {"outcome": "completed"}

    async def run() -> None:
        manager = HookManager()
        manager.register(FailingHook())
        manager.register(RecordingHook())

        assert await manager.run_after_turn(object(), 12.0, 3.0) == {"outcome": "completed"}

    asyncio.run(run())


def test_after_turn_ignores_non_mapping_result():
    """验证终态 Hook 的非法结果不会进入监控字段。"""

    class InvalidHook(AgentHook):
        """返回非法终态结果。"""

        async def after_turn(self, ctx, turn_duration_ms, session_lock_wait_ms):
            """返回非 Mapping 结果。"""
            del ctx, turn_duration_ms, session_lock_wait_ms
            return ["invalid"]

    class ValidHook(AgentHook):
        """返回合法终态结果。"""

        async def after_turn(self, ctx, turn_duration_ms, session_lock_wait_ms):
            """返回合法监控字段。"""
            del ctx, turn_duration_ms, session_lock_wait_ms
            return {"outcome": "completed"}

    async def run() -> None:
        manager = HookManager()
        manager.register(InvalidHook())
        manager.register(ValidHook())

        assert await manager.run_after_turn(object(), 12.0, 3.0) == {"outcome": "completed"}

    asyncio.run(run())


def test_turn_monitor_returns_completed_without_tool_failures():
    """验证正常会话产生完成态监控字段。"""

    async def run() -> None:
        ctx = TurnContext(inbound=InboundMessage.new("hello", "demo", "user-1", "cli"))

        result = await TurnMonitorHook().after_turn(ctx, 12.0, 3.0)

        assert result == {
            "outcome": "completed",
            "turn_duration_ms": 12.0,
            "session_lock_wait_ms": 3.0,
            "tool_failure_count": 0,
        }

    asyncio.run(run())


def test_turn_monitor_prioritizes_build_rejection_over_error():
    """验证 BUILD 拒绝优先于一般失败。"""

    async def run() -> None:
        ctx = TurnContext(inbound=InboundMessage.new("hello", "demo", "user-1", "cli"))
        ctx.error = "上下文过大"
        ctx.trace.append(StateTrace(ctx.turn_id, "demo", "BUILD", 1.0, "rejected", ctx.error))

        result = await TurnMonitorHook().after_turn(ctx, 12.0, 3.0)

        assert result["outcome"] == "rejected"

    asyncio.run(run())


def test_turn_monitor_returns_failed_for_runtime_error():
    """验证普通异常产生失败态。"""

    async def run() -> None:
        ctx = TurnContext(inbound=InboundMessage.new("hello", "demo", "user-1", "cli"))
        ctx.error = "模型请求失败"

        result = await TurnMonitorHook().after_turn(ctx, 12.0, 3.0)

        assert result["outcome"] == "failed"

    asyncio.run(run())


def test_turn_monitor_counts_failed_tool_records():
    """验证终态监控只统计失败工具数量。"""

    async def run() -> None:
        ctx = TurnContext(inbound=InboundMessage.new("hello", "demo", "user-1", "cli"))
        ctx.tool_calls = [
            {"tool_name": "now", "error": None},
            {"tool_name": "exec", "error": "拒绝执行"},
        ]

        result = await TurnMonitorHook().after_turn(ctx, 12.0, 3.0)

        assert result["tool_failure_count"] == 1

    asyncio.run(run())


def test_before_tool_call_returns_first_block_reason():
    """验证工具前 Hook 返回首个阻断原因。"""

    class DecisionHook(AgentHook):
        """按配置返回阻断原因。"""

        def __init__(self, reason, calls):
            """保存阻断原因和调用记录。"""
            self.reason = reason
            self.calls = calls

        async def before_tool_call(self, call, channel_adapter, auto_approve_tools):
            """记录调用并返回阻断原因。"""
            del channel_adapter, auto_approve_tools
            self.calls.append(call.name)
            return self.reason

    async def run() -> None:
        calls = []
        manager = HookManager()
        manager.register(DecisionHook(None, calls))
        manager.register(DecisionHook("禁止工具", calls))
        manager.register(DecisionHook("不应执行", calls))

        reason = await manager.run_before_tool_call(
            ToolCall("call-1", "echo", {}),
            SilentChannelAdapter(),
            False,
        )

        assert reason == "禁止工具"
        assert calls == ["echo", "echo"]

    asyncio.run(run())


def test_after_tool_call_pipes_record_in_registration_order():
    """验证工具结果按注册顺序经过 Hook 管道。"""

    class AppendHook(AgentHook):
        """向工具结果追加标记。"""

        def __init__(self, suffix):
            """保存待追加文本。"""
            self.suffix = suffix

        async def after_tool_call(self, call, record):
            """返回追加内容后的记录。"""
            del call
            return {"content": record["content"] + self.suffix}

    async def run() -> None:
        call = ToolCall("call-1", "echo", {})
        manager = HookManager()
        manager.register(AppendHook("-first"))
        manager.register(AppendHook("-second"))
        source = {"content": "result", "error": None}

        record = await manager.run_after_tool_call(call, source)

        assert record == {"content": "result-first-second", "error": None}

    asyncio.run(run())


def test_compact_hooks_isolate_errors_and_continue():
    """验证压缩 Hook 异常不会阻断后续 Hook。"""

    class FailingHook(AgentHook):
        """模拟压缩 Hook 异常。"""

        async def before_compact(self, ctx):
            """抛出模拟异常。"""
            del ctx
            raise RuntimeError("hook failed")

    class RecordingHook(AgentHook):
        """记录异常后的调用。"""

        def __init__(self, calls):
            """保存调用记录。"""
            self.calls = calls

        async def before_compact(self, ctx):
            """记录压缩前调用。"""
            del ctx
            self.calls.append("called")

    async def run() -> None:
        calls = []
        manager = HookManager()
        manager.register(FailingHook())
        manager.register(RecordingHook(calls))

        await manager.run_before_compact(object())

        assert calls == ["called"]

    asyncio.run(run())

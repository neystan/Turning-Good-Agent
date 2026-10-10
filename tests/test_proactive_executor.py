from __future__ import annotations

import asyncio
import importlib


executor_module = importlib.import_module("Turning-Good-Agent.proactive.executor")
llm_types_module = importlib.import_module("Turning-Good-Agent.llm.types")
agent_loop_module = importlib.import_module("Turning-Good-Agent.runtime.agent_loop")
registry_module = importlib.import_module("Turning-Good-Agent.tools.registry")

ProactiveExecutor = executor_module.ProactiveExecutor
AgentLoopResult = agent_loop_module.AgentLoopResult
LLMUsage = llm_types_module.LLMUsage
ToolRegistry = registry_module.ToolRegistry


class RecordingLoop:
    def __init__(self, *, tool_error: str | None = None) -> None:
        self.messages: list[dict[str, object]] | None = None
        self.auto_approve_tools: bool | None = None
        self.allowed_tool_names: frozenset[str] | None = None
        self.approval_reason: str | None = None
        self.llm_override: object | None = None
        self.streaming_enabled: bool | None = None
        self.tools = ToolRegistry()
        self._tool_error = tool_error

    async def run(
        self,
        messages,
        channel_adapter,
        auto_approve_tools,
        *,
        allowed_tool_names=None,
        llm_override=None,
        streaming_enabled=None,
    ):
        self.messages = messages
        self.auto_approve_tools = auto_approve_tools
        self.allowed_tool_names = allowed_tool_names
        self.llm_override = llm_override
        self.streaming_enabled = streaming_enabled
        self.approval_reason = await channel_adapter.request_tool_approval(object())
        return AgentLoopResult(
            final_content="后台任务完成。",
            messages=messages,
            usage=LLMUsage(input_tokens=3, output_tokens=2, total_tokens=5),
            tool_calls=[] if self._tool_error is None else [{"error": self._tool_error}],
        )


class ApprovalTool:
    description = "审批工具"
    input_schema = {"type": "object", "properties": {}}
    parallel_safe = False

    def __init__(self, name: str, *, approval_required: bool = False) -> None:
        self.name = name
        self.approval_required = approval_required


def test_executor_intersects_requested_tools_with_background_safe_tools() -> None:
    async def run() -> None:
        loop = RecordingLoop()
        for tool in (
            ApprovalTool("safe"),
            ApprovalTool("configured"),
            ApprovalTool("metadata", approval_required=True),
            ApprovalTool("run_dream"),
        ):
            loop.tools.register(tool)
        executor = ProactiveExecutor(loop, approval_required_tool_names=frozenset({"configured"}))

        await executor.run(
            "cron",
            "执行后台任务。",
            allowed_tool_names=frozenset({"safe", "configured", "metadata", "run_dream"}),
        )

        assert loop.allowed_tool_names == frozenset({"safe"})
        assert loop.auto_approve_tools is False
        assert loop.approval_reason is not None

    asyncio.run(run())


def test_executor_excludes_live_proactive_tools_from_the_requested_catalog() -> None:
    """Would fail if a newly installed proactive Tool entered a background Cron loop."""

    async def run() -> None:
        loop = RecordingLoop()
        loop.tools.register(ApprovalTool("weather"))
        loop.tools.register(ApprovalTool("installed_later"))
        executor = ProactiveExecutor(
            loop,
            proactive_tool_names=lambda: frozenset({"installed_later"}),
        )

        await executor.run(
            "cron",
            "执行后台任务。",
            allowed_tool_names=frozenset({"weather", "installed_later"}),
        )

        assert loop.allowed_tool_names == frozenset({"weather"})

    asyncio.run(run())


def test_executor_defaults_to_an_empty_background_allowlist() -> None:
    async def run() -> None:
        loop = RecordingLoop()
        loop.tools.register(ApprovalTool("safe"))
        loop.tools.register(ApprovalTool("run_dream"))
        executor = ProactiveExecutor(loop)

        result = await executor.run("cron", "整理本周 AI 大事件。")

        assert loop.allowed_tool_names == frozenset()
        assert result.success is True
        assert result.content == "后台任务完成。"
        assert result.usage.total_tokens == 5
        assert not hasattr(result, "tool_calls")

    asyncio.run(run())


def test_executor_keeps_empty_allowlist_for_structured_review_jobs() -> None:
    async def run() -> None:
        loop = RecordingLoop()
        loop.tools.register(ApprovalTool("safe"))
        executor = ProactiveExecutor(loop)

        await executor.run("dream", "仅提取结构化证据。", allowed_tool_names=frozenset())

        assert loop.allowed_tool_names == frozenset()

    asyncio.run(run())


def test_executor_uses_review_provider_through_the_existing_agent_loop() -> None:
    async def run() -> None:
        loop = RecordingLoop()
        review_provider = object()
        executor = ProactiveExecutor(loop, review_llm=review_provider)

        result = await executor.run(
            "breakbeat",
            "仅提取结构化事项。",
            allowed_tool_names=frozenset(),
            use_review_model=True,
        )

        assert result.success is True
        assert loop.llm_override is review_provider
        assert loop.streaming_enabled is False

    asyncio.run(run())


def test_executor_reports_tool_errors_without_exposing_records() -> None:
    async def run() -> None:
        result = await ProactiveExecutor(RecordingLoop(tool_error="blocked")).run("cron", "任务。")

        assert result.success is False
        assert result.error == "后台 Tool 调用失败"
        assert not hasattr(result, "tool_calls")

    asyncio.run(run())


def test_executor_propagates_cancellation_without_execution_audit() -> None:
    class CancelledLoop(RecordingLoop):
        async def run(self, *args, **kwargs):
            del args, kwargs
            raise asyncio.CancelledError

    async def run() -> None:
        try:
            await ProactiveExecutor(CancelledLoop()).run("dream", "运行 Dream。", allowed_tool_names=frozenset())
        except asyncio.CancelledError:
            pass
        else:
            raise AssertionError("取消必须继续向调用方传播")

    asyncio.run(run())

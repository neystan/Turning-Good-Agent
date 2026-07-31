from __future__ import annotations

import asyncio
import importlib
from collections.abc import Sequence
from typing import Any

import pytest


agent_loop_module = importlib.import_module("Turning-Good-Agent.runtime.agent_loop")
settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
llm_types_module = importlib.import_module("Turning-Good-Agent.llm.types")
attachment_module = importlib.import_module("Turning-Good-Agent.tools.context_attachment")
token_counter_module = importlib.import_module("Turning-Good-Agent.sessions.token_counter")

AgentLoop = agent_loop_module.AgentLoop
RuntimeSettings = settings_module.RuntimeSettings
ContextAttachment = attachment_module.ContextAttachment
LLMResponse = llm_types_module.LLMResponse
LLMChunk = llm_types_module.LLMChunk
LLMUsage = llm_types_module.LLMUsage
ToolCall = llm_types_module.ToolCall
count_content_tokens = token_counter_module.count_content_tokens


USAGE = LLMUsage(input_tokens=1, output_tokens=1, total_tokens=2)


class EmptyRegistry:
    def openai_tools(self) -> list[dict[str, Any]]:
        return []


class RecordingLLM:
    def __init__(self, responses: Sequence[LLMResponse]) -> None:
        self.responses = list(responses)
        self.inputs: list[list[dict[str, Any]]] = []

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        del tools
        self.inputs.append([dict(message) for message in messages])
        return self.responses.pop(0)


class ControlledAdapter:
    def __init__(self, guidance_by_read: dict[int, list[str]] | None = None) -> None:
        self.guidance_by_read = guidance_by_read or {}
        self.guidance_reads = 0
        self.deltas: list[str] = []
        self.stopped = False

    async def on_delta(self, text: str) -> None:
        self.deltas.append(text)

    async def consume_guidance(self) -> list[str]:
        self.guidance_reads += 1
        return self.guidance_by_read.get(self.guidance_reads, [])

    def is_stop_requested(self) -> bool:
        return self.stopped


class FixedToolRunner:
    def __init__(self, records: Sequence[dict[str, Any]]) -> None:
        self.records = list(records)

    async def execute_calls(
        self,
        calls: list[ToolCall],
        channel_adapter: ControlledAdapter,
        auto_approve_tools: bool,
    ) -> list[dict[str, Any]]:
        del calls, channel_adapter, auto_approve_tools
        return [dict(record) for record in self.records]


class StopDuringFinalStreamLLM:
    def __init__(self, adapter: ControlledAdapter) -> None:
        self.adapter = adapter

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Any:
        del messages, tools
        yield LLMChunk(delta_text="partial answer")
        self.adapter.stopped = True
        yield LLMChunk(usage=USAGE, finish_reason="stop")


class ToolLimitStreamLLM:
    def __init__(self, summary: str, protocol_error: str | None = None) -> None:
        self.summary = summary
        self.protocol_error = protocol_error
        self.call_count = 0

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> Any:
        del messages, tools
        self.call_count += 1
        if self.call_count == 1:
            yield LLMChunk(tool_calls=[ToolCall("call-1", "demo", {})], finish_reason="tool_calls")
            yield LLMChunk(usage=USAGE)
            return
        yield LLMChunk(delta_text=self.summary)
        if self.protocol_error:
            yield LLMChunk(protocol_error=self.protocol_error)
        yield LLMChunk(usage=USAGE, finish_reason="stop")


class EmptyResponseLLM:
    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        del messages, tools
        return LLMResponse("", [], USAGE)


def test_guidance_is_appended_after_tool_results() -> None:
    async def run() -> None:
        llm = RecordingLLM(
            [
                LLMResponse("", [ToolCall("call-1", "demo", {})], USAGE),
                LLMResponse("done", [], USAGE),
            ]
        )
        adapter = ControlledAdapter({2: ["change direction"]})
        loop = AgentLoop(llm, EmptyRegistry(), RuntimeSettings(max_tool_rounds=2))
        loop.tool_call_runner = FixedToolRunner(
            [{"tool_name": "demo", "args": {}, "content": "ok", "duration_ms": 0.0, "error": None}]
        )

        await loop.run([{"role": "user", "content": "start"}], adapter)

        assert [message["role"] for message in llm.inputs[1]] == [
            "user",
            "assistant",
            "tool",
            "user",
        ]

    asyncio.run(run())


def test_context_attachments_follow_all_tool_results() -> None:
    async def run() -> None:
        llm = RecordingLLM(
            [
                LLMResponse(
                    "",
                    [ToolCall("call-1", "load_context", {}), ToolCall("call-2", "demo", {})],
                    USAGE,
                ),
                LLMResponse("done", [], USAGE),
            ]
        )
        attachment_content = "attached context"
        attachment = ContextAttachment(
            source="mcp:test",
            messages=[{"role": "user", "content": attachment_content}],
            token_count=count_content_tokens(attachment_content),
        )
        loop = AgentLoop(llm, EmptyRegistry(), RuntimeSettings(max_tool_rounds=2))
        loop.tool_call_runner = FixedToolRunner(
            [
                {
                    "tool_name": "load_context",
                    "args": {},
                    "content": "loaded",
                    "duration_ms": 0.0,
                    "error": None,
                    "context_attachment": attachment,
                },
                {
                    "tool_name": "demo",
                    "args": {},
                    "content": "ok",
                    "duration_ms": 0.0,
                    "error": None,
                },
            ]
        )

        await loop.run([{"role": "user", "content": "start"}], ControlledAdapter())

        assert [message["role"] for message in llm.inputs[1]] == [
            "user",
            "assistant",
            "tool",
            "tool",
            "user",
        ]
        assert [message.get("tool_call_id") for message in llm.inputs[1]] == [
            None,
            None,
            "call-1",
            "call-2",
            None,
        ]

    asyncio.run(run())


def test_stop_during_final_stream_returns_cancelled_result() -> None:
    async def run() -> None:
        adapter = ControlledAdapter()
        loop = AgentLoop(
            StopDuringFinalStreamLLM(adapter),
            EmptyRegistry(),
            RuntimeSettings(),
            streaming_enabled=True,
        )

        result = await loop.run([{"role": "user", "content": "start"}], adapter)

        assert result.cancelled is True
        assert result.final_content == "partial answer"

    asyncio.run(run())


def test_tool_limit_summary_streams_once_after_validation() -> None:
    async def run() -> None:
        adapter = ControlledAdapter()
        loop = AgentLoop(
            ToolLimitStreamLLM("summary"),
            EmptyRegistry(),
            RuntimeSettings(max_tool_rounds=1),
            streaming_enabled=True,
        )
        loop.tool_call_runner = FixedToolRunner(
            [{"tool_name": "demo", "args": {}, "content": "ok", "duration_ms": 0.0, "error": None}]
        )

        result = await loop.run([{"role": "user", "content": "start"}], adapter)

        assert result.final_content == "summary"
        assert adapter.deltas == ["summary"]

    asyncio.run(run())


def test_invalid_tool_limit_summary_is_not_streamed() -> None:
    async def run() -> None:
        adapter = ControlledAdapter()
        loop = AgentLoop(
            ToolLimitStreamLLM("raw protocol text", protocol_error="invalid protocol"),
            EmptyRegistry(),
            RuntimeSettings(max_tool_rounds=1),
            streaming_enabled=True,
        )
        loop.tool_call_runner = FixedToolRunner(
            [{"tool_name": "demo", "args": {}, "content": "ok", "duration_ms": 0.0, "error": None}]
        )

        result = await loop.run([{"role": "user", "content": "start"}], adapter)

        expected = (
            "工具调用轮数已达到上限，已完成 1 次工具调用（demo）。"
            "模型未能生成最终总结，可使用 /tools 查看本轮完整工具结果。"
        )
        assert result.final_content == expected
        assert adapter.deltas == [expected]

    asyncio.run(run())


def test_empty_final_response_is_rejected() -> None:
    async def run() -> None:
        loop = AgentLoop(EmptyResponseLLM(), EmptyRegistry(), RuntimeSettings())

        with pytest.raises(RuntimeError):
            await loop.run([{"role": "user", "content": "start"}])

    asyncio.run(run())

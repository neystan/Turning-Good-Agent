import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from ..channels.base import ChannelAdapter, SilentChannelAdapter
from ..config.settings import RuntimeSettings
from ..context.tool_round_limit_prompt import TOOL_ROUND_LIMIT_SUMMARY_PROMPT
from ..hooks.manager import HookManager
from ..llm.client import LLMProvider
from ..llm.types import LLMResponse, LLMUsage, ToolCall
from ..tools.executor import ToolExecutor
from ..tools.registry import ToolRegistry

@dataclass(slots=True)
class AgentLoopResult:
    """保存 AgentLoop 的最终回复和工具记录。"""

    final_content: str
    messages: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: LLMUsage | None = None


class AgentLoop:
    """执行 LLM 对话与工具调用循环。"""

    def __init__(
        self,
        llm: LLMProvider,
        tools: ToolRegistry,
        runtime: RuntimeSettings,
        streaming_enabled: bool = False,
        hooks: HookManager | None = None,
    ) -> None:
        """初始化模型、工具执行器和 Hook 管理器。"""
        self.llm = llm
        self.tools = tools
        self.runtime = runtime
        self.executor = ToolExecutor(tools)
        self.streaming_enabled = streaming_enabled
        self.hooks = hooks or HookManager()

    async def run(
        self,
        messages: list[dict[str, Any]],
        channel_adapter: ChannelAdapter | None = None,
        auto_approve_tools: bool = False,
    ) -> AgentLoopResult:
        """运行模型调用和工具循环直到得到最终文本。"""
        channel_adapter = channel_adapter or SilentChannelAdapter()
        working = list(messages)
        tool_records: list[dict[str, Any]] = []
        usage = LLMUsage()
        for _ in range(self.runtime.max_tool_rounds):
            response = await self._complete(working, self.tools.openai_tools(), channel_adapter)
            usage = usage.add(response.usage)
            if not response.tool_calls:
                return AgentLoopResult(response.content, working, tool_records, usage)
            calls = response.tool_calls[: self.runtime.max_tool_calls_per_round]
            working.append(self._assistant_tool_message(response.content, calls))
            records = await self._execute_tool_calls(calls, channel_adapter, auto_approve_tools)
            for call, record in zip(calls, records, strict=True):
                tool_records.append(record)
                working.append(self._tool_result_message(call, record))
        working.append({"role": "system", "content": TOOL_ROUND_LIMIT_SUMMARY_PROMPT})
        summary = await self._complete(working, [], channel_adapter)
        usage = usage.add(summary.usage)
        content = summary.content.strip()
        if summary.protocol_error or summary.tool_calls or not content:
            content = self._tool_round_limit_fallback(tool_records)
        await channel_adapter.on_delta(content)
        return AgentLoopResult(content, working, tool_records, usage)

    async def _execute_tool_calls(
        self,
        calls: list[ToolCall],
        channel_adapter: ChannelAdapter,
        auto_approve_tools: bool,
    ) -> list[dict[str, Any]]:
        """按安全边界执行连续工具批次。"""
        records: list[dict[str, Any]] = []
        parallel_batch: list[ToolCall] = []
        for call in calls:
            if self._is_parallel_safe(call):
                parallel_batch.append(call)
                continue
            records.extend(await self._execute_parallel_batch(parallel_batch, channel_adapter, auto_approve_tools))
            parallel_batch.clear()
            records.append(await self._execute_tool_call(call, channel_adapter, auto_approve_tools))
        records.extend(await self._execute_parallel_batch(parallel_batch, channel_adapter, auto_approve_tools))
        return records

    def _is_parallel_safe(self, call: ToolCall) -> bool:
        """判断工具是否允许加入当前并行批次。"""
        if not self.runtime.parallel_tool_calls_enabled:
            return False
        tool, _args, validation_error = self.tools.prepare_call(call.name, call.args)
        return tool is not None and validation_error is None and bool(getattr(tool, "parallel_safe", False))

    async def _execute_parallel_batch(
        self,
        calls: list[ToolCall],
        channel_adapter: ChannelAdapter,
        auto_approve_tools: bool,
    ) -> list[dict[str, Any]]:
        """在并发上限内执行同一安全批次。"""
        if not calls:
            return []
        if len(calls) == 1:
            return [await self._execute_tool_call(calls[0], channel_adapter, auto_approve_tools)]
        semaphore = asyncio.Semaphore(max(1, self.runtime.max_parallel_tool_calls))

        async def execute(call: ToolCall) -> dict[str, Any]:
            """在并发槽位内执行单个工具。"""
            async with semaphore:
                return await self._execute_tool_call(call, channel_adapter, auto_approve_tools)

        return list(await asyncio.gather(*(execute(call) for call in calls)))

    async def _execute_tool_call(
        self,
        call: ToolCall,
        channel_adapter: ChannelAdapter,
        auto_approve_tools: bool,
    ) -> dict[str, Any]:
        """完成单次工具校验、审批、执行和结果处理。"""
        tool, args, validation_error = self.tools.prepare_call(call.name, call.args)
        normalized_call = ToolCall(call.id, call.name, args)
        if validation_error:
            record = self._error_record(
                normalized_call,
                f"工具 {call.name} 参数错误：{validation_error}",
                validation_error,
            )
            return await self._finalize_tool_call(normalized_call, record)

        assert tool is not None
        security_error = self.executor.precheck(tool, args)
        if security_error:
            record = self._error_record(
                normalized_call,
                f"工具 {call.name} 安全检查失败：{security_error}",
                security_error,
            )
            return await self._finalize_tool_call(normalized_call, record)

        block_reason = await self.hooks.run_before_tool_call(
            normalized_call,
            channel_adapter,
            auto_approve_tools,
        )
        if block_reason:
            record = self._error_record(
                normalized_call,
                f"工具 {call.name} 被 Hook 阻止：{block_reason}",
                block_reason,
            )
            return await self._finalize_tool_call(normalized_call, record)

        await self.hooks.run_tool_started(normalized_call, channel_adapter)
        record = await self.executor.run(normalized_call.name, normalized_call.args)
        return await self._finalize_tool_call(normalized_call, record)

    async def _finalize_tool_call(self, call: ToolCall, record: dict[str, Any]) -> dict[str, Any]:
        """补齐工具调用 ID 并执行结果 Hook。"""
        record["tool_call_id"] = call.id
        return await self.hooks.run_after_tool_call(call, record)

    @staticmethod
    def _assistant_tool_message(content: str, calls: list[ToolCall]) -> dict[str, Any]:
        """构造包含工具请求的 assistant 消息。"""
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.args, ensure_ascii=False),
                    },
                }
                for call in calls
            ],
        }

    @staticmethod
    def _tool_result_message(call: ToolCall, record: dict[str, Any]) -> dict[str, Any]:
        """构造注入下一轮模型调用的工具结果消息。"""
        return {
            "role": "tool",
            "tool_call_id": call.id,
            "name": call.name,
            "content": record["content"],
        }

    @staticmethod
    def _error_record(call: ToolCall, content: str, error: str) -> dict[str, Any]:
        """构造兼容现有落盘格式的工具错误记录。"""
        return {
            "tool_name": call.name,
            "args": dict(call.args),
            "content": content,
            "duration_ms": 0.0,
            "error": error,
        }

    @staticmethod
    def _tool_round_limit_fallback(tool_records: list[dict[str, Any]]) -> str:
        """返回工具上限后的确定性降级结果。"""
        tool_names = ", ".join(record["tool_name"] for record in tool_records) or "无"
        return (
            f"工具调用轮数已达到上限，已完成 {len(tool_records)} 次工具调用（{tool_names}）。"
            "模型未能生成最终总结，可使用 /tools 查看本轮完整工具结果。"
        )

    async def _complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        channel_adapter: ChannelAdapter,
    ) -> LLMResponse:
        """按配置选择非流式或流式模型调用。"""
        if not self.streaming_enabled or not hasattr(self.llm, "stream"):
            return await self.llm.complete(messages, tools)

        content_parts: list[str] = []
        tool_calls = []
        usage = LLMUsage()
        protocol_error: str | None = None
        async for chunk in self.llm.stream(messages, tools):
            usage = usage.add(chunk.usage)
            if chunk.delta_text:
                content_parts.append(chunk.delta_text)
                await channel_adapter.on_delta(chunk.delta_text)
            if chunk.tool_calls:
                tool_calls = chunk.tool_calls
            if chunk.protocol_error:
                protocol_error = chunk.protocol_error
        if usage.total_tokens <= 0:
            raise RuntimeError("流式 LLM 响应缺少 usage，无法保存本轮结果。")
        return LLMResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            usage=usage,
            protocol_error=protocol_error,
        )

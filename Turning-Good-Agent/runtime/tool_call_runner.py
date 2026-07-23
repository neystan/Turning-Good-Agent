import asyncio
from dataclasses import dataclass
from typing import Any

from ..channels.base import ChannelAdapter
from ..config.settings import RuntimeSettings
from ..hooks.manager import HookManager
from ..llm.types import ToolCall
from ..tools.base import BaseTool
from ..tools.executor import ToolExecutor
from ..tools.registry import ToolRegistry


@dataclass(slots=True)
class PreparedToolCall:
    """保存已规范化的单次工具调用。"""

    call: ToolCall
    tool: BaseTool | None
    validation_error: str | None


class ToolCallRunner:
    """执行一批经过模型请求的工具调用。"""

    def __init__(
        self,
        tools: ToolRegistry,
        executor: ToolExecutor,
        hooks: HookManager,
        runtime: RuntimeSettings,
    ) -> None:
        """保存工具执行所需的边界依赖。"""
        self.tools = tools
        self.executor = executor
        self.hooks = hooks
        self.runtime = runtime

    async def execute_calls(
        self,
        calls: list[ToolCall],
        channel_adapter: ChannelAdapter,
        auto_approve_tools: bool,
    ) -> list[dict[str, Any]]:
        """按安全边界执行模型给出的工具批次。"""
        records: list[dict[str, Any]] = []
        parallel_batch: list[PreparedToolCall] = []
        for call in calls:
            prepared = self._prepare_call(call)
            if self._is_parallel_safe(prepared):
                parallel_batch.append(prepared)
                continue
            records.extend(await self._execute_parallel_batch(parallel_batch, channel_adapter, auto_approve_tools))
            parallel_batch.clear()
            records.append(await self._execute_prepared(prepared, channel_adapter, auto_approve_tools))
        records.extend(await self._execute_parallel_batch(parallel_batch, channel_adapter, auto_approve_tools))
        return records

    def _prepare_call(self, call: ToolCall) -> PreparedToolCall:
        """规范化参数并保留校验错误。"""
        tool, args, validation_error = self.tools.prepare_call(call.name, call.args)
        return PreparedToolCall(ToolCall(call.id, call.name, args), tool, validation_error)

    def _is_parallel_safe(self, prepared: PreparedToolCall) -> bool:
        """判断已准备工具是否可加入并行批次。"""
        return (
            self.runtime.parallel_tool_calls_enabled
            and prepared.tool is not None
            and prepared.validation_error is None
            and bool(getattr(prepared.tool, "parallel_safe", False))
        )

    async def _execute_parallel_batch(
        self,
        calls: list[PreparedToolCall],
        channel_adapter: ChannelAdapter,
        auto_approve_tools: bool,
    ) -> list[dict[str, Any]]:
        """在并发上限内执行连续的安全工具。"""
        if not calls:
            return []
        if len(calls) == 1:
            return [await self._execute_prepared(calls[0], channel_adapter, auto_approve_tools)]
        semaphore = asyncio.Semaphore(max(1, self.runtime.max_parallel_tool_calls))

        async def execute(prepared: PreparedToolCall) -> dict[str, Any]:
            """在单个并发槽位执行工具。"""
            async with semaphore:
                return await self._execute_prepared(prepared, channel_adapter, auto_approve_tools)

        return list(await asyncio.gather(*(execute(prepared) for prepared in calls)))

    async def _execute_prepared(
        self,
        prepared: PreparedToolCall,
        channel_adapter: ChannelAdapter,
        auto_approve_tools: bool,
    ) -> dict[str, Any]:
        """执行单个已规范化调用的审批和结果管道。"""
        call = prepared.call
        if prepared.validation_error:
            return await self._finalize(
                call,
                self._error_record(call, f"工具 {call.name} 参数错误：{prepared.validation_error}", prepared.validation_error),
            )
        assert prepared.tool is not None
        security_error = self.executor.precheck(prepared.tool, call.args)
        if security_error:
            return await self._finalize(
                call,
                self._error_record(call, f"工具 {call.name} 安全检查失败：{security_error}", security_error),
            )
        block_reason = await self.hooks.run_before_tool_call(call, channel_adapter, auto_approve_tools)
        if block_reason:
            return await self._finalize(
                call,
                self._error_record(call, f"工具 {call.name} 被 Hook 阻止：{block_reason}", block_reason),
            )
        await self.hooks.run_tool_started(call, channel_adapter)
        record = await self.executor.run(prepared.tool, call.args)
        return await self._finalize(call, record)

    async def _finalize(self, call: ToolCall, record: dict[str, Any]) -> dict[str, Any]:
        """补齐调用 ID 并运行工具结果 Hook。"""
        record["tool_call_id"] = call.id
        return await self.hooks.run_after_tool_call(call, record)

    @staticmethod
    def _error_record(call: ToolCall, content: str, error: str) -> dict[str, Any]:
        """构造兼容持久化格式的工具错误记录。"""
        return {
            "tool_name": call.name,
            "args": dict(call.args),
            "content": content,
            "duration_ms": 0.0,
            "error": error,
        }

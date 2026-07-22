from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..channels.output import ChannelOutput
from ..llm.types import ToolCall
from .base import AgentHook

if TYPE_CHECKING:
    from ..runtime.turn_context import TurnContext


class ChannelStatusHook(AgentHook):
    """向当前 Channel 输出工具与压缩状态。"""

    def __init__(self) -> None:
        """保存已开始工具对应的输出对象。"""
        self._tool_outputs: dict[str, ChannelOutput] = {}

    async def on_tool_started(self, call: ToolCall, output: ChannelOutput) -> None:
        """提示当前 Channel 即将执行的工具。"""
        self._tool_outputs[call.id] = output
        await output.on_tool_started(call.id, call.name)

    async def after_tool_call(self, call: ToolCall, record: Mapping[str, Any]) -> dict[str, Any]:
        """提示当前 Channel 工具已经结束。"""
        output = self._tool_outputs.pop(call.id, None)
        if output is not None:
            await output.on_tool_finished(call.id, call.name, bool(record.get("error")))
        return dict(record)

    async def before_compact(self, ctx: "TurnContext") -> None:
        """提示当前 Channel 开始压缩上下文。"""
        await ctx.output.on_status("正在压缩会话上下文...")

    async def after_compact(self, ctx: "TurnContext") -> None:
        """提示当前 Channel 压缩完成。"""
        stats = ctx.compact_stats
        await ctx.output.on_status(
            "压缩完成："
            f"已压缩 {stats['compacted_message_count']} 条消息，"
            f"压缩 {stats['compacted_token_count']} tokens，"
            f"保留最近 {stats['raw_window_token_count']} tokens 原文。"
        )

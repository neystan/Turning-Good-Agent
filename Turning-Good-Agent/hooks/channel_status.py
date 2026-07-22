from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..channels.base import ChannelAdapter
from ..llm.types import ToolCall
from .base import AgentHook

if TYPE_CHECKING:
    from ..runtime.turn_context import TurnContext


class ChannelStatusHook(AgentHook):
    """向当前 Channel 输出工具与压缩状态。"""

    def __init__(self) -> None:
        """保存已开始工具对应的输出对象。"""
        self._tool_adapters: dict[str, ChannelAdapter] = {}

    async def on_tool_started(self, call: ToolCall, channel_adapter: ChannelAdapter) -> None:
        """提示当前 Channel 即将执行的工具。"""
        self._tool_adapters[call.id] = channel_adapter
        await channel_adapter.on_tool_started(call.id, call.name)

    async def after_tool_call(self, call: ToolCall, record: Mapping[str, Any]) -> dict[str, Any]:
        """提示当前 Channel 工具已经结束。"""
        channel_adapter = self._tool_adapters.pop(call.id, None)
        if channel_adapter is not None:
            await channel_adapter.on_tool_finished(call.id, call.name, bool(record.get("error")))
        return dict(record)

    async def before_compact(self, ctx: "TurnContext") -> None:
        """提示当前 Channel 开始压缩上下文。"""
        await ctx.channel_adapter.on_status("正在压缩会话上下文...")

    async def after_compact(self, ctx: "TurnContext") -> None:
        """提示当前 Channel 压缩完成。"""
        stats = ctx.compact_stats
        await ctx.channel_adapter.on_status(
            "压缩完成："
            f"已压缩 {stats['compacted_message_count']} 条消息，"
            f"压缩 {stats['compacted_token_count']} tokens，"
            f"保留最近 {stats['raw_window_token_count']} tokens 原文。"
        )

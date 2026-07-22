from typing import TYPE_CHECKING

from ..channels.output import ChannelOutput
from ..llm.types import ToolCall
from .base import AgentHook

if TYPE_CHECKING:
    from ..runtime.turn_context import TurnContext


class ChannelStatusHook(AgentHook):
    """向当前 Channel 输出工具与压缩状态。"""

    async def on_tool_started(self, call: ToolCall, output: ChannelOutput) -> None:
        """提示当前 Channel 即将执行的工具。"""
        await output.on_status(f"正在调用工具：{call.name}")

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

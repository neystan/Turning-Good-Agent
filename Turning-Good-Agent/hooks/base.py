from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..channels.base import ChannelAdapter
from ..llm.types import ToolCall

if TYPE_CHECKING:
    from ..runtime.turn_context import TurnContext


class AgentHook:
    """定义工具和压缩 Hook 接口。"""

    async def before_tool_call(
        self,
        call: ToolCall,
        channel_adapter: ChannelAdapter,
        auto_approve_tools: bool,
    ) -> str | None:
        """在工具执行前返回可选阻断原因。"""
        del call, channel_adapter, auto_approve_tools
        return None

    async def on_tool_started(self, call: ToolCall, channel_adapter: ChannelAdapter) -> None:
        """在工具即将执行时发送通知。"""
        del call, channel_adapter

    async def after_tool_call(
        self,
        call: ToolCall,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        """在工具处理完成后返回模型可见记录。"""
        return dict(record)

    async def before_compact(self, ctx: "TurnContext") -> None:
        """在真实压缩开始前执行扩展。"""

    async def after_compact(self, ctx: "TurnContext") -> None:
        """在真实压缩完成后执行扩展。"""

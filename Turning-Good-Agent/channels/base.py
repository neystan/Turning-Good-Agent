from collections.abc import Callable
from typing import Protocol

from ..bus.messages import InboundMessage
from ..llm.types import ToolCall


class TurnControl(Protocol):
    """定义单轮运行中引导与停止控制。"""

    async def consume_guidance(self) -> list[str]:
        """取走当前安全检查点前积累的用户引导。"""

    def is_stop_requested(self) -> bool:
        """返回当前任务是否已请求协作式停止。"""


class ChannelAdapter(Protocol):
    """定义 Channel 的输出与工具审批能力。"""

    async def on_delta(self, text: str) -> None:
        """处理模型流式文本。"""

    async def on_status(self, text: str) -> None:
        """处理任务中间状态。"""

    async def on_tool_started(self, tool_call_id: str, tool_name: str) -> None:
        """处理工具开始事件。"""

    async def on_tool_finished(self, tool_call_id: str, tool_name: str, failed: bool) -> None:
        """处理工具结束事件。"""

    async def on_completed(self, content: str) -> None:
        """处理本轮成功回复。"""

    async def on_error(self, content: str) -> None:
        """处理本轮错误回复。"""

    async def request_tool_approval(self, call: ToolCall) -> str | None:
        """请求用户确认工具调用。"""

    async def consume_guidance(self) -> list[str]:
        """取走当前任务的运行中引导。"""

    def is_stop_requested(self) -> bool:
        """返回当前任务是否需要停止。"""


class SilentChannelAdapter:
    """忽略中间输出并拒绝工具审批。"""

    async def on_delta(self, text: str) -> None:
        """忽略流式文本。"""
        del text

    async def on_status(self, text: str) -> None:
        """忽略任务状态。"""
        del text

    async def on_tool_started(self, tool_call_id: str, tool_name: str) -> None:
        """忽略工具开始事件。"""
        del tool_call_id, tool_name

    async def on_tool_finished(self, tool_call_id: str, tool_name: str, failed: bool) -> None:
        """忽略工具结束事件。"""
        del tool_call_id, tool_name, failed

    async def on_completed(self, content: str) -> None:
        """忽略成功回复。"""
        del content

    async def on_error(self, content: str) -> None:
        """忽略错误回复。"""
        del content

    async def request_tool_approval(self, call: ToolCall) -> str | None:
        """拒绝当前 Channel 无法处理的审批请求。"""
        del call
        return "当前 Channel 不支持工具审批。"

    async def consume_guidance(self) -> list[str]:
        """返回空引导列表。"""
        return []

    def is_stop_requested(self) -> bool:
        """始终不请求停止。"""
        return False


AdapterFactory = Callable[[InboundMessage], ChannelAdapter]


class ChannelRouter:
    """按 Channel 创建当前轮的适配器。"""

    def __init__(self) -> None:
        """初始化 Channel 适配器工厂表。"""
        self._factories: dict[str, AdapterFactory] = {}

    def register(self, channel: str, factory: AdapterFactory) -> None:
        """注册指定 Channel 的适配器工厂。"""
        self._factories[channel] = factory

    def create(self, message: InboundMessage | str) -> ChannelAdapter:
        """创建指定 Channel 的单轮适配器。"""
        channel = message if isinstance(message, str) else message.channel
        factory = self._factories.get(channel)
        if factory is None:
            return SilentChannelAdapter()
        try:
            return factory(message)  # type: ignore[arg-type]
        except TypeError:
            return factory()  # type: ignore[call-arg]

from collections.abc import Callable
from typing import Protocol


class ChannelOutput(Protocol):
    """定义单轮 Channel 输出能力。"""

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


class SilentChannelOutput:
    """忽略中间输出的 Channel 默认实现。"""

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


OutputFactory = Callable[[], ChannelOutput]


class ChannelOutputRouter:
    """按 Channel 创建当前轮的输出实现。"""

    def __init__(self) -> None:
        """初始化 Channel 输出工厂表。"""
        self._factories: dict[str, OutputFactory] = {}

    def register(self, channel: str, factory: OutputFactory) -> None:
        """注册指定 Channel 的输出工厂。"""
        self._factories[channel] = factory

    def create(self, channel: str) -> ChannelOutput:
        """创建指定 Channel 的单轮输出对象。"""
        factory = self._factories.get(channel)
        return factory() if factory is not None else SilentChannelOutput()

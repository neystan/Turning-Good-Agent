from typing import Protocol


class ProactiveHandler(Protocol):
    """定义主动能力处理器接口。"""

    async def handle(self, event: str, payload: dict[str, object]) -> None:
        """处理 Runtime 事件。"""

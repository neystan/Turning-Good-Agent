from .base import ProactiveHandler


class ProactiveManager:
    """管理主动能力处理器。"""

    def __init__(self) -> None:
        self.handlers: list[ProactiveHandler] = []

    def register(self, handler: ProactiveHandler) -> None:
        """注册主动能力处理器。"""
        self.handlers.append(handler)

    async def emit(self, event: str, payload: dict[str, object]) -> None:
        """向所有主动能力广播事件。"""
        for handler in self.handlers:
            await handler.handle(event, payload)

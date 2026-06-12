from collections.abc import Awaitable, Callable
from typing import Any

Hook = Callable[[dict[str, Any]], Awaitable[None]]


class HookManager:
    """注册并触发轻量事件 hook。"""

    def __init__(self) -> None:
        self._event_hooks: dict[str, list[Hook]] = {}

    def on(self, event: str, hook: Hook) -> None:
        """注册事件 hook。"""
        self._event_hooks.setdefault(event, []).append(hook)

    async def emit(self, event: str, payload: dict[str, Any]) -> None:
        """顺序触发事件 hook。"""
        for hook in self._event_hooks.get(event, []):
            await hook(payload)

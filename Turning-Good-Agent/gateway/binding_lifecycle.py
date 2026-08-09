from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from ..bus.messages import ChannelRoute
from ..channels.registry import Platform


ReleaseReservation = Callable[[str], Awaitable[None]]

__all__ = [
    "BindingRouteReservation",
    "BindingRouteScope",
    "ChannelDeletionResult",
]


@dataclass(frozen=True, slots=True)
class BindingRouteScope:
    """描述一个 Binding 在 Gateway 中占用的规范路由范围。"""

    principal_id: str
    channel: str
    conversation_id: str
    prefix: bool = False

    def matches(self, route: ChannelRoute) -> bool:
        """判断规范路由是否严格落在当前 Binding 范围内。"""
        return (
            route.principal_id == self.principal_id
            and route.channel == self.channel
            and (
                route.conversation_id.startswith(self.conversation_id)
                if self.prefix
                else route.conversation_id == self.conversation_id
            )
        )


@dataclass(slots=True)
class BindingRouteReservation:
    """持有一个可显式释放或异步上下文管理的路由预留。"""

    _reservation_id: str
    _release_reservation: ReleaseReservation = field(repr=False)
    _released: bool = field(default=False, init=False, repr=False)

    async def release(self) -> None:
        """幂等释放预留，使后续匹配提交可以再次进入 Gateway。"""
        if self._released:
            return
        await self._release_reservation(self._reservation_id)
        self._released = True

    async def __aenter__(self) -> BindingRouteReservation:
        return self

    async def __aexit__(self, exc_type: object, exc: object, traceback: object) -> None:
        del exc_type, exc, traceback
        await self.release()


@dataclass(frozen=True, slots=True)
class ChannelDeletionResult:
    """返回一次永久 Binding 删除的安全、非凭据结果。"""

    platform: Platform
    account_id: str
    principal_kind: str
    deleted_session_ids: list[str]

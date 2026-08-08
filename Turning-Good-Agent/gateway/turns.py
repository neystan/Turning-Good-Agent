from __future__ import annotations

import asyncio
import inspect
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from ..bus.messages import ChannelRoute, InboundMessage, OutboundMessage, Recipient
from ..bus.queue import AsyncMessageBus


RuntimeProvider = Callable[[], Any | Awaitable[Any]]
IdleCallback = Callable[[], Awaitable[None]]
TurnDispatch = Callable[[InboundMessage], Awaitable[None]]
RouteKey = tuple[str, str]
_TERMINAL_CACHE_LIMIT = 2_048


class GatewayTurnCoordinator:
    """唯一消费入站总线并运行共享 Runtime 的 Gateway 调度器。"""

    def __init__(
        self,
        bus: AsyncMessageBus,
        runtime_provider: RuntimeProvider,
        *,
        max_concurrent_turns: int,
        on_idle: IdleCallback | None = None,
        execution_semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        if max_concurrent_turns <= 0:
            raise ValueError("max_concurrent_turns 必须大于 0")
        self._bus = bus
        self._runtime_provider = runtime_provider
        self._on_idle = on_idle
        self._semaphore = execution_semaphore or asyncio.Semaphore(max_concurrent_turns)
        self._dispatcher: asyncio.Task[None] | None = None
        self._running: set[asyncio.Task[None]] = set()
        self._inflight: set[str] = set()
        self._terminal: OrderedDict[str, OutboundMessage] = OrderedDict()
        self._active_routes: dict[RouteKey, str] = {}
        self._pending_routes: dict[RouteKey, deque[tuple[InboundMessage, TurnDispatch]]] = {}
        self._lock = asyncio.Lock()
        self._closed = False

    async def start(self) -> None:
        """启动唯一共享入站消费者。"""
        if self._dispatcher is not None:
            return
        self._closed = False
        self._dispatcher = asyncio.create_task(self._consume_inbound(), name="gateway-inbound")

    async def close(self) -> None:
        """停止消费并取消尚未完成的 Gateway turn。"""
        self._closed = True
        if self._dispatcher is not None:
            self._dispatcher.cancel()
            await asyncio.gather(self._dispatcher, return_exceptions=True)
            self._dispatcher = None
        for task in tuple(self._running):
            task.cancel()
        if self._running:
            await asyncio.gather(*tuple(self._running), return_exceptions=True)

    async def submit(self, message: InboundMessage, *, dispatch: TurnDispatch) -> bool:
        """接收一条 Adapter 输入；重复 request-id 不会重新执行。"""
        if self._closed:
            raise RuntimeError("Gateway 已关闭")
        route_key = self._route_key(message.route)
        async with self._lock:
            if message.id in self._inflight or message.id in self._terminal:
                return False
            self._inflight.add(message.id)
            if route_key in self._active_routes:
                self._pending_routes.setdefault(route_key, deque()).append((message, dispatch))
                return True
            self._active_routes[route_key] = message.id
        await dispatch(message)
        return True

    async def complete_route_turn(self, route: ChannelRoute, request_id: str) -> None:
        """完成一个 Route 的当前 turn，并按 FIFO 调度下一条普通消息。"""
        route_key = self._route_key(route)
        async with self._lock:
            if self._active_routes.get(route_key) != request_id:
                return
            pending = self._pending_routes.get(route_key)
            if not pending:
                self._active_routes.pop(route_key, None)
                self._pending_routes.pop(route_key, None)
                return
            message, dispatch = pending.popleft()
            if not pending:
                self._pending_routes.pop(route_key, None)
            self._active_routes[route_key] = message.id
        await dispatch(message)

    async def discard_pending_route(self, route: ChannelRoute) -> None:
        """丢弃指定 Route 尚未调度的普通消息，不影响当前或其他工作。"""
        route_key = self._route_key(route)
        async with self._lock:
            pending = self._pending_routes.pop(route_key, ())
            for message, _dispatch in pending:
                self._inflight.discard(message.id)

    async def resync(self, request_id: str) -> OutboundMessage | None:
        """返回已完成请求的最后终态，供断线 Client 恢复展示。"""
        async with self._lock:
            return self._terminal.get(request_id)

    def is_globally_idle(self) -> bool:
        """报告没有已受理、等待或运行中的前台 turn。"""
        return not self._inflight and not self._running

    async def _consume_inbound(self) -> None:
        while not self._closed:
            message = await self._bus.consume_inbound()
            task = asyncio.create_task(self._run_message(message), name=f"gateway-turn-{message.id}")
            self._running.add(task)
            task.add_done_callback(self._running.discard)

    async def _run_message(self, message: InboundMessage) -> None:
        try:
            async with self._semaphore:
                runtime = self._runtime_provider()
                if inspect.isawaitable(runtime):
                    runtime = await runtime
                outbound = await runtime.run_turn(message)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            outbound = OutboundMessage(
                str(uuid4()),
                message.session_id,
                recipient=Recipient(
                    message.route.principal_id,
                    message.route.channel,
                    message.route.conversation_id,
                ),
                content=f"请求失败：{exc}",
                event_type="response.error",
                event_id=message.id,
            )
        else:
            outbound.event_id = message.id
        outbound.metadata["request_id"] = message.id
        async with self._lock:
            self._inflight.discard(message.id)
            self._terminal[message.id] = outbound
            self._terminal.move_to_end(message.id)
            while len(self._terminal) > _TERMINAL_CACHE_LIMIT:
                self._terminal.popitem(last=False)
        await self._bus.publish_outbound(outbound)
        current = asyncio.current_task()
        if current is not None:
            self._running.discard(current)
        if self._on_idle is not None and self.is_globally_idle():
            await self._on_idle()

    @staticmethod
    def _route_key(route: ChannelRoute) -> RouteKey:
        return route.channel, route.conversation_id

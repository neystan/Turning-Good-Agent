from __future__ import annotations

import asyncio
import inspect
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import uuid4

from ..bus.messages import ChannelRoute, InboundMessage, OutboundMessage, Recipient
from ..bus.queue import AsyncMessageBus
from .binding_lifecycle import BindingRouteReservation, BindingRouteScope


RuntimeProvider = Callable[[], Any | Awaitable[Any]]
IdleCallback = Callable[[], Awaitable[None]]
TurnDispatch = Callable[[InboundMessage], Awaitable[None]]
RouteKey = tuple[str, str, str]
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
        self._reserved_scopes: dict[str, BindingRouteScope] = {}
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
        running = tuple(self._running)
        for task in running:
            task.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)
        async with self._lock:
            self._running.clear()
            self._inflight.clear()
            self._active_routes.clear()
            self._pending_routes.clear()
            self._reserved_scopes.clear()

    async def submit(self, message: InboundMessage, *, dispatch: TurnDispatch) -> bool:
        """接收一条 Adapter 输入；重复 request-id 不会重新执行。"""
        route_key = self._route_key(message.route)
        async with self._lock:
            if self._closed:
                raise RuntimeError("Gateway 已关闭")
            if any(scope.matches(message.route) for scope in self._reserved_scopes.values()):
                return False
            if message.id in self._inflight or message.id in self._terminal:
                return False
            self._inflight.add(message.id)
            if route_key in self._active_routes:
                self._pending_routes.setdefault(route_key, deque()).append((message, dispatch))
                return True
            self._active_routes[route_key] = message.id
        await self._dispatch(message, dispatch)
        return True

    async def reserve_scope(self, scope: BindingRouteScope) -> BindingRouteReservation:
        """原子预留一个 Binding 路由范围，并阻止后续匹配提交。"""
        if not isinstance(scope, BindingRouteScope):
            raise TypeError("scope 必须是 BindingRouteScope")
        reservation_id = str(uuid4())
        async with self._lock:
            if self._closed:
                raise RuntimeError("Gateway 已关闭")
            self._reserved_scopes[reservation_id] = scope
        return BindingRouteReservation(reservation_id, self._release_scope_reservation)

    async def reserve_scope_if_idle(
        self,
        scope: BindingRouteScope,
    ) -> BindingRouteReservation | None:
        """仅当范围内没有 active/pending turn 时才原子加入预留。"""
        if not isinstance(scope, BindingRouteScope):
            raise TypeError("scope 必须是 BindingRouteScope")
        reservation_id = str(uuid4())
        async with self._lock:
            if self._closed:
                raise RuntimeError("Gateway 已关闭")
            if scope in self._reserved_scopes.values() or any(
                self._scope_matches_route_key(scope, route_key)
                for route_key in (*self._active_routes, *self._pending_routes)
            ):
                return None
            self._reserved_scopes[reservation_id] = scope
        return BindingRouteReservation(reservation_id, self._release_scope_reservation)

    async def _release_scope_reservation(self, reservation_id: str) -> None:
        """释放一个由当前协调器签发的路由预留。"""
        async with self._lock:
            self._reserved_scopes.pop(reservation_id, None)

    def is_scope_busy(self, scope: BindingRouteScope) -> bool:
        """检查范围内是否有已激活或 FIFO 等待的普通消息。"""
        if not isinstance(scope, BindingRouteScope):
            raise TypeError("scope 必须是 BindingRouteScope")
        return any(
            self._scope_matches_route_key(scope, route_key)
            for route_key in (*self._active_routes, *self._pending_routes)
        )

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
        await self._dispatch(message, dispatch)

    async def _dispatch(self, message: InboundMessage, dispatch: TurnDispatch) -> None:
        """执行 Adapter dispatch，并在入站 Bus 失败时回滚路由状态。"""
        try:
            await dispatch(message)
        except asyncio.CancelledError:
            await self._rollback_dispatch(message.route, message.id, advance=False)
            raise
        except BaseException:
            await self._rollback_dispatch(message.route, message.id, advance=True)
            raise

    async def _rollback_dispatch(
        self,
        route: ChannelRoute,
        request_id: str,
        *,
        advance: bool,
    ) -> None:
        """清理失败 dispatch，并尽可能继续该 Route 的待处理 FIFO。"""
        route_key = self._route_key(route)
        next_item: tuple[InboundMessage, TurnDispatch] | None = None
        async with self._lock:
            self._inflight.discard(request_id)
            if self._active_routes.get(route_key) != request_id:
                return
            pending = self._pending_routes.get(route_key)
            if advance and pending:
                next_item = pending.popleft()
                self._active_routes[route_key] = next_item[0].id
                if not pending:
                    self._pending_routes.pop(route_key, None)
            else:
                self._active_routes.pop(route_key, None)
                pending = self._pending_routes.pop(route_key, ())
                for pending_message, _pending_dispatch in pending:
                    self._inflight.discard(pending_message.id)
        if next_item is not None:
            await self._dispatch(*next_item)
        elif self._on_idle is not None and self.is_globally_idle():
            await self._on_idle()

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
        return route.principal_id, route.channel, route.conversation_id

    @staticmethod
    def _scope_matches_route_key(scope: BindingRouteScope, route_key: RouteKey) -> bool:
        principal_id, channel, conversation_id = route_key
        return (
            principal_id == scope.principal_id
            and channel == scope.channel
            and (
                conversation_id.startswith(scope.conversation_id)
                if scope.prefix
                else conversation_id == scope.conversation_id
            )
        )

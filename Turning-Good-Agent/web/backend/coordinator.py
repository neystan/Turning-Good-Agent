from __future__ import annotations

import asyncio
import inspect
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from ...bus.messages import ChannelRoute, InboundMessage, OutboundMessage, Recipient
from ...bus.queue import AsyncMessageBus
from ...gateway.routing import derive_session_id
from .events import SessionEventHub


_ACTION_RECEIPT_LIMIT = 2_048
SubmitTurn = Callable[..., Awaitable[bool]]
CompleteRouteTurn = Callable[[ChannelRoute, str], Awaitable[None]]
RuntimeProvider = Callable[[], Any | Awaitable[Any]]
IdleCallback = Callable[[], Awaitable[None]]


@dataclass(slots=True)
class WebTurnControl:
    """保存单轮 guidance、Stop 和审批等待状态。"""

    session_id: str
    request_id: str
    route: ChannelRoute | None = None
    guidance: deque[str] = field(default_factory=deque)
    stop_requested: bool = False
    approvals: dict[str, asyncio.Future[bool]] = field(default_factory=dict)
    _semaphore: asyncio.Semaphore | None = field(default=None, repr=False)
    _slot_held: bool = field(default=False, repr=False)

    async def consume_guidance(self) -> list[str]:
        """按进入顺序取走所有待注入 guidance。"""
        items = list(self.guidance)
        self.guidance.clear()
        return items

    def is_stop_requested(self) -> bool:
        """返回当前任务是否已收到停止请求。"""
        return self.stop_requested

    def create_approval(self) -> str:
        """创建一个等待浏览器完成的审批 Future。"""
        approval_id = str(uuid4())
        self.approvals[approval_id] = asyncio.get_running_loop().create_future()
        return approval_id

    async def wait_approval(self, approval_id: str) -> bool:
        """等待指定审批 Future 被用户或 Stop 完成。"""
        future = self.approvals[approval_id]
        self.release_slot()
        try:
            approved = await future
            await self.acquire_slot()
            return approved
        finally:
            self.approvals.pop(approval_id, None)

    def bind_slot(self, semaphore: asyncio.Semaphore) -> None:
        """兼容运行时在审批等待期间临时释放的执行槽位。"""
        self._semaphore = semaphore
        self._slot_held = True

    async def acquire_slot(self) -> None:
        """在审批结束后重新取得执行槽位。"""
        if self._semaphore is not None and not self._slot_held:
            await self._semaphore.acquire()
            self._slot_held = True

    def release_slot(self) -> None:
        """在等待审批时释放执行槽位给其他会话。"""
        if self._semaphore is not None and self._slot_held:
            self._semaphore.release()
            self._slot_held = False

    def resolve_approval(self, approval_id: str, approved: bool) -> bool:
        """完成一个仍在等待的审批 Future。"""
        future = self.approvals.get(approval_id)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True

    def stop(self) -> None:
        """请求停止，并拒绝当前全部等待审批。"""
        self.stop_requested = True
        for future in self.approvals.values():
            if not future.done():
                future.set_result(False)

    @property
    def recipient(self) -> Recipient | None:
        """返回当前 Web turn 的唯一出站目标。"""
        if self.route is None:
            return None
        return Recipient(
            self.route.principal_id,
            self.route.channel,
            self.route.conversation_id,
        )


class WebSessionCoordinator:
    """Web 的会话控制与事件投影，不消费或运行共享 MessageBus。"""

    def __init__(
        self,
        *,
        submit: SubmitTurn,
        complete_route_turn: CompleteRouteTurn,
        runtime_provider: RuntimeProvider,
        principal_id: str,
        event_buffer_size: int,
        on_idle: IdleCallback | None = None,
        bus: AsyncMessageBus | None = None,
        execution_semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        self._submit = submit
        self._complete_route_turn = complete_route_turn
        self._runtime_provider = runtime_provider
        self._principal_id = principal_id
        self._on_idle = on_idle
        self._bus = bus
        self._execution_semaphore = execution_semaphore
        self.controls: dict[str, WebTurnControl] = {}
        self.hub = SessionEventHub(event_buffer_size, self.snapshot)
        self._closed = False
        self._accepted_actions: dict[str, tuple[str, str]] = {}
        self._action_lock = asyncio.Lock()
        self._catalog_actions: dict[str, set[str]] = {}

    async def start(self) -> None:
        """保留 Gateway 生命周期中的统一启动入口。"""
        self._closed = False

    async def close(self) -> None:
        """停止 Web 控制面，不取消 Gateway 正在执行的共享 turn。"""
        self._closed = True
        for control in self.controls.values():
            control.stop()
        self._catalog_actions.clear()

    def register_runtime(self, runtime: Any) -> None:
        """给当前或替换后的 Runtime 注册 Web 单轮适配器。"""
        if self._bus is None:
            raise RuntimeError("Web Coordinator 缺少 Gateway MessageBus")
        from ...channels.web import WebChannelAdapter

        runtime.channel_router.register(
            "web",
            lambda message: WebChannelAdapter(
                self._bus,
                self.controls[message.session_id],
                route=message.route,
                execution_semaphore=self._execution_semaphore,
            ),
        )

    def is_globally_idle(self) -> bool:
        """控制器为空即表示没有等待审批或等待终态投影的 Web turn。"""
        return not self.controls and not self._catalog_actions

    async def begin_catalog_action(
        self,
        session_id: str,
        request_id: str,
        *,
        exclusive: bool,
    ) -> None:
        """登记无聊天输出的 Catalog 控制任务。"""
        async with self._action_lock:
            await self._route_for_session(session_id)
            if exclusive and (session_id in self.controls or self._catalog_actions.get(session_id)):
                raise RuntimeError("当前会话已有任务运行")
            self._catalog_actions.setdefault(session_id, set()).add(request_id)

    async def finish_catalog_action(self, session_id: str, request_id: str) -> None:
        """移除已经发送终态的 Catalog 控制任务。"""
        async with self._action_lock:
            request_ids = self._catalog_actions.get(session_id)
            if request_ids is None:
                return
            request_ids.discard(request_id)
            if not request_ids:
                self._catalog_actions.pop(session_id, None)
        if self._on_idle is not None and self.is_globally_idle():
            await self._on_idle()

    async def send(
        self,
        session_id: str | None,
        content: str,
        client_action_id: str | None = None,
    ) -> tuple[str, str]:
        """向 Gateway 提交一条拥有独立 request-id 的普通消息。"""
        text = content.strip()
        if not text:
            raise ValueError("消息不能为空")
        if self._closed:
            raise RuntimeError("Web Gateway 已关闭")
        async with self._action_lock:
            receipt = self._accepted_actions.get(client_action_id) if client_action_id else None
            if receipt is not None:
                return receipt
            route = await self._route_for_session(session_id)
            request_id = str(uuid4())
            await self.hub.publish(route.session_id, request_id, "task.queued")

            async def dispatch(message: InboundMessage) -> None:
                if self._bus is None:
                    raise RuntimeError("Web Coordinator 缺少 Gateway MessageBus")
                control = WebTurnControl(route.session_id, request_id, route)
                self.controls[route.session_id] = control
                try:
                    await self._bus.publish_inbound(message)
                except BaseException:
                    if self.controls.get(route.session_id) is control:
                        self.controls.pop(route.session_id, None)
                    raise

            accepted = await self._submit(
                InboundMessage(request_id, route=route, content=text),
                dispatch=dispatch,
            )
            if not accepted:
                control = self.controls.get(route.session_id)
                if control is not None and control.request_id == request_id:
                    self.controls.pop(route.session_id, None)
                raise RuntimeError("Gateway 拒绝重复请求")
            receipt = (route.session_id, request_id)
            if client_action_id:
                self._remember_action_receipt(client_action_id, receipt)
            return receipt

    async def send_guidance(self, session_id: str, content: str) -> None:
        """将补充方向加入正在运行的指定会话。"""
        control = self.controls.get(session_id)
        if control is None:
            raise RuntimeError("当前会话没有运行中的任务")
        text = content.strip()
        if not text:
            raise ValueError("引导不能为空")
        control.guidance.append(text)
        await self.hub.publish(session_id, control.request_id, "task.status", {"content": "已加入运行中引导"})

    async def stop(self, session_id: str) -> None:
        """请求指定会话在下一个安全检查点停止。"""
        control = self.controls.get(session_id)
        if control is None:
            raise RuntimeError("当前会话没有运行中的任务")
        control.stop()
        await self.hub.publish(session_id, control.request_id, "task.stopping")

    async def resolve_approval(self, session_id: str, approval_id: str, approved: bool) -> bool:
        """提交当前审批卡的用户决定。"""
        control = self.controls.get(session_id)
        return control.resolve_approval(approval_id, approved) if control is not None else False

    def is_active(self, session_id: str) -> bool:
        """判断会话是否正在排队、运行、停止或等待审批。"""
        return session_id in self.controls or bool(self._catalog_actions.get(session_id))

    def snapshot(self, session_id: str) -> dict[str, object]:
        """返回当前会话不落盘的运行快照。"""
        control = self.controls.get(session_id)
        if control is None:
            return {"state": "running" if self._catalog_actions.get(session_id) else "idle"}
        return {
            "state": "stopping" if control.stop_requested else "running",
            "pending_guidance_count": len(control.guidance),
            "pending_approval_count": len(control.approvals),
        }

    async def deliver(self, outbound: OutboundMessage) -> bool:
        """把 ChannelManager 已定向的 Web 聊天输出投影为 SessionEvent。"""
        if outbound.recipient.channel != "web" or outbound.disposition != "chat_reply":
            return False
        control = self.controls.get(outbound.session_id)
        request_id = str(outbound.metadata.get("request_id", outbound.event_id))
        if control is None or control.request_id != request_id:
            return False
        if outbound.recipient != control.recipient:
            return False
        payload = {key: value for key, value in outbound.metadata.items() if key != "request_id"}
        if outbound.content:
            payload["content"] = outbound.content
        await self.hub.publish(outbound.session_id, request_id, outbound.event_type, payload)
        if outbound.event_type not in {"response.completed", "response.error"}:
            return True
        event_type = (
            "task.cancelled"
            if outbound.metadata.get("outcome") == "cancelled"
            else "task.failed"
            if outbound.event_type == "response.error"
            else "task.completed"
        )
        pending = list(control.guidance)
        await self.hub.publish(outbound.session_id, request_id, event_type, payload)
        if pending:
            await self.hub.publish(outbound.session_id, request_id, "guidance.pending", {"items": pending})
        self.controls.pop(outbound.session_id, None)
        if control.route is not None:
            await self._complete_route_turn(control.route, request_id)
        if self._on_idle is not None and self.is_globally_idle():
            await self._on_idle()
        return True

    async def _route_for_session(self, session_id: str | None) -> ChannelRoute:
        if session_id is None:
            conversation_id = f"web-{uuid4()}"
            return ChannelRoute(
                self._principal_id,
                "web",
                conversation_id,
                derive_session_id(self._principal_id, "web", conversation_id),
            )
        runtime = await _resolve(self._runtime_provider())
        session = await runtime.sessions.store.load_session(session_id)
        if session is None:
            raise ValueError("会话不存在")
        if (session.principal_id, session.channel) != (self._principal_id, "web"):
            raise ValueError("会话不属于当前 Web Channel")
        if not session.conversation_id:
            raise ValueError("会话缺少 conversation_id")
        return ChannelRoute(self._principal_id, "web", session.conversation_id, session.id)

    async def route_for_session(self, session_id: str) -> ChannelRoute:
        """供 Gateway 控制任务获取当前 Web 会话的稳定路由。"""
        return await self._route_for_session(session_id)

    def _remember_action_receipt(self, client_action_id: str, receipt: tuple[str, str]) -> None:
        self._accepted_actions[client_action_id] = receipt
        while len(self._accepted_actions) > _ACTION_RECEIPT_LIMIT:
            self._accepted_actions.pop(next(iter(self._accepted_actions)))
async def _resolve(value: Any | Awaitable[Any]) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value

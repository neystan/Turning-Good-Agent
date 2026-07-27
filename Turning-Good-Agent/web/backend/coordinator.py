from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import uuid4

from ...bus.messages import InboundMessage, OutboundMessage
from ...bus.queue import AsyncMessageBus
from .events import SessionEventHub

if TYPE_CHECKING:
    from ...runtime.runtime import AgentRuntime


_ACTION_RECEIPT_LIMIT = 2_048


@dataclass(slots=True)
class WebTurnControl:
    """保存单轮 guidance、Stop 和审批等待状态。"""

    session_id: str
    request_id: str
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
        future: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        self.approvals[approval_id] = future
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
        """绑定当前 Runtime 任务占用的全局执行槽位。"""
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


class WebSessionCoordinator:
    """调度 Web 会话、消息队列和单轮控制器。"""

    def __init__(self, runtime: AgentRuntime, bus: AsyncMessageBus, max_concurrent_sessions: int, event_buffer_size: int) -> None:
        """初始化唯一 Web 调度器及并发限制。"""
        self.runtime = runtime
        self.bus = bus
        self.controls: dict[str, WebTurnControl] = {}
        self.hub = SessionEventHub(event_buffer_size, self.snapshot)
        self._semaphore = asyncio.Semaphore(max_concurrent_sessions)
        self._dispatcher: asyncio.Task[None] | None = None
        self._outbound: asyncio.Task[None] | None = None
        self._closed = False
        self._accepted_actions: dict[str, tuple[str, str]] = {}
        self._action_lock = asyncio.Lock()

    async def start(self) -> None:
        """注册 Web Adapter 工厂并启动 MessageBus 消费者。"""
        from ...channels.web import WebChannelAdapter

        self.runtime.channel_router.register(
            "web", lambda message: WebChannelAdapter(self.bus, self.controls[message.session_id])
        )
        self._dispatcher = asyncio.create_task(self._dispatch_inbound())
        self._outbound = asyncio.create_task(self._consume_outbound())

    async def close(self) -> None:
        """停止队列消费者并终止未开始的协作任务。"""
        self._closed = True
        for control in self.controls.values():
            control.stop()
        for task in (self._dispatcher, self._outbound):
            if task is not None:
                task.cancel()
        await asyncio.gather(*(item for item in (self._dispatcher, self._outbound) if item is not None), return_exceptions=True)

    async def send(
        self,
        session_id: str,
        content: str,
        user_id: str,
        client_action_id: str | None = None,
    ) -> tuple[str, str]:
        """启动任务或加入引导，并为重复浏览器动作复用受理回执。"""
        text = content.strip()
        if not text:
            raise ValueError("消息不能为空")
        async with self._action_lock:
            receipt = self._accepted_actions.get(client_action_id) if client_action_id else None
            if receipt is not None:
                return receipt
            active = self.controls.get(session_id)
            if active is not None:
                active.guidance.append(text)
                await self.hub.publish(session_id, active.request_id, "task.status", {"content": "已加入运行中引导"})
                receipt = (session_id, active.request_id)
            else:
                request_id = str(uuid4())
                control = WebTurnControl(session_id, request_id)
                self.controls[session_id] = control
                await self.hub.publish(session_id, request_id, "task.queued")
                await self.bus.publish_inbound(InboundMessage(request_id, session_id, user_id, "web", text))
                receipt = (session_id, request_id)
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
        """判断会话是否正在排队、运行或停止。"""
        return session_id in self.controls

    def snapshot(self, session_id: str) -> dict[str, object]:
        """返回当前会话不落盘的运行快照。"""
        control = self.controls.get(session_id)
        if control is None:
            return {"state": "idle"}
        return {
            "state": "stopping" if control.stop_requested else "running",
            "pending_guidance_count": len(control.guidance),
            "pending_approval_count": len(control.approvals),
        }

    async def _dispatch_inbound(self) -> None:
        """并发受限地将 MessageBus 入站消息交给唯一 Runtime。"""
        while not self._closed:
            message = await self.bus.consume_inbound()
            asyncio.create_task(self._run_message(message))

    async def _run_message(self, message: InboundMessage) -> None:
        """在全局槽位内执行一个会话的唯一 Runtime turn。"""
        control = self.controls.get(message.session_id)
        if control is None:
            return
        await self._semaphore.acquire()
        control.bind_slot(self._semaphore)
        try:
            await self.hub.publish(message.session_id, message.id, "task.running")
            outbound = await self.runtime.run_turn(message)
            outbound.metadata["request_id"] = message.id
            await self.bus.publish_outbound(outbound)
        finally:
            control.release_slot()

    async def _consume_outbound(self) -> None:
        """把 Runtime 终态消息转换为最终会话事件。"""
        while not self._closed:
            outbound = await self.bus.consume_outbound()
            await self._finish(outbound)

    async def _finish(self, outbound: OutboundMessage) -> None:
        """将出站事件写入 EventHub，并在终态清理控制器。"""
        control = self.controls.get(outbound.session_id)
        request_id = str(outbound.metadata.get("request_id", ""))
        if control is None or control.request_id != request_id:
            return
        payload = {key: value for key, value in outbound.metadata.items() if key != "request_id"}
        if outbound.content:
            payload["content"] = outbound.content
        await self.hub.publish(outbound.session_id, request_id, outbound.event_type, payload)
        if outbound.event_type not in {"response.completed", "response.error"}:
            return
        event_type = "task.cancelled" if outbound.metadata.get("outcome") == "cancelled" else "task.failed" if outbound.event_type == "response.error" else "task.completed"
        pending = list(control.guidance)
        await self.hub.publish(outbound.session_id, request_id, event_type, payload)
        if pending:
            await self.hub.publish(outbound.session_id, request_id, "guidance.pending", {"items": pending})
        self.controls.pop(outbound.session_id, None)

    def _remember_action_receipt(self, client_action_id: str, receipt: tuple[str, str]) -> None:
        """保留有限的受理回执，避免重试重复入队。"""
        self._accepted_actions[client_action_id] = receipt
        while len(self._accepted_actions) > _ACTION_RECEIPT_LIMIT:
            self._accepted_actions.pop(next(iter(self._accepted_actions)))

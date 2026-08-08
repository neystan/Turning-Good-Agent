from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from ..bus.messages import ChannelRoute, InboundMessage, OutboundMessage, Recipient
from ..bus.queue import AsyncMessageBus
from ..llm.types import ToolCall


IM_CHANNELS = frozenset({"feishu", "weixin"})

__all__ = ["IM_CHANNELS", "ImTurnControl", "ImChannelAdapter", "ImGatewayCoordinator"]

SubmitTurn = Callable[..., Awaitable[bool]]
CompleteRouteTurn = Callable[[ChannelRoute, str], Awaitable[None]]
IdleCallback = Callable[[], Awaitable[None]]


@dataclass(slots=True)
class ImTurnControl:
    """保存一个 IM turn 的稳定请求标识与规范路由。"""

    request_id: str
    route: ChannelRoute

    @property
    def session_id(self) -> str:
        """返回该 turn 的 opaque session ID。"""
        return self.route.session_id

    @property
    def recipient(self) -> Recipient:
        """返回该 turn 的唯一 IM 收件目标。"""
        return Recipient(
            self.route.principal_id,
            self.route.channel,
            self.route.conversation_id,
        )


class ImChannelAdapter:
    """将共享 Runtime 的中间输出安全地投影为 IM 增量。"""

    def __init__(self, bus: AsyncMessageBus, control: ImTurnControl) -> None:
        self.bus = bus
        self.control = control
        self.route = control.route

    async def on_delta(self, text: str) -> None:
        """仅发布非空的普通文本增量。"""
        if not text:
            return
        await self.bus.publish_outbound(
            OutboundMessage(
                str(uuid4()),
                self.control.session_id,
                recipient=self.control.recipient,
                content=text,
                event_type="response.delta",
                event_id=self.control.request_id,
                disposition="chat_reply",
                metadata={"request_id": self.control.request_id},
            )
        )

    async def on_status(self, text: str) -> None:
        """IM v1 不展示运行状态。"""
        del text

    async def on_tool_started(
        self,
        tool_call_id: str,
        tool_name: str,
        args: Mapping[str, Any],
    ) -> None:
        """IM v1 不展示工具生命周期、名称或参数。"""
        del tool_call_id, tool_name, args

    async def on_tool_finished(self, tool_call_id: str, tool_name: str, failed: bool) -> None:
        """IM v1 不展示工具生命周期。"""
        del tool_call_id, tool_name, failed

    async def on_completed(self, content: str) -> None:
        """终态由 GatewayTurnCoordinator 统一生成并投递。"""
        del content

    async def on_error(self, content: str) -> None:
        """终态由 GatewayTurnCoordinator 统一生成并投递。"""
        del content

    async def request_tool_approval(self, call: ToolCall) -> str | None:
        """防御性拒绝审批；正常 IM 路径由 Tool Policy 先行隐藏审批工具。"""
        del call
        return "当前 Channel 不支持工具审批。"

    async def consume_guidance(self) -> list[str]:
        """IM v1 不支持运行中 guidance。"""
        return []

    def is_stop_requested(self) -> bool:
        """IM v1 不支持远程停止。"""
        return False


class ImGatewayCoordinator:
    """把已验证的 IM 输入接入唯一 Gateway turn 协调器。"""

    def __init__(
        self,
        bus: AsyncMessageBus,
        turn_coordinator: Any | None = None,
        complete_route_turn: CompleteRouteTurn | None = None,
        *,
        submit: SubmitTurn | None = None,
        on_idle: IdleCallback | None = None,
    ) -> None:
        """初始化共享 Bus 边界；可传入协调器或其两个回调。"""
        self._bus = bus
        if turn_coordinator is not None:
            if callable(turn_coordinator):
                if submit is None:
                    submit = turn_coordinator
            else:
                if submit is None:
                    submit = turn_coordinator.submit
                if complete_route_turn is None:
                    complete_route_turn = turn_coordinator.complete_route_turn
        if submit is None or complete_route_turn is None:
            raise TypeError("submit 和 complete_route_turn 为必填")
        self._submit = submit
        self._complete_route_turn = complete_route_turn
        self._on_idle = on_idle
        self._controls: dict[str, ImTurnControl] = {}
        self._action_lock = asyncio.Lock()
        self._closed = False
        self._generation = 0

    async def start(self) -> None:
        """开启 IM 控制边界；平台 Transport 的连接由其自身管理。"""
        async with self._action_lock:
            self._generation += 1
            self._closed = False

    async def close(self) -> None:
        """关闭控制边界并丢弃当前 turn 控制。"""
        async with self._action_lock:
            self._generation += 1
            self._closed = True
            self._controls.clear()

    def register_runtime(self, runtime: Any) -> None:
        """为当前或替换后的 Runtime 注册两个 IM Channel 工厂。"""

        def factory(message: InboundMessage) -> ImChannelAdapter:
            if not isinstance(message, InboundMessage):
                raise TypeError("IM Adapter 需要 InboundMessage")
            control = self._controls.get(message.session_id)
            if control is None:
                raise RuntimeError("当前 IM 会话没有运行中的任务")
            if control.route != message.route:
                raise RuntimeError("当前 IM 会话路由不一致")
            if control.request_id != message.id:
                raise RuntimeError("当前 IM 会话请求不一致")
            return ImChannelAdapter(self._bus, control)

        for channel in IM_CHANNELS:
            runtime.channel_router.register(channel, factory)

    async def accept(self, message: InboundMessage) -> bool:
        """提交一条已由平台 Transport 验证的 IM 文本消息。"""
        if not isinstance(message, InboundMessage):
            return False
        if message.channel not in IM_CHANNELS:
            return False
        if not message.content or not message.content.strip():
            return False
        async with self._action_lock:
            if self._closed:
                return False
            generation = self._generation
            active = self._controls.get(message.session_id)
            if active is not None and active.route != message.route:
                return False

        async def dispatch(inbound: InboundMessage) -> None:
            control = ImTurnControl(inbound.id, inbound.route)
            async with self._action_lock:
                if self._closed or generation != self._generation:
                    raise RuntimeError("Gateway IM 控制器已关闭")
                active = self._controls.get(inbound.session_id)
                if active is not None:
                    if active.route != inbound.route:
                        raise RuntimeError("当前 IM 会话路由不一致")
                    raise RuntimeError("当前 IM 会话已有运行中的任务")
                self._controls[inbound.session_id] = control
            try:
                await self._bus.publish_inbound(inbound)
            except BaseException:
                async with self._action_lock:
                    if self._controls.get(inbound.session_id) is control:
                        self._controls.pop(inbound.session_id, None)
                raise

        return await self._submit(message, dispatch=dispatch)

    async def on_delivery(self, message: OutboundMessage, accepted: bool) -> None:
        """终态投递结束后释放同一路由 FIFO，无论平台是否接受。"""
        del accepted
        if message.recipient.channel not in IM_CHANNELS:
            return
        if message.disposition != "chat_reply":
            return
        if message.event_type not in {"response.completed", "response.error"}:
            return

        async with self._action_lock:
            control = self._controls.get(message.session_id)
            if control is None or control.recipient != message.recipient:
                return
            request_id = str(message.metadata.get("request_id", message.event_id))
            if request_id != control.request_id:
                return
            self._controls.pop(message.session_id, None)

        await self._complete_route_turn(control.route, control.request_id)
        if self._on_idle is not None and self.is_globally_idle():
            await self._on_idle()

    def is_globally_idle(self) -> bool:
        """报告是否没有尚未收到终态的 IM turn。"""
        return not self._controls

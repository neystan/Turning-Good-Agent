from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from ..bus.messages import ChannelRoute, InboundMessage, OutboundMessage, Recipient
from ..bus.queue import AsyncMessageBus
from ..gateway.routing import derive_session_id
from ..llm.types import ToolCall
from ..multi_agent.events import MULTI_AGENT_LIVE_EVENT_FIELDS
from .base import ChannelCapabilities


logger = logging.getLogger(__name__)
CliSender = Callable[[dict[str, Any]], Awaitable[object]]
SubmitTurn = Callable[..., Awaitable[bool]]
CompleteRouteTurn = Callable[[ChannelRoute, str], Awaitable[None]]
DiscardPendingRoute = Callable[[ChannelRoute], Awaitable[None]]
IdleCallback = Callable[[], Awaitable[None]]
CLI_EVENT_FIELDS = frozenset(
    {
        "request_id",
        "content",
        "tool_call_id",
        "tool_name",
        "args",
        "failed",
        "approval_id",
        "approved",
        "outcome",
        *MULTI_AGENT_LIVE_EVENT_FIELDS,
    }
)


# 严格校验 CLI 一次性模式，省略时保留默认 auto。
def _multi_agent_metadata(value: str | None) -> dict[str, str]:
    if value is None:
        return {}
    from ..multi_agent.types import parse_multi_agent_mode

    return {"multi_agent_mode": parse_multi_agent_mode(value).value}


@dataclass(slots=True)
class _CliConnection:
    connection_id: str
    principal_id: str
    route: ChannelRoute
    sender: CliSender
    activation_order: int


class CliGatewayTransport:
    """Gateway 持有的 CLI 即时传输层，不持久化离线投递。"""

    name = "cli"
    capabilities = ChannelCapabilities()

    def __init__(self) -> None:
        self._connections: dict[str, _CliConnection] = {}
        self._activation_order = 0

    async def start(self) -> None:
        """CLI Client 按需注册，Transport 本身没有后台任务。"""

    async def close(self) -> None:
        """断开所有瞬态 CLI 连接。"""
        self._connections.clear()

    def register(
        self,
        connection_id: str,
        principal_id: str,
        conversation_id: str,
        sender: CliSender,
    ) -> ChannelRoute:
        """注册一个已认证 CLI 连接，并将其路由设为当前活动路由。"""
        self._require_connection_id(connection_id)
        if connection_id in self._connections:
            raise ValueError("CLI connection_id is already registered")
        route = self._route_for(principal_id, conversation_id)
        self._connections[connection_id] = _CliConnection(
            connection_id=connection_id,
            principal_id=principal_id,
            route=route,
            sender=sender,
            activation_order=self._next_activation_order(),
        )
        return route

    def deregister(self, connection_id: str) -> None:
        """移除一个断开的 CLI 连接。"""
        self._connections.pop(connection_id, None)

    def bind_route(self, connection_id: str, conversation_id: str) -> ChannelRoute:
        """将已连接的 CLI Client 切换到一个派生的新会话路由。"""
        connection = self._connection(connection_id)
        connection.route = self._route_for(connection.principal_id, conversation_id)
        self.activate(connection_id)
        return connection.route

    def activate(self, connection_id: str) -> None:
        """把一个已连接 CLI Client 标记为该主体的当前会话。"""
        self._connection(connection_id).activation_order = self._next_activation_order()

    def active_route(self, principal_id: str) -> ChannelRoute | None:
        """返回主体当前仍在线的最新 CLI 会话路由。"""
        connection = self._active_connection(principal_id)
        return connection.route if connection is not None else None

    async def send(self, message: OutboundMessage) -> bool:
        """向精确 CLI 会话发送消息，或丢弃不再有效的主动通知。"""
        if message.recipient.channel != self.name:
            return False
        connection = self._recipient_connection(message)
        if connection is None:
            return False
        try:
            accepted = await connection.sender(self._payload_for(message))
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("CLI connection %s rejected outbound message", connection.connection_id)
            return False
        return accepted is not False

    def _recipient_connection(self, message: OutboundMessage) -> _CliConnection | None:
        recipient = message.recipient
        if message.disposition == "proactive_notification":
            active = self._active_connection(recipient.principal_id)
            if active is None or self._recipient_for(active.route) != recipient:
                return None
            return active
        return self._latest_connection_for(recipient)

    def _latest_connection_for(self, recipient: Recipient) -> _CliConnection | None:
        matching = (
            connection
            for connection in self._connections.values()
            if connection.principal_id == recipient.principal_id
            and connection.route.conversation_id == recipient.conversation_id
        )
        return max(matching, key=lambda connection: connection.activation_order, default=None)

    def _active_connection(self, principal_id: str) -> _CliConnection | None:
        connections = (
            connection
            for connection in self._connections.values()
            if connection.principal_id == principal_id
        )
        return max(connections, key=lambda connection: connection.activation_order, default=None)

    def _connection(self, connection_id: str) -> _CliConnection:
        try:
            return self._connections[connection_id]
        except KeyError as exc:
            raise KeyError(f"unknown CLI connection: {connection_id}") from exc

    def _next_activation_order(self) -> int:
        self._activation_order += 1
        return self._activation_order

    @staticmethod
    def _route_for(principal_id: str, conversation_id: str) -> ChannelRoute:
        return ChannelRoute(
            principal_id=principal_id,
            channel="cli",
            conversation_id=conversation_id,
            session_id=derive_session_id(principal_id, "cli", conversation_id),
        )

    @staticmethod
    def _recipient_for(route: ChannelRoute) -> Recipient:
        return Recipient(route.principal_id, route.channel, route.conversation_id)

    @staticmethod
    def _payload_for(message: OutboundMessage) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "type": message.event_type,
            "session_id": message.session_id,
            "content": message.content,
        }
        for field in CLI_EVENT_FIELDS - {"content"}:
            if field not in message.metadata:
                continue
            value = message.metadata[field]
            payload[field] = (
                CliGatewayTransport._normalize_args(value) if field == "args" else value
            )
        return payload

    @staticmethod
    def _normalize_args(value: object) -> dict[str, object]:
        if not isinstance(value, Mapping):
            return {}
        return {str(key): CliGatewayTransport._normalize_value(item) for key, item in value.items()}

    @staticmethod
    def _normalize_value(value: object) -> object:
        if isinstance(value, Mapping):
            return {str(key): CliGatewayTransport._normalize_value(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [CliGatewayTransport._normalize_value(item) for item in value]
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    @staticmethod
    def _require_connection_id(connection_id: str) -> None:
        if not isinstance(connection_id, str) or not connection_id:
            raise ValueError("connection_id 必须是非空字符串")


@dataclass(slots=True)
class CliTurnControl:
    """保存一个远程 CLI turn 的停止和审批状态。"""

    session_id: str
    request_id: str
    route: ChannelRoute
    stop_requested: bool
    approvals: dict[str, asyncio.Future[bool]]
    _semaphore: asyncio.Semaphore | None
    _slot_held: bool

    def __init__(self, session_id: str, request_id: str, route: ChannelRoute) -> None:
        self.session_id = session_id
        self.request_id = request_id
        self.route = route
        self.stop_requested = False
        self.approvals = {}
        self._semaphore = None
        self._slot_held = False

    def is_stop_requested(self) -> bool:
        """返回当前 turn 是否已经请求协作式停止。"""
        return self.stop_requested

    def create_approval(self) -> str:
        """创建一个等待远程 CLI 确认的审批 Future。"""
        approval_id = str(uuid4())
        self.approvals[approval_id] = asyncio.get_running_loop().create_future()
        return approval_id

    async def wait_approval(self, approval_id: str) -> bool:
        """等待远程 CLI 对工具调用作出决定。"""
        future = self.approvals[approval_id]
        self.release_slot()
        try:
            approved = await future
            return approved
        finally:
            try:
                await self.acquire_slot()
            finally:
                self.approvals.pop(approval_id, None)

    def bind_slot(self, semaphore: asyncio.Semaphore) -> None:
        """标识当前 turn 已持有 Gateway 全局执行槽位。"""
        self._semaphore = semaphore
        self._slot_held = True

    async def acquire_slot(self) -> None:
        """审批结束后重新取得执行槽位。"""
        if self._semaphore is not None and not self._slot_held:
            await self._semaphore.acquire()
            self._slot_held = True

    def release_slot(self) -> None:
        """等待审批时释放执行槽位给其他 Channel。"""
        if self._semaphore is not None and self._slot_held:
            self._semaphore.release()
            self._slot_held = False

    def resolve_approval(self, approval_id: str, approved: bool) -> bool:
        """完成一个仍然有效的审批请求。"""
        future = self.approvals.get(approval_id)
        if future is None or future.done():
            return False
        future.set_result(approved)
        return True

    def stop(self) -> None:
        """停止当前 turn，并以拒绝方式解除全部等待审批。"""
        self.stop_requested = True
        for future in self.approvals.values():
            if not future.done():
                future.set_result(False)

    @property
    def recipient(self) -> Recipient:
        """返回本轮唯一的 CLI 收件目标。"""
        return Recipient(self.route.principal_id, self.route.channel, self.route.conversation_id)


class CliGatewayAdapter:
    """将共享 Runtime 的 CLI 回调转换为定向出站消息。"""

    def __init__(
        self,
        bus: AsyncMessageBus,
        control: CliTurnControl,
        *,
        execution_semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        self._bus = bus
        self._control = control
        if execution_semaphore is not None:
            self._control.bind_slot(execution_semaphore)

    async def on_delta(self, text: str) -> None:
        """发布模型流式文本。"""
        if text:
            await self._publish("response.delta", {"content": text})

    async def on_status(self, text: str) -> None:
        """发布运行中状态。"""
        await self._publish("task.status", {"content": text})

    async def on_tool_started(
        self,
        tool_call_id: str,
        tool_name: str,
        args: Mapping[str, Any],
    ) -> None:
        """发布工具开始状态。"""
        await self._publish(
            "tool.started",
            {"tool_call_id": tool_call_id, "tool_name": tool_name, "args": dict(args)},
        )

    async def on_tool_finished(self, tool_call_id: str, tool_name: str, failed: bool) -> None:
        """发布工具结束状态。"""
        await self._publish(
            "tool.finished",
            {"tool_call_id": tool_call_id, "tool_name": tool_name, "failed": failed},
        )

    async def on_completed(self, content: str) -> None:
        """终态由 GatewayTurnCoordinator 统一投递。"""
        del content

    async def on_error(self, content: str) -> None:
        """终态由 GatewayTurnCoordinator 统一投递。"""
        del content

    # 将固定字段的 Multi-Agent 事件投影到现有 CLI 通道。
    async def on_multi_agent_event(self, event_type: str, payload: Mapping[str, Any]) -> None:
        safe_payload = {
            key: value
            for key, value in payload.items()
            if key in MULTI_AGENT_LIVE_EVENT_FIELDS
        }
        await self._publish(event_type, safe_payload)

    async def request_tool_approval(self, call: ToolCall) -> str | None:
        """向当前 CLI 发送审批请求并等待其选择。"""
        approval_id = self._control.create_approval()
        await self._publish(
            "approval.requested",
            {
                "approval_id": approval_id,
                "tool_name": call.name,
                "args": call.args,
            },
        )
        approved = await self._control.wait_approval(approval_id)
        await self._publish("approval.resolved", {"approval_id": approval_id, "approved": approved})
        return None if approved else "用户拒绝执行工具"

    async def consume_guidance(self) -> list[str]:
        """CLI 不支持运行中引导，保留 Runtime 兼容接口。"""
        return []

    def is_stop_requested(self) -> bool:
        """返回当前 CLI 是否请求停止。"""
        return self._control.is_stop_requested()

    async def _publish(self, event_type: str, payload: dict[str, object]) -> None:
        content = str(payload.pop("content", ""))
        await self._bus.publish_outbound(
            OutboundMessage(
                str(uuid4()),
                self._control.session_id,
                recipient=self._control.recipient,
                content=content,
                event_type=event_type,
                event_id=self._control.request_id,
                metadata={"request_id": self._control.request_id, **payload},
            )
        )


class CliGatewayCoordinator:
    """将远程 CLI 输入和控制操作提交给唯一 Gateway turn 协调器。"""

    def __init__(
        self,
        bus: AsyncMessageBus,
        *,
        submit: SubmitTurn,
        complete_route_turn: CompleteRouteTurn,
        discard_pending_route: DiscardPendingRoute,
        on_idle: IdleCallback | None = None,
        execution_semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        self._bus = bus
        self._submit = submit
        self._complete_route_turn = complete_route_turn
        self._discard_pending_route = discard_pending_route
        self._on_idle = on_idle
        self._execution_semaphore = execution_semaphore
        self._controls: dict[str, CliTurnControl] = {}
        self._action_lock = asyncio.Lock()
        self._closed = False

    async def start(self) -> None:
        """允许 Gateway 生命周期显式开启 CLI 控制面。"""
        self._closed = False

    async def close(self) -> None:
        """停止全部 CLI 控制，不会创建或关闭 Runtime。"""
        self._closed = True
        for control in self._controls.values():
            control.stop()

    def register_runtime(self, runtime: Any) -> None:
        """为当前或替换后的唯一 Runtime 注册 CLI 单轮适配器。"""
        runtime.channel_router.register(
            "cli",
            lambda message: CliGatewayAdapter(
                self._bus,
                self._controls[message.session_id],
                execution_semaphore=self._execution_semaphore,
            ),
        )

    # 发送单次 CLI 消息并附带可选的多 Agent 模式。
    async def send(
        self,
        route: ChannelRoute,
        request_id: str,
        content: str,
        multi_agent_mode: str | None = None,
    ) -> str:
        """提交一条拥有独立 request-id 的普通 CLI 消息。"""
        text = content.strip()
        if not text:
            raise ValueError("消息不能为空")
        if not request_id:
            raise ValueError("request_id 不能为空")
        if route.channel != "cli":
            raise ValueError("CLI 控制器只接受 cli route")
        if self._closed:
            raise RuntimeError("Gateway CLI 控制器已关闭")
        metadata = _multi_agent_metadata(multi_agent_mode)
        async with self._action_lock:
            active = self._controls.get(route.session_id)
            if active is not None and active.route != route:
                raise RuntimeError("CLI 会话路由不一致")

            async def dispatch(message: InboundMessage) -> None:
                control = CliTurnControl(route.session_id, request_id, route)
                self._controls[route.session_id] = control
                try:
                    await self._bus.publish_inbound(message)
                except BaseException:
                    if self._controls.get(route.session_id) is control:
                        self._controls.pop(route.session_id, None)
                    raise

            accepted = await self._submit(
                InboundMessage(request_id, route=route, content=text, metadata=metadata),
                dispatch=dispatch,
            )
            if not accepted:
                raise RuntimeError("Gateway 拒绝重复请求")
            return request_id

    async def stop(self, route: ChannelRoute) -> None:
        """请求指定 CLI turn 在下一个安全检查点停止。"""
        control = self._control_for(route)
        control.stop()
        await self._publish_status(control, "正在停止", event_type="task.stopping")

    async def resolve_approval(self, route: ChannelRoute, approval_id: str, approved: bool) -> bool:
        """将当前连接的审批选择交给对应 CLI turn。"""
        return self._control_for(route).resolve_approval(approval_id, approved)

    async def disconnect(self, route: ChannelRoute) -> None:
        """断线时停止来源 CLI turn，后台任务和其他 Channel 不受影响。"""
        control = self._controls.get(route.session_id)
        if control is not None and control.route == route:
            control.stop()
        await self._discard_pending_route(route)

    async def on_delivery(self, message: OutboundMessage, accepted: bool) -> None:
        """终态已尝试投递后释放来源 CLI 控制，即使连接已断开。"""
        del accepted
        if message.recipient.channel != "cli" or message.disposition != "chat_reply":
            return
        control = self._controls.get(message.session_id)
        if control is None or message.recipient != control.recipient:
            return
        request_id = str(message.metadata.get("request_id", message.event_id))
        if request_id != control.request_id:
            return
        if message.event_type not in {"response.completed", "response.error"}:
            return
        self._controls.pop(message.session_id, None)
        await self._complete_route_turn(control.route, request_id)
        if self._on_idle is not None and self.is_globally_idle():
            await self._on_idle()

    def is_globally_idle(self) -> bool:
        """报告没有等待审批或尚未收到终态的 CLI turn。"""
        return not self._controls

    def _control_for(self, route: ChannelRoute) -> CliTurnControl:
        control = self._controls.get(route.session_id)
        if control is None or control.route != route:
            raise RuntimeError("当前 CLI 会话没有运行中的任务")
        return control

    async def _publish_status(
        self,
        control: CliTurnControl,
        content: str,
        *,
        event_type: str = "task.status",
    ) -> None:
        await self._bus.publish_outbound(
            OutboundMessage(
                str(uuid4()),
                control.session_id,
                recipient=control.recipient,
                content=content,
                event_type=event_type,
                event_id=control.request_id,
                metadata={"request_id": control.request_id},
            )
        )

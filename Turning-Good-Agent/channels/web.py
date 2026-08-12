from __future__ import annotations

import asyncio
import inspect
import json
from collections.abc import Awaitable, Callable, Mapping
from typing import Any
from uuid import uuid4

from ..bus.messages import ChannelRoute, OutboundMessage, Recipient
from ..bus.queue import AsyncMessageBus
from ..llm.types import ToolCall
from ..multi_agent.events import MULTI_AGENT_LIVE_EVENT_FIELDS
from ..web.backend.coordinator import WebTurnControl


class WebChannelTransport:
    """由 Gateway 定向调用的 Web Chat 与主动面板传输层。"""

    name = "web"

    def __init__(
        self,
        coordinator: Any,
        publish_proactive_notice: Callable[[OutboundMessage], Awaitable[object] | object],
    ) -> None:
        self._coordinator = coordinator
        self._publish_proactive_notice = publish_proactive_notice

    async def start(self) -> None:
        """WebSocket 路由由 FastAPI 管理，无需独立连接。"""

    async def close(self) -> None:
        """WebSocket 路由由 FastAPI 管理，无需独立连接。"""

    async def send(self, message: OutboundMessage) -> bool:
        """按 disposition 分别投影聊天事件和主动面板 notice。"""
        if message.recipient.channel != self.name:
            return False
        if message.disposition == "proactive_notification":
            result = self._publish_proactive_notice(message)
            if inspect.isawaitable(result):
                result = await result
            return result is not False
        return bool(await self._coordinator.deliver(message))


class WebChannelAdapter:
    """将 Runtime Channel 回调转换为 Web 会话事件。"""

    def __init__(
        self,
        bus: AsyncMessageBus,
        control: WebTurnControl,
        *,
        route: ChannelRoute | None = None,
        execution_semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        """绑定消息队列和单轮控制器。"""
        self.bus = bus
        self.control = control
        self.route = route or control.route
        if self.route is None:
            raise ValueError("Web Channel Adapter 缺少 ChannelRoute")
        if execution_semaphore is not None:
            self.control.bind_slot(execution_semaphore)

    async def on_delta(self, text: str) -> None:
        """推送 assistant 流式增量。"""
        if text:
            await self._publish("response.delta", {"content": text})

    async def on_status(self, text: str) -> None:
        """推送运行状态文本。"""
        await self._publish("task.status", {"content": text})

    async def on_tool_started(
        self,
        tool_call_id: str,
        tool_name: str,
        args: Mapping[str, Any],
    ) -> None:
        """推送工具开始状态。"""
        await self._publish(
            "tool.started",
            {"tool_call_id": tool_call_id, "tool_name": tool_name, "args": dict(args)},
        )

    async def on_tool_finished(self, tool_call_id: str, tool_name: str, failed: bool) -> None:
        """推送工具完成状态。"""
        await self._publish(
            "tool.finished", {"tool_call_id": tool_call_id, "tool_name": tool_name, "failed": failed}
        )

    async def on_completed(self, content: str) -> None:
        """由 Runtime 终态 OutboundMessage 统一发送成功内容。"""
        del content

    async def on_error(self, content: str) -> None:
        """由 Runtime 终态 OutboundMessage 统一发送错误内容。"""
        del content

    # 将固定字段的 Multi-Agent 事件投影到现有 Web 通道。
    async def on_multi_agent_event(self, event_type: str, payload: Mapping[str, Any]) -> None:
        safe_payload = {
            key: value
            for key, value in payload.items()
            if key in MULTI_AGENT_LIVE_EVENT_FIELDS
        }
        await self._publish(event_type, safe_payload)

    async def request_tool_approval(self, call: ToolCall) -> str | None:
        """发布审批卡并等待当前会话的用户决定。"""
        approval_id = self.control.create_approval()
        await self._publish(
            "approval.requested",
            {"approval_id": approval_id, "tool_name": call.name, "args": json.dumps(call.args, ensure_ascii=False, sort_keys=True)},
        )
        approved = await self.control.wait_approval(approval_id)
        await self._publish("approval.resolved", {"approval_id": approval_id, "approved": approved})
        return None if approved else "用户拒绝执行工具"

    async def consume_guidance(self) -> list[str]:
        """取走已排队的运行中引导。"""
        return await self.control.consume_guidance()

    def is_stop_requested(self) -> bool:
        """返回协作式停止标记。"""
        return self.control.is_stop_requested()

    async def _publish(self, event_type: str, payload: dict[str, object]) -> None:
        """将中间输出写入统一 OutboundMessage 队列。"""
        content = str(payload.pop("content", ""))
        await self.bus.publish_outbound(
            OutboundMessage(
                str(uuid4()),
                self.control.session_id,
                recipient=Recipient(
                    self.route.principal_id,
                    self.route.channel,
                    self.route.conversation_id,
                ),
                content=content,
                event_type=event_type,
                event_id=self.control.request_id,
                metadata={"request_id": self.control.request_id, **payload},
            )
        )

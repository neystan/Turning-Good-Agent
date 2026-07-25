from __future__ import annotations

import json
from uuid import uuid4

from ..bus.messages import OutboundMessage
from ..bus.queue import AsyncMessageBus
from ..llm.types import ToolCall
from ..web.backend.coordinator import WebTurnControl


class WebChannelAdapter:
    """将 Runtime Channel 回调转换为 Web 会话事件。"""

    def __init__(self, bus: AsyncMessageBus, control: WebTurnControl) -> None:
        """绑定消息队列和单轮控制器。"""
        self.bus = bus
        self.control = control

    async def on_delta(self, text: str) -> None:
        """推送 assistant 流式增量。"""
        if text:
            await self._publish("response.delta", {"content": text})

    async def on_status(self, text: str) -> None:
        """推送运行状态文本。"""
        await self._publish("task.status", {"content": text})

    async def on_tool_started(self, tool_call_id: str, tool_name: str) -> None:
        """推送工具开始状态。"""
        await self._publish("tool.started", {"tool_call_id": tool_call_id, "tool_name": tool_name})

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
                "web",
                content,
                event_type,
                {"request_id": self.control.request_id, **payload},
            )
        )

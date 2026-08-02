from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any, Protocol

from ..bus.messages import OutboundMessage
from ..bus.queue import AsyncMessageBus
from ..llm.types import ToolCall


class ApprovalPrompt(Protocol):
    """CLI 共享输入会话的最小接口。"""

    async def prompt_async(self, question: str) -> str:
        """异步读取用户回答。"""
        ...


class CliChannelAdapter:
    """将 CLI 事件发布到 Bus，并负责 CLI 审批交互。"""

    def __init__(
        self,
        bus: AsyncMessageBus,
        session_id: str,
        *,
        prompt: ApprovalPrompt | None = None,
        request_id: str | None = None,
        input_reader: Callable[[str], str] | None = None,
    ) -> None:
        self._bus = bus
        self._session_id = session_id
        self._prompt = prompt
        self._request_id = request_id
        self._input_reader = input_reader or input

    async def on_delta(self, text: str) -> None:
        """发布流式文本增量。"""
        if text:
            message = OutboundMessage.delta(self._session_id, "cli", text)
            if self._request_id:
                message.metadata["request_id"] = self._request_id
            await self._bus.publish_outbound(message)

    async def on_status(self, text: str) -> None:
        """发布渠道状态事件。"""
        await self._publish("cli.status", text)

    async def on_tool_started(self, tool_call_id: str, tool_name: str) -> None:
        """发布工具开始事件。"""
        await self._publish(
            "cli.tool.started",
            f"正在调用工具：{tool_name}",
            {"tool_call_id": tool_call_id},
        )

    async def on_tool_finished(self, tool_call_id: str, tool_name: str, failed: bool) -> None:
        """发布工具结束事件。"""
        status = "工具失败" if failed else "工具完成"
        await self._publish(
            "cli.tool.finished",
            f"{status}：{tool_name}",
            {"tool_call_id": tool_call_id},
        )

    async def on_completed(self, content: str) -> None:
        """终态由 Dispatcher 统一发布。"""
        del content

    async def on_error(self, content: str) -> None:
        """错误终态由 Dispatcher 统一发布。"""
        del content

    async def request_tool_approval(self, call: ToolCall) -> str | None:
        """使用当前 CLI 输入会话询问用户是否批准。"""
        args = json.dumps(call.args, ensure_ascii=False)
        question = f"[审批] 允许执行 {call.name} {args}？[y/N] "
        answer = (
            await self._prompt.prompt_async(question)
            if self._prompt is not None
            else await asyncio.to_thread(self._input_reader, question)
        )
        return None if answer.strip().lower() in {"y", "yes", "允许"} else "用户拒绝执行工具"

    async def consume_guidance(self) -> list[str]:
        """CLI 当前不支持运行中追加引导。"""
        return []

    def is_stop_requested(self) -> bool:
        """CLI 当前不支持独立停止请求。"""
        return False

    async def _publish(
        self,
        event_type: str,
        content: str,
        metadata: dict[str, str] | None = None,
    ) -> None:
        message = OutboundMessage.new(self._session_id, "cli", content)
        message.event_type = event_type
        if self._request_id:
            message.metadata["request_id"] = self._request_id
        if metadata:
            message.metadata.update(metadata)
        await self._bus.publish_outbound(message)

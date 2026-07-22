import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

from ..channels.base import ChannelAdapter
from ..llm.types import ToolCall
from .base import AgentHook

if TYPE_CHECKING:
    from ..runtime.turn_context import TurnContext

logger = logging.getLogger(__name__)


class HookManager:
    """按注册顺序触发进程内生命周期 Hook。"""

    def __init__(self) -> None:
        """初始化按注册顺序保存的 Hook 列表。"""
        self._hooks: list[AgentHook] = []

    def register(self, hook: AgentHook) -> None:
        """按调用顺序注册 Hook。"""
        self._hooks.append(hook)

    async def run_before_tool_call(
        self,
        call: ToolCall,
        channel_adapter: ChannelAdapter,
        auto_approve_tools: bool,
    ) -> str | None:
        """执行工具前 Hook 并返回首个阻断原因。"""
        for hook in self._hooks:
            try:
                reason = await hook.before_tool_call(call, channel_adapter, auto_approve_tools)
            except Exception:
                logger.exception("Hook %s.before_tool_call 执行失败", type(hook).__name__)
                continue
            if reason:
                return str(reason)
        return None

    async def run_tool_started(self, call: ToolCall, channel_adapter: ChannelAdapter) -> None:
        """通知工具即将执行。"""
        for hook in self._hooks:
            try:
                await hook.on_tool_started(call, channel_adapter)
            except Exception:
                logger.exception("Hook %s.on_tool_started 执行失败", type(hook).__name__)

    async def run_after_tool_call(
        self,
        call: ToolCall,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        """执行工具结果处理管道并返回最终记录。"""
        current = dict(record)
        for hook in self._hooks:
            try:
                updated = await hook.after_tool_call(call, dict(current))
            except Exception:
                logger.exception("Hook %s.after_tool_call 执行失败", type(hook).__name__)
                continue
            if not isinstance(updated, Mapping):
                logger.error("Hook %s.after_tool_call 返回值不是 Mapping", type(hook).__name__)
                continue
            current.update(updated)
        return current

    async def run_before_compact(self, ctx: "TurnContext") -> None:
        """执行全部压缩前 Hook。"""
        for hook in self._hooks:
            try:
                await hook.before_compact(ctx)
            except Exception:
                logger.exception("Hook %s.before_compact 执行失败", type(hook).__name__)

    async def run_after_compact(self, ctx: "TurnContext") -> None:
        """执行全部压缩后 Hook。"""
        for hook in self._hooks:
            try:
                await hook.after_compact(ctx)
            except Exception:
                logger.exception("Hook %s.after_compact 执行失败", type(hook).__name__)

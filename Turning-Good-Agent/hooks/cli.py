import json
from typing import TYPE_CHECKING

from ..llm.types import ToolCall
from .base import AgentHook

if TYPE_CHECKING:
    from ..runtime.turn_context import TurnContext


APPROVAL_REQUIRED_TOOLS = frozenset({"write_file", "edit_file", "exec", "write_stdin"})


class CliToolApprovalHook(AgentHook):
    """在 CLI 中同步审批具有副作用的工具。"""

    async def before_tool_call(self, call: ToolCall) -> str | None:
        """询问用户是否允许执行当前副作用工具。"""
        if call.name not in APPROVAL_REQUIRED_TOOLS:
            return None
        args = json.dumps(call.args, ensure_ascii=False)
        answer = input(f"\n[审批] 允许执行 {call.name} {args}？[y/N] ").strip().lower()
        return None if answer in {"y", "yes", "允许"} else "用户拒绝执行工具"


class CliCompactStatusHook(AgentHook):
    """向 CLI 输出上下文压缩状态。"""

    async def before_compact(self, ctx: "TurnContext") -> None:
        """提示 CLI 正在压缩会话上下文。"""
        del ctx
        print("\n[系统] 正在压缩会话上下文...", flush=True)

    async def after_compact(self, ctx: "TurnContext") -> None:
        """向 CLI 输出本次压缩统计。"""
        stats = ctx.compact_stats
        print(
            "\n[系统] 压缩完成："
            f"已压缩 {stats['compacted_message_count']} 条消息，"
            f"压缩 {stats['compacted_token_count']} tokens，"
            f"保留最近 {stats['raw_window_token_count']} tokens 原文。",
            flush=True,
        )

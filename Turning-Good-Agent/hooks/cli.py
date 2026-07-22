import json

from ..llm.types import ToolCall
from .base import AgentHook


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

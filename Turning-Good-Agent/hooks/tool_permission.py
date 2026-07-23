from ..channels.base import ChannelAdapter
from ..llm.types import ToolCall
from ..tools.registry import ToolRegistry
from .base import AgentHook


class ToolPermissionHook(AgentHook):
    """按当前会话设置处理审批类工具。"""

    def __init__(
        self,
        approval_required_tools: frozenset[str],
        tools: ToolRegistry | None = None,
        mcp_manager: object | None = None,
    ) -> None:
        """保存内置工具、注册表与可选 MCP 审批策略。"""
        self.approval_required_tools = approval_required_tools
        self.tools = tools
        self.mcp_manager = mcp_manager

    async def before_tool_call(
        self,
        call: ToolCall,
        channel_adapter: ChannelAdapter,
        auto_approve_tools: bool,
    ) -> str | None:
        """在关闭自动审批时委托当前 Channel 请求确认。"""
        if auto_approve_tools:
            return None
        tool = self.tools.get(call.name) if self.tools is not None and self.tools.has(call.name) else None
        needs_approval = call.name in self.approval_required_tools or bool(
            getattr(tool, "approval_required", False)
        )
        if self.mcp_manager is not None:
            requires_approval = getattr(self.mcp_manager, "requires_approval", None)
            if callable(requires_approval):
                is_mcp_operation = call.name.startswith("mcp_") or call.name in {
                    "attach_mcp_resource",
                    "apply_mcp_prompt",
                }
                if is_mcp_operation:
                    needs_approval = bool(requires_approval(call.name, call.args))
        if not needs_approval:
            return None
        request_approval = getattr(channel_adapter, "request_tool_approval", None)
        if not callable(request_approval):
            return "当前 Channel 不支持工具审批。"
        try:
            return await request_approval(call)
        except Exception:
            return "当前 Channel 不支持工具审批。"

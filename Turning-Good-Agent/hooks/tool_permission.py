from ..channels.base import ChannelAdapter
from ..llm.types import ToolCall
from .base import AgentHook


class ToolPermissionHook(AgentHook):
    """按当前会话设置处理审批类工具。"""

    def __init__(self, approval_required_tools: frozenset[str]) -> None:
        """保存需要审批的工具名称。"""
        self.approval_required_tools = approval_required_tools

    async def before_tool_call(
        self,
        call: ToolCall,
        channel_adapter: ChannelAdapter,
        auto_approve_tools: bool,
    ) -> str | None:
        """在关闭自动审批时委托当前 Channel 请求确认。"""
        if call.name not in self.approval_required_tools or auto_approve_tools:
            return None
        request_approval = getattr(channel_adapter, "request_tool_approval", None)
        if not callable(request_approval):
            return "当前 Channel 不支持工具审批。"
        try:
            return await request_approval(call)
        except Exception:
            return "当前 Channel 不支持工具审批。"

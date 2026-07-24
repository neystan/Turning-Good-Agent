from ..channels.base import ChannelAdapter
from ..llm.types import ToolCall
from ..tools.registry import ToolRegistry
from .base import AgentHook


def validate_tool_permission_tools(tools: ToolRegistry, configured_names: list[str]) -> None:
    """校验审批配置和审批 Tool 都不会并行执行。"""
    for name in configured_names:
        if not tools.has(name):
            raise ValueError(f"审批工具未注册：{name}")
    names = set(configured_names)
    names.update(name for name in tools.tool_names if bool(getattr(tools.get(name), "approval_required", False)))
    for name in names:
        if bool(getattr(tools.get(name), "parallel_safe", False)):
            raise ValueError(f"审批工具不能设置 parallel_safe=true：{name}")


class ToolPermissionHook(AgentHook):
    """按当前会话设置处理审批类工具。"""

    def __init__(
        self,
        approval_required_tools: frozenset[str],
        tools: ToolRegistry | None = None,
    ) -> None:
        """保存内置审批工具与注册表。"""
        self.approval_required_tools = approval_required_tools
        self.tools = tools

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
        if not needs_approval:
            return None
        request_approval = getattr(channel_adapter, "request_tool_approval", None)
        if not callable(request_approval):
            return "当前 Channel 不支持工具审批。"
        try:
            return await request_approval(call)
        except Exception:
            return "当前 Channel 不支持工具审批。"

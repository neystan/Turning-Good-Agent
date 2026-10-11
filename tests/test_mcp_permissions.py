import asyncio
from importlib import import_module


SilentChannelAdapter = import_module("Turning-Good-Agent.channels.base").SilentChannelAdapter
ToolPermissionHook = import_module("Turning-Good-Agent.hooks.tool_permission").ToolPermissionHook
ToolCall = import_module("Turning-Good-Agent.llm.types").ToolCall
ToolRegistry = import_module("Turning-Good-Agent.tools.registry").ToolRegistry


class AdapterTool:
    name = "mcp_github_list_issues"
    description = "issues"
    input_schema = {"type": "object", "properties": {}}
    parallel_safe = False
    approval_required = True


class AttachmentTool:
    """模拟会读取远端内容的 MCP 附件工具。"""

    approval_required = True

    def __init__(self, name: str) -> None:
        """保存工具名称。"""
        self.name = name
        self.description = name
        self.input_schema = {"type": "object", "properties": {}}
        self.parallel_safe = False


def test_mcp_approval_only_uses_session_auto_approval():
    """验证 MCP 审批仅由会话自动审批开关控制。"""
    async def run_test():
        registry = ToolRegistry()
        registry.register(AdapterTool())
        registry.register(AttachmentTool("attach_mcp_resource"))
        registry.register(AttachmentTool("apply_mcp_prompt"))
        hook = ToolPermissionHook(frozenset(), registry)
        adapter = SilentChannelAdapter()
        return (
            await hook.before_tool_call(ToolCall("1", "mcp_github_list_issues", {}), adapter, False),
            await hook.before_tool_call(ToolCall("2", "mcp_github_list_issues", {}), adapter, True),
            await hook.before_tool_call(
                ToolCall("3", "attach_mcp_resource", {"server_name": "github"}), adapter, False
            ),
            await hook.before_tool_call(
                ToolCall("4", "apply_mcp_prompt", {"server_name": "github"}), adapter, False
            ),
        )

    blocked_tool, session_auto, blocked_attachment, blocked_prompt = asyncio.run(run_test())

    assert blocked_tool == "当前 Channel 不支持工具审批。"
    assert session_auto is None
    assert blocked_attachment == "当前 Channel 不支持工具审批。"
    assert blocked_prompt == "当前 Channel 不支持工具审批。"


def test_mcp_annotations_do_not_bypass_approval():
    """验证 MCP annotations 只用于展示，不能免审批。"""

    async def run_test():
        registry = ToolRegistry()
        tool = AdapterTool()
        tool.metadata = {"annotations": {"readOnlyHint": True}}
        registry.register(tool)
        hook = ToolPermissionHook(frozenset(), registry)
        return await hook.before_tool_call(
            ToolCall("1", "mcp_github_list_issues", {}), SilentChannelAdapter(), False
        )

    assert asyncio.run(run_test()) == "当前 Channel 不支持工具审批。"

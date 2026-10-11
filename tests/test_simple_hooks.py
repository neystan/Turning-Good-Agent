import asyncio
from importlib import import_module


AgentHook = import_module("Turning-Good-Agent.hooks.base").AgentHook
HookManager = import_module("Turning-Good-Agent.hooks.manager").HookManager
ToolPermissionHook = import_module("Turning-Good-Agent.hooks.tool_permission").ToolPermissionHook
SilentChannelAdapter = import_module("Turning-Good-Agent.channels.base").SilentChannelAdapter
ToolCall = import_module("Turning-Good-Agent.llm.types").ToolCall
ToolRegistry = import_module("Turning-Good-Agent.tools.registry").ToolRegistry


def test_tool_permission_hook_only_requests_approval_when_required():
    """验证权限 Hook 仅对关闭自动审批的目标工具请求确认。"""

    class RecordingAdapter:
        """记录工具审批请求。"""

        def __init__(self) -> None:
            """初始化请求记录。"""
            self.calls = []

        async def request_tool_approval(self, call):
            """记录并批准工具。"""
            self.calls.append(call.name)
            return None

    async def run() -> None:
        hook = ToolPermissionHook(frozenset({"exec"}))
        adapter = RecordingAdapter()

        assert await hook.before_tool_call(ToolCall("call-1", "read_file", {}), adapter, False) is None
        assert await hook.before_tool_call(ToolCall("call-2", "exec", {"command": "pwd"}), adapter, True) is None
        assert await hook.before_tool_call(ToolCall("call-3", "exec", {"command": "pwd"}), adapter, False) is None
        assert adapter.calls == ["exec"]

    asyncio.run(run())


def test_silent_channel_adapter_rejects_tool_approval():
    """验证未实现审批交互的 Channel 返回固定拒绝原因。"""

    async def run() -> None:
        adapter = SilentChannelAdapter()
        return await adapter.request_tool_approval(ToolCall("call-1", "exec", {"command": "pwd"}))

    assert asyncio.run(run()) == "当前 Channel 不支持工具审批。"


def test_tool_permission_rejects_adapter_without_approval_capability():
    """验证缺少审批能力的适配器不会绕过工具权限。"""

    class IncompleteAdapter:
        """模拟未实现审批能力的 Channel。"""

    async def run() -> str | None:
        manager = HookManager()
        manager.register(ToolPermissionHook(frozenset({"exec"})))
        return await manager.run_before_tool_call(
            ToolCall("call-1", "exec", {"command": "pwd"}),
            IncompleteAdapter(),
            False,
        )

    assert asyncio.run(run()) == "当前 Channel 不支持工具审批。"


def test_search_mcp_capabilities_does_not_require_approval():
    """验证本地 MCP Catalog 查询不进入审批。"""

    class SearchTool:
        """模拟只查询本地 Catalog 的工具。"""

        name = "search_mcp_capabilities"
        approval_required = False

    async def run() -> str | None:
        registry = ToolRegistry()
        registry.register(SearchTool())
        hook = ToolPermissionHook(frozenset(), registry)
        return await hook.before_tool_call(
            ToolCall("call-1", "search_mcp_capabilities", {"query": "demo"}),
            SilentChannelAdapter(),
            False,
        )

    assert asyncio.run(run()) is None


def test_hook_manager_uses_clear_run_methods():
    """验证 HookManager 使用明确的 run 方法调度 Hook。"""

    class BlockingHook(AgentHook):
        """阻止测试工具执行。"""

        async def before_tool_call(self, call, channel_adapter, auto_approve_tools):
            """返回工具阻断原因。"""
            del call, channel_adapter, auto_approve_tools
            return "测试阻断"

    async def run() -> None:
        manager = HookManager()
        manager.register(BlockingHook())
        reason = await manager.run_before_tool_call(
            ToolCall("call-1", "echo", {}),
            SilentChannelAdapter(),
            False,
        )
        assert reason == "测试阻断"

    asyncio.run(run())


def test_hook_manager_notifies_tool_started():
    """验证 HookManager 通知工具即将执行。"""

    class RecordingHook(AgentHook):
        """记录工具开始事件。"""

        def __init__(self):
            """初始化工具名记录。"""
            self.names = []

        async def on_tool_started(self, call, output):
            """记录工具名。"""
            del output
            self.names.append(call.name)

    async def run() -> list[str]:
        manager = HookManager()
        hook = RecordingHook()
        manager.register(hook)
        await manager.run_tool_started(ToolCall("call-1", "web_search", {}), SilentChannelAdapter())
        return hook.names

    assert asyncio.run(run()) == ["web_search"]

import asyncio
from importlib import import_module


McpManager = import_module("Turning-Good-Agent.mcp.manager").McpManager
McpCatalog = import_module("Turning-Good-Agent.mcp.types").McpCatalog
McpCapability = import_module("Turning-Good-Agent.mcp.types").McpCapability
McpServerSettings = import_module("Turning-Good-Agent.config.settings").McpServerSettings
McpSettings = import_module("Turning-Good-Agent.config.settings").McpSettings
ToolRegistry = import_module("Turning-Good-Agent.tools.registry").ToolRegistry


async def eventually(predicate, timeout=1.0):
    """等待异步状态满足断言。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("等待 Manager 状态超时")
        await asyncio.sleep(0.001)


class FakeTool:
    name = "mcp_old_lookup"
    description = "old"
    input_schema = {"type": "object", "properties": {}}
    parallel_safe = False


class FakeClient:
    def __init__(self, name, settings, should_fail=False):
        self.name = name
        self.should_fail = should_fail
        self.closed = False
        self.discover_count = 0
        self._list_changed_handler = None

    async def connect(self):
        if self.should_fail:
            raise RuntimeError("offline")

    async def discover(self):
        self.discover_count += 1
        return McpCatalog(
            tools=[McpCapability(self.name, "tool", "lookup", "lookup issue", {"input_schema": {}})],
            resources=[McpCapability(self.name, "resource", "issue", "issue resource", {"uri": "issue://1"})],
        )

    async def close(self):
        self.closed = True

    def set_list_changed_handler(self, handler):
        """记录目录变更回调。"""
        self._list_changed_handler = handler


def test_manager_isolates_failed_servers_and_searches_catalog():
    created = {}

    def factory(name, settings):
        client = FakeClient(name, settings, name == "bad")
        created[name] = client
        return client

    async def run_test():
        settings = McpSettings(
            servers={
                "good": McpServerSettings(enabled=True, command="python"),
                "bad": McpServerSettings(enabled=True, command="python"),
            }
        )
        manager = McpManager(settings, client_factory=factory)
        registry = ToolRegistry()
        await manager.start_background(registry)
        await eventually(lambda: manager.statuses.get("good") and manager.statuses["good"].connected)
        await eventually(lambda: manager.statuses.get("bad") and manager.statuses["bad"].state == "failed")
        found = await manager.search_capabilities("issue", ["tool", "resource"], 1)
        statuses = manager.statuses.copy()
        await manager.close()
        return statuses, found

    statuses, found = asyncio.run(run_test())

    assert statuses["good"].connected is True
    assert statuses["bad"].error == "offline"
    assert found[0].server_name == "good"
    assert created["good"].closed is True


def test_refresh_unregisters_server_prefix_and_invalidates_schema_cache():
    clients = []

    def factory(name, settings):
        client = FakeClient(name, settings)
        clients.append(client)
        return client

    async def run_test():
        registry = ToolRegistry()
        registry.register(FakeTool())
        assert registry.schemas()[0]["name"] == "mcp_old_lookup"
        manager = McpManager(
            McpSettings(servers={"old": McpServerSettings(enabled=True, command="python")}),
            client_factory=factory,
        )
        await manager.refresh_server("old", registry)
        await eventually(lambda: manager.statuses.get("old") and manager.statuses["old"].connected)
        await manager.close()
        return registry, clients

    registry, clients = asyncio.run(run_test())

    assert registry.tool_names == []
    assert registry.schemas() == []
    assert clients[0].closed is True


def test_list_changed_only_refreshes_catalog_without_reconnecting_client():
    """验证目录通知不跨任务关闭已建立的 MCP transport。"""

    created = []

    def factory(name, settings):
        client = FakeClient(name, settings)
        created.append(client)
        return client

    async def run_test():
        registry = ToolRegistry()
        manager = McpManager(
            McpSettings(
                servers={"demo": McpServerSettings(enabled=True, command="python", enabled_tools=["lookup"])}
            ),
            client_factory=factory,
        )
        await manager.start_background(registry)
        await eventually(lambda: manager.statuses.get("demo") and manager.statuses["demo"].connected)
        status = await manager.handle_list_changed("demo", registry)
        result = status, registry, created[0].discover_count
        await manager.close()
        return result

    status, registry, discover_count = asyncio.run(run_test())

    assert status.connected is True
    assert len(created) == 1
    assert discover_count == 2
    assert registry.tool_names == ["mcp_demo_lookup"]


def test_manager_starts_servers_in_background_without_waiting_for_slow_server():
    """验证慢 MCP Server 不阻塞其他 Server 注册工具。"""

    class SlowClient(FakeClient):
        async def connect(self):
            await gate.wait()

    async def run_test():
        nonlocal gate
        gate = asyncio.Event()

        def factory(name, settings):
            if name == "slow":
                return SlowClient(name, settings)
            return FakeClient(name, settings)

        manager = McpManager(
            McpSettings(
                servers={
                    "fast": McpServerSettings(enabled=True, command="python", enabled_tools=["lookup"]),
                    "slow": McpServerSettings(enabled=True, command="python", enabled_tools=["lookup"]),
                }
            ),
            client_factory=factory,
        )
        registry = ToolRegistry()
        await manager.start_background(registry)
        await eventually(lambda: manager.statuses.get("fast", None) and manager.statuses["fast"].connected)
        result = (manager.statuses["slow"].state, registry.tool_names)
        gate.set()
        await manager.close()
        return result

    gate = None
    slow_state, tool_names = asyncio.run(run_test())

    assert slow_state == "connecting"
    assert tool_names == ["mcp_fast_lookup"]

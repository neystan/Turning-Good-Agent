import asyncio
from importlib import import_module


McpServerSettings = import_module("Turning-Good-Agent.config.settings").McpServerSettings
McpCatalog = import_module("Turning-Good-Agent.mcp.types").McpCatalog
McpCapability = import_module("Turning-Good-Agent.mcp.types").McpCapability
McpServerWorker = import_module("Turning-Good-Agent.mcp.server_worker").McpServerWorker


async def eventually(predicate, timeout=1.0):
    """等待异步状态满足断言。"""
    deadline = asyncio.get_running_loop().time() + timeout
    while not predicate():
        if asyncio.get_running_loop().time() >= deadline:
            raise AssertionError("等待 Worker 状态超时")
        await asyncio.sleep(0.001)


class ClientFactory:
    """按预设结果创建模拟 MCP Client。"""

    def __init__(self, connect_errors=None, tool_error=None):
        self.connect_errors = list(connect_errors or [])
        self.tool_error = tool_error
        self.clients = []

    def __call__(self, name, settings):
        client = FakeClient(name, settings, self)
        self.clients.append(client)
        return client


class FakeClient:
    """模拟可连接、可关闭的 MCP Client。"""

    def __init__(self, name, settings, factory):
        self.name = name
        self.settings = settings
        self.factory = factory
        self.closed_by = None
        self._handler = None

    def set_list_changed_handler(self, handler):
        """保存目录变更通知处理器。"""
        self._handler = handler

    async def connect(self):
        """按预设抛出连接异常。"""
        if self.factory.connect_errors:
            raise self.factory.connect_errors.pop(0)

    async def discover(self):
        """返回固定工具目录。"""
        return McpCatalog(tools=[McpCapability(self.name, "tool", "lookup", "lookup", {"input_schema": {}})])

    async def call_tool(self, name, arguments):
        """返回或抛出预设工具结果。"""
        del name, arguments
        if self.factory.tool_error is not None:
            raise self.factory.tool_error
        return "ok"

    async def read_resource(self, uri):
        """返回固定 Resource。"""
        return str(uri)

    async def get_prompt(self, name, arguments):
        """返回固定 Prompt。"""
        return [{"role": "user", "content": f"{name}:{arguments}"}]

    async def close(self):
        """记录关闭所在 Task。"""
        self.closed_by = asyncio.current_task()


def worker_settings():
    """创建快速重试的测试配置。"""
    return McpServerSettings(
        enabled=True,
        command="python",
        connect_retry_attempts=3,
        connect_retry_delay_seconds=0.001,
        connect_retry_max_delay_seconds=0.001,
    )


def test_worker_retries_connection_and_closes_client_in_owner_task():
    """验证连接失败重试且由 Worker Task 关闭 Client。"""

    async def run_test():
        statuses = []
        catalogs = []
        factory = ClientFactory(connect_errors=[OSError("offline"), OSError("offline")])

        async def on_catalog(catalog):
            catalogs.append(catalog)

        async def on_status(status):
            statuses.append(status)

        worker = McpServerWorker("demo", worker_settings(), factory, on_catalog, on_status)
        await worker.start()
        await eventually(lambda: statuses and statuses[-1].state == "connected")
        owner = worker.task
        await worker.close()
        return factory, statuses, catalogs, owner

    factory, statuses, catalogs, owner = asyncio.run(run_test())

    assert [status.state for status in statuses] == ["connecting", "retry_wait", "connecting", "retry_wait", "connecting", "connected", "closed"]
    assert statuses[-2].attempt == 3
    assert len(catalogs) == 1
    assert factory.clients[-1].closed_by is owner


def test_worker_does_not_retry_after_tool_business_error():
    """验证业务错误只失败当前工具调用。"""

    async def run_test():
        statuses = []
        factory = ClientFactory(tool_error=RuntimeError("permission denied"))

        async def on_catalog(catalog):
            del catalog

        async def on_status(status):
            statuses.append(status)

        worker = McpServerWorker("demo", worker_settings(), factory, on_catalog, on_status)
        await worker.start()
        await eventually(lambda: statuses and statuses[-1].state == "connected")
        try:
            await worker.call_tool("lookup", {})
        except RuntimeError as exc:
            error = str(exc)
        await asyncio.sleep(0.01)
        await worker.close()
        return factory, error

    factory, error = asyncio.run(run_test())

    assert error == "permission denied"
    assert len(factory.clients) == 1


def test_worker_retries_after_tool_connection_error():
    """验证连接级工具错误会重启连接周期。"""

    async def run_test():
        statuses = []
        factory = ClientFactory(tool_error=OSError("connection lost"))

        async def on_catalog(catalog):
            del catalog

        async def on_status(status):
            statuses.append(status)

        worker = McpServerWorker("demo", worker_settings(), factory, on_catalog, on_status)
        await worker.start()
        await eventually(lambda: statuses and statuses[-1].state == "connected")
        try:
            await worker.call_tool("lookup", {})
        except RuntimeError as exc:
            error = str(exc)
        factory.tool_error = None
        await eventually(lambda: len(factory.clients) == 2 and statuses[-1].state == "connected")
        await worker.close()
        return statuses, error

    statuses, error = asyncio.run(run_test())

    assert error == "connection lost"
    assert [status.attempt for status in statuses if status.state == "connected"] == [1, 1]


def test_worker_closes_while_waiting_to_retry():
    """验证退避等待期间的关闭不会卡住 Worker。"""

    async def run_test():
        statuses = []
        factory = ClientFactory(connect_errors=[OSError("offline")])

        async def on_catalog(catalog):
            del catalog

        async def on_status(status):
            statuses.append(status)

        settings = worker_settings()
        settings.connect_retry_delay_seconds = 1
        settings.connect_retry_max_delay_seconds = 1
        worker = McpServerWorker("demo", settings, factory, on_catalog, on_status)
        await worker.start()
        await eventually(lambda: statuses and statuses[-1].state == "retry_wait")
        await asyncio.wait_for(worker.close(), timeout=0.1)
        return statuses

    statuses = asyncio.run(run_test())

    assert statuses[-1].state == "closed"


def test_worker_reconnect_restarts_retry_cycle_immediately():
    """验证手动重连会跳过等待并从首次尝试重新开始。"""

    async def run_test():
        statuses = []
        factory = ClientFactory(connect_errors=[OSError("offline")])

        async def on_catalog(catalog):
            del catalog

        async def on_status(status):
            statuses.append(status)

        settings = worker_settings()
        settings.connect_retry_delay_seconds = 1
        settings.connect_retry_max_delay_seconds = 1
        worker = McpServerWorker("demo", settings, factory, on_catalog, on_status)
        await worker.start()
        await eventually(lambda: statuses and statuses[-1].state == "retry_wait")
        await asyncio.wait_for(worker.reconnect(), timeout=0.1)
        await eventually(lambda: statuses and statuses[-1].state == "connected")
        await worker.close()
        return statuses

    statuses = asyncio.run(run_test())

    assert [(status.state, status.attempt) for status in statuses] == [
        ("connecting", 1),
        ("retry_wait", 1),
        ("connecting", 1),
        ("connected", 1),
        ("closed", 0),
    ]

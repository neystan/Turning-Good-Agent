import asyncio
from contextlib import asynccontextmanager
from importlib import import_module
from types import SimpleNamespace

import httpx
import pytest


McpClient = import_module("Turning-Good-Agent.mcp.client").McpClient
McpServerSettings = import_module("Turning-Good-Agent.config.settings").McpServerSettings
is_mcp_connection_error = import_module("Turning-Good-Agent.mcp.client").is_mcp_connection_error


class FakeSession:
    """模拟 MCP SDK Session。"""

    async def list_tools(self, cursor=None):
        return SimpleNamespace(
            tools=[SimpleNamespace(name=f"tool-{cursor or 'one'}", description="tool", inputSchema={})],
            nextCursor="two" if cursor is None else None,
        )

    async def list_resources(self, cursor=None):
        return SimpleNamespace(resources=[SimpleNamespace(uri="file:///a", name="a", description="resource")], nextCursor=None)

    async def list_resource_templates(self, cursor=None):
        return SimpleNamespace(resourceTemplates=[SimpleNamespace(uriTemplate="file:///{id}", name="template")], nextCursor=None)

    async def list_prompts(self, cursor=None):
        return SimpleNamespace(prompts=[SimpleNamespace(name="review", description="prompt", arguments=[])], nextCursor=None)

    async def call_tool(self, name, arguments):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=f"{name}:{arguments['id']}")])

    async def read_resource(self, uri):
        return SimpleNamespace(contents=[SimpleNamespace(uri=str(uri), mimeType="text/plain", text="resource text")])

    async def get_prompt(self, name, arguments):
        return SimpleNamespace(
            messages=[
                SimpleNamespace(role="user", content=SimpleNamespace(type="text", text="question")),
                SimpleNamespace(role="assistant", content=SimpleNamespace(type="image", mimeType="image/png", data="abc")),
            ]
        )


class ToolsOnlySession:
    """模拟仅声明 Tools 能力的 MCP Server。"""

    async def list_tools(self, cursor=None):
        return SimpleNamespace(
            tools=[SimpleNamespace(name="lookup", description="tool", inputSchema={})],
            nextCursor=None,
        )


def test_discovers_all_catalog_kinds_with_pagination():
    async def run_test():
        client = McpClient("demo", McpServerSettings())
        client._session = FakeSession()
        return await client.discover()

    catalog = asyncio.run(run_test())

    assert [item.name for item in catalog.tools] == ["tool-one", "tool-two"]
    assert catalog.resources[0].metadata["uri"] == "file:///a"
    assert catalog.resource_templates[0].metadata["uri_template"] == "file:///{id}"
    assert catalog.prompts[0].name == "review"


def test_discovers_only_advertised_catalog_capabilities():
    async def run_test():
        client = McpClient("demo", McpServerSettings())
        client._session = ToolsOnlySession()
        client._server_capabilities = {
            "tools": {"listChanged": None},
            "resources": None,
            "prompts": None,
        }
        return await client.discover()

    catalog = asyncio.run(run_test())

    assert [item.name for item in catalog.tools] == ["lookup"]
    assert catalog.resources == []
    assert catalog.resource_templates == []
    assert catalog.prompts == []


def test_normalizes_tool_resource_and_prompt_content():
    async def run_test():
        client = McpClient("demo", McpServerSettings(timeout_seconds=1))
        client._session = FakeSession()
        return (
            await client.call_tool("lookup", {"id": "1"}),
            await client.read_resource("file:///a"),
            await client.get_prompt("review", {}),
        )

    tool, resource, prompt = asyncio.run(run_test())
    assert tool == "lookup:1"
    assert resource == "resource text"
    assert prompt == [
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "[MCP 非文本内容：来源 demo/review，MIME image/png，大小 3 bytes]"},
    ]


def test_normalizes_embedded_text_resource_from_tool_result():
    """验证工具结果中的嵌入文本 Resource 会进入模型上下文。"""

    class EmbeddedResourceSession(FakeSession):
        async def call_tool(self, name, arguments):
            return SimpleNamespace(
                content=[
                    SimpleNamespace(type="text", text="已下载 README"),
                    SimpleNamespace(
                        type="resource",
                        resource=SimpleNamespace(
                            uri="github://neystan/Turning-Good-Agent/README.md",
                            mimeType="text/markdown",
                            text="# Turning Good Agent\n\n项目说明",
                        ),
                    ),
                ]
            )

    async def run_test():
        client = McpClient("github", McpServerSettings(timeout_seconds=1))
        client._session = EmbeddedResourceSession()
        return await client.call_tool("get_file_contents", {})

    assert asyncio.run(run_test()) == "已下载 README\n# Turning Good Agent\n\n项目说明"


def test_converts_timeout_to_clear_error():
    class SlowSession(FakeSession):
        async def list_tools(self, cursor=None):
            await asyncio.sleep(0.05)
            return await super().list_tools(cursor)

    async def run_test():
        client = McpClient("demo", McpServerSettings(timeout_seconds=0.001))
        client._session = SlowSession()
        await client.discover()

    with pytest.raises(RuntimeError, match="MCP Server demo 请求超时"):
        asyncio.run(run_test())


def test_connect_closes_transport_when_initialize_is_cancelled(monkeypatch):
    """验证初始化被取消时会在原任务中关闭已打开的 transport。"""
    events = []

    @asynccontextmanager
    async def transport():
        events.append("transport.enter")
        try:
            yield "read", "write"
        finally:
            events.append("transport.exit")

    class CancelledSession:
        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            events.append("session.enter")
            return self

        async def __aexit__(self, *_args):
            events.append("session.exit")

        async def initialize(self):
            raise asyncio.CancelledError("connection cancelled")

    module = import_module("Turning-Good-Agent.mcp.client")
    monkeypatch.setattr(module, "_NotifyingClientSession", CancelledSession)

    async def run_test():
        client = McpClient("demo", McpServerSettings())
        monkeypatch.setattr(client, "_transport_context", transport)
        with pytest.raises(asyncio.CancelledError, match="connection cancelled"):
            await client.connect()
        return client

    client = asyncio.run(run_test())

    assert events == ["transport.enter", "session.enter", "session.exit", "transport.exit"]
    assert client._session is None
    assert client._stack is None


@pytest.mark.parametrize("error", [OSError("offline"), TimeoutError(), httpx.ConnectError("offline")])
def test_recognizes_mcp_connection_errors(error):
    """验证 transport 异常会被归类为连接级错误。"""
    assert is_mcp_connection_error(error) is True


def test_recognizes_connection_exception_group():
    """验证 SDK 包装的连接异常仍会触发重试。"""
    assert is_mcp_connection_error(ExceptionGroup("transport", [OSError("offline"), httpx.ConnectError("offline")])) is True


def test_does_not_treat_business_error_as_mcp_connection_error():
    """验证工具业务错误不会触发重连。"""
    assert is_mcp_connection_error(RuntimeError("permission denied")) is False

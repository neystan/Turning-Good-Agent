import asyncio
import logging
from collections.abc import Callable

from ..config.settings import McpSettings
from ..tools.registry import ToolRegistry
from .adapter import McpToolAdapter
from .client import McpClient
from .types import McpCapability, McpCatalog, McpServerStatus

logger = logging.getLogger(__name__)


class McpManager:
    """管理 MCP Server 生命周期与内存 Catalog。"""

    def __init__(
        self,
        settings: McpSettings,
        client_factory: Callable[..., McpClient] = McpClient,
    ) -> None:
        """保存 MCP 配置与独立 Client 工厂。"""
        self.settings = settings
        self._client_factory = client_factory
        self.clients: dict[str, McpClient] = {}
        self.catalogs: dict[str, McpCatalog] = {}
        self.statuses: dict[str, McpServerStatus] = {}
        self._tool_servers: dict[str, str] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def start(self, registry: ToolRegistry) -> None:
        """连接全部启用 Server，单个失败不影响其他 Server。"""
        for name, settings in self.settings.servers.items():
            if settings.enabled:
                await self.refresh_server(name, registry)

    async def close(self) -> None:
        """关闭全部已建立的 MCP Client。"""
        clients = list(self.clients.values())
        self.clients.clear()
        self.catalogs.clear()
        for client in clients:
            try:
                await client.close()
            except Exception:
                logger.exception("关闭 MCP Server 失败")

    async def refresh_server(self, name: str, registry: ToolRegistry) -> McpServerStatus:
        """串行刷新一个 Server 的连接与 Catalog。"""
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            registry.unregister_prefix(self._tool_prefix(name))
            self._tool_servers = {
                tool_name: server_name
                for tool_name, server_name in self._tool_servers.items()
                if server_name != name
            }
            old_client = self.clients.pop(name, None)
            self.catalogs.pop(name, None)
            if old_client is not None:
                try:
                    await old_client.close()
                except Exception:
                    logger.exception("关闭旧 MCP Server 失败")
            server_settings = self.settings.servers.get(name)
            if server_settings is None:
                status = McpServerStatus(name=name, error="MCP Server 不存在")
                self.statuses[name] = status
                return status
            client = self._client_factory(name, server_settings)
            set_handler = getattr(client, "set_list_changed_handler", None)
            if callable(set_handler):
                set_handler(lambda: asyncio.create_task(self.handle_list_changed(name, registry)))
            try:
                await client.connect()
                catalog = await client.discover()
            except Exception as exc:
                status = McpServerStatus(name=name, error=str(exc))
                self.statuses[name] = status
                try:
                    await client.close()
                except Exception:
                    logger.exception("清理失败 MCP Server 失败")
                return status
            self.clients[name] = client
            self.catalogs[name] = catalog
            self._register_enabled_tools(name, catalog, registry)
            status = McpServerStatus(name=name, connected=True)
            self.statuses[name] = status
            return status

    async def handle_list_changed(self, name: str, registry: ToolRegistry) -> McpServerStatus:
        """处理某个 Server 的 Catalog 变更通知。"""
        return await self.refresh_server(name, registry)

    async def search_capabilities(
        self,
        query: str,
        kinds: list[str] | None = None,
        limit: int = 5,
    ) -> list[McpCapability]:
        """只在内存 Catalog 中搜索能力描述。"""
        wanted_kinds = set(kinds or {"tool", "resource", "resource_template", "prompt"})
        needle = query.strip().lower()
        matches: list[McpCapability] = []
        for server_name, catalog in self.catalogs.items():
            for capability in self._catalog_values(catalog):
                if capability.kind not in wanted_kinds:
                    continue
                haystack = f"{server_name} {capability.name} {capability.description}".lower()
                if not needle or needle in haystack:
                    matches.append(capability)
        return matches[: max(1, limit)]

    async def call_tool(self, server_name: str, tool_name: str, arguments: dict[str, object]) -> str:
        """调用指定 Server 的原始 MCP Tool。"""
        client = self.clients.get(server_name)
        if client is None:
            raise RuntimeError(f"MCP Server 未连接：{server_name}")
        return await client.call_tool(tool_name, arguments)

    def requires_approval(self, tool_name: str, arguments: dict[str, object] | None = None) -> bool:
        """判断 MCP Tool 是否未被 Server 显式自动批准。"""
        server_name = self._tool_servers.get(tool_name) or self._server_name_from_tool(tool_name)
        if tool_name in {"attach_mcp_resource", "apply_mcp_prompt"}:
            server_name = str((arguments or {}).get("server_name", ""))
        if not server_name:
            return False
        server = self.settings.servers.get(server_name)
        if server is None:
            return True
        allowed = set(server.auto_approve_tools)
        raw_name = tool_name.removeprefix(f"mcp_{server_name}_")
        return raw_name not in allowed and tool_name not in allowed

    def _register_enabled_tools(self, name: str, catalog: McpCatalog, registry: ToolRegistry) -> None:
        """仅注册配置显式启用的远端 MCP Tool。"""
        server = self.settings.servers[name]
        enabled = set(server.enabled_tools)
        if not enabled:
            return
        available = {capability.name: capability for capability in catalog.tools}
        for raw_name, capability in available.items():
            wrapped_name = f"mcp_{name}_{raw_name}"
            if raw_name not in enabled and wrapped_name not in enabled:
                continue
            adapter = McpToolAdapter(self, capability)
            registry.register(adapter)
            self._tool_servers[adapter.name] = name
        for tool_name in sorted(enabled - set(available) - {f"mcp_{name}_{item}" for item in available}):
            logger.warning("MCP Server %s 未发现已启用工具：%s", name, tool_name)

    @staticmethod
    def _catalog_values(catalog: McpCatalog) -> list[McpCapability]:
        """返回 Catalog 中的全部能力。"""
        return catalog.tools + catalog.resources + catalog.resource_templates + catalog.prompts

    @staticmethod
    def _tool_prefix(name: str) -> str:
        """生成 Server 对应的 MCP Tool 前缀。"""
        return f"mcp_{name}_"

    def _server_name_from_tool(self, tool_name: str) -> str | None:
        """从包装 Tool 名称解析已配置的 Server 名称。"""
        for name in sorted(self.settings.servers, key=len, reverse=True):
            if tool_name.startswith(self._tool_prefix(name)):
                return name
        return None

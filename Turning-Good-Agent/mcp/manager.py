import asyncio
import logging
from collections.abc import Callable

from ..config.settings import McpSettings
from ..sessions.token_counter import TOKEN_ENCODING, count_content_tokens
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

    async def attach_resource(
        self,
        server_name: str,
        uri: str,
        arguments: dict[str, str],
    ) -> "McpContextAttachment":
        """读取 Resource 并生成受限的当前轮附件。"""
        client = self._client_or_raise(server_name)
        resolved_uri = uri.format(**arguments) if arguments else uri
        content = await client.read_resource(resolved_uri)
        content = self._truncate_resource(content, self.settings.resource_context_token_limit)
        return self._attachment(f"MCP Resource：{server_name}/{resolved_uri}", [{"role": "user", "content": content}])

    async def apply_prompt(
        self,
        server_name: str,
        prompt_name: str,
        arguments: dict[str, str],
    ) -> "McpContextAttachment":
        """读取 Prompt 并拒绝超出单 Prompt 限额的内容。"""
        client = self._client_or_raise(server_name)
        messages = await client.get_prompt(prompt_name, arguments)
        token_count = sum(count_content_tokens(str(message["content"])) for message in messages)
        if token_count > self.settings.prompt_context_token_limit:
            raise RuntimeError(f"MCP Prompt 超过 {self.settings.prompt_context_token_limit} tokens 限制")
        return self._attachment(f"MCP Prompt：{server_name}/{prompt_name}", messages)

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
        for tool_name in sorted(enabled - set(available) - {f"mcp_{name}_{item}" for item in available}):
            logger.warning("MCP Server %s 未发现已启用工具：%s", name, tool_name)

    def _client_or_raise(self, server_name: str) -> McpClient:
        """返回已连接的指定 MCP Client。"""
        client = self.clients.get(server_name)
        if client is None:
            raise RuntimeError(f"MCP Server 未连接：{server_name}")
        return client

    def _attachment(self, source: str, messages: list[dict[str, str]]) -> "McpContextAttachment":
        """构造带 token 计数的当前轮附件。"""
        from .types import McpContextAttachment

        token_count = sum(count_content_tokens(str(message["content"])) for message in messages)
        return McpContextAttachment(source=source, messages=list(messages), token_count=token_count)

    @staticmethod
    def _truncate_resource(content: str, limit: int) -> str:
        """按头尾策略截断超长 Resource。"""
        tokens = TOKEN_ENCODING.encode(content)
        if len(tokens) <= limit:
            return content
        notice = "\n\n[MCP Resource 内容已截断]"
        notice_tokens = TOKEN_ENCODING.encode(notice)
        budget = max(0, limit - len(notice_tokens))
        head_size = budget // 2
        tail_size = budget - head_size
        return TOKEN_ENCODING.decode(tokens[:head_size] + tokens[-tail_size:]) + notice

    @staticmethod
    def _catalog_values(catalog: McpCatalog) -> list[McpCapability]:
        """返回 Catalog 中的全部能力。"""
        return catalog.tools + catalog.resources + catalog.resource_templates + catalog.prompts

    @staticmethod
    def _tool_prefix(name: str) -> str:
        """生成 Server 对应的 MCP Tool 前缀。"""
        return f"mcp_{name}_"

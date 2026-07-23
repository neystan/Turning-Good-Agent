import asyncio
from collections.abc import Callable

from ..config.settings import McpServerSettings, McpSettings
from ..sessions.token_counter import TOKEN_ENCODING, count_content_tokens
from ..tools.context_attachment import ContextAttachment
from ..tools.registry import ToolRegistry
from .adapter import McpToolAdapter
from .client import McpClient
from .server_worker import McpServerWorker
from .types import McpCapability, McpCatalog, McpServerStatus


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
        self.workers: dict[str, McpServerWorker] = {}
        self.catalogs: dict[str, McpCatalog] = {}
        self.statuses: dict[str, McpServerStatus] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._registry: ToolRegistry | None = None

    async def start_background(self, registry: ToolRegistry) -> None:
        """后台启动全部启用 Server，不等待连接完成。"""
        self._registry = registry
        for name, server_settings in self.settings.servers.items():
            if server_settings.enabled:
                await self._start_worker(name, server_settings)

    async def close(self) -> None:
        """等待全部 Worker 在自身 Task 中关闭 Client。"""
        workers = list(self.workers.values())
        self.workers.clear()
        self.catalogs.clear()
        await asyncio.gather(*(worker.close() for worker in workers), return_exceptions=True)

    async def refresh_server(self, name: str, registry: ToolRegistry | None = None) -> McpServerStatus:
        """请求一个 Server 在所属 Worker 内重新连接。"""
        if registry is not None:
            self._registry = registry
        server_settings = self.settings.servers.get(name)
        if server_settings is None:
            status = McpServerStatus(name=name, error="MCP Server 不存在", state="failed")
            self.statuses[name] = status
            return status
        worker = self.workers.get(name)
        if worker is None or worker.task is None or worker.task.done():
            await self._start_worker(name, server_settings)
        else:
            await worker.reconnect()
        return self.statuses[name]

    async def handle_list_changed(self, name: str, registry: ToolRegistry | None = None) -> McpServerStatus:
        """请求所属 Worker 复用连接刷新 Catalog。"""
        if registry is not None:
            self._registry = registry
        worker = self._worker_or_raise(name)
        await worker.refresh_catalog()
        return self.statuses[name]

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
        return await self._worker_or_raise(server_name).call_tool(tool_name, arguments)

    async def attach_resource(
        self,
        server_name: str,
        uri: str,
        arguments: dict[str, str],
    ) -> ContextAttachment:
        """读取 Resource 并生成受限的当前轮附件。"""
        resolved_uri = uri.format(**arguments) if arguments else uri
        content = await self._worker_or_raise(server_name).read_resource(resolved_uri)
        content = self._truncate_resource(content, self.settings.resource_context_token_limit)
        return self._attachment(f"MCP Resource：{server_name}/{resolved_uri}", [{"role": "user", "content": content}])

    async def apply_prompt(
        self,
        server_name: str,
        prompt_name: str,
        arguments: dict[str, str],
    ) -> ContextAttachment:
        """读取 Prompt 并拒绝超出单 Prompt 限额的内容。"""
        messages = await self._worker_or_raise(server_name).get_prompt(prompt_name, arguments)
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

    async def _start_worker(self, name: str, server_settings: McpServerSettings) -> None:
        """创建一个独立连接的 MCP Worker。"""
        self.statuses[name] = McpServerStatus(name=name, state="connecting")

        async def on_catalog(catalog: McpCatalog) -> None:
            """将 Worker 发现结果应用到 Manager。"""
            await self._apply_catalog(name, catalog)

        async def on_status(status: McpServerStatus) -> None:
            """保存 Worker 发布的状态快照。"""
            self.statuses[name] = status

        worker = McpServerWorker(name, server_settings, self._client_factory, on_catalog, on_status)
        self.workers[name] = worker
        await worker.start()

    async def _apply_catalog(self, name: str, catalog: McpCatalog) -> None:
        """原子替换一个 Server 的 Catalog 与 Tool 注册。"""
        registry = self._registry
        if registry is None:
            return
        lock = self._locks.setdefault(name, asyncio.Lock())
        async with lock:
            registry.unregister_prefix(self._tool_prefix(name))
            self.catalogs[name] = catalog
            self._register_enabled_tools(name, catalog, registry)

    def _worker_or_raise(self, server_name: str) -> McpServerWorker:
        """返回指定 Server 的 Worker。"""
        worker = self.workers.get(server_name)
        if worker is None:
            raise RuntimeError(f"MCP Server 未启动：{server_name}")
        return worker

    def _attachment(self, source: str, messages: list[dict[str, str]]) -> ContextAttachment:
        """构造带 token 计数的当前轮附件。"""
        token_count = sum(count_content_tokens(str(message["content"])) for message in messages)
        return ContextAttachment(source=source, messages=list(messages), token_count=token_count)

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

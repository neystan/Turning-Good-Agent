import asyncio
from collections.abc import Callable
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp import types as mcp_types
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client
from pydantic import AnyUrl

from ..config.settings import McpServerSettings
from .types import McpCapability, McpCatalog


class _NotifyingClientSession(ClientSession):
    """在 SDK 原有通知处理后转发 Catalog 变更。"""

    def __init__(self, *args: Any, on_list_changed: Callable[[], None] | None = None, **kwargs: Any) -> None:
        """保存可选的目录变更通知回调。"""
        super().__init__(*args, **kwargs)
        self._on_list_changed = on_list_changed

    async def _received_notification(self, notification: mcp_types.ServerNotification) -> None:
        """识别 tools/resources/prompts 的 list_changed 通知。"""
        await super()._received_notification(notification)
        if isinstance(
            notification.root,
            (
                mcp_types.ToolListChangedNotification,
                mcp_types.ResourceListChangedNotification,
                mcp_types.PromptListChangedNotification,
            ),
        ) and self._on_list_changed is not None:
            self._on_list_changed()


class McpClient:
    """封装单个 MCP SDK Session 的连接与请求。"""

    def __init__(self, server_name: str, settings: McpServerSettings) -> None:
        """保存 Server 名称与连接配置。"""
        self.server_name = server_name
        self.settings = settings
        self._stack: AsyncExitStack | None = None
        self._session: ClientSession | Any | None = None
        self._on_list_changed: Callable[[], None] | None = None
        self._server_capabilities: dict[str, Any] | None = None

    def set_list_changed_handler(self, handler: Callable[[], None]) -> None:
        """设置 SDK 收到目录变更通知后的回调。"""
        self._on_list_changed = handler

    async def connect(self) -> None:
        """通过官方 SDK 建立并初始化 MCP Session。"""
        if self._session is not None:
            return
        stack = AsyncExitStack()
        try:
            transport = await stack.enter_async_context(self._transport_context())
            read_stream, write_stream = transport[:2]
            session = await stack.enter_async_context(
                _NotifyingClientSession(read_stream, write_stream, on_list_changed=self._on_list_changed)
            )
            initialize_result = await self._request(session.initialize())
        except Exception:
            await stack.aclose()
            raise
        self._stack = stack
        self._session = session
        self._server_capabilities = self._as_mapping(getattr(initialize_result, "capabilities", None))

    async def discover(self) -> McpCatalog:
        """分页读取 Server 的完整能力目录。"""
        tools = await self._list_capabilities("list_tools", "tools", "tool") if self._supports("tools") else []
        resources = (
            await self._list_capabilities("list_resources", "resources", "resource")
            if self._supports("resources")
            else []
        )
        templates = (
            await self._list_capabilities("list_resource_templates", "resourceTemplates", "resource_template")
            if self._supports("resources")
            else []
        )
        prompts = await self._list_capabilities("list_prompts", "prompts", "prompt") if self._supports("prompts") else []
        return McpCatalog(tools=tools, resources=resources, resource_templates=templates, prompts=prompts)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """调用远端原始工具名称并归一化文本内容。"""
        result = await self._request(self._session_or_raise().call_tool(name, arguments))
        return self._normalize_content(getattr(result, "content", []), f"{self.server_name}/{name}")

    async def read_resource(self, uri: str) -> str:
        """读取 Resource 并归一化文本或二进制占位内容。"""
        result = await self._request(self._session_or_raise().read_resource(AnyUrl(uri)))
        return self._normalize_content(getattr(result, "contents", []), f"{self.server_name}/{uri}")

    async def get_prompt(self, name: str, arguments: dict[str, str]) -> list[dict[str, str]]:
        """读取 Prompt 并仅保留 user/assistant 文本消息。"""
        result = await self._request(self._session_or_raise().get_prompt(name, arguments or None))
        messages: list[dict[str, str]] = []
        for message in getattr(result, "messages", []):
            role = str(getattr(message, "role", ""))
            if role not in {"user", "assistant"}:
                raise RuntimeError(f"MCP Server {self.server_name} 返回了不允许的 Prompt role：{role}")
            source = f"{self.server_name}/{name}"
            messages.append({"role": role, "content": self._normalize_content([message.content], source)})
        return messages

    async def close(self) -> None:
        """关闭当前 Server 的 SDK Session 与 transport。"""
        stack, self._stack = self._stack, None
        self._session = None
        self._server_capabilities = None
        if stack is not None:
            await stack.aclose()

    def _transport_context(self) -> Any:
        """按配置创建官方 SDK transport 上下文。"""
        if self.settings.transport == "stdio":
            return stdio_client(
                StdioServerParameters(
                    command=self.settings.command or "",
                    args=self.settings.args,
                    env=self.settings.env or None,
                    cwd=self.settings.cwd,
                )
            )
        return streamablehttp_client(
            self.settings.url or "",
            headers=self.settings.headers or None,
            timeout=self.settings.timeout_seconds,
        )

    async def _list_capabilities(self, method_name: str, field_name: str, kind: str) -> list[McpCapability]:
        """分页读取一种 MCP Catalog 能力。"""
        method = getattr(self._session_or_raise(), method_name)
        cursor: str | None = None
        capabilities: list[McpCapability] = []
        while True:
            result = await self._request(method(cursor))
            capabilities.extend(
                self._capability_from_item(kind, item)
                for item in getattr(result, field_name, [])
            )
            cursor = getattr(result, "nextCursor", None)
            if not cursor:
                return capabilities

    def _capability_from_item(self, kind: str, item: Any) -> McpCapability:
        """将 SDK Catalog 对象转为稳定描述。"""
        name = str(getattr(item, "name", ""))
        metadata = {
            "title": getattr(item, "title", None),
            "annotations": self._as_mapping(getattr(item, "annotations", None)),
        }
        if kind == "tool":
            metadata["input_schema"] = self._as_mapping(getattr(item, "inputSchema", {}))
            metadata["annotations"] = self._as_mapping(getattr(item, "annotations", None))
        elif kind == "resource":
            metadata["uri"] = str(getattr(item, "uri", ""))
            metadata["mime_type"] = getattr(item, "mimeType", None)
            metadata["size"] = getattr(item, "size", None)
            name = name or metadata["uri"]
        elif kind == "resource_template":
            metadata["uri_template"] = str(getattr(item, "uriTemplate", ""))
            metadata["mime_type"] = getattr(item, "mimeType", None)
            name = name or metadata["uri_template"]
        elif kind == "prompt":
            metadata["arguments"] = [self._as_mapping(value) for value in getattr(item, "arguments", [])]
        return McpCapability(
            server_name=self.server_name,
            kind=kind,
            name=name,
            description=str(getattr(item, "description", "") or ""),
            metadata=metadata,
        )

    async def _request(self, awaitable: Any) -> Any:
        """统一转换 MCP 请求超时错误。"""
        try:
            return await asyncio.wait_for(awaitable, timeout=self.settings.timeout_seconds)
        except TimeoutError as exc:
            raise RuntimeError(f"MCP Server {self.server_name} 请求超时") from exc

    def _session_or_raise(self) -> Any:
        """返回已初始化 Session。"""
        if self._session is None:
            raise RuntimeError(f"MCP Server {self.server_name} 尚未连接")
        return self._session

    def _supports(self, capability: str) -> bool:
        """判断 Server 是否声明指定能力。"""
        return self._server_capabilities is None or self._server_capabilities.get(capability) is not None

    @staticmethod
    def _as_mapping(value: Any) -> dict[str, Any]:
        """将 SDK 模型转换为普通字典。"""
        if isinstance(value, dict):
            return dict(value)
        model_dump = getattr(value, "model_dump", None)
        return model_dump() if callable(model_dump) else {}

    def _normalize_content(self, items: list[Any], source: str) -> str:
        """将 MCP 内容块转换为不可信文本或占位文本。"""
        normalized: list[str] = []
        for item in items:
            item_type = getattr(item, "type", None)
            if item_type == "text" or hasattr(item, "text"):
                normalized.append(str(getattr(item, "text", "")))
                continue
            mime_type = getattr(item, "mimeType", None) or getattr(item, "mime_type", None) or "unknown"
            raw = getattr(item, "data", None) or getattr(item, "blob", None) or ""
            normalized.append(f"[MCP 非文本内容：来源 {source}，MIME {mime_type}，大小 {len(str(raw))} bytes]")
        return "\n".join(normalized)

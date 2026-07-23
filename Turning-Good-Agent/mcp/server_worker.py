from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config.settings import McpServerSettings
from .client import McpClient, is_mcp_connection_error
from .types import McpCatalog, McpServerStatus

CatalogHandler = Callable[[McpCatalog], Awaitable[None]]
StatusHandler = Callable[[McpServerStatus], Awaitable[None]]


@dataclass(slots=True)
class _WorkerCommand:
    """表示 Worker 串行处理的一项请求。"""

    name: str
    args: tuple[Any, ...] = ()
    future: asyncio.Future[Any] | None = None


class McpServerWorker:
    """在单个 Task 中管理一个 MCP Client。"""

    def __init__(
        self,
        name: str,
        settings: McpServerSettings,
        client_factory: Callable[..., McpClient],
        on_catalog: CatalogHandler,
        on_status: StatusHandler,
    ) -> None:
        """保存 Worker 的连接配置和 Manager 回调。"""
        self.name = name
        self.settings = settings
        self._client_factory = client_factory
        self._on_catalog = on_catalog
        self._on_status = on_status
        self._commands: asyncio.Queue[_WorkerCommand] = asyncio.Queue()
        self._client: McpClient | None = None
        self._task: asyncio.Task[None] | None = None
        self._status = McpServerStatus(name=name)
        self._close_requested = False

    @property
    def task(self) -> asyncio.Task[None] | None:
        """返回持有 MCP Client 的后台 Task。"""
        return self._task

    @property
    def status(self) -> McpServerStatus:
        """返回当前连接状态快照。"""
        return self._status

    async def start(self) -> None:
        """创建不阻塞调用方的 Worker Task。"""
        if self._task is None or self._task.done():
            self._close_requested = False
            self._task = asyncio.create_task(self._run(), name=f"mcp:{self.name}")

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """在 Worker 中调用远端工具。"""
        return await self._request("call_tool", name, arguments)

    async def read_resource(self, uri: str) -> str:
        """在 Worker 中读取远端 Resource。"""
        return await self._request("read_resource", uri)

    async def get_prompt(self, name: str, arguments: dict[str, str]) -> list[dict[str, str]]:
        """在 Worker 中读取远端 Prompt。"""
        return await self._request("get_prompt", name, arguments)

    async def refresh_catalog(self) -> None:
        """请求复用当前连接刷新 Catalog。"""
        await self._request("refresh_catalog")

    async def reconnect(self) -> None:
        """请求在 Worker 内关闭并重建连接。"""
        await self._enqueue("reconnect", require_connected=False)

    async def close(self) -> None:
        """请求 Worker 在自身 Task 中关闭 Client。"""
        if self._task is None or self._task.done():
            return
        await self._enqueue("close", require_connected=False)
        await self._task

    async def _request(self, name: str, *args: Any) -> Any:
        """拒绝未连接请求或投递可执行命令。"""
        return await self._enqueue(name, *args, require_connected=True)

    async def _enqueue(self, name: str, *args: Any, require_connected: bool) -> Any:
        """向 Worker 队列投递命令并等待结果。"""
        if self._task is None or self._task.done():
            raise RuntimeError(f"MCP Server 未启动：{self.name}")
        if require_connected and self._status.state != "connected":
            raise RuntimeError(self._unavailable_message())
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        await self._commands.put(_WorkerCommand(name, args, future))
        return await future

    async def _run(self) -> None:
        """持续连接并串行处理 Server 命令。"""
        try:
            while True:
                await self._close_client()
                if not await self._connect_with_retries():
                    if self._close_requested:
                        return
                    if not await self._wait_when_failed():
                        return
                    continue
                if not await self._serve_connected_commands():
                    return
        finally:
            await self._close_client()
            await self._set_status("closed")
            self._fail_queued_commands()

    async def _connect_with_retries(self) -> bool:
        """按配置建立连接并发现 Catalog。"""
        retry_index = 0
        while True:
            attempt = retry_index + 1
            await self._set_status("connecting", attempt=attempt)
            client = self._client_factory(self.name, self.settings)
            client.set_list_changed_handler(self._queue_catalog_refresh)
            try:
                await client.connect()
                catalog = await client.discover()
                await self._on_catalog(catalog)
            except Exception as exc:
                await self._close_specific_client(client)
                if not is_mcp_connection_error(exc) or retry_index >= self.settings.connect_retry_attempts:
                    await self._set_status("failed", attempt=attempt, error=str(exc))
                    return False
                delay = min(
                    self.settings.connect_retry_delay_seconds * (2**retry_index),
                    self.settings.connect_retry_max_delay_seconds,
                )
                next_retry_at = (datetime.now(timezone.utc) + timedelta(seconds=delay)).isoformat()
                await self._set_status("retry_wait", attempt=attempt, error=str(exc), next_retry_at=next_retry_at)
                retry_index += 1
                if not await self._wait_for_retry(delay):
                    return False
                continue
            self._client = client
            await self._set_status("connected", attempt=attempt)
            return True

    async def _wait_for_retry(self, delay: float) -> bool:
        """在退避等待期间处理关闭或重连命令。"""
        deadline = asyncio.get_running_loop().time() + delay
        while True:
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return True
            try:
                command = await asyncio.wait_for(self._commands.get(), timeout=remaining)
            except TimeoutError:
                return True
            if command.name == "close":
                self._close_requested = True
                self._resolve(command, None)
                return False
            self._reject(command, self._unavailable_message())

    async def _wait_when_failed(self) -> bool:
        """在最终失败后等待手动重连或关闭。"""
        while True:
            command = await self._commands.get()
            if command.name == "close":
                self._close_requested = True
                self._resolve(command, None)
                return False
            if command.name == "reconnect":
                self._resolve(command, None)
                return True
            self._reject(command, self._unavailable_message())

    async def _serve_connected_commands(self) -> bool:
        """处理已连接 Server 的串行请求。"""
        while True:
            command = await self._commands.get()
            if command.name == "close":
                self._close_requested = True
                self._resolve(command, None)
                return False
            if command.name == "reconnect":
                self._resolve(command, None)
                return True
            try:
                result = await self._execute(command)
            except Exception as exc:
                self._reject(command, str(exc))
                if is_mcp_connection_error(exc):
                    return True
            else:
                self._resolve(command, result)

    async def _execute(self, command: _WorkerCommand) -> Any:
        """执行一项已连接的远端请求。"""
        client = self._client_or_raise()
        if command.name == "call_tool":
            return await client.call_tool(*command.args)
        if command.name == "read_resource":
            return await client.read_resource(*command.args)
        if command.name == "get_prompt":
            return await client.get_prompt(*command.args)
        if command.name == "refresh_catalog":
            catalog = await client.discover()
            await self._on_catalog(catalog)
            return None
        raise RuntimeError(f"不支持的 MCP Worker 命令：{command.name}")

    def _queue_catalog_refresh(self) -> None:
        """接收 SDK 通知并请求后续刷新 Catalog。"""
        if self._task is not None and not self._task.done():
            self._commands.put_nowait(_WorkerCommand("refresh_catalog"))

    async def _close_client(self) -> None:
        """在 Worker Task 中关闭当前 Client。"""
        client, self._client = self._client, None
        if client is not None:
            await self._close_specific_client(client)

    @staticmethod
    async def _close_specific_client(client: McpClient) -> None:
        """忽略关闭过程中的次要 SDK 异常。"""
        try:
            await client.close()
        except Exception:
            pass

    async def _set_status(
        self,
        state: str,
        *,
        attempt: int = 0,
        error: str | None = None,
        next_retry_at: str | None = None,
    ) -> None:
        """更新并发布新的连接状态快照。"""
        self._status = McpServerStatus(
            name=self.name,
            connected=state == "connected",
            state=state,
            attempt=attempt,
            error=error,
            next_retry_at=next_retry_at,
        )
        await self._on_status(self._status)

    def _client_or_raise(self) -> McpClient:
        """返回当前已连接 Client。"""
        if self._client is None:
            raise RuntimeError(self._unavailable_message())
        return self._client

    def _unavailable_message(self) -> str:
        """生成当前连接不可用的清晰错误。"""
        if self._status.state in {"connecting", "retry_wait"}:
            return f"MCP Server 正在连接或重试：{self.name}"
        if self._status.error:
            return f"MCP Server 连接失败：{self.name}，{self._status.error}"
        return f"MCP Server 未连接：{self.name}"

    @staticmethod
    def _resolve(command: _WorkerCommand, value: Any) -> None:
        """安全完成命令的成功 Future。"""
        if command.future is not None and not command.future.done():
            command.future.set_result(value)

    @staticmethod
    def _reject(command: _WorkerCommand, message: str) -> None:
        """安全完成命令的失败 Future。"""
        if command.future is not None and not command.future.done():
            command.future.set_exception(RuntimeError(message))

    def _fail_queued_commands(self) -> None:
        """拒绝 Worker 退出时尚未执行的命令。"""
        while not self._commands.empty():
            command = self._commands.get_nowait()
            self._reject(command, f"MCP Server 已关闭：{self.name}")

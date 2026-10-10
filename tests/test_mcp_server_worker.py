from __future__ import annotations

import asyncio
import importlib


settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
worker_module = importlib.import_module("Turning-Good-Agent.mcp.server_worker")

McpServerSettings = settings_module.McpServerSettings
McpServerWorker = worker_module.McpServerWorker


# 验证关闭会中断阻塞连接。
def test_worker_close_interrupts_a_blocked_connection() -> None:
    async def run() -> None:
        connection_started = asyncio.Event()
        connection_blocked = asyncio.Event()
        created_clients = []

        class BlockingClient:
            def __init__(self, name, settings) -> None:
                del name, settings
                self.close_calls = 0
                created_clients.append(self)

            def set_list_changed_handler(self, handler) -> None:
                del handler

            async def connect(self) -> None:
                connection_started.set()
                await connection_blocked.wait()

            async def close(self) -> None:
                self.close_calls += 1

        async def on_catalog(catalog) -> None:
            del catalog

        async def on_status(status) -> None:
            del status

        worker = McpServerWorker(
            "blocked",
            McpServerSettings(connect_retry_attempts=0),
            BlockingClient,
            on_catalog,
            on_status,
        )
        await worker.start()
        await asyncio.wait_for(connection_started.wait(), timeout=0.5)
        try:
            await asyncio.wait_for(worker.close(), timeout=0.1)
            assert created_clients[0].close_calls == 1
            assert worker.task is not None
            assert worker.task.done()
        finally:
            task = worker.task
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    asyncio.run(run())

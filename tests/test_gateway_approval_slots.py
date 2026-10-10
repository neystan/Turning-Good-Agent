from __future__ import annotations

import asyncio
import importlib


def test_web_approval_releases_the_shared_gateway_slot_until_resolved() -> None:
    """Would fail if a waiting Web approval monopolized Gateway concurrency."""

    async def run() -> None:
        messages = importlib.import_module("Turning-Good-Agent.bus.messages")
        queue_module = importlib.import_module("Turning-Good-Agent.bus.queue")
        coordinator_module = importlib.import_module("Turning-Good-Agent.web.backend.coordinator")
        adapter_module = importlib.import_module("Turning-Good-Agent.channels.web")

        slot = asyncio.Semaphore(1)
        await slot.acquire()
        route = messages.ChannelRoute("local-user", "web", "conversation", "session")
        control = coordinator_module.WebTurnControl("session", "request", route)
        adapter_module.WebChannelAdapter(
            queue_module.AsyncMessageBus(),
            control,
            route=route,
            execution_semaphore=slot,
        )
        approval_id = control.create_approval()
        waiting = asyncio.create_task(control.wait_approval(approval_id))

        await asyncio.sleep(0)
        assert slot.locked() is False
        assert control.resolve_approval(approval_id, True) is True
        assert await waiting is True
        assert slot.locked() is True
        slot.release()

    asyncio.run(run())


def test_cli_approval_releases_the_shared_gateway_slot_until_resolved() -> None:
    """Would fail if a waiting CLI approval monopolized Gateway concurrency."""

    async def run() -> None:
        messages = importlib.import_module("Turning-Good-Agent.bus.messages")
        queue_module = importlib.import_module("Turning-Good-Agent.bus.queue")
        cli_module = importlib.import_module("Turning-Good-Agent.channels.cli_gateway")

        slot = asyncio.Semaphore(1)
        await slot.acquire()
        route = messages.ChannelRoute("local-user", "cli", "conversation", "session")
        control = cli_module.CliTurnControl("session", "request", route)
        cli_module.CliGatewayAdapter(queue_module.AsyncMessageBus(), control, execution_semaphore=slot)
        approval_id = control.create_approval()
        waiting = asyncio.create_task(control.wait_approval(approval_id))

        await asyncio.sleep(0)
        assert slot.locked() is False
        assert control.resolve_approval(approval_id, True) is True
        assert await waiting is True
        assert slot.locked() is True
        slot.release()

    asyncio.run(run())


# 验证取消 Web 审批等待不会让外层 Gateway 槽位多释放一次。
def test_cancelled_web_approval_keeps_gateway_slot_balanced() -> None:
    # 在真实外层 semaphore 上取消 Web 审批等待。
    async def run() -> None:
        messages = importlib.import_module("Turning-Good-Agent.bus.messages")
        queue_module = importlib.import_module("Turning-Good-Agent.bus.queue")
        coordinator_module = importlib.import_module("Turning-Good-Agent.web.backend.coordinator")
        adapter_module = importlib.import_module("Turning-Good-Agent.channels.web")

        slot = asyncio.Semaphore(1)
        route = messages.ChannelRoute("local-user", "web", "conversation", "session")
        control = coordinator_module.WebTurnControl("session", "request", route)
        async with slot:
            adapter_module.WebChannelAdapter(
                queue_module.AsyncMessageBus(),
                control,
                route=route,
                execution_semaphore=slot,
            )
            approval_id = control.create_approval()
            waiting = asyncio.create_task(control.wait_approval(approval_id))
            await asyncio.sleep(0)
            waiting.cancel()
            try:
                await waiting
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("Web 审批等待应被取消")

        await slot.acquire()
        assert slot.locked() is True
        slot.release()

    asyncio.run(run())


# 验证取消 CLI 审批等待不会让外层 Gateway 槽位多释放一次。
def test_cancelled_cli_approval_keeps_gateway_slot_balanced() -> None:
    # 在真实外层 semaphore 上取消 CLI 审批等待。
    async def run() -> None:
        messages = importlib.import_module("Turning-Good-Agent.bus.messages")
        queue_module = importlib.import_module("Turning-Good-Agent.bus.queue")
        cli_module = importlib.import_module("Turning-Good-Agent.channels.cli_gateway")

        slot = asyncio.Semaphore(1)
        route = messages.ChannelRoute("local-user", "cli", "conversation", "session")
        control = cli_module.CliTurnControl("session", "request", route)
        async with slot:
            cli_module.CliGatewayAdapter(
                queue_module.AsyncMessageBus(), control, execution_semaphore=slot
            )
            approval_id = control.create_approval()
            waiting = asyncio.create_task(control.wait_approval(approval_id))
            await asyncio.sleep(0)
            waiting.cancel()
            try:
                await waiting
            except asyncio.CancelledError:
                pass
            else:
                raise AssertionError("CLI 审批等待应被取消")

        await slot.acquire()
        assert slot.locked() is True
        slot.release()

    asyncio.run(run())

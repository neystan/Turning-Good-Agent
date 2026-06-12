import asyncio

from .messages import InboundMessage, OutboundMessage


class AsyncMessageBus:
    """提供 Channel 与 Runtime 之间的异步消息队列。"""

    def __init__(self) -> None:
        self.inbound: asyncio.Queue[InboundMessage] = asyncio.Queue()
        self.outbound: asyncio.Queue[OutboundMessage] = asyncio.Queue()

    async def publish_inbound(self, message: InboundMessage) -> None:
        """写入一条入站消息。"""
        await self.inbound.put(message)

    async def consume_inbound(self) -> InboundMessage:
        """消费一条入站消息。"""
        return await self.inbound.get()

    async def publish_outbound(self, message: OutboundMessage) -> None:
        """写入一条出站消息。"""
        await self.outbound.put(message)

    async def consume_outbound(self) -> OutboundMessage:
        """消费一条出站消息。"""
        return await self.outbound.get()

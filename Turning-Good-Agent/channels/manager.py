from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable

from ..bus.messages import OutboundMessage
from ..bus.queue import AsyncMessageBus
from .base import ChannelTransport


logger = logging.getLogger(__name__)
DeliveryResultListener = Callable[[OutboundMessage, bool], Awaitable[None]]
_DEFAULT_DELIVERY_TIMEOUT_SECONDS = 5.0


class ChannelManager:
    """作为唯一出站消费者，将已定向消息交给目标 Channel。"""

    def __init__(
        self,
        bus: AsyncMessageBus,
        transports: Iterable[ChannelTransport] = (),
        *,
        delivery_timeout_seconds: float = _DEFAULT_DELIVERY_TIMEOUT_SECONDS,
    ) -> None:
        if delivery_timeout_seconds <= 0:
            raise ValueError("delivery_timeout_seconds 必须大于 0")
        self._bus = bus
        self._delivery_timeout_seconds = delivery_timeout_seconds
        self._transports: dict[str, ChannelTransport] = {}
        self._listeners: list[DeliveryResultListener] = []
        self._consumer: asyncio.Task[None] | None = None
        self._closed = False
        for transport in transports:
            self.register(transport)

    def register(self, transport: ChannelTransport) -> None:
        """注册一个唯一名称的 Channel Transport。"""
        if transport.name in self._transports:
            raise ValueError(f"duplicate channel transport: {transport.name}")
        self._transports[transport.name] = transport

    def add_delivery_listener(self, listener: DeliveryResultListener) -> None:
        """注册 Adapter 接受或拒绝消息后的状态观察者。"""
        self._listeners.append(listener)

    async def start(self) -> None:
        """启动所有 Transport 与唯一共享出站消费者。"""
        if self._consumer is not None:
            return
        self._closed = False
        started: list[ChannelTransport] = []
        try:
            for transport in self._transports.values():
                await transport.start()
                started.append(transport)
        except Exception:
            await asyncio.gather(*(item.close() for item in reversed(started)), return_exceptions=True)
            raise
        self._consumer = asyncio.create_task(self._consume_outbound(), name="channel-manager-outbound")

    async def close(self) -> None:
        """停止唯一消费者并关闭所有 Transport。"""
        self._closed = True
        if self._consumer is not None:
            self._consumer.cancel()
            await asyncio.gather(self._consumer, return_exceptions=True)
            self._consumer = None
        await asyncio.gather(*(transport.close() for transport in self._transports.values()), return_exceptions=True)

    async def deliver(self, message: OutboundMessage) -> bool:
        """将一条明确收件人的消息定向交给对应 Transport。"""
        transport = self._transports.get(message.recipient.channel)
        accepted = False
        if transport is None:
            logger.info("No ChannelTransport registered for %s", message.recipient.channel)
        else:
            try:
                accepted = await asyncio.wait_for(
                    transport.send(message),
                    timeout=self._delivery_timeout_seconds,
                )
            except TimeoutError:
                logger.warning(
                    "ChannelTransport %s timed out delivering outbound message",
                    transport.name,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("ChannelTransport %s rejected outbound message", transport.name)
        for listener in tuple(self._listeners):
            try:
                await listener(message, accepted)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Channel delivery listener failed")
        return accepted

    async def _consume_outbound(self) -> None:
        while not self._closed:
            message = await self._bus.consume_outbound()
            await self.deliver(message)

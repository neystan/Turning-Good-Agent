from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from ..bus.messages import OutboundMessage
from ..bus.queue import AsyncMessageBus
from .types import DeliveryRecord, new_id, utc_now_iso
from .store import ProactiveStore


class DeliveryGate:
    """在 Runtime 全局空闲且既有输出已消费后投递主动通知。"""

    def __init__(
        self,
        store: ProactiveStore,
        bus: AsyncMessageBus,
        idle_probe: Callable[[], bool],
        active_session_id: Callable[[], str | None] | None = None,
    ) -> None:
        """初始化对象状态。"""
        self._store = store
        self._bus = bus
        self._idle_probe = idle_probe
        self._active_session_id = active_session_id
        self._flush_lock = asyncio.Lock()

    async def enqueue(
        self,
        *,
        session_id: str,
        target_channel: str,
        event_type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> DeliveryRecord:
        """先 durable 写入一条待投递记录。"""
        record = DeliveryRecord(
            id=new_id("delivery"),
            created_at=utc_now_iso(),
            session_id=session_id,
            target_channel=target_channel,
            event_type=event_type,
            content=content,
            metadata=dict(metadata or {}),
        )
        self._store.append_jsonl("deliveries.jsonl", record.to_dict())
        return record

    async def flush(self) -> int:
        """至多投递一条符合顺序约束的主动通知。"""
        async with self._flush_lock:
            if not self._idle_probe() or not self._bus.outbound.empty():
                return 0
            pending = self._next_pending()
            if pending is None:
                return 0
            metadata = dict(pending["metadata"])
            metadata["proactive_delivery_id"] = pending["id"]
            session_id = pending["session_id"]
            if self._active_session_id is not None:
                session_id = self._active_session_id() or session_id
            await self._bus.publish_outbound(
                OutboundMessage(
                    id=pending["id"],
                    session_id=session_id,
                    target_channel=pending["target_channel"],
                    content=pending["content"],
                    event_type=pending["event_type"],
                    metadata=metadata,
                )
            )
            delivered = dict(pending)
            delivered["state"] = "delivered"
            delivered["delivered_at"] = utc_now_iso()
            self._store.append_jsonl("deliveries.jsonl", delivered)
            return 1

    async def cancel_pending(self) -> int:
        """将当前进程未投递的记录标为取消，避免下次 CLI 补发。"""
        async with self._flush_lock:
            pending = self._pending_records()
            for record in pending:
                cancelled = dict(record)
                cancelled["state"] = "cancelled"
                cancelled["cancelled_at"] = utc_now_iso()
                self._store.append_jsonl("deliveries.jsonl", cancelled)
            return len(pending)

    def _next_pending(self) -> dict[str, Any] | None:
        """从 append-only 审计重建当前待投递记录，并保留首次写入顺序。"""
        pending = self._pending_records()
        return pending[0] if pending else None

    def _pending_records(self) -> list[dict[str, Any]]:
        """返回按首次持久化顺序排列的当前 pending 记录。"""
        current: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for record in self._store.read_jsonl("deliveries.jsonl"):
            delivery_id = str(record.get("id", ""))
            if not delivery_id:
                continue
            if delivery_id not in current:
                order.append(delivery_id)
            current[delivery_id] = record
        return [current[delivery_id] for delivery_id in order if current[delivery_id].get("state") == "pending"]

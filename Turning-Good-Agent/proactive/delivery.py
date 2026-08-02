from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from ..bus.messages import OutboundMessage
from ..bus.queue import AsyncMessageBus
from .store import ProactiveStore
from .types import new_id, utc_now_iso


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
        self._lock = asyncio.Lock()

    async def enqueue(
        self,
        *,
        target_channel: str,
        event_type: str,
        content: str,
        source_id: str,
    ) -> dict[str, str]:
        """先 durable 写入一条待投递记录。"""
        record = {
            "id": new_id("delivery"),
            "created_at": utc_now_iso(),
            "target_channel": target_channel,
            "event_type": event_type,
            "content": content,
            "source_id": source_id,
        }
        async with self._lock:
            deliveries = self._load_deliveries()
            deliveries.append(record)
            self._save_deliveries(deliveries)
        return record

    async def flush(self) -> int:
        """至多将一条 durable 通知转交给 Outbound Bus；成功入队即完成投递。"""
        async with self._lock:
            if not self._idle_probe() or not self._bus.outbound.empty():
                return 0
            deliveries = self._load_deliveries()
            if not deliveries:
                return 0
            pending = deliveries[0]
            session_id = "proactive"
            if self._active_session_id is not None:
                session_id = self._active_session_id() or session_id
            await self._bus.publish_outbound(
                OutboundMessage(
                    id=pending["id"],
                    session_id=session_id,
                    target_channel=pending["target_channel"],
                    content=pending["content"],
                    event_type=pending["event_type"],
                    metadata={"proactive_delivery_id": pending["id"]},
                )
            )
            self._save_deliveries(deliveries[1:])
            return 1

    async def delete_by_source_id(self, source_id: str) -> int:
        """硬删除指定来源尚未投递的记录。"""
        async with self._lock:
            deliveries = self._load_deliveries()
            retained = [record for record in deliveries if record["source_id"] != source_id]
            removed = len(deliveries) - len(retained)
            if removed:
                self._save_deliveries(retained)
            return removed

    def _load_deliveries(self) -> list[dict[str, str]]:
        """读取当前待投递快照。"""
        payload = self._store.read_json("pending_deliveries.json", {"deliveries": []})
        if not isinstance(payload, dict) or set(payload) != {"deliveries"}:
            raise ValueError("pending_deliveries.json 必须仅包含 deliveries")
        deliveries = payload["deliveries"]
        if not isinstance(deliveries, list) or not all(isinstance(record, dict) for record in deliveries):
            raise ValueError("pending_deliveries.json 的 deliveries 必须是对象数组")
        required = {"id", "created_at", "target_channel", "event_type", "content", "source_id"}
        if any(set(record) != required or not all(isinstance(value, str) for value in record.values()) for record in deliveries):
            raise ValueError("pending_deliveries.json 包含无效记录")
        return [dict(record) for record in deliveries]

    def _save_deliveries(self, deliveries: list[dict[str, str]]) -> None:
        """写入当前待投递快照。"""
        self._store.write_json("pending_deliveries.json", {"deliveries": deliveries})


class WebDeliverySink:
    """将 Web 主动通知保留在内存事件流，绝不触碰 CLI Outbox。"""

    def __init__(self, publish: Callable[..., Awaitable[None]]) -> None:
        self._publish = publish

    async def enqueue(
        self,
        *,
        target_channel: str,
        event_type: str,
        content: str,
        source_id: str,
    ) -> dict[str, str]:
        """直接发布当前浏览器生命周期内的通知。"""
        del target_channel
        domain = _web_domain(event_type)
        severity = "error" if event_type == "proactive.incident.opened" else "info"
        await self._publish(
            domain=domain,
            entity_id=source_id,
            severity=severity,
            title=_web_notice_title(event_type),
            message=content,
        )
        return {"event_type": event_type, "source_id": source_id}

    async def delete_by_source_id(self, source_id: str) -> int:
        """Web 没有 durable delivery，因此删除不会改写 pending_deliveries.json。"""
        del source_id
        return 0


def _web_domain(event_type: str) -> str:
    if ".cron." in event_type:
        return "cron"
    if ".breakbeat." in event_type:
        return "breakbeat"
    if ".dream." in event_type:
        return "dream"
    if ".skill_evolution." in event_type:
        return "skill"
    return "incident"


def _web_notice_title(event_type: str) -> str:
    titles: dict[str, str] = {
        "proactive.cron.completed": "定时提醒已完成",
        "proactive.breakbeat.completed": "Breakbeat 已完成",
        "proactive.dream.completed": "长期记忆已更新",
        "proactive.skill_evolution.completed": "Skill 演进已完成",
        "proactive.incident.opened": "发现主动能力异常",
        "proactive.incident.resolved": "主动能力已恢复",
    }
    return titles.get(event_type, "主动能力更新")

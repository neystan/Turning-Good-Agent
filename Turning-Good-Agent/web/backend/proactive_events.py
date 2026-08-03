from __future__ import annotations

import asyncio
import copy
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any
from uuid import uuid4

from .proactive_control import ProactiveDomain, ProactiveSnapshot, WebProactiveControlService
from .proactive_ownership import OwnershipState


_DOMAINS = frozenset({"cron", "breakbeat", "dream", "skill", "incident"})
_NOTICE_FIELDS = frozenset({"id", "domain", "entity_id", "severity", "title", "message", "target"})


def snapshot_payload(snapshot: ProactiveSnapshot, owner: OwnershipState) -> dict[str, object]:
    """将领域快照转换成唯一的 WebSocket/REST 传输形状。"""
    return {
        "type": "snapshot",
        "domain": snapshot.domain,
        "data": snapshot.data,
        "runtime": asdict(snapshot.runtime),
        "proactive_revision": snapshot.proactive_revision,
        "owner": owner.to_dict(),
    }


class ProactiveEventHub:
    """维护主动快照 revision，并向 App 级订阅者广播完整领域快照。"""

    def __init__(
        self,
        controller: WebProactiveControlService,
        ownership: Callable[[], OwnershipState],
    ) -> None:
        self._controller = controller
        self._ownership = ownership
        self._queues: set[asyncio.Queue[dict[str, object]]] = set()
        self._lock = asyncio.Lock()
        self._last_bridge_payload: dict[str, object] | None = None

    async def subscribe(self) -> asyncio.Queue[dict[str, object]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        async with self._lock:
            self._queues.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, object]]) -> None:
        async with self._lock:
            self._queues.discard(queue)

    def initial_snapshots(self) -> list[dict[str, object]]:
        owner = self._ownership()
        return [snapshot_payload(snapshot, owner) for snapshot in self._controller.snapshot_all().values()]

    async def publish_all(self) -> None:
        """广播当前完整快照，用于所有权变化和重连校正。"""
        for payload in self.initial_snapshots():
            await self._broadcast(payload)

    async def publish_snapshot(
        self,
        domain: ProactiveDomain,
        *,
        changed: bool = True,
    ) -> dict[str, object]:
        """推送完整领域快照；后台变更时递增 revision，动作结果复用既有 revision。"""
        if changed:
            self._controller.bump_revision()
        payload = snapshot_payload(self._controller.snapshot_domain(domain), self._ownership())
        await self._broadcast(payload)
        return payload

    async def publish_notice(
        self,
        *,
        domain: ProactiveDomain,
        entity_id: str,
        severity: str,
        title: str,
        message: str,
        target: str,
    ) -> dict[str, object]:
        self._controller.bump_revision()
        payload: dict[str, object] = {
            "type": "notice",
            "id": str(uuid4()),
            "domain": domain,
            "entity_id": entity_id,
            "severity": severity,
            "title": title,
            "message": message,
            "target": target,
            "proactive_revision": self._controller.proactive_revision,
            "owner": self._ownership().to_dict(),
        }
        await self._broadcast(payload)
        return payload

    async def accept_bridge_event(self, payload: dict[str, object]) -> None:
        """接收本机 CLI 所有者转发的完整事件，不接受公网或旧事件。"""
        self._validate_bridge_payload(payload)
        if self._last_bridge_payload == payload:
            return
        revision = payload["proactive_revision"]
        assert isinstance(revision, int)
        if revision <= self._controller.proactive_revision:
            raise ValueError("bridge revision 必须严格递增")
        self._controller.advance_revision(revision)
        self._last_bridge_payload = copy.deepcopy(payload)
        await self._broadcast(payload)

    def _validate_bridge_payload(self, payload: dict[str, object]) -> None:
        if not isinstance(payload, dict):
            raise ValueError("bridge payload 必须是对象")
        event_type = payload.get("type")
        if event_type not in {"snapshot", "notice"}:
            raise ValueError("bridge payload type 必须是 snapshot 或 notice")
        domain = payload.get("domain")
        if domain not in _DOMAINS:
            raise ValueError("bridge payload domain 无效")
        revision = payload.get("proactive_revision")
        if not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
            raise ValueError("bridge revision 无效")
        owner = payload.get("owner")
        if not isinstance(owner, dict) or owner.get("owner_kind") != "cli":
            raise ValueError("bridge owner 必须是 CLI 所有者")
        if not isinstance(owner.get("owner_id"), str) or not owner["owner_id"]:
            raise ValueError("bridge owner 缺少 owner_id")
        active_owner = self._ownership()
        if active_owner.owner_kind != "cli" or owner["owner_id"] != active_owner.owner_id:
            raise ValueError("bridge event 来自 foreign owner")
        if event_type == "snapshot":
            if not isinstance(payload.get("data"), dict) or not isinstance(payload.get("runtime"), dict):
                raise ValueError("bridge snapshot 必须包含 data 和 runtime")
            return
        if not _NOTICE_FIELDS.issubset(payload):
            raise ValueError("bridge notice 缺少通知字段")
        if any(not isinstance(payload[field], str) or not payload[field] for field in _NOTICE_FIELDS):
            raise ValueError("bridge notice 字段无效")

    async def _broadcast(self, payload: dict[str, object]) -> None:
        async with self._lock:
            queues = tuple(self._queues)
        for queue in queues:
            if queue.qsize() > 64:
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(payload)

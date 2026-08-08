from __future__ import annotations

import asyncio
import os
from dataclasses import asdict, dataclass, field
from uuid import uuid4

from .proactive_control import ProactiveDomain, ProactiveSnapshot, WebProactiveControlService


@dataclass(frozen=True, slots=True)
class GatewayProactiveState:
    """Web 兼容投影：主动能力始终由当前 Gateway 唯一持有。"""

    owner_id: str = "gateway"
    owner_pid: int = field(default_factory=os.getpid)
    owner_kind: str = "gateway"

    @property
    def mode(self) -> str:
        return "owner"

    @property
    def writable(self) -> bool:
        return True

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "writable": self.writable,
            "owner_id": self.owner_id,
            "owner_kind": self.owner_kind,
            "owner_pid": self.owner_pid,
        }


def snapshot_payload(
    snapshot: ProactiveSnapshot,
    owner: GatewayProactiveState,
) -> dict[str, object]:
    """将领域快照转换成唯一的 WebSocket/REST 传输形状。"""
    return {
        "type": "snapshot",
        "domain": snapshot.domain,
        "data": snapshot.data,
        "runtime": asdict(snapshot.runtime),
        "proactive_revision": snapshot.proactive_revision,
        "owner": owner.to_dict(),
        "mutations_allowed": True,
    }


class ProactiveEventHub:
    """维护主动快照 revision，并向 App 级订阅者广播完整领域快照。"""

    def __init__(
        self,
        controller: WebProactiveControlService,
        owner: GatewayProactiveState,
    ) -> None:
        self._controller = controller
        self._owner = owner
        self._queues: set[asyncio.Queue[dict[str, object]]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[dict[str, object]]:
        queue: asyncio.Queue[dict[str, object]] = asyncio.Queue()
        async with self._lock:
            self._queues.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, object]]) -> None:
        async with self._lock:
            self._queues.discard(queue)

    def initial_snapshots(self) -> list[dict[str, object]]:
        return [
            snapshot_payload(snapshot, self._owner)
            for snapshot in self._controller.snapshot_all().values()
        ]

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
        payload = snapshot_payload(
            self._controller.snapshot_domain(domain),
            self._owner,
        )
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
            "owner": self._owner.to_dict(),
            "mutations_allowed": True,
        }
        await self._broadcast(payload)
        return payload

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

from __future__ import annotations

import asyncio
from typing import Any

from ..tools.base import ToolResult
from .delivery import DeliveryGate
from .types import Incident, new_id, utc_now_iso
from .store import ProactiveStore


class IncidentMonitor:
    """持久化主动基础设施的失败/恢复边沿，避免重复通知。"""

    def __init__(
        self,
        store: ProactiveStore,
        delivery_gate: DeliveryGate,
        *,
        target_channel: str,
        session_id: str = "proactive",
    ) -> None:
        """初始化对象状态。"""
        self._store = store
        self._delivery_gate = delivery_gate
        self._target_channel = target_channel
        self._session_id = session_id
        self._lock = asyncio.Lock()

    async def report_failure(self, *, fingerprint: str, source: str, message: str) -> bool:
        """记录失败；仅从健康或已恢复状态进入失败时通知。"""
        async with self._lock:
            open_incident = self._open_incident(fingerprint)
            now = utc_now_iso()
            if open_incident is not None:
                open_incident["last_detected_at"] = now
                open_incident["occurrence_count"] = int(open_incident["occurrence_count"]) + 1
                open_incident["message"] = message
                self._store.append_jsonl("incidents.jsonl", open_incident)
                return False

            incident = Incident(
                id=new_id("incident"),
                fingerprint=fingerprint,
                source=source,
                state="open",
                first_detected_at=now,
                last_detected_at=now,
                occurrence_count=1,
                message=message,
            ).to_dict()
            self._store.append_jsonl("incidents.jsonl", incident)
            await self._delivery_gate.enqueue(
                session_id=self._session_id,
                target_channel=self._target_channel,
                event_type="proactive.incident.opened",
                content=f"系统异常：{message}",
                metadata={"incident_id": incident["id"], "fingerprint": fingerprint, "source": source},
            )
            return True

    async def report_recovery(self, *, fingerprint: str, source: str, message: str) -> bool:
        """记录恢复；仅关闭仍处于 open 的同一 incident。"""
        async with self._lock:
            incident = self._open_incident(fingerprint)
            if incident is None:
                return False
            now = utc_now_iso()
            incident["state"] = "resolved"
            incident["last_detected_at"] = now
            incident["resolved_at"] = now
            incident["message"] = message
            self._store.append_jsonl("incidents.jsonl", incident)
            await self._delivery_gate.enqueue(
                session_id=self._session_id,
                target_channel=self._target_channel,
                event_type="proactive.incident.resolved",
                content=f"系统已恢复：{message}",
                metadata={"incident_id": incident["id"], "fingerprint": fingerprint, "source": source},
            )
            return True

    def list_incidents(self, state: str | None = None) -> list[Incident]:
        """从 durable 日志重建每个 Incident 的最新状态。"""
        if state not in {None, "open", "resolved"}:
            raise ValueError("Incident state 只能是 open 或 resolved")
        latest: dict[str, Incident] = {}
        for record in self._store.read_jsonl("incidents.jsonl"):
            incident = Incident.from_dict(record)
            latest[incident.id] = incident
        values = [item for item in latest.values() if state is None or item.state == state]
        return sorted(values, key=lambda item: (item.last_detected_at, item.id), reverse=True)

    def _open_incident(self, fingerprint: str) -> dict[str, Any] | None:
        """创建新的系统异常记录。"""
        current: dict[str, dict[str, Any]] = {}
        for record in self._store.read_jsonl("incidents.jsonl"):
            incident_id = str(record.get("id", ""))
            if incident_id:
                current[incident_id] = record
        for incident in reversed(list(current.values())):
            if incident.get("fingerprint") == fingerprint and incident.get("state") == "open":
                return dict(incident)
        return None


class ListIncidentsTool:
    """列出规则监控记录的系统异常。"""

    name = "list_incidents"
    description = "列出系统异常及其最新状态；可筛选 open 或 resolved。"
    parallel_safe = True
    input_schema = {
        "type": "object",
        "properties": {
            "state": {
                "type": "string",
                "enum": ["open", "resolved"],
                "description": "可选的异常状态筛选",
            }
        },
    }

    def __init__(self, monitor: IncidentMonitor) -> None:
        self._monitor = monitor

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """返回 Incident 摘要，不调用模型。"""
        raw_state = args.get("state")
        state = None if raw_state is None else str(raw_state)
        incidents = self._monitor.list_incidents(state)
        if not incidents:
            return ToolResult("暂无系统异常。")
        return ToolResult(
            "\n".join(
                f"[{item.state}] {item.id} | {item.source} | {item.occurrence_count} 次 | {item.message}"
                for item in incidents
            )
        )

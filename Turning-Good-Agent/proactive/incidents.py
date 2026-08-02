from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from copy import deepcopy
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
        state_changed: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """初始化对象状态。"""
        self._store = store
        self._delivery_gate = delivery_gate
        self._target_channel = target_channel
        self._state_changed = state_changed
        self._lock = asyncio.Lock()

    async def report_failure(self, *, fingerprint: str, source: str, message: str) -> bool:
        """记录失败；仅从健康或已恢复状态进入失败时通知。"""
        changed = False
        async with self._lock:
            incidents = self._load_incidents()
            index = self._index_for_fingerprint(incidents, fingerprint)
            now = utc_now_iso()
            if index is not None and incidents[index]["state"] == "open":
                incident = incidents[index]
                incident["occurrence_count"] = int(incident["occurrence_count"]) + 1
                incident["last_detected_at"] = now
                self._save_incidents(incidents)
                changed = True
                should_notify = False
            else:
                previous = deepcopy(incidents)
                if index is None:
                    incident: dict[str, Any] = {
                        "id": new_id("incident"),
                        "fingerprint": fingerprint,
                        "source": source,
                        "state": "open",
                        "first_detected_at": now,
                        "last_detected_at": now,
                        "occurrence_count": 1,
                        "message": message,
                        "history": [self._history_entry("open", now, message)],
                    }
                    incidents.append(incident)
                else:
                    incident = incidents[index]
                    incident["state"] = "open"
                    incident["last_detected_at"] = now
                    incident["occurrence_count"] = int(incident["occurrence_count"]) + 1
                    incident["message"] = message
                    incident["history"].append(self._history_entry("open", now, message))
                self._save_incidents(incidents)
                try:
                    await self._delivery_gate.enqueue(
                        target_channel=self._target_channel,
                        event_type="proactive.incident.opened",
                        content=f"系统异常：{message}",
                        source_id=fingerprint,
                    )
                except Exception:
                    self._save_incidents(previous)
                    raise
                changed = True
                should_notify = True
        if changed:
            await self._notify_state_changed()
        return should_notify

    async def report_recovery(self, *, fingerprint: str, source: str, message: str) -> bool:
        """记录恢复；仅关闭仍处于 open 的同一 incident。"""
        changed = False
        async with self._lock:
            incidents = self._load_incidents()
            index = self._index_for_fingerprint(incidents, fingerprint)
            if index is None:
                return False
            incident = incidents[index]
            if incident["state"] == "resolved":
                return False
            previous = deepcopy(incidents)
            now = utc_now_iso()
            incident["state"] = "resolved"
            incident["last_detected_at"] = now
            incident["message"] = message
            incident["history"].append(self._history_entry("resolved", now, message))
            self._save_incidents(incidents)
            try:
                await self._delivery_gate.enqueue(
                    target_channel=self._target_channel,
                    event_type="proactive.incident.resolved",
                    content=f"系统已恢复：{message}",
                    source_id=fingerprint,
                )
            except Exception:
                self._save_incidents(previous)
                raise
            changed = True
        if changed:
            await self._notify_state_changed()
        return True

    def list_incidents(self, state: str | None = None) -> list[Incident]:
        """读取当前 Incident 快照。"""
        if state not in {None, "open", "resolved"}:
            raise ValueError("Incident state 只能是 open 或 resolved")
        values = [
            Incident.from_dict(record)
            for record in self._load_incidents()
            if state is None or record["state"] == state
        ]
        return sorted(values, key=lambda item: (item.last_detected_at, item.id), reverse=True)

    def get_by_fingerprint(self, fingerprint: str) -> Incident | None:
        """返回指定 fingerprint 的当前 Incident。"""
        incidents = self._load_incidents()
        index = self._index_for_fingerprint(incidents, fingerprint)
        return None if index is None else Incident.from_dict(incidents[index])

    async def delete_by_fingerprint(self, fingerprint: str) -> int:
        """硬删除指定 fingerprint 的当前 Incident。"""
        async with self._lock:
            incidents = self._load_incidents()
            retained = [record for record in incidents if record["fingerprint"] != fingerprint]
            removed = len(incidents) - len(retained)
            if removed:
                self._save_incidents(retained)
        if removed:
            await self._notify_state_changed()
        return removed

    async def _notify_state_changed(self) -> None:
        if self._state_changed is not None:
            await self._state_changed()

    def _load_incidents(self) -> list[dict[str, Any]]:
        """读取当前 Incident 快照。"""
        payload = self._store.read_json("incidents.json", {"incidents": []})
        if not isinstance(payload, dict) or set(payload) != {"incidents"}:
            raise ValueError("incidents.json 必须仅包含 incidents")
        incidents = payload["incidents"]
        if not isinstance(incidents, list) or not all(isinstance(record, dict) for record in incidents):
            raise ValueError("incidents.json 的 incidents 必须是对象数组")
        parsed = [Incident.from_dict(record) for record in incidents]
        if len({incident.fingerprint for incident in parsed}) != len(parsed):
            raise ValueError("incidents.json 包含重复 fingerprint")
        return [incident.to_dict() for incident in parsed]

    def _save_incidents(self, incidents: list[dict[str, Any]]) -> None:
        """写入当前 Incident 快照。"""
        self._store.write_json("incidents.json", {"incidents": incidents})

    @staticmethod
    def _index_for_fingerprint(incidents: list[dict[str, Any]], fingerprint: str) -> int | None:
        """查找一个 fingerprint 对应的唯一当前记录。"""
        for index, incident in enumerate(incidents):
            if incident.get("fingerprint") == fingerprint:
                return index
        return None

    @staticmethod
    def _history_entry(state: str, occurred_at: str, message: str) -> dict[str, str]:
        """记录一次状态转换。"""
        return {"state": state, "occurred_at": occurred_at, "message": message}


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
                f"[{item.state}] {item.fingerprint} | {item.source} | {item.occurrence_count} 次 | {item.message}"
                for item in incidents
            )
        )

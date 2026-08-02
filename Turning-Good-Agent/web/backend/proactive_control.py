from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Literal

from ...proactive import breakbeat as breakbeat_module
from ...proactive import dream as dream_module
from ...proactive import skill_evolution as skill_module
from ...proactive.store import ProactiveStore
from ...proactive.types import CronJob, Incident, UsageTotals, utc_now_iso

ProactiveDomain = Literal["cron", "breakbeat", "dream", "skill", "incident"]
_DOMAINS = ("cron", "breakbeat", "dream", "skill", "incident")

class ProactiveControlError(RuntimeError):
    pass
class ProactiveNotFoundError(ProactiveControlError):
    pass
class ProactiveConflictError(ProactiveControlError):
    pass

@dataclass(frozen=True, slots=True)
class ProactiveRuntime:
    running: bool = False
    next_run_at: str | None = None
    usage: dict[str, int] = field(default_factory=dict)

@dataclass(frozen=True, slots=True)
class ProactiveSnapshot:
    domain: ProactiveDomain
    data: dict[str, Any]
    runtime: ProactiveRuntime
    proactive_revision: int

@dataclass(frozen=True, slots=True)
class ProactiveNoticeTarget:
    domain: ProactiveDomain
    identifier: str
    action: str

class WebProactiveControlService:
    def __init__(
        self,
        runtime: Any,
        *,
        proactive_service: Any | None = None,
        store: ProactiveStore | None = None,
    ) -> None:
        self.runtime = runtime
        self.proactive_service = proactive_service
        self.store = store or ProactiveStore(runtime.settings.data_dir)
        self._locks = {domain: asyncio.Lock() for domain in _DOMAINS}
        self._proactive_revision = 0

    @property
    def proactive_revision(self) -> int:
        return self._proactive_revision

    def snapshot_all(self) -> dict[ProactiveDomain, ProactiveSnapshot]:
        return {domain: self.snapshot_domain(domain) for domain in _DOMAINS}

    def snapshot_domain(self, domain: ProactiveDomain) -> ProactiveSnapshot:
        data = self._domain_data(domain)
        return ProactiveSnapshot(domain, data, self._runtime_view(domain, data), self._proactive_revision)

    async def delete_cron(self, identifier: str) -> ProactiveNoticeTarget:
        async with self._locks["cron"]:
            payload = self._read("cron.json", {"jobs": [], "usage": self._usage()})
            jobs = self._items(payload, "jobs")
            self._find(jobs, identifier, "Cron Job")
            payload["jobs"] = [item for item in jobs if item.get("id") != identifier]
            self.store.write_json("cron.json", payload)
            return self._changed("cron", identifier, "delete")

    async def complete_breakbeat(self, identifier: str) -> ProactiveNoticeTarget:
        async with self._locks["breakbeat"]:
            payload = self._read("breakbeat.json", self._breakbeat_default())
            item = self._find(self._items(payload, "items"), identifier, "Breakbeat")
            if item.get("status") == "completed":
                raise ProactiveConflictError(f"Breakbeat 已完成：{identifier}")
            item["status"] = "completed"
            item["updated_at"] = utc_now_iso()
            self.store.write_json("breakbeat.json", payload)
            return self._changed("breakbeat", identifier, "complete")

    async def delete_breakbeat(self, identifier: str) -> ProactiveNoticeTarget:
        async with self._locks["breakbeat"]:
            payload = self._read("breakbeat.json", self._breakbeat_default())
            items = self._items(payload, "items")
            self._find(items, identifier, "Breakbeat")
            payload["items"] = [item for item in items if item.get("id") != identifier]
            self.store.write_json("breakbeat.json", payload)
            return self._changed("breakbeat", identifier, "delete")

    async def delete_draft(self, name: str) -> ProactiveNoticeTarget:
        async with self._locks["skill"]:
            if name not in self.runtime.skills.list_drafts():
                raise ProactiveNotFoundError(f"Skill Draft 不存在：{name}")
            self.runtime.skills.validator.validate(self.runtime.skills.directory / ".drafts" / name)
            await self.runtime.skills.delete_draft(name)
            return self._changed("skill", name, "delete_draft")

    async def resolve_incident(self, identifier: str) -> ProactiveNoticeTarget:
        async with self._locks["incident"]:
            payload = self._read("incidents.json", {"incidents": []})
            item = self._find(self._items(payload, "incidents"), identifier, "Incident")
            if item.get("state") == "resolved":
                raise ProactiveConflictError(f"Incident 已解决：{identifier}")
            occurred_at = utc_now_iso()
            item["state"] = "resolved"
            item["last_detected_at"] = occurred_at
            item["history"].append(
                {"state": "resolved", "occurred_at": occurred_at, "message": item["message"]}
            )
            self.store.write_json("incidents.json", payload)
            return self._changed("incident", identifier, "resolve")

    async def delete_incident(self, identifier: str) -> ProactiveNoticeTarget:
        async with self._locks["incident"]:
            payload = self._read("incidents.json", {"incidents": []})
            items = self._items(payload, "incidents")
            self._find(items, identifier, "Incident")
            payload["incidents"] = [item for item in items if item.get("id") != identifier]
            self.store.write_json("incidents.json", payload)
            return self._changed("incident", identifier, "delete")

    def _domain_data(self, domain: ProactiveDomain) -> dict[str, Any]:
        if domain == "cron":
            return self._read("cron.json", {"jobs": [], "usage": self._usage()})
        if domain == "breakbeat":
            return self._read("breakbeat.json", self._breakbeat_default())
        if domain == "dream":
            data = self._read("dream.json", {"cursors": {}, "next_run_at": None, "usage": self._usage()})
            memory = self.runtime.profile_memory.read()
            return {**data, "memory": {"user": memory.user, "soul": memory.soul}}
        if domain == "skill":
            data = self._read("skill.json", {"observations": [], "next_run_at": None, "usage": self._usage()})
            return {**data, "drafts": self._draft_views()}
        return self._read("incidents.json", {"incidents": []})

    def _runtime_view(self, domain: ProactiveDomain, data: dict[str, Any]) -> ProactiveRuntime:
        manager = getattr(self.proactive_service, domain, None)
        next_run_at = getattr(manager, "next_run_at", data.get("next_run_at"))
        if hasattr(next_run_at, "isoformat"):
            next_run_at = next_run_at.isoformat()
        usage = data.get("usage", {})
        return ProactiveRuntime(bool(getattr(manager, "running", False)), next_run_at, dict(usage) if isinstance(usage, dict) else {})

    def _draft_views(self) -> list[dict[str, str]]:
        root = self.runtime.skills.directory / ".drafts"
        result = []
        for name in self.runtime.skills.list_drafts():
            try:
                manifest = self.runtime.skills.validator.validate(root / name)
            except (OSError, ValueError):
                continue
            result.append({"name": manifest.name, "description": manifest.description, "body": manifest.body})
        return result

    def _changed(self, domain: ProactiveDomain, identifier: str, action: str) -> ProactiveNoticeTarget:
        self._proactive_revision += 1
        return ProactiveNoticeTarget(domain, identifier, action)

    def _read(self, name: str, default: dict[str, Any]) -> dict[str, Any]:
        payload = self.store.read_json(name, default)
        if not isinstance(payload, dict):
            raise ValueError(f"{name} 必须是 object")
        self._validate_snapshot(name, payload)
        return payload

    @staticmethod
    def _validate_snapshot(name: str, payload: dict[str, Any]) -> None:
        if name == "cron.json":
            _validate_keys(payload, {"jobs", "usage"}, name)
            if not isinstance(payload["jobs"], list):
                raise ValueError("cron.json.jobs 必须是数组")
            jobs = [CronJob.from_dict(item) for item in payload["jobs"]]
            if len({job.id for job in jobs}) != len(jobs):
                raise ValueError("cron.json 包含重复 Cron Job ID")
            UsageTotals.from_dict(payload["usage"])
            return
        if name == "breakbeat.json":
            _validate_keys(payload, {"items", "cursors", "next_run_at", "usage"}, name)
            if not isinstance(payload["items"], list) or not all(isinstance(item, dict) for item in payload["items"]):
                raise ValueError("breakbeat.json.items 必须是对象数组")
            [breakbeat_module._item_from_dict(item) for item in payload["items"]]
            if not isinstance(payload["cursors"], dict):
                raise ValueError("breakbeat.json.cursors 必须是对象")
            [breakbeat_module._cursor_from_dict(cursor) for cursor in payload["cursors"].values()]
            breakbeat_module._next_run_at(payload["next_run_at"])
            breakbeat_module._usage_from_dict(payload["usage"])
            return
        if name == "dream.json":
            _validate_keys(payload, {"cursors", "next_run_at", "usage"}, name)
            if not isinstance(payload["cursors"], dict):
                raise ValueError("dream.json.cursors 必须是对象")
            [dream_module._cursor_from_dict(cursor) for cursor in payload["cursors"].values()]
            dream_module._next_run_at(payload["next_run_at"])
            dream_module._usage_from_dict(payload["usage"])
            return
        if name == "skill.json":
            _validate_keys(payload, {"observations", "next_run_at", "usage"}, name)
            if not isinstance(payload["observations"], list) or not all(
                isinstance(item, dict) for item in payload["observations"]
            ):
                raise ValueError("skill.json.observations 必须是对象数组")
            [skill_module._observation_from_dict(item) for item in payload["observations"]]
            skill_module._next_run_at(payload["next_run_at"])
            skill_module._usage_from_dict(payload["usage"])
            return
        if name == "incidents.json":
            _validate_keys(payload, {"incidents"}, name)
            if not isinstance(payload["incidents"], list):
                raise ValueError("incidents.json.incidents 必须是数组")
            incidents = [Incident.from_dict(item) for item in payload["incidents"]]
            if len({incident.fingerprint for incident in incidents}) != len(incidents):
                raise ValueError("incidents.json 包含重复 fingerprint")

    @staticmethod
    def _items(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
        values = payload.get(key)
        if not isinstance(values, list) or not all(isinstance(item, dict) for item in values):
            raise ValueError(f"{key} 必须是对象数组")
        return values

    @staticmethod
    def _find(items: list[dict[str, Any]], identifier: str, label: str) -> dict[str, Any]:
        for item in items:
            if item.get("id") == identifier:
                return item
        raise ProactiveNotFoundError(f"{label} 不存在：{identifier}")

    @staticmethod
    def _usage() -> dict[str, int]:
        return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    @classmethod
    def _breakbeat_default(cls) -> dict[str, Any]:
        return {"items": [], "cursors": {}, "next_run_at": None, "usage": cls._usage()}


def _validate_keys(payload: dict[str, Any], required: set[str], name: str) -> None:
    if set(payload) != required:
        raise ValueError(f"{name} 字段无效")

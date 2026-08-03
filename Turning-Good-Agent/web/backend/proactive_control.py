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
    entity_states: dict[str, str] = field(default_factory=dict)

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


@dataclass(frozen=True, slots=True)
class _ProactiveBinding:
    """将 Runtime、Service 与 Store 作为一个不可分割的读模型绑定。"""

    runtime: Any
    proactive_service: Any | None
    store: ProactiveStore

class WebProactiveControlService:
    def __init__(
        self,
        runtime: Any,
        *,
        proactive_service: Any | None = None,
        store: ProactiveStore | None = None,
    ) -> None:
        self._binding = _ProactiveBinding(
            runtime,
            proactive_service,
            store or ProactiveStore(runtime.settings.data_dir),
        )
        self._locks = {domain: asyncio.Lock() for domain in _DOMAINS}
        self._proactive_revision = 0

    @property
    def runtime(self) -> Any:
        return self._binding.runtime

    @property
    def proactive_service(self) -> Any | None:
        return self._binding.proactive_service

    @property
    def store(self) -> ProactiveStore:
        return self._binding.store

    def replace_runtime(self, runtime: Any, proactive_service: Any | None) -> None:
        """原子切换 Web 读模型绑定，不影响旧绑定直到替换已可用。"""
        self._binding = _ProactiveBinding(runtime, proactive_service, ProactiveStore(runtime.settings.data_dir))

    @property
    def proactive_revision(self) -> int:
        return self._proactive_revision

    def bump_revision(self) -> int:
        """记录一个后台领域变更并返回新的独立主动 revision。"""
        self._proactive_revision += 1
        return self._proactive_revision

    def advance_revision(self, revision: int) -> int:
        """接受本机所有者桥接而来的较新 revision。"""
        self._proactive_revision = max(self._proactive_revision, revision)
        return self._proactive_revision

    def snapshot_all(self) -> dict[ProactiveDomain, ProactiveSnapshot]:
        return {domain: self.snapshot_domain(domain) for domain in _DOMAINS}

    def snapshot_domain(self, domain: ProactiveDomain) -> ProactiveSnapshot:
        self._require_domain(domain)
        data = self._domain_data(domain)
        return ProactiveSnapshot(domain, data, self._runtime_view(domain, data), self._proactive_revision)

    async def delete_cron(self, identifier: str) -> ProactiveNoticeTarget:
        async with self._locks["cron"]:
            payload = self._read("cron.json", {"jobs": [], "usage": self._usage()})
            jobs = self._items(payload, "jobs")
            self._find(jobs, identifier, "Cron Job")
            cron = self._manager_for_domain("cron")
            if cron is None:
                payload["jobs"] = [item for item in jobs if item.get("id") != identifier]
                self.store.write_json("cron.json", payload)
            else:
                await cron.delete_job(identifier)
            return self._changed("cron", identifier, "delete")

    async def complete_breakbeat(self, identifier: str) -> ProactiveNoticeTarget:
        async with self._locks["breakbeat"]:
            payload = self._read("breakbeat.json", self._breakbeat_default())
            item = self._find(self._items(payload, "items"), identifier, "Breakbeat")
            if item.get("status") == "completed":
                raise ProactiveConflictError(f"Breakbeat 已完成：{identifier}")
            breakbeat = self._manager_for_domain("breakbeat")
            if breakbeat is None:
                item["status"] = "completed"
                item["updated_at"] = utc_now_iso()
                self.store.write_json("breakbeat.json", payload)
            else:
                await breakbeat.complete_item(identifier, strict=True)
            return self._changed("breakbeat", identifier, "complete")

    async def delete_breakbeat(self, identifier: str) -> ProactiveNoticeTarget:
        async with self._locks["breakbeat"]:
            payload = self._read("breakbeat.json", self._breakbeat_default())
            items = self._items(payload, "items")
            self._find(items, identifier, "Breakbeat")
            breakbeat = self._manager_for_domain("breakbeat")
            if breakbeat is None:
                payload["items"] = [item for item in items if item.get("id") != identifier]
                self.store.write_json("breakbeat.json", payload)
            else:
                await breakbeat.delete_item(identifier)
            return self._changed("breakbeat", identifier, "delete")

    async def delete_draft(self, name: str) -> ProactiveNoticeTarget:
        async with self._locks["skill"]:
            if name not in self.runtime.skills.list_drafts():
                raise ProactiveNotFoundError(f"Skill Draft 不存在：{name}")
            self.runtime.skills.validator.validate(self.runtime.skills.directory / ".drafts" / name)
            await self.runtime.skills.delete_draft(name)
            return self._changed("skill", name, "delete_draft")

    async def resolve_incident(self, fingerprint: str) -> ProactiveNoticeTarget:
        async with self._locks["incident"]:
            payload = self._read("incidents.json", {"incidents": []})
            item = self._find_by_field(
                self._items(payload, "incidents"),
                "fingerprint",
                fingerprint,
                "Incident",
            )
            if item.get("state") == "resolved":
                raise ProactiveConflictError(f"Incident 已解决：{fingerprint}")
            incidents = self._manager_for_domain("incident")
            if incidents is None:
                occurred_at = utc_now_iso()
                item["state"] = "resolved"
                item["last_detected_at"] = occurred_at
                item["history"].append(
                    {
                        "state": "resolved",
                        "occurred_at": occurred_at,
                        "message": "用户在 Web 中标记已解决",
                    }
                )
                self.store.write_json("incidents.json", payload)
            else:
                await incidents.resolve_by_fingerprint(fingerprint)
            return self._changed("incident", fingerprint, "resolve")

    async def delete_incident(self, fingerprint: str) -> ProactiveNoticeTarget:
        async with self._locks["incident"]:
            payload = self._read("incidents.json", {"incidents": []})
            items = self._items(payload, "incidents")
            self._find_by_field(items, "fingerprint", fingerprint, "Incident")
            incidents = self._manager_for_domain("incident")
            if incidents is None:
                payload["incidents"] = [
                    item for item in items if item.get("fingerprint") != fingerprint
                ]
                self.store.write_json("incidents.json", payload)
            else:
                await incidents.delete_by_fingerprint(fingerprint)
            return self._changed("incident", fingerprint, "delete")

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
        if domain == "incident":
            return self._read("incidents.json", {"incidents": []})
        raise ValueError(f"未知主动领域：{domain}")

    def _runtime_view(self, domain: ProactiveDomain, data: dict[str, Any]) -> ProactiveRuntime:
        manager = self._manager_for_domain(domain)
        if domain == "cron":
            cron = manager
            if cron is None:
                return ProactiveRuntime()
            states = {
                str(job["id"]): str(cron.runtime_state(str(job["id"])))
                for job in self._items(data, "jobs")
            }
            deadline = cron.next_deadline()
            return ProactiveRuntime(
                running=any(state in {"queued", "running"} for state in states.values()),
                next_run_at=self._iso_value(deadline),
                entity_states=states,
            )
        if manager is None:
            return ProactiveRuntime()
        return ProactiveRuntime(
            running=bool(getattr(manager, "running", False)),
            next_run_at=self._iso_value(getattr(manager, "next_run_at", None)),
            entity_states={"service": "running" if getattr(manager, "running", False) else "idle"},
        )

    def _manager_for_domain(self, domain: ProactiveDomain) -> Any | None:
        if self.proactive_service is None:
            return None
        names = {
            "cron": "cron",
            "breakbeat": "breakbeat",
            "dream": "dream",
            "skill": "skill_evolution",
            "incident": "incidents",
        }
        return getattr(self.proactive_service, names[domain], None)

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
        self.bump_revision()
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
        return WebProactiveControlService._find_by_field(items, "id", identifier, label)

    @staticmethod
    def _find_by_field(
        items: list[dict[str, Any]],
        field: str,
        value: str,
        label: str,
    ) -> dict[str, Any]:
        for item in items:
            if item.get(field) == value:
                return item
        raise ProactiveNotFoundError(f"{label} 不存在：{value}")

    @staticmethod
    def _iso_value(value: object) -> str | None:
        if value is None:
            return None
        if hasattr(value, "isoformat"):
            return str(value.isoformat())
        return value if isinstance(value, str) else None

    @staticmethod
    def _require_domain(domain: object) -> None:
        if domain not in _DOMAINS:
            raise ValueError(f"未知主动领域：{domain}")

    @staticmethod
    def _usage() -> dict[str, int]:
        return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    @classmethod
    def _breakbeat_default(cls) -> dict[str, Any]:
        return {"items": [], "cursors": {}, "next_run_at": None, "usage": cls._usage()}


def _validate_keys(payload: dict[str, Any], required: set[str], name: str) -> None:
    if set(payload) != required:
        raise ValueError(f"{name} 字段无效")

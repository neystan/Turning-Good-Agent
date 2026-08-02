from __future__ import annotations

import asyncio
import importlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
import pytest


control_module = importlib.import_module("Turning-Good-Agent.web.backend.proactive_control")
settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
memory_module = importlib.import_module("Turning-Good-Agent.memory.long_term")
skills_module = importlib.import_module("Turning-Good-Agent.skills.manager")
store_module = importlib.import_module("Turning-Good-Agent.proactive.store")

ProactiveConflictError = control_module.ProactiveConflictError
ProactiveNotFoundError = control_module.ProactiveNotFoundError
WebProactiveControlService = control_module.WebProactiveControlService
ProfileMemory = memory_module.ProfileMemory
ProactiveSettings = settings_module.ProactiveSettings
Settings = settings_module.Settings
SkillManager = skills_module.SkillManager
ProactiveStore = store_module.ProactiveStore


class _FakeCron:
    def __init__(self, states: dict[str, str]) -> None:
        self._states = states

    def runtime_state(self, job_id: str) -> str:
        return self._states[job_id]

    @staticmethod
    def next_deadline() -> datetime:
        return datetime(2026, 8, 2, 13, 0, tzinfo=UTC)


class _FakeManager:
    def __init__(self, *, running: bool, next_run_at: datetime | None) -> None:
        self.running = running
        self.next_run_at = next_run_at


def _usage() -> dict[str, int]:
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _runtime(tmp_path: Path) -> SimpleNamespace:
    settings = Settings(data_dir=tmp_path)
    settings.proactive = ProactiveSettings()
    skills = SkillManager(tmp_path / ".skills", settings.skills)
    return SimpleNamespace(
        settings=settings,
        profile_memory=ProfileMemory(tmp_path, settings.proactive),
        skills=skills,
    )


def _proactive_service() -> SimpleNamespace:
    next_run_at = datetime(2026, 8, 2, 14, 0, tzinfo=UTC)
    return SimpleNamespace(
        cron=_FakeCron({"cron-1": "running"}),
        breakbeat=_FakeManager(running=True, next_run_at=next_run_at),
        dream=_FakeManager(running=False, next_run_at=next_run_at),
        skill_evolution=_FakeManager(running=True, next_run_at=next_run_at),
        incidents=object(),
    )


def _service(
    tmp_path: Path,
    *,
    proactive_service: object | None = None,
) -> tuple[WebProactiveControlService, ProactiveStore]:
    runtime = _runtime(tmp_path)
    return (
        WebProactiveControlService(runtime, proactive_service=proactive_service),
        ProactiveStore(tmp_path),
    )


def _seed_snapshots(store: ProactiveStore) -> None:
    store.write_json(
        "cron.json",
        {
            "jobs": [
                {
                    "id": "cron-1",
                    "cron": None,
                    "created_at": "2026-08-02T12:00:00+00:00",
                    "prompt": "check",
                    "recurring": False,
                    "delivery_channels": ["cli"],
                    "updated_at": "2026-08-02T12:00:00+00:00",
                    "next_run_at": "2026-08-02T13:00:00+00:00",
                }
            ],
            "usage": _usage(),
        },
    )
    store.write_json(
        "breakbeat.json",
        {
            "items": [
                {
                    "id": "breakbeat-1",
                    "todo": "follow up",
                    "deadline": None,
                    "source_session_id": "session-1",
                    "status": "in_progress",
                    "created_at": "2026-08-02T12:00:00+00:00",
                    "updated_at": "2026-08-02T12:00:00+00:00",
                }
            ],
            "cursors": {},
            "next_run_at": None,
            "usage": _usage(),
        },
    )
    store.write_json("dream.json", {"cursors": {}, "next_run_at": None, "usage": _usage()})
    store.write_json("skill.json", {"observations": [], "next_run_at": None, "usage": _usage()})
    store.write_json(
        "incidents.json",
        {
            "incidents": [
                {
                    "id": "incident-1",
                    "fingerprint": "cron:failed",
                    "source": "cron",
                    "state": "open",
                    "first_detected_at": "2026-08-02T12:00:00+00:00",
                    "last_detected_at": "2026-08-02T12:00:00+00:00",
                    "occurrence_count": 1,
                    "message": "failed",
                    "history": [
                        {
                            "state": "open",
                            "occurred_at": "2026-08-02T12:00:00+00:00",
                            "message": "failed",
                        }
                    ],
                }
            ]
        },
    )


def _create_draft(service: WebProactiveControlService) -> None:
    draft = service.runtime.skills.directory / ".drafts" / "web-review"
    draft.mkdir(parents=True)
    (draft / "SKILL.md").write_text(
        "---\nname: web-review\ndescription: Review web changes\n---\n\nUse focused checks.\n",
        encoding="utf-8",
    )


def test_snapshot_all_separates_persisted_data_runtime_and_shared_revision(tmp_path: Path) -> None:
    service, store = _service(tmp_path, proactive_service=_proactive_service())
    _seed_snapshots(store)
    runtime = service.runtime
    runtime.profile_memory.write(memory_module.ProfileMemorySnapshot(user="prefers tests", soul="be precise"))
    _create_draft(service)

    snapshots = service.snapshot_all()

    assert set(snapshots) == {"cron", "breakbeat", "dream", "skill", "incident"}
    assert {snapshot.proactive_revision for snapshot in snapshots.values()} == {0}
    assert snapshots["cron"].data["jobs"][0]["id"] == "cron-1"
    assert snapshots["cron"].runtime.entity_states == {"cron-1": "running"}
    assert snapshots["cron"].runtime.next_run_at == "2026-08-02T13:00:00+00:00"
    assert snapshots["breakbeat"].runtime.running is True
    assert snapshots["breakbeat"].runtime.entity_states == {"service": "running"}
    assert snapshots["dream"].data["memory"] == {"user": "prefers tests", "soul": "be precise"}
    assert snapshots["dream"].data["usage"] == _usage()
    assert not hasattr(snapshots["dream"].runtime, "usage")
    assert snapshots["skill"].runtime.running is True
    assert snapshots["skill"].data["drafts"] == [
        {"name": "web-review", "description": "Review web changes", "body": "Use focused checks."}
    ]
    assert snapshots["incident"].data["incidents"][0]["state"] == "open"


def test_direct_actions_update_only_their_snapshot_and_increment_revision(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _seed_snapshots(store)
    _create_draft(service)

    cron_target = asyncio.run(service.delete_cron("cron-1"))
    completed_target = asyncio.run(service.complete_breakbeat("breakbeat-1"))
    resolved_target = asyncio.run(service.resolve_incident("cron:failed"))
    deleted_draft = asyncio.run(service.delete_draft("web-review"))

    assert (cron_target.domain, cron_target.identifier) == ("cron", "cron-1")
    assert (completed_target.domain, completed_target.identifier) == ("breakbeat", "breakbeat-1")
    assert (resolved_target.domain, resolved_target.identifier) == ("incident", "cron:failed")
    assert (deleted_draft.domain, deleted_draft.identifier) == ("skill", "web-review")
    assert store.read_json("cron.json", {})["jobs"] == []
    assert store.read_json("breakbeat.json", {})["items"][0]["status"] == "completed"
    incident = store.read_json("incidents.json", {})["incidents"][0]
    assert incident["state"] == "resolved"
    assert incident["history"][-1]["message"] == "用户在 Web 中标记已解决"
    assert service.snapshot_domain("cron").proactive_revision == 4


def test_delete_breakbeat_and_incident_are_targeted(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _seed_snapshots(store)

    breakbeat_target = asyncio.run(service.delete_breakbeat("breakbeat-1"))
    incident_target = asyncio.run(service.delete_incident("cron:failed"))

    assert (breakbeat_target.domain, breakbeat_target.identifier) == ("breakbeat", "breakbeat-1")
    assert (incident_target.domain, incident_target.identifier) == ("incident", "cron:failed")
    assert store.read_json("breakbeat.json", {})["items"] == []
    assert store.read_json("incidents.json", {})["incidents"] == []
    assert service.proactive_revision == 2


def test_actions_report_missing_and_conflicting_records_without_partial_writes(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _seed_snapshots(store)
    _create_draft(service)
    original = {
        name: store.read_json(name, {})
        for name in ("cron.json", "breakbeat.json", "incidents.json")
    }

    for operation in (
        lambda: service.delete_cron("missing"),
        lambda: service.delete_breakbeat("missing"),
        lambda: service.delete_draft("missing"),
        lambda: service.resolve_incident("missing"),
        lambda: service.delete_incident("missing"),
    ):
        with pytest.raises(ProactiveNotFoundError):
            asyncio.run(operation())

    asyncio.run(service.complete_breakbeat("breakbeat-1"))
    asyncio.run(service.resolve_incident("cron:failed"))
    with pytest.raises(ProactiveConflictError):
        asyncio.run(service.complete_breakbeat("breakbeat-1"))
    with pytest.raises(ProactiveConflictError):
        asyncio.run(service.resolve_incident("cron:failed"))

    assert store.read_json("cron.json", {}) == original["cron.json"]
    assert store.read_json("breakbeat.json", {})["items"][0]["status"] == "completed"
    assert store.read_json("incidents.json", {})["incidents"][0]["state"] == "resolved"
    assert service.proactive_revision == 2


def test_snapshot_domain_rejects_unknown_domain(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)

    with pytest.raises(ValueError, match="未知主动领域"):
        service.snapshot_domain("unknown")  # type: ignore[arg-type]

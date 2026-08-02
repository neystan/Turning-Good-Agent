from __future__ import annotations

import asyncio
import importlib
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


def _service(tmp_path: Path) -> tuple[WebProactiveControlService, ProactiveStore]:
    runtime = _runtime(tmp_path)
    return WebProactiveControlService(runtime), ProactiveStore(tmp_path)


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


def test_snapshot_all_separates_persisted_data_runtime_and_shared_revision(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _seed_snapshots(store)
    runtime = service.runtime
    runtime.profile_memory.write(memory_module.ProfileMemorySnapshot(user="prefers tests", soul="be precise"))
    draft = runtime.skills.directory / ".drafts" / "web-review"
    draft.mkdir(parents=True)
    (draft / "SKILL.md").write_text(
        "---\nname: web-review\ndescription: Review web changes\n---\n\nUse focused checks.\n",
        encoding="utf-8",
    )

    snapshots = service.snapshot_all()

    assert set(snapshots) == {"cron", "breakbeat", "dream", "skill", "incident"}
    assert {snapshot.proactive_revision for snapshot in snapshots.values()} == {0}
    assert snapshots["cron"].data["jobs"][0]["id"] == "cron-1"
    assert snapshots["breakbeat"].runtime.running is False
    assert snapshots["dream"].data["memory"] == {"user": "prefers tests", "soul": "be precise"}
    assert snapshots["skill"].data["drafts"] == [
        {"name": "web-review", "description": "Review web changes", "body": "Use focused checks."}
    ]
    assert snapshots["incident"].data["incidents"][0]["state"] == "open"
    assert "pending_deliveries.json" not in {path.name for path in store.root.iterdir()}


def test_direct_actions_update_only_their_snapshot_and_increment_revision(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _seed_snapshots(store)
    store.write_json("pending_deliveries.json", {"deliveries": [{"source_id": "cron-1"}]})
    draft = service.runtime.skills.directory / ".drafts" / "web-review"
    draft.mkdir(parents=True)
    (draft / "SKILL.md").write_text(
        "---\nname: web-review\ndescription: Review web changes\n---\n\nUse focused checks.\n",
        encoding="utf-8",
    )

    cron_target = asyncio.run(service.delete_cron("cron-1"))
    completed_target = asyncio.run(service.complete_breakbeat("breakbeat-1"))
    resolved_target = asyncio.run(service.resolve_incident("incident-1"))
    deleted_draft = asyncio.run(service.delete_draft("web-review"))

    assert (cron_target.domain, cron_target.identifier) == ("cron", "cron-1")
    assert (completed_target.domain, completed_target.identifier) == ("breakbeat", "breakbeat-1")
    assert (resolved_target.domain, resolved_target.identifier) == ("incident", "incident-1")
    assert (deleted_draft.domain, deleted_draft.identifier) == ("skill", "web-review")
    assert store.read_json("cron.json", {})["jobs"] == []
    assert store.read_json("breakbeat.json", {})["items"][0]["status"] == "completed"
    assert store.read_json("incidents.json", {})["incidents"][0]["state"] == "resolved"
    assert store.read_json("pending_deliveries.json", {}) == {"deliveries": [{"source_id": "cron-1"}]}
    assert service.snapshot_domain("cron").proactive_revision == 4


def test_actions_report_missing_and_conflicting_records_without_partial_writes(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _seed_snapshots(store)
    original = store.read_json("breakbeat.json", {})

    with pytest.raises(ProactiveNotFoundError):
        asyncio.run(service.delete_breakbeat("missing"))
    asyncio.run(service.complete_breakbeat("breakbeat-1"))
    with pytest.raises(ProactiveConflictError):
        asyncio.run(service.complete_breakbeat("breakbeat-1"))

    assert store.read_json("breakbeat.json", {})["items"][0]["status"] == "completed"
    assert service.proactive_revision == 1
    assert original["items"][0]["status"] == "in_progress"

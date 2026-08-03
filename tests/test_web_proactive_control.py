from __future__ import annotations

import asyncio
import importlib
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient


control_module = importlib.import_module("Turning-Good-Agent.web.backend.proactive_control")
settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
memory_module = importlib.import_module("Turning-Good-Agent.memory.long_term")
skills_module = importlib.import_module("Turning-Good-Agent.skills.manager")
store_module = importlib.import_module("Turning-Good-Agent.proactive.store")
app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
incidents_module = importlib.import_module("Turning-Good-Agent.proactive.incidents")

ProactiveConflictError = control_module.ProactiveConflictError
ProactiveNotFoundError = control_module.ProactiveNotFoundError
WebProactiveControlService = control_module.WebProactiveControlService
ProfileMemory = memory_module.ProfileMemory
ProactiveSettings = settings_module.ProactiveSettings
Settings = settings_module.Settings
SkillManager = skills_module.SkillManager
ProactiveStore = store_module.ProactiveStore
create_app = app_module.create_app
IncidentMonitor = incidents_module.IncidentMonitor


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


class _LiveCron(_FakeCron):
    def __init__(self) -> None:
        super().__init__({"cron-1": "active"})
        self.deleted: list[str] = []

    async def delete_job(self, job_id: str) -> object:
        self.deleted.append(job_id)
        return object()


class _LiveBreakbeat(_FakeManager):
    def __init__(self) -> None:
        super().__init__(running=False, next_run_at=None)
        self.completed: list[tuple[str, bool]] = []
        self.deleted: list[str] = []

    async def complete_item(self, item_id: str, *, strict: bool = False) -> object:
        self.completed.append((item_id, strict))
        return object()

    async def delete_item(self, item_id: str) -> object:
        self.deleted.append(item_id)
        return object()


class _LiveIncidents:
    def __init__(self) -> None:
        self.resolved: list[str] = []
        self.deleted: list[str] = []

    async def resolve_by_fingerprint(self, fingerprint: str) -> object:
        self.resolved.append(fingerprint)
        return object()

    async def delete_by_fingerprint(self, fingerprint: str) -> int:
        self.deleted.append(fingerprint)
        return 1


class _MissingLiveCron(_LiveCron):
    def __init__(self, store: ProactiveStore) -> None:
        super().__init__()
        self._store = store

    async def delete_job(self, job_id: str) -> object:
        self._store.write_json("cron.json", {"jobs": [], "usage": _usage()})
        raise ValueError(f"Cron Job 不存在：{job_id}")


class _CompletedLiveBreakbeat(_LiveBreakbeat):
    def __init__(self, store: ProactiveStore) -> None:
        super().__init__()
        self._store = store

    async def complete_item(self, item_id: str, *, strict: bool = False) -> object:
        payload = self._store.read_json("breakbeat.json", {})
        payload["items"][0]["status"] = "completed"
        self._store.write_json("breakbeat.json", payload)
        raise ValueError(f"Breakbeat 已完成：{item_id}")


class _ResolvedLiveIncidents(_LiveIncidents):
    def __init__(self, store: ProactiveStore) -> None:
        super().__init__()
        self._store = store

    async def resolve_by_fingerprint(self, fingerprint: str) -> object:
        payload = self._store.read_json("incidents.json", {})
        payload["incidents"][0]["state"] = "resolved"
        self._store.write_json("incidents.json", payload)
        raise ValueError(f"Incident 已解决：{fingerprint}")


class _MissingLiveIncidents(_LiveIncidents):
    async def delete_by_fingerprint(self, fingerprint: str) -> int:
        self.deleted.append(fingerprint)
        return 0


class _DeliveryRecorder:
    def __init__(self) -> None:
        self.enqueued: list[dict[str, object]] = []

    async def enqueue(self, **payload: object) -> None:
        self.enqueued.append(payload)


def _usage() -> dict[str, int]:
    return {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _runtime(tmp_path: Path) -> SimpleNamespace:
    settings = Settings(data_dir=tmp_path)
    settings.proactive = ProactiveSettings()
    settings.tool_permissions.approval_required_tools = []
    settings.local_config_path = tmp_path / "settings.local.json"
    settings.local_config_path.write_text(
        '{"data_dir":".","tool_permissions":{"approval_required_tools":[]}}',
        encoding="utf-8",
    )
    skills = SkillManager(tmp_path / ".skills", settings.skills)
    return SimpleNamespace(
        settings=settings,
        profile_memory=ProfileMemory(tmp_path, settings.proactive),
        skills=skills,
        agent_loop=SimpleNamespace(tools=SimpleNamespace(tool_names=[])),
        mcp=SimpleNamespace(statuses={}),
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


def _owned_live_app(tmp_path: Path, proactive_service: object) -> tuple[object, ProactiveStore]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    runtime = _runtime(tmp_path)
    runtime.settings.proactive.enabled = True
    store = ProactiveStore(tmp_path)
    _seed_snapshots(store)
    app = create_app(runtime.settings, runtime)
    app.state.proactive_control.replace_runtime(runtime, proactive_service)
    app.state.proactive_ownership._acquired = True
    return app, store


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


def test_dream_snapshot_exposes_canonical_profile_budget_and_timezone(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _seed_snapshots(store)
    service.runtime.settings.proactive.timezone = "America/New_York"
    service.runtime.settings.proactive.user_profile_token_limit = 17
    service.runtime.settings.proactive.soul_profile_token_limit = 11
    service.runtime.settings.proactive.profile_total_token_limit = 23
    service.runtime.profile_memory.write(
        memory_module.ProfileMemorySnapshot(user="hello world", soul="be precise")
    )

    data = service.snapshot_domain("dream").data

    assert data["timezone"] == "America/New_York"
    assert data["memory_tokens"] == {
        "user_tokens": 2,
        "soul_tokens": 2,
        "total_tokens": 4,
    }
    assert data["profile_limits"] == {
        "user_profile_token_limit": 17,
        "soul_profile_token_limit": 11,
        "profile_total_token_limit": 23,
    }


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


def test_owner_actions_delegate_to_live_managers(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _seed_snapshots(store)
    cron = _LiveCron()
    breakbeat = _LiveBreakbeat()
    incidents = _LiveIncidents()
    service.replace_runtime(
        service.runtime,
        SimpleNamespace(
            cron=cron,
            breakbeat=breakbeat,
            dream=None,
            skill_evolution=None,
            incidents=incidents,
        ),
    )

    asyncio.run(service.delete_cron("cron-1"))
    asyncio.run(service.complete_breakbeat("breakbeat-1"))
    asyncio.run(service.delete_breakbeat("breakbeat-1"))
    asyncio.run(service.resolve_incident("cron:failed"))
    asyncio.run(service.delete_incident("cron:failed"))

    assert cron.deleted == ["cron-1"]
    assert breakbeat.completed == [("breakbeat-1", True)]
    assert breakbeat.deleted == ["breakbeat-1"]
    assert incidents.resolved == ["cron:failed"]
    assert incidents.deleted == ["cron:failed"]


def test_live_manager_revalidation_preserves_not_found_and_conflict_errors(tmp_path: Path) -> None:
    service, store = _service(tmp_path)
    _seed_snapshots(store)
    service.replace_runtime(
        service.runtime,
        SimpleNamespace(
            cron=_MissingLiveCron(store),
            breakbeat=_CompletedLiveBreakbeat(store),
            dream=None,
            skill_evolution=None,
            incidents=_ResolvedLiveIncidents(store),
        ),
    )

    with pytest.raises(ProactiveNotFoundError):
        asyncio.run(service.delete_cron("cron-1"))
    with pytest.raises(ProactiveConflictError):
        asyncio.run(service.complete_breakbeat("breakbeat-1"))
    with pytest.raises(ProactiveConflictError):
        asyncio.run(service.resolve_incident("cron:failed"))

    service.replace_runtime(
        service.runtime,
        SimpleNamespace(
            cron=None,
            breakbeat=None,
            dream=None,
            skill_evolution=None,
            incidents=_MissingLiveIncidents(),
        ),
    )
    with pytest.raises(ProactiveNotFoundError):
        asyncio.run(service.delete_incident("cron:failed"))
    assert store.read_json("incidents.json", {})["incidents"][0]["state"] == "resolved"


def test_manual_incident_resolve_does_not_enqueue_delivery(tmp_path: Path) -> None:
    store = ProactiveStore(tmp_path)
    _seed_snapshots(store)
    delivery = _DeliveryRecorder()
    monitor = IncidentMonitor(store, delivery, target_channel="web")

    asyncio.run(monitor.resolve_by_fingerprint("cron:failed"))

    incident = store.read_json("incidents.json", {})["incidents"][0]
    assert incident["state"] == "resolved"
    assert incident["history"][-1]["message"] == "用户在 Web 中标记已解决"
    assert delivery.enqueued == []


def test_http_reads_return_complete_domain_snapshots(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.settings.proactive.enabled = False
    _seed_snapshots(ProactiveStore(tmp_path))
    _create_draft(SimpleNamespace(runtime=runtime))
    app = create_app(runtime.settings, runtime)

    client = TestClient(app)
    for path, domain in (
        ("/api/proactive/cron", "cron"),
        ("/api/proactive/breakbeat", "breakbeat"),
        ("/api/proactive/memory", "dream"),
        ("/api/proactive/skills", "skill"),
        ("/api/proactive/incidents", "incident"),
    ):
        response = client.get(path)

        assert response.status_code == 200
        assert response.json()["domain"] == domain
        assert {"data", "runtime", "proactive_revision", "owner"} <= response.json().keys()
    client.close()


def test_http_disabled_mode_actions_clean_snapshots_and_return_updated_snapshot(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.settings.proactive.enabled = False
    store = ProactiveStore(tmp_path)
    _seed_snapshots(store)
    _create_draft(SimpleNamespace(runtime=runtime))
    app = create_app(runtime.settings, runtime)

    client = TestClient(app)
    requests = (
        ("delete", "/api/proactive/cron/cron-1", "cron", lambda data: data["jobs"] == []),
        (
            "post",
            "/api/proactive/breakbeat/breakbeat-1/complete",
            "breakbeat",
            lambda data: data["items"][0]["status"] == "completed",
        ),
        ("delete", "/api/proactive/breakbeat/breakbeat-1", "breakbeat", lambda data: data["items"] == []),
        ("delete", "/api/proactive/skills/drafts/web-review", "skill", lambda data: data["drafts"] == []),
        (
            "post",
            "/api/proactive/incidents/cron:failed/resolve",
            "incident",
            lambda data: data["incidents"][0]["state"] == "resolved",
        ),
        ("delete", "/api/proactive/incidents/cron:failed", "incident", lambda data: data["incidents"] == []),
    )
    for method, path, domain, updated in requests:
        response = getattr(client, method)(path)

        assert response.status_code == 200
        assert response.json()["domain"] == domain
        assert {"data", "runtime", "proactive_revision", "owner"} <= response.json().keys()
        assert updated(response.json()["data"])
    client.close()

    assert store.read_json("incidents.json", {})["incidents"] == []


def test_http_owner_conflict_and_missing_action_keep_snapshots_unchanged(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.settings.proactive.enabled = True
    store = ProactiveStore(tmp_path)
    _seed_snapshots(store)
    app = create_app(runtime.settings, runtime)
    ownership = app.state.proactive_ownership
    ownership._path.parent.mkdir(parents=True, exist_ok=True)
    ownership._path.write_text(
        '{"owner_id":"other-host","owner_kind":"cli","owner_pid":1}',
        encoding="utf-8",
    )

    client = TestClient(app)
    assert client.delete("/api/proactive/cron/cron-1").status_code == 409
    client.close()

    ownership._path.unlink()
    runtime.settings.proactive.enabled = False
    client = TestClient(app)
    assert client.delete("/api/proactive/cron/missing").status_code == 404
    client.close()

    assert store.read_json("cron.json", {})["jobs"][0]["id"] == "cron-1"


def test_http_disabled_mode_with_another_owner_remains_read_only(tmp_path: Path) -> None:
    runtime = _runtime(tmp_path)
    runtime.settings.proactive.enabled = False
    store = ProactiveStore(tmp_path)
    _seed_snapshots(store)
    app = create_app(runtime.settings, runtime)
    ownership = app.state.proactive_ownership
    ownership._path.parent.mkdir(parents=True, exist_ok=True)
    ownership._path.write_text(
        '{"owner_id":"cli-owner","owner_kind":"cli","owner_pid":1}',
        encoding="utf-8",
    )

    client = TestClient(app)
    assert client.delete("/api/proactive/cron/cron-1").status_code == 409
    client.close()

    assert store.read_json("cron.json", {})["jobs"][0]["id"] == "cron-1"


@pytest.mark.parametrize(
    ("path", "snapshot_name"),
    (
        ("/api/proactive/cron", "cron.json"),
        ("/api/proactive/breakbeat", "breakbeat.json"),
        ("/api/proactive/memory", "dream.json"),
        ("/api/proactive/skills", "skill.json"),
        ("/api/proactive/incidents", "incidents.json"),
    ),
)
def test_http_malformed_snapshot_reads_return_422(tmp_path: Path, path: str, snapshot_name: str) -> None:
    runtime = _runtime(tmp_path)
    store = ProactiveStore(tmp_path)
    _seed_snapshots(store)
    store.write_json(snapshot_name, {})
    app = create_app(runtime.settings, runtime)

    client = TestClient(app, raise_server_exceptions=False)
    response = client.get(path)
    client.close()

    assert response.status_code == 422


def test_http_live_manager_revalidation_maps_to_contract_statuses(tmp_path: Path) -> None:
    app, _ = _owned_live_app(tmp_path / "missing", SimpleNamespace(
        cron=_MissingLiveCron(ProactiveStore(tmp_path / "missing")),
        breakbeat=None,
        dream=None,
        skill_evolution=None,
        incidents=None,
    ))
    client = TestClient(app)
    assert client.delete("/api/proactive/cron/cron-1").status_code == 404
    client.close()

    app, _ = _owned_live_app(tmp_path / "completed", SimpleNamespace(
        cron=None,
        breakbeat=_CompletedLiveBreakbeat(ProactiveStore(tmp_path / "completed")),
        dream=None,
        skill_evolution=None,
        incidents=None,
    ))
    client = TestClient(app)
    assert client.post("/api/proactive/breakbeat/breakbeat-1/complete").status_code == 409
    client.close()

    app, _ = _owned_live_app(tmp_path / "resolved", SimpleNamespace(
        cron=None,
        breakbeat=None,
        dream=None,
        skill_evolution=None,
        incidents=_ResolvedLiveIncidents(ProactiveStore(tmp_path / "resolved")),
    ))
    client = TestClient(app)
    assert client.post("/api/proactive/incidents/cron:failed/resolve").status_code == 409
    client.close()

    app, _ = _owned_live_app(tmp_path / "deleted", SimpleNamespace(
        cron=None,
        breakbeat=None,
        dream=None,
        skill_evolution=None,
        incidents=_MissingLiveIncidents(),
    ))
    client = TestClient(app)
    assert client.delete("/api/proactive/incidents/cron:failed").status_code == 404
    client.close()


def test_snapshot_domain_rejects_unknown_domain(tmp_path: Path) -> None:
    service, _ = _service(tmp_path)

    with pytest.raises(ValueError, match="未知主动领域"):
        service.snapshot_domain("unknown")  # type: ignore[arg-type]

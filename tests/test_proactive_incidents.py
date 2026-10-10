from __future__ import annotations

import asyncio
import importlib

import pytest


incidents_module = importlib.import_module("Turning-Good-Agent.proactive.incidents")
store_module = importlib.import_module("Turning-Good-Agent.proactive.store")

IncidentMonitor = incidents_module.IncidentMonitor
ListIncidentsTool = incidents_module.ListIncidentsTool
ProactiveStore = store_module.ProactiveStore


class RecordingNotificationPublisher:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def enqueue(self, **payload: object) -> None:
        self.events.append(payload)


def test_incident_transition_publishes_all_subscribed_event_after_persisting_snapshot(tmp_path) -> None:
    """Would fail if IncidentMonitor still selected a target_channel or rolled back state on publish failure."""

    async def run() -> None:
        publisher = RecordingNotificationPublisher()
        monitor = IncidentMonitor(
            ProactiveStore(tmp_path),
            notification_publisher=publisher,
        )

        assert await monitor.report_failure(fingerprint="mcp:filesystem", source="mcp", message="失败") is True

        assert publisher.events == [
            {
                "event_type": "proactive.incident.opened",
                "content": "系统异常：失败",
                "source_id": "mcp:filesystem",
                "notification_scope": "all_subscribed",
            }
        ]
        assert monitor.get_by_fingerprint("mcp:filesystem") is not None

    asyncio.run(run())


def test_incident_snapshot_records_only_state_transition_history(tmp_path) -> None:
    async def run() -> None:
        store = ProactiveStore(tmp_path)
        publisher = RecordingNotificationPublisher()
        monitor = IncidentMonitor(store, publisher)

        assert await monitor.report_failure(fingerprint="mcp:filesystem", source="mcp", message="失败") is True
        assert await monitor.report_failure(fingerprint="mcp:filesystem", source="mcp", message="仍失败") is False
        assert await monitor.report_recovery(fingerprint="mcp:filesystem", source="mcp", message="恢复") is True
        assert await monitor.report_recovery(fingerprint="mcp:filesystem", source="mcp", message="仍恢复") is False
        assert await monitor.report_failure(fingerprint="mcp:filesystem", source="mcp", message="再次失败") is True

        incident = store.read_json("incidents.json", {})["incidents"][0]
        assert incident["occurrence_count"] == 3
        assert [entry["state"] for entry in incident["history"]] == ["open", "resolved", "open"]
        assert incident["state"] == "open"
        assert [event["event_type"] for event in publisher.events] == [
            "proactive.incident.opened",
            "proactive.incident.resolved",
            "proactive.incident.opened",
        ]
        assert {event["notification_scope"] for event in publisher.events} == {"all_subscribed"}

    asyncio.run(run())


def test_incident_silent_transition_retains_history_without_publishing(tmp_path) -> None:
    async def run() -> None:
        store = ProactiveStore(tmp_path)
        publisher = RecordingNotificationPublisher()
        monitor = IncidentMonitor(store, publisher)

        assert await monitor.report_failure(
            fingerprint="proactive:breakbeat", source="breakbeat", message="审阅失败", notify=False
        ) is True
        assert await monitor.report_recovery(
            fingerprint="proactive:breakbeat", source="breakbeat", message="审阅恢复", notify=False
        ) is True

        incident = store.read_json("incidents.json", {})["incidents"][0]
        assert [entry["state"] for entry in incident["history"]] == ["open", "resolved"]
        assert publisher.events == []

    asyncio.run(run())


def test_repeated_open_incident_refreshes_last_detected_at(tmp_path, monkeypatch) -> None:
    async def run() -> None:
        moments = iter(("2026-08-02T12:00:00+00:00", "2026-08-02T12:05:00+00:00"))
        monkeypatch.setattr(incidents_module, "utc_now_iso", lambda: next(moments))
        store = ProactiveStore(tmp_path)
        monitor = IncidentMonitor(
            store,
            RecordingNotificationPublisher(),
        )

        await monitor.report_failure(fingerprint="mcp:filesystem", source="mcp", message="失败")
        await monitor.report_failure(fingerprint="mcp:filesystem", source="mcp", message="仍失败")

        incident = store.read_json("incidents.json", {})["incidents"][0]
        assert incident["last_detected_at"] == "2026-08-02T12:05:00+00:00"
        assert incident["occurrence_count"] == 2
        assert [entry["state"] for entry in incident["history"]] == ["open"]

    asyncio.run(run())


def test_incident_snapshot_rejects_legacy_fields_and_duplicate_fingerprints(tmp_path) -> None:
    async def run() -> None:
        store = ProactiveStore(tmp_path)
        monitor = IncidentMonitor(
            store,
            RecordingNotificationPublisher(),
        )
        await monitor.report_failure(fingerprint="mcp:filesystem", source="mcp", message="失败")
        payload = store.read_json("incidents.json", {})
        payload["incidents"][0]["resolved_at"] = "2026-08-02T12:00:00+00:00"
        store.write_json("incidents.json", payload)

        with pytest.raises(ValueError, match="字段"):
            monitor.list_incidents()

        del payload["incidents"][0]["resolved_at"]
        payload["incidents"].append(dict(payload["incidents"][0]))
        store.write_json("incidents.json", payload)

        with pytest.raises(ValueError, match="重复"):
            monitor.list_incidents()

    asyncio.run(run())


def test_incident_list_reads_snapshot_and_filters_state(tmp_path) -> None:
    async def run() -> None:
        store = ProactiveStore(tmp_path)
        monitor = IncidentMonitor(store, RecordingNotificationPublisher())
        await monitor.report_failure(fingerprint="a", source="cron", message="A")
        await monitor.report_failure(fingerprint="b", source="dream", message="B")
        await monitor.report_recovery(fingerprint="a", source="cron", message="A recovered")

        assert [item.fingerprint for item in monitor.list_incidents("open")] == ["b"]
        assert [item.fingerprint for item in monitor.list_incidents("resolved")] == ["a"]
        with pytest.raises(ValueError, match="open 或 resolved"):
            monitor.list_incidents("closed")

    asyncio.run(run())


def test_list_incidents_tool_uses_snapshot(tmp_path) -> None:
    async def run() -> None:
        store = ProactiveStore(tmp_path)
        monitor = IncidentMonitor(
            store,
            RecordingNotificationPublisher(),
        )
        await monitor.report_failure(fingerprint="open-id", source="test", message="open message")
        content = (await ListIncidentsTool(monitor).run({})).content

        assert "open-id" in content

    asyncio.run(run())


def test_incident_delete_by_fingerprint_removes_current_snapshot(tmp_path) -> None:
    async def run() -> None:
        store = ProactiveStore(tmp_path)
        monitor = IncidentMonitor(
            store,
            RecordingNotificationPublisher(),
        )
        await monitor.report_failure(fingerprint="proactive:cron:job-1", source="cron", message="失败")

        assert await monitor.delete_by_fingerprint("proactive:cron:job-1") == 1
        assert monitor.get_by_fingerprint("proactive:cron:job-1") is None
        assert store.read_json("incidents.json", {})["incidents"] == []

    asyncio.run(run())


def test_incident_transition_waits_for_prior_notification_enqueue(tmp_path) -> None:
    class BlockingGate:
        def __init__(self) -> None:
            self.open_started = asyncio.Event()
            self.release_open = asyncio.Event()
            self.event_types: list[str] = []

        async def enqueue(self, **payload) -> None:
            if payload["event_type"] == "proactive.incident.opened":
                self.open_started.set()
                await self.release_open.wait()
            self.event_types.append(payload["event_type"])

    async def run() -> None:
        gate = BlockingGate()
        monitor = IncidentMonitor(ProactiveStore(tmp_path), gate)

        opening = asyncio.create_task(
            monitor.report_failure(fingerprint="mcp:ordered", source="mcp", message="失败")
        )
        await gate.open_started.wait()
        recovery = asyncio.create_task(
            monitor.report_recovery(fingerprint="mcp:ordered", source="mcp", message="恢复")
        )
        await asyncio.sleep(0)

        assert gate.event_types == []
        gate.release_open.set()
        await asyncio.gather(opening, recovery)
        assert gate.event_types == ["proactive.incident.opened", "proactive.incident.resolved"]

    asyncio.run(run())


def test_incident_keeps_persisted_transition_when_notification_publisher_fails(tmp_path) -> None:
    class FailingOnceGate:
        def __init__(self) -> None:
            self.calls = 0

        async def enqueue(self, **payload) -> None:
            del payload
            self.calls += 1
            if self.calls == 1:
                raise OSError("disk full")

    async def run() -> None:
        gate = FailingOnceGate()
        monitor = IncidentMonitor(ProactiveStore(tmp_path), gate)

        with pytest.raises(OSError, match="disk full"):
            await monitor.report_failure(fingerprint="mcp:retry", source="mcp", message="失败")
        assert await monitor.report_failure(fingerprint="mcp:retry", source="mcp", message="失败") is False
        incident = monitor.get_by_fingerprint("mcp:retry")
        assert incident is not None
        assert incident.occurrence_count == 2

    asyncio.run(run())

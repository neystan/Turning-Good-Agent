from __future__ import annotations

import asyncio
import importlib
from datetime import UTC, datetime, timedelta

import pytest


cron_module = importlib.import_module("Turning-Good-Agent.proactive.cron")
executor_module = importlib.import_module("Turning-Good-Agent.proactive.executor")
settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
store_module = importlib.import_module("Turning-Good-Agent.proactive.store")

CronManager = cron_module.CronManager
CreateCronTool = cron_module.CreateCronTool
DeleteCronTool = cron_module.DeleteCronTool
ListCronsTool = cron_module.ListCronsTool
ProactiveExecutionResult = executor_module.ProactiveExecutionResult
LLMUsage = importlib.import_module("Turning-Good-Agent.llm.types").LLMUsage
ProactiveSettings = settings_module.ProactiveSettings
ProactiveStore = store_module.ProactiveStore


class RecordingExecutor:
    def __init__(self) -> None:
        self.prompts: list[str] = []
        self.allowed_tool_names: list[frozenset[str] | None] = []

    async def run(self, capability, prompt, *, allowed_tool_names=None, on_started=None):
        assert capability == "cron"
        self.allowed_tool_names.append(allowed_tool_names)
        if on_started is not None:
            await on_started()
        self.prompts.append(prompt)
        return ProactiveExecutionResult(True, "任务结果", LLMUsage(input_tokens=2, output_tokens=1, total_tokens=3))


class BlockingExecutor(RecordingExecutor):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, capability, prompt, *, allowed_tool_names=None, on_started=None):
        assert capability == "cron"
        self.allowed_tool_names.append(allowed_tool_names)
        if on_started is not None:
            await on_started()
        self.prompts.append(prompt)
        self.started.set()
        await self.release.wait()
        return ProactiveExecutionResult(True, "任务结果", LLMUsage(input_tokens=2, output_tokens=1, total_tokens=3))


class RecordingDeliveryGate:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    async def enqueue(self, **payload):
        self.records.append(payload)


class RecordingNotificationPublisher(RecordingDeliveryGate):
    """模拟 Gateway 已装配的主动结果发布器。"""

    async def enqueue(self, **payload):
        self.records.append(payload)


class RecordingIncidents:
    def __init__(self) -> None:
        self.deleted_fingerprints: list[str] = []
        self.failures: list[str] = []
        self.recoveries: list[str] = []

    async def report_failure(self, *, fingerprint, source, message):
        del source, message
        self.failures.append(fingerprint)
        return True

    async def report_recovery(self, *, fingerprint, source, message):
        del source, message
        self.recoveries.append(fingerprint)
        return True

    async def delete_by_fingerprint(self, fingerprint: str) -> int:
        self.deleted_fingerprints.append(fingerprint)
        return 0


def manager(
    tmp_path,
    executor=None,
    deliveries=None,
    *,
    clock=None,
    timezone="Asia/Shanghai",
    incidents=None,
    available_tool_names=None,
) -> CronManager:
    kwargs = {}
    if available_tool_names is not None:
        kwargs["available_tool_names"] = available_tool_names
    return CronManager(
        ProactiveStore(tmp_path),
        executor or RecordingExecutor(),
        notification_publisher=deliveries or RecordingDeliveryGate(),
        proactive_settings=ProactiveSettings(timezone=timezone),
        clock=clock or (lambda: datetime(2026, 8, 3, 0, 0, tzinfo=UTC)),
        incidents=incidents,
        **kwargs,
    )


def test_one_shot_job_persists_fixed_next_run_at(tmp_path) -> None:
    current = manager(tmp_path)

    job = current.create_job(
        "生成早报。",
        recurring=False,
        next_run_at="2026-08-03T09:00:00+08:00",
    )

    payload = ProactiveStore(tmp_path).read_json("cron.json", {})
    assert payload["jobs"][0]["next_run_at"] == job.next_run_at
    assert "run_at" not in payload["jobs"][0]
    assert payload["usage"] == {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def test_constructing_cron_manager_does_not_rewrite_persisted_schedule(tmp_path) -> None:
    """Would fail if an unstarted Gateway candidate rewrote Cron state during construction."""
    current = manager(tmp_path, clock=lambda: datetime(2026, 8, 3, 0, 0, tzinfo=UTC))
    current.create_job("任务。", recurring=True, cron="0 9 * * *")
    path = ProactiveStore(tmp_path).root / "cron.json"
    before = path.read_bytes()

    restarted = manager(tmp_path, clock=lambda: datetime(2026, 8, 3, 0, 1, tzinfo=UTC))

    assert restarted.list_jobs()
    assert path.read_bytes() == before


def test_cron_rejects_legacy_job_fields_in_current_snapshot(tmp_path) -> None:
    current = manager(tmp_path)
    current.create_job("任务。", recurring=True, cron="0 9 * * *")
    store = ProactiveStore(tmp_path)
    payload = store.read_json("cron.json", {})
    payload["jobs"][0]["run_at"] = "2026-08-03T09:00:00"
    store.write_json("cron.json", payload)

    with pytest.raises(ValueError, match="字段"):
        manager(tmp_path)


def test_cron_rejects_incomplete_usage_and_duplicate_job_ids(tmp_path) -> None:
    current = manager(tmp_path)
    current.create_job("任务。", recurring=True, cron="0 9 * * *")
    store = ProactiveStore(tmp_path)
    payload = store.read_json("cron.json", {})
    payload["usage"] = {"calls": 0}
    store.write_json("cron.json", payload)

    with pytest.raises(ValueError, match="usage"):
        manager(tmp_path)

    payload["usage"] = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}
    payload["jobs"].append(dict(payload["jobs"][0]))
    store.write_json("cron.json", payload)

    with pytest.raises(ValueError, match="重复"):
        manager(tmp_path)


def test_create_cron_tool_converts_delay_to_offset_bearing_next_run_at(tmp_path) -> None:
    async def run() -> None:
        current = manager(tmp_path, clock=lambda: datetime(2026, 8, 3, 0, 0, tzinfo=UTC))

        result = await CreateCronTool(current).run(
            {"delay_seconds": 90, "prompt": "稍后提醒。", "recurring": False}
        )
        job = current.get_job(result.metadata["cron_id"])

        assert job.cron is None
        assert job.next_run_at == "2026-08-03T08:01:30+08:00"
        assert "run_at" not in job.to_dict()

    asyncio.run(run())


def test_one_shot_runs_once_removes_job_and_accumulates_usage(tmp_path) -> None:
    async def run() -> None:
        executor = RecordingExecutor()
        deliveries = RecordingDeliveryGate()
        current = manager(tmp_path, executor, deliveries)
        job = current.create_job("生成早报。", recurring=False, next_run_at="2026-08-03T09:00:00+08:00")
        fire_time = datetime(2026, 8, 3, 1, 0, tzinfo=UTC)

        assert current.next_deadline() == fire_time
        assert await current.run_due(fire_time) == 1
        await current.wait_for_jobs()

        assert executor.prompts == ["生成早报。"]
        assert executor.allowed_tool_names == [frozenset()]
        with pytest.raises(ValueError, match="不存在"):
            current.get_job(job.id)
        payload = ProactiveStore(tmp_path).read_json("cron.json", {})
        assert payload["jobs"] == []
        assert payload["usage"] == {"calls": 1, "input_tokens": 2, "output_tokens": 1, "total_tokens": 3}
        assert deliveries.records == [
            {
                "event_type": "proactive.cron.completed",
                "content": "定时任务已完成：任务结果",
                "source_id": job.id,
                "notification_scope": "all_subscribed",
            }
        ]
        assert await current.run_due(fire_time) == 0

    asyncio.run(run())


def test_cron_captures_the_active_tool_catalog_when_a_job_starts(tmp_path) -> None:
    """Would fail if Cron retained an empty or stale Tool allowlist."""

    async def run() -> None:
        executor = RecordingExecutor()
        current = manager(
            tmp_path,
            executor,
            available_tool_names=lambda: frozenset({"weather"}),
        )
        current.create_job("查询天气。", recurring=False, next_run_at="2026-08-03T09:00:00+08:00")

        assert await current.run_due(datetime(2026, 8, 3, 1, 0, tzinfo=UTC)) == 1
        await current.wait_for_jobs()

        assert executor.allowed_tool_names == [frozenset({"weather"})]

    asyncio.run(run())


def test_cron_notifies_gateway_after_its_task_leaves_the_runtime_projection(tmp_path) -> None:
    """Would fail if a completed Cron could leave Gateway reloads pending forever."""

    async def run() -> None:
        executor = BlockingExecutor()
        idle_changed = asyncio.Event()
        current = CronManager(
            ProactiveStore(tmp_path),
            executor,
            notification_publisher=RecordingNotificationPublisher(),
            proactive_settings=ProactiveSettings(timezone="Asia/Shanghai"),
            clock=lambda: datetime(2026, 8, 3, 0, 0, tzinfo=UTC),
            idle_changed=idle_changed.set,
        )
        current.create_job("生成早报。", recurring=False, next_run_at="2026-08-03T09:00:00+08:00")

        assert await current.run_due(datetime(2026, 8, 3, 1, 0, tzinfo=UTC)) == 1
        await executor.started.wait()
        assert current.is_idle is False

        executor.release.set()
        await current.wait_for_jobs()
        await asyncio.wait_for(idle_changed.wait(), timeout=1)

        assert current.is_idle is True

    asyncio.run(run())


def test_cron_completion_publishes_one_all_subscribed_event_regardless_of_legacy_delivery_channels(tmp_path) -> None:
    """Would fail if a Cron result were still filtered by target_channel or fanned out by CronManager."""

    async def run() -> None:
        publisher = RecordingNotificationPublisher()
        current = CronManager(
            ProactiveStore(tmp_path),
            RecordingExecutor(),
            notification_publisher=publisher,
            proactive_settings=ProactiveSettings(timezone="Asia/Shanghai"),
            clock=lambda: datetime(2026, 8, 3, 0, 0, tzinfo=UTC),
        )
        job = current.create_job(
            "生成早报。",
            recurring=False,
            next_run_at="2026-08-03T09:00:00+08:00",
            delivery_channels=["cli"],
        )

        assert await current.run_due(datetime(2026, 8, 3, 1, 0, tzinfo=UTC)) == 1
        await current.wait_for_jobs()

        assert publisher.records == [
            {
                "event_type": "proactive.cron.completed",
                "content": "定时任务已完成：任务结果",
                "source_id": job.id,
                "notification_scope": "all_subscribed",
            }
        ]

    asyncio.run(run())


def test_cron_tools_list_and_hard_delete_related_incidents(tmp_path) -> None:
    async def run() -> None:
        deliveries = RecordingDeliveryGate()
        incidents = RecordingIncidents()
        current = manager(tmp_path, deliveries=deliveries, incidents=incidents)
        created = await CreateCronTool(current).run(
            {"cron": "0 9 * * 1", "prompt": "整理本周 AI 大事件。", "recurring": True}
        )
        job_id = created.metadata["cron_id"]
        assert job_id in (await ListCronsTool(current).run({})).content

        deleted = await DeleteCronTool(current).run({"id": job_id})

        fingerprint = f"proactive:cron:{job_id}"
        assert "已删除" in deleted.content
        assert current.list_jobs() == []
        assert incidents.deleted_fingerprints == [fingerprint]
        assert ProactiveStore(tmp_path).read_json("cron.json", {})["jobs"] == []

    asyncio.run(run())

def test_periodic_job_recomputes_and_persists_next_run_at_after_timezone_change(tmp_path) -> None:
    current = manager(tmp_path)
    job = current.create_job("任务。", cron="0 9 * * *", recurring=True)

    assert current.next_deadline() == datetime(2026, 8, 3, 1, 0, tzinfo=UTC)
    assert job.next_run_at == "2026-08-03T09:00:00+08:00"

    current.update_timezone("America/New_York")

    updated = current.get_job(job.id)
    assert current.next_deadline() == datetime(2026, 8, 3, 13, 0, tzinfo=UTC)
    assert updated.next_run_at == "2026-08-03T09:00:00-04:00"
    assert ProactiveStore(tmp_path).read_json("cron.json", {})["jobs"][0]["next_run_at"] == updated.next_run_at


def test_timezone_change_does_not_change_one_shot_next_run_at(tmp_path) -> None:
    current = manager(tmp_path)
    job = current.create_job("任务。", recurring=False, next_run_at="2026-08-03T09:00:00+08:00")

    current.update_timezone("America/New_York")

    assert current.get_job(job.id).next_run_at == "2026-08-03T09:00:00+08:00"
    assert current.next_deadline() == datetime(2026, 8, 3, 1, 0, tzinfo=UTC)


def test_cron_skips_overlapping_periodic_execution_without_queueing_catch_up(tmp_path) -> None:
    async def run() -> None:
        executor = BlockingExecutor()
        current = manager(tmp_path, executor)
        job = current.create_job("执行耗时任务。", cron="* * * * *", recurring=True)
        first_fire = datetime(2026, 8, 3, 0, 1, tzinfo=UTC)

        assert await current.run_due(first_fire) == 1
        await executor.started.wait()
        assert await current.run_due(first_fire + timedelta(minutes=1)) == 0
        executor.release.set()
        await current.wait_for_jobs()

        assert executor.prompts == ["执行耗时任务。"]
        assert current.runtime_state(job.id) == "active"
        assert current.get_job(job.id).next_run_at == "2026-08-03T08:03:00+08:00"

    asyncio.run(run())


def test_restart_discards_missed_one_shot_without_replaying_it(tmp_path) -> None:
    first = manager(tmp_path, clock=lambda: datetime(2026, 8, 3, 0, 0, tzinfo=UTC))
    first.create_job("已错过的任务。", recurring=False, next_run_at="2026-08-03T08:30:00+08:00")

    restarted = manager(tmp_path, clock=lambda: datetime(2026, 8, 3, 1, 0, tzinfo=UTC))
    restarted.initialize_schedule()

    assert restarted.list_jobs() == []
    assert ProactiveStore(tmp_path).read_json("cron.json", {})["jobs"] == []


def test_create_cron_schema_has_no_run_at_field() -> None:
    assert "run_at" not in CreateCronTool.input_schema["properties"]
    assert "next_run_at" in CreateCronTool.input_schema["properties"]

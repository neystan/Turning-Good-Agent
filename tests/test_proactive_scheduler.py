from __future__ import annotations

import importlib
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest


scheduler_module = importlib.import_module("Turning-Good-Agent.proactive.scheduler")
types_module = importlib.import_module("Turning-Good-Agent.proactive.types")

cron_matches = scheduler_module.cron_matches
next_cron_fire = scheduler_module.next_cron_fire
next_job_fire = scheduler_module.next_job_fire
next_run_at_after_delay = scheduler_module.next_run_at_after_delay
validate_cron = scheduler_module.validate_cron
CronJob = types_module.CronJob


def cron_job(*, cron: str | None, next_run_at: str | None, recurring: bool) -> CronJob:
    return CronJob(
        id="cron-test",
        cron=cron,
        next_run_at=next_run_at,
        created_at="2026-08-01T00:00:00+00:00",
        prompt="执行任务。",
        recurring=recurring,
        delivery_channels=["all"],
        updated_at="2026-08-01T00:00:00+00:00",
    )


def test_cron_uses_or_when_day_of_month_and_day_of_week_are_both_constrained() -> None:
    expression = "0 9 1 * 1"

    assert cron_matches(expression, datetime(2026, 6, 1, 9, 0)) is True
    assert cron_matches(expression, datetime(2026, 6, 8, 9, 0)) is True
    assert cron_matches(expression, datetime(2026, 6, 2, 9, 0)) is False


def test_next_cron_fire_evaluates_expression_in_global_timezone() -> None:
    next_fire = next_cron_fire(
        "0 9 * * 1",
        datetime(2026, 8, 2, 1, 30, tzinfo=UTC),
        ZoneInfo("Asia/Shanghai"),
    )

    assert next_fire == datetime(2026, 8, 3, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))


def test_next_cron_fire_handles_dst_transition_in_iana_timezone() -> None:
    next_fire = next_cron_fire(
        "0 3 * * *",
        datetime(2026, 3, 8, 6, 30, tzinfo=UTC),
        ZoneInfo("America/New_York"),
    )

    assert next_fire == datetime(2026, 3, 8, 3, 0, tzinfo=ZoneInfo("America/New_York"))


def test_one_time_next_run_at_is_a_fixed_offset_bearing_instant() -> None:
    job = cron_job(cron=None, next_run_at="2026-08-01T20:00:00+08:00", recurring=False)

    next_fire = next_job_fire(
        job,
        datetime(2026, 8, 1, 11, 0, tzinfo=UTC),
        ZoneInfo("Asia/Shanghai"),
    )

    assert next_fire == datetime.fromisoformat("2026-08-01T20:00:00+08:00")


def test_delay_seconds_returns_offset_bearing_next_run_at() -> None:
    next_run_at = next_run_at_after_delay(
        90,
        datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
        ZoneInfo("Asia/Shanghai"),
    )

    assert next_run_at == "2026-08-01T20:01:30+08:00"


def test_recurring_next_fire_recomputes_when_global_timezone_changes() -> None:
    job = cron_job(cron="0 9 * * *", next_run_at="2026-08-01T09:00:00+08:00", recurring=True)
    after = datetime(2026, 8, 1, 0, 0, tzinfo=UTC)

    shanghai = next_job_fire(job, after, ZoneInfo("Asia/Shanghai"))
    new_york = next_job_fire(job, after, ZoneInfo("America/New_York"))

    assert shanghai == datetime(2026, 8, 1, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert new_york == datetime(2026, 8, 1, 9, 0, tzinfo=ZoneInfo("America/New_York"))
    assert shanghai.astimezone(UTC) != new_york.astimezone(UTC)


def test_one_time_next_run_at_is_not_reinterpreted_in_new_timezone() -> None:
    job = cron_job(cron=None, next_run_at="2026-08-01T20:00:00+08:00", recurring=False)
    after = datetime(2026, 8, 1, 11, 0, tzinfo=UTC)

    shanghai = next_job_fire(job, after, ZoneInfo("Asia/Shanghai"))
    new_york = next_job_fire(job, after, ZoneInfo("America/New_York"))

    assert shanghai == new_york == datetime.fromisoformat("2026-08-01T20:00:00+08:00")


@pytest.mark.parametrize(
    ("cron", "next_run_at", "recurring"),
    [
        (None, None, True),
        ("0 9 * * *", "2026-08-01T20:00:00+08:00", False),
        (None, "2026-08-01T20:00:00", False),
    ],
)
def test_cron_job_rejects_conflicting_or_invalid_schedule_parameters(
    cron: str | None, next_run_at: str | None, recurring: bool
) -> None:
    with pytest.raises(ValueError):
        cron_job(cron=cron, next_run_at=next_run_at, recurring=recurring)


def test_validate_cron_rejects_out_of_range_minute() -> None:
    assert validate_cron("60 9 * * *") == "minute 超出范围 0-59"

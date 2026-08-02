from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from .types import CronJob


_FIELD_SPECS = (
    ("minute", 0, 59),
    ("hour", 0, 23),
    ("day-of-month", 1, 31),
    ("month", 1, 12),
    ("day-of-week", 0, 7),
)


@dataclass(frozen=True, slots=True)
class CronSpec:
    """标准五字段 Cron 的已校验取值集合。"""

    minute: frozenset[int]
    hour: frozenset[int]
    day_of_month: frozenset[int]
    month: frozenset[int]
    day_of_week: frozenset[int]
    day_of_month_unconstrained: bool
    day_of_week_unconstrained: bool


def validate_cron(expression: str) -> str | None:
    """验证五字段 Cron，返回用户可读错误或 None。"""
    try:
        _parse(expression)
    except ValueError as exc:
        return str(exc)
    return None


def cron_matches(expression: str, moment: datetime) -> bool:
    """判断某个本地时刻是否命中 Cron，DOM/DOW 同时受限时采用 OR。"""
    return _matches(_parse(expression), moment)


def next_cron_fire(expression: str, after: datetime, timezone: ZoneInfo) -> datetime:
    """从 after 的下一分钟起，在全局时区中推导下次命中时刻。"""
    spec = _parse(expression)
    current = _as_utc(after).replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60 * 2):
        local = current.astimezone(timezone)
        if _matches(spec, local):
            return local
        current += timedelta(minutes=1)
    raise ValueError("Cron 在两年内没有可执行时刻")


def next_run_at_after_delay(delay_seconds: float, now: datetime, timezone: ZoneInfo) -> str:
    """把相对延迟转换为带 UTC offset 的一次性固定时刻。"""
    if isinstance(delay_seconds, bool) or not isinstance(delay_seconds, (int, float)):
        raise ValueError("delay_seconds 必须是数字")
    if delay_seconds <= 0:
        raise ValueError("delay_seconds 必须大于 0")
    next_run_at = (_as_utc(now) + timedelta(seconds=delay_seconds)).astimezone(timezone)
    return next_run_at.isoformat(timespec="seconds")


def next_job_fire(job: "CronJob", after: datetime, timezone: ZoneInfo) -> datetime | None:
    """按全局时区计算周期或一次性 Job 在 after 之后的下一触发时刻。"""
    if job.recurring:
        if job.cron is None:
            raise ValueError("周期 Cron Job 缺少 cron")
        return next_cron_fire(job.cron, after, timezone)
    if job.next_run_at is None:
        return None
    scheduled = datetime.fromisoformat(job.next_run_at)
    if scheduled.tzinfo is None or scheduled.utcoffset() is None:
        raise ValueError("next_run_at 必须包含 UTC offset")
    return scheduled if scheduled.astimezone(UTC) > _as_utc(after) else None


def _parse(expression: str) -> CronSpec:
    """解析并校验输入内容。"""
    fields = expression.strip().split()
    if len(fields) != 5:
        raise ValueError(f"Cron 必须包含 5 个字段，实际为 {len(fields)}")
    values = []
    for raw, (name, minimum, maximum) in zip(fields, _FIELD_SPECS, strict=True):
        values.append(_parse_field(raw, name, minimum, maximum))
    day_of_week = frozenset(0 if value == 7 else value for value in values[4])
    return CronSpec(
        minute=values[0],
        hour=values[1],
        day_of_month=values[2],
        month=values[3],
        day_of_week=day_of_week,
        day_of_month_unconstrained=fields[2] == "*",
        day_of_week_unconstrained=fields[4] == "*",
    )


def _parse_field(raw: str, name: str, minimum: int, maximum: int) -> frozenset[int]:
    """解析并校验输入内容。"""
    if not raw:
        raise ValueError(f"{name} 不能为空")
    values: set[int] = set()
    for part in raw.split(","):
        values.update(_parse_part(part, name, minimum, maximum))
    return frozenset(values)


def _parse_part(part: str, name: str, minimum: int, maximum: int) -> set[int]:
    """解析并校验输入内容。"""
    range_text, step = _split_step(part, name)
    if range_text == "*":
        start, end = minimum, maximum
    elif "-" in range_text:
        boundaries = range_text.split("-", 1)
        if len(boundaries) != 2:
            raise ValueError(f"{name} 格式错误：{part}")
        start = _parse_value(boundaries[0], name, minimum, maximum)
        end = _parse_value(boundaries[1], name, minimum, maximum)
        if start > end:
            raise ValueError(f"{name} 区间起点不能大于终点")
    else:
        value = _parse_value(range_text, name, minimum, maximum)
        if step != 1:
            raise ValueError(f"{name} 只有 * 或区间可以使用步长")
        return {value}
    return set(range(start, end + 1, step))


def _split_step(part: str, name: str) -> tuple[str, int]:
    """执行当前处理。"""
    if "/" not in part:
        return part, 1
    range_text, step_text = part.split("/", 1)
    if not step_text.isdigit() or int(step_text) <= 0:
        raise ValueError(f"{name} 步长必须大于 0")
    return range_text, int(step_text)


def _parse_value(text: str, name: str, minimum: int, maximum: int) -> int:
    """解析并校验输入内容。"""
    if not text.isdigit():
        raise ValueError(f"{name} 格式错误：{text}")
    value = int(text)
    if value < minimum or value > maximum:
        raise ValueError(f"{name} 超出范围 {minimum}-{maximum}")
    return value


def _matches(spec: CronSpec, moment: datetime) -> bool:
    """判断 Cron 是否命中。"""
    if moment.minute not in spec.minute or moment.hour not in spec.hour or moment.month not in spec.month:
        return False
    day_of_month_matches = moment.day in spec.day_of_month
    day_of_week_matches = ((moment.weekday() + 1) % 7) in spec.day_of_week
    if spec.day_of_month_unconstrained and spec.day_of_week_unconstrained:
        return True
    if spec.day_of_month_unconstrained:
        return day_of_week_matches
    if spec.day_of_week_unconstrained:
        return day_of_month_matches
    return day_of_month_matches or day_of_week_matches


def _as_utc(moment: datetime) -> datetime:
    """转换为 UTC 时间。"""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)

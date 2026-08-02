from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


OBSERVATION_KINDS = frozenset(
    {"workflow", "tool_procedure", "failure_recovery", "interaction_protocol"}
)


@dataclass(frozen=True, slots=True)
class UsageTotals:
    """保存一项主动能力的累计模型用量。"""

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0

    def __post_init__(self) -> None:
        """拒绝无法恢复的负数统计。"""
        values = (self.calls, self.input_tokens, self.output_tokens, self.total_tokens)
        if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in values):
            raise ValueError("usage 字段必须是非负整数")
    def add(
        self,
        *,
        calls: int = 1,
        input_tokens: int = 0,
        output_tokens: int = 0,
        total_tokens: int | None = None,
    ) -> "UsageTotals":
        """返回叠加一次调用后的新累计值。"""
        consumed = input_tokens + output_tokens if total_tokens is None else total_tokens
        return UsageTotals(
            calls=self.calls + calls,
            input_tokens=self.input_tokens + input_tokens,
            output_tokens=self.output_tokens + output_tokens,
            total_tokens=self.total_tokens + consumed,
        )

    def to_dict(self) -> dict[str, int]:
        """转换为最小 JSON 对象。"""
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
        }

    @classmethod
    def from_dict(cls, payload: object) -> "UsageTotals":
        """从能力快照恢复累计用量。"""
        if not isinstance(payload, dict):
            raise ValueError("usage 必须是 object")
        input_tokens = payload.get("input_tokens", 0)
        output_tokens = payload.get("output_tokens", 0)
        return cls(
            calls=payload.get("calls", 0),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=payload.get("total_tokens", input_tokens + output_tokens),
        )


def utc_now_iso() -> str:
    """返回主动能力审计使用的 UTC 时间。"""
    return datetime.now(UTC).isoformat()


def new_id(prefix: str) -> str:
    """生成带能力前缀的稳定记录 ID。"""
    return f"{prefix}_{uuid4()}"


@dataclass(slots=True)
class CronJob:
    """保存一个可持久化的 Cron 任务。"""

    id: str
    cron: str | None
    created_at: str
    prompt: str
    recurring: bool
    delivery_channels: list[str]
    updated_at: str
    next_run_at: str | None = None

    def __post_init__(self) -> None:
        """确保周期与一次性计划只保存各自所需的最小字段。"""
        if self.recurring:
            if self.cron is None or not self.cron.strip() or self.next_run_at is None:
                raise ValueError("周期 Cron Job 必须设置 cron 与 next_run_at")
            self.cron = self.cron.strip()
        elif self.cron is not None:
            raise ValueError("一次性 Cron Job 不能设置 cron")
        if self.next_run_at is not None:
            self.next_run_at = _aware_iso(self.next_run_at, field_name="next_run_at")

    def to_dict(self) -> dict[str, Any]:
        """转换为可持久化字典。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CronJob":
        """从字典恢复对象。"""
        required = {
            "id",
            "cron",
            "created_at",
            "prompt",
            "recurring",
            "delivery_channels",
            "updated_at",
            "next_run_at",
        }
        if set(payload) != required:
            raise ValueError("Cron Job 字段无效")
        recurring = payload["recurring"]
        if not isinstance(recurring, bool):
            raise ValueError("Cron recurring 必须是 boolean")
        string_fields = ("id", "created_at", "prompt", "updated_at")
        if any(not isinstance(payload[field], str) for field in string_fields):
            raise ValueError("Cron Job 字符串字段无效")
        cron = payload["cron"]
        next_run_at = payload["next_run_at"]
        if cron is not None and not isinstance(cron, str):
            raise ValueError("Cron cron 必须是 string 或 null")
        if next_run_at is not None and not isinstance(next_run_at, str):
            raise ValueError("Cron next_run_at 必须是 string 或 null")
        channels = payload["delivery_channels"]
        if not isinstance(channels, list) or not channels or not all(
            isinstance(item, str) and item.strip() for item in channels
        ):
            raise ValueError("Cron delivery_channels 必须是非空字符串数组")
        return cls(
            id=payload["id"],
            cron=cron,
            created_at=payload["created_at"],
            prompt=payload["prompt"],
            recurring=recurring,
            delivery_channels=list(channels),
            updated_at=payload["updated_at"],
            next_run_at=next_run_at,
        )


@dataclass(slots=True)
class BreakbeatItem:
    """保存一条可追溯的未完成事项。"""

    id: str
    todo: str
    deadline: str | None
    source_session_id: str
    status: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, Any]:
        """转换为可持久化字典。"""
        return asdict(self)


@dataclass(slots=True)
class Observation:
    """Skill 自进化的有界学习观察。"""

    id: str
    created_at: str
    kind: str
    observation: str
    source_session_id: str
    source_message_ids: list[str]

    def __post_init__(self) -> None:
        """拒绝已经废弃或未知的 Observation 类型。"""
        if self.kind not in OBSERVATION_KINDS:
            raise ValueError(f"无效的 Observation kind：{self.kind}")

    def to_dict(self) -> dict[str, Any]:
        """转换为可持久化字典。"""
        return asdict(self)


@dataclass(slots=True)
class Incident:
    """保存系统异常的去重状态。"""

    id: str
    fingerprint: str
    source: str
    state: str
    first_detected_at: str
    last_detected_at: str
    occurrence_count: int
    message: str
    history: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """转换为可持久化字典。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Incident":
        """从持久化记录恢复并校验 Incident。"""
        required = {
            "id",
            "fingerprint",
            "source",
            "state",
            "first_detected_at",
            "last_detected_at",
            "occurrence_count",
            "message",
            "history",
        }
        if set(payload) != required:
            raise ValueError("Incident 字段无效")
        string_fields = (
            "id",
            "fingerprint",
            "source",
            "state",
            "first_detected_at",
            "last_detected_at",
            "message",
        )
        if any(not isinstance(payload[field], str) for field in string_fields):
            raise ValueError("Incident 字符串字段无效")
        state = payload["state"]
        if state not in {"open", "resolved"}:
            raise ValueError(f"无效的 Incident state：{state}")
        occurrence_count = payload["occurrence_count"]
        if isinstance(occurrence_count, bool) or not isinstance(occurrence_count, int) or occurrence_count < 1:
            raise ValueError("Incident occurrence_count 必须是正整数")
        history = payload["history"]
        if not isinstance(history, list) or not all(
            isinstance(entry, dict)
            and set(entry) == {"state", "occurred_at", "message"}
            and all(isinstance(value, str) for value in entry.values())
            for entry in history
        ):
            raise ValueError("Incident history 无效")
        return cls(
            id=payload["id"],
            fingerprint=payload["fingerprint"],
            source=payload["source"],
            state=state,
            first_detected_at=payload["first_detected_at"],
            last_detected_at=payload["last_detected_at"],
            occurrence_count=occurrence_count,
            message=payload["message"],
            history=[dict(entry) for entry in history],
        )


def _aware_iso(value: str, *, field_name: str) -> str:
    """校验并规范化带 UTC offset 的 ISO 时刻。"""
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"{field_name} 必须包含 UTC offset")
    return parsed.isoformat(timespec="seconds")

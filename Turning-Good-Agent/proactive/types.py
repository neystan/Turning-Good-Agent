from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


OBSERVATION_KINDS = frozenset(
    {"workflow", "tool_procedure", "failure_recovery", "interaction_protocol"}
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
    run_at: str | None = None

    def __post_init__(self) -> None:
        """确保周期与一次性计划只保存各自所需的最小字段。"""
        if self.recurring:
            if self.cron is None or not self.cron.strip() or self.run_at is not None:
                raise ValueError("周期 Cron Job 必须设置 cron 且不能设置 run_at")
            self.cron = self.cron.strip()
            return
        if self.cron is not None or self.run_at is None or not self.run_at.strip():
            raise ValueError("一次性 Cron Job 必须设置 run_at 且不能设置 cron")
        parsed = datetime.fromisoformat(self.run_at)
        if parsed.tzinfo is not None:
            raise ValueError("run_at 必须是不含 UTC offset 的本地时间")
        self.run_at = parsed.isoformat(timespec="seconds")

    def to_dict(self) -> dict[str, Any]:
        """转换为可持久化字典。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CronJob":
        """从字典恢复对象。"""
        if set(payload).intersection({"status", "last_triggered_at", "last_completed_at", "last_error"}):
            raise ValueError("检测到旧版 Cron 生命周期字段")
        if payload.get("recurring") is False and payload.get("cron") is not None:
            raise ValueError("检测到旧版一次性 Cron 结构")
        recurring = payload["recurring"]
        if not isinstance(recurring, bool):
            raise ValueError("Cron recurring 必须是 boolean")
        channels = payload.get("delivery_channels", ["all"])
        if not isinstance(channels, list) or not channels or not all(
            isinstance(item, str) and item.strip() for item in channels
        ):
            raise ValueError("Cron delivery_channels 必须是非空字符串数组")
        return cls(
            id=str(payload["id"]),
            cron=_optional_string(payload.get("cron")),
            created_at=str(payload["created_at"]),
            prompt=str(payload["prompt"]),
            recurring=recurring,
            delivery_channels=list(channels),
            updated_at=str(payload.get("updated_at", payload["created_at"])),
            run_at=_optional_string(payload.get("run_at")),
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
    resolved_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为可持久化字典。"""
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "Incident":
        """从持久化记录恢复并校验 Incident。"""
        state = str(payload["state"])
        if state not in {"open", "resolved"}:
            raise ValueError(f"无效的 Incident state：{state}")
        return cls(
            id=str(payload["id"]),
            fingerprint=str(payload["fingerprint"]),
            source=str(payload["source"]),
            state=state,
            first_detected_at=str(payload["first_detected_at"]),
            last_detected_at=str(payload["last_detected_at"]),
            occurrence_count=int(payload["occurrence_count"]),
            message=str(payload["message"]),
            resolved_at=_optional_string(payload.get("resolved_at")),
        )


@dataclass(slots=True)
class DeliveryRecord:
    """保存主动输出的可靠投递状态。"""

    id: str
    created_at: str
    session_id: str
    target_channel: str
    event_type: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    state: str = "pending"
    delivered_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """转换为可持久化字典。"""
        return asdict(self)


def _optional_string(value: object) -> str | None:
    """读取可选字符串值。"""
    return None if value is None else str(value)

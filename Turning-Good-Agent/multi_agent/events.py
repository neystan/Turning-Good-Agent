from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


MULTI_AGENT_EVENT_TYPES = frozenset(
    {
        "multi_agent.run.started",
        "multi_agent.node.updated",
        "multi_agent.run.completed",
        "multi_agent.run.failed",
        "multi_agent.run.cancelled",
    }
)
MULTI_AGENT_EVENT_FIELDS = frozenset(
    {
        "run_id",
        "node_id",
        "task_label",
        "strategy",
        "status",
        "duration_ms",
        "usage",
        "error_code",
        "error",
        "content",
    }
)
MULTI_AGENT_LIVE_EVENT_FIELDS = MULTI_AGENT_EVENT_FIELDS - frozenset(
    {"content"}
)


@dataclass(frozen=True, slots=True)
class MultiAgentEventPayload:
    """保存可投影的安全 Multi-Agent 事件字段。"""

    run_id: str
    node_id: str | None
    task_label: str | None
    strategy: str
    status: str
    duration_ms: float | None = None
    usage: dict[str, int] | None = None
    error_code: str | None = None
    error: str | None = None
    content: str | None = None

    # 将不可变事件转换为通道可发送的普通字典。
    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

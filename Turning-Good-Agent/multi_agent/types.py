from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from ..memory.long_term import ProfileMemorySnapshot


RUN_STATUSES = frozenset({"queued", "running", "waiting", "completed", "failed", "timed_out", "cancelled", "interrupted"})
NODE_STATUSES = frozenset({"queued", "running", "completed", "failed", "timed_out", "cancelled", "interrupted"})
WORKER_RESULT_STATUSES = frozenset({"completed", "failed", "timed_out", "cancelled", "interrupted"})


class RequestMode(str, Enum):
    AUTO = "auto"
    OFF = "off"


class Strategy(str, Enum):
    FAN_OUT_FAN_IN = "fan_out_fan_in"
    PIPELINE = "pipeline"


@dataclass(frozen=True, slots=True)
class DelegateParentTurn:
    """保存 Coordinator 所需的最小父 Turn 输入。"""

    session_id: str
    request_id: str
    profile_memory: ProfileMemorySnapshot
    initial_context_tokens: int
    is_stop_requested: Callable[[], bool]

    # 校验父 Turn 输入不携带可变 Runtime 上下文。
    def __post_init__(self) -> None:
        if not isinstance(self.session_id, str) or not self.session_id.strip():
            raise ValueError("父 Turn session_id 必须是非空字符串")
        if not isinstance(self.request_id, str) or not self.request_id.strip():
            raise ValueError("父 Turn request_id 必须是非空字符串")
        if (
            not isinstance(self.initial_context_tokens, int)
            or isinstance(self.initial_context_tokens, bool)
            or self.initial_context_tokens < 0
        ):
            raise ValueError("父 Turn initial_context_tokens 必须是非负整数")
        if not callable(self.is_stop_requested):
            raise ValueError("父 Turn is_stop_requested 必须可调用")

# 解析一次性客户端多 Agent 模式。
def parse_multi_agent_mode(value: object) -> RequestMode:
    """Parse the only client-selectable multi-agent modes."""
    if not isinstance(value, str):
        raise ValueError("multi_agent_mode 必须是 auto 或 off")
    try:
        return RequestMode(value)
    except ValueError as exc:
        raise ValueError("multi_agent_mode 必须是 auto 或 off") from exc


@dataclass(frozen=True, slots=True)
class MultiAgentTask:
    role: str
    brief: str


@dataclass(frozen=True, slots=True)
class MultiAgentRequest:
    strategy: Strategy
    tasks: tuple[MultiAgentTask, ...]


@dataclass(frozen=True, slots=True)
class WorkerResult:
    status: str
    content: str | None
    error: str | None = None

    # 强制成功内容与失败错误的互斥结果契约。
    def __post_init__(self) -> None:
        if self.status not in WORKER_RESULT_STATUSES:
            raise ValueError("不支持的 WorkerResult 状态")
        if self.status == "completed":
            if not isinstance(self.content, str) or not self.content.strip() or self.error is not None:
                raise ValueError("completed 结果必须包含非空 content 且 error 为 null")
        elif self.content is not None or not isinstance(self.error, str) or not self.error.strip():
            raise ValueError("失败结果必须 content 为 null 且包含非空 error")


@dataclass(frozen=True, slots=True)
class MultiAgentNode:
    node_id: str
    role: str
    status: str
    duration_ms: float | None = None
    result: WorkerResult | None = None
    error: str | None = None
    error_code: str | None = None

    # 限制节点状态和耗时边界。
    def __post_init__(self) -> None:
        if self.status not in NODE_STATUSES:
            raise ValueError("不支持的 MultiAgentNode 状态")
        if self.duration_ms is not None and (isinstance(self.duration_ms, bool) or self.duration_ms < 0):
            raise ValueError("duration_ms 必须为非负数或 null")


@dataclass(frozen=True, slots=True)
class MultiAgentRun:
    run_id: str
    parent_session_id: str
    parent_request_id: str
    strategy: Strategy
    status: str
    nodes: tuple[MultiAgentNode, ...] = field(default_factory=tuple)
    created_at: str | None = None
    started_at: str | None = None
    finished_at: str | None = None
    error: str | None = None
    error_code: str | None = None

    # 限制 Run 状态。
    def __post_init__(self) -> None:
        if self.status not in RUN_STATUSES:
            raise ValueError("不支持的 MultiAgentRun 状态")

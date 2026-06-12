from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class StateTrace:
    """记录单个状态的耗时和事件。"""

    turn_id: str
    session_id: str
    state: str
    duration_ms: float
    event: str
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

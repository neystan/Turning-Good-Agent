from ..runtime.turn_context import TurnContext
from .base import AgentHook


class TurnMonitorHook(AgentHook):
    """计算单轮会话的只读终态监控字段。"""

    async def after_turn(
        self,
        ctx: TurnContext,
        turn_duration_ms: float,
        session_lock_wait_ms: float,
    ) -> dict[str, int | float | str]:
        """返回结果状态、耗时、锁等待和失败工具数量。"""
        rejected = any(trace.state == "BUILD" and trace.event == "rejected" for trace in ctx.trace)
        outcome = "rejected" if rejected else "failed" if ctx.error else "completed"
        return {
            "outcome": outcome,
            "turn_duration_ms": turn_duration_ms,
            "session_lock_wait_ms": session_lock_wait_ms,
            "tool_failure_count": sum(1 for record in ctx.tool_calls if record.get("error")),
        }

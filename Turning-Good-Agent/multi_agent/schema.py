from __future__ import annotations

from collections.abc import Mapping

from ..config.validate import (
    MULTI_AGENT_BRIEF_MAX_LENGTH,
    MULTI_AGENT_ROLE_MAX_LENGTH,
)
from .types import MultiAgentRequest, MultiAgentTask, Strategy


_TOP_LEVEL_FIELDS = frozenset({"strategy", "tasks"})
_TASK_FIELDS = frozenset({"role", "brief"})

DELEGATE_MULTI_AGENT_SCHEMA: dict[str, object] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["strategy", "tasks"],
    "properties": {
        "strategy": {"type": "string", "enum": ["fan_out_fan_in", "pipeline"]},
        "tasks": {
            "type": "array",
            "minItems": 1,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["role", "brief"],
                "properties": {
                    "role": {"type": "string", "minLength": 1, "maxLength": MULTI_AGENT_ROLE_MAX_LENGTH},
                    "brief": {"type": "string", "minLength": 1, "maxLength": MULTI_AGENT_BRIEF_MAX_LENGTH},
                },
            },
        },
    },
}


class DelegateSchemaError(ValueError):
    """Raised when a delegate_multi_agent tool payload violates its contract."""

# 校验父 Agent 提交的严格委派请求。
def validate_delegate_request(
    payload: object,
    max_workers_per_run: int = 8,
) -> MultiAgentRequest:
    """Validate a tool payload and return immutable execution input."""
    if type(max_workers_per_run) is not int or max_workers_per_run <= 0:
        raise ValueError("max_workers_per_run 必须是正整数")
    if not isinstance(payload, Mapping):
        raise DelegateSchemaError("请求必须是 object")
    unknown = set(payload) - _TOP_LEVEL_FIELDS
    if unknown:
        raise DelegateSchemaError(f"不支持的请求字段：{', '.join(sorted(map(str, unknown)))}")
    if "strategy" not in payload or "tasks" not in payload:
        raise DelegateSchemaError("请求必须包含 strategy 和 tasks")
    strategy = _parse_strategy(payload["strategy"])
    tasks_payload = payload["tasks"]
    if not isinstance(tasks_payload, list):
        raise DelegateSchemaError("tasks 必须是数组")
    if not tasks_payload:
        raise DelegateSchemaError("tasks 不能为空")
    if len(tasks_payload) > max_workers_per_run:
        raise DelegateSchemaError("tasks 不能超过 max_workers_per_run")

    tasks = tuple(_parse_task(task) for task in tasks_payload)
    if strategy is Strategy.PIPELINE and len(tasks) < 2:
        raise DelegateSchemaError("pipeline 至少需要两个 Worker")
    return MultiAgentRequest(strategy=strategy, tasks=tasks)


# 将策略字符串转换为固定枚举。
def _parse_strategy(value: object) -> Strategy:
    if not isinstance(value, str):
        raise DelegateSchemaError("strategy 必须是受支持的字符串")
    try:
        return Strategy(value)
    except ValueError as exc:
        raise DelegateSchemaError("strategy 不受支持") from exc


# 解析并限制单个 Worker 任务字段。
def _parse_task(payload: object) -> MultiAgentTask:
    if not isinstance(payload, Mapping):
        raise DelegateSchemaError("tasks 的每项必须是 object")
    unknown = set(payload) - _TASK_FIELDS
    if unknown:
        raise DelegateSchemaError(f"不支持的 task 字段：{', '.join(sorted(map(str, unknown)))}")
    required = ("role", "brief")
    if any(name not in payload for name in required):
        raise DelegateSchemaError("每个 task 必须包含 role 和 brief")
    role = _non_empty_string(payload["role"], "role", MULTI_AGENT_ROLE_MAX_LENGTH)
    brief = _non_empty_string(payload["brief"], "brief", MULTI_AGENT_BRIEF_MAX_LENGTH)
    return MultiAgentTask(role=role, brief=brief)


# 校验字符串非空并执行固定长度上限。
def _non_empty_string(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DelegateSchemaError(f"{field} 必须是非空字符串")
    if len(value) > maximum:
        raise DelegateSchemaError(f"{field} 不能超过 {maximum} 个字符")
    return value

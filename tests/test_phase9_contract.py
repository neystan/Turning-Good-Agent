from __future__ import annotations

import importlib
import json
from dataclasses import fields
from dataclasses import FrozenInstanceError

import pytest


settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
validate_module = importlib.import_module("Turning-Good-Agent.config.validate")
schema_module = importlib.import_module("Turning-Good-Agent.multi_agent.schema")
types_module = importlib.import_module("Turning-Good-Agent.multi_agent.types")
multi_agent_module = importlib.import_module("Turning-Good-Agent.multi_agent")
control_module = importlib.import_module("Turning-Good-Agent.web.backend.config_control")
memory_module = importlib.import_module("Turning-Good-Agent.memory.long_term")


# 验证默认配置保持全局关闭并使用固定预算。
def test_defaults_keep_multi_agent_disabled() -> None:
    settings = settings_module.Settings()

    assert settings.multi_agent.enabled is False
    assert settings.multi_agent.run_timeout_seconds == 3600
    assert settings.multi_agent.worker_timeout_seconds == 900
    assert settings.multi_agent.max_workers_per_run == 8
    assert settings.multi_agent.max_concurrent_workers_per_run == 4
    assert settings.multi_agent.max_concurrent_workers_global == 8
    assert settings.multi_agent.worker_result_token_limit == 60000
    assert settings.multi_agent.parent_result_token_limit == 120000


# 验证配置加载只接受契约内字段。
def test_multi_agent_loads_only_known_fields(tmp_path) -> None:
    path = tmp_path / "settings.local.json"
    path.write_text(
        '{"multi_agent": {"enabled": true, "max_workers_per_run": 3, "max_concurrent_workers_per_run": 3, "unknown": 99}}',
        encoding="utf-8",
    )

    settings = settings_module.Settings.load(local_config_path=path)

    assert settings.multi_agent.enabled is True
    assert settings.multi_agent.max_workers_per_run == 3
    assert not hasattr(settings.multi_agent, "unknown")


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (lambda s: setattr(s.multi_agent, "run_timeout_seconds", 0), "multi_agent.run_timeout_seconds"),
        (lambda s: setattr(s.multi_agent, "worker_timeout_seconds", 3601), "multi_agent.worker_timeout_seconds"),
        (lambda s: setattr(s.multi_agent, "max_concurrent_workers_per_run", 9), "multi_agent.max_concurrent_workers_per_run"),
        (lambda s: setattr(s.multi_agent, "run_timeout_seconds", 14401), "multi_agent.run_timeout_seconds"),
        (lambda s: setattr(s.multi_agent, "worker_result_token_limit", s.runtime.max_context_tokens + 1), "multi_agent.worker_result_token_limit"),
    ],
)
# 验证跨字段和硬上限会拒绝非法配置。
def test_multi_agent_settings_reject_invalid_limits(mutate, field) -> None:
    settings = settings_module.Settings()
    mutate(settings)

    with pytest.raises(validate_module.SettingsValidationError) as raised:
        validate_module.validate_settings(settings)

    assert field in raised.value.field_errors


# 验证 Web 配置沿用现有脱敏和原子 Apply 边界。
def test_control_config_redacts_and_applies_multi_agent_settings(tmp_path) -> None:
    config_path = tmp_path / "settings.local.json"
    config_path.write_text('{"multi_agent": {"enabled": false}}', encoding="utf-8")
    service = control_module.WebConfigControlService(
        config_path,
        native_tool_names={"write_file", "edit_file", "exec", "write_stdin"},
        live_tool_names=lambda: set(),
        unavailable_approval_names=lambda: set(),
    )

    before = service.read_desired()
    after = service.apply(
        control_module.ConfigApplyRequest(
            changes={"multi_agent": {"enabled": True, "max_workers_per_run": 6}}
        )
    )

    assert before.desired["multi_agent"]["enabled"] is False
    assert after.desired["multi_agent"]["enabled"] is True
    assert json.loads(config_path.read_text(encoding="utf-8"))["multi_agent"]["max_workers_per_run"] == 6


# 验证请求模式只接受两种公开值。
@pytest.mark.parametrize("value", ["auto", "off"])
# 验证请求模式只接受两种公开值。
def test_parse_multi_agent_mode_accepts_supported_values(value: str) -> None:
    assert types_module.parse_multi_agent_mode(value).value == value


# 验证请求模式拒绝已移除、空值和未知值。
@pytest.mark.parametrize("value", ["", "AUTO", "always", "on", None, 1])
# 验证请求模式拒绝已移除、空值和未知值。
def test_parse_multi_agent_mode_rejects_unsupported_values(value) -> None:
    with pytest.raises(ValueError, match="auto 或 off"):
        types_module.parse_multi_agent_mode(value)


# 验证 Coordinator 只接收明确的 Phase 9 父 Turn 输入。
def test_delegate_parent_turn_keeps_runtime_context_out_of_coordinator() -> None:
    parent = types_module.DelegateParentTurn(
        session_id="session-1",
        request_id="request-1",
        profile_memory=memory_module.ProfileMemorySnapshot(user="user", soul="soul"),
        initial_context_tokens=42,
        is_stop_requested=lambda: False,
    )

    assert parent.session_id == "session-1"
    assert parent.request_id == "request-1"
    assert parent.initial_context_tokens == 42
    assert multi_agent_module.DelegateParentTurn is types_module.DelegateParentTurn
    with pytest.raises(FrozenInstanceError):
        parent.session_id = "other"


# 验证 Schema 拒绝旧策略、未知字段和 pipeline 的错误基数。
def test_schema_rejects_unknown_fields_and_wrong_cardinality() -> None:
    with pytest.raises(schema_module.DelegateSchemaError):
        schema_module.validate_delegate_request({"strategy": "review", "tasks": []})

    with pytest.raises(schema_module.DelegateSchemaError):
        schema_module.validate_delegate_request(
            {
                "strategy": "pipeline",
                "tasks": [{"role": "reviewer", "brief": "review"}],
            }
        )

    with pytest.raises(schema_module.DelegateSchemaError):
        schema_module.validate_delegate_request(
            {
                "strategy": "fan_out_fan_in",
                "tasks": [
                    {"role": "researcher", "brief": "collect facts", "depends_on": []},
                    {"role": "reviewer", "brief": "check facts"},
                ],
            }
        )


# 验证单 Worker fan-out 请求产生不可变任务契约。
def test_schema_accepts_valid_fan_out_request_and_returns_immutable_tasks() -> None:
    request = schema_module.validate_delegate_request(
        {
            "strategy": "fan_out_fan_in",
            "tasks": [
                {"role": "researcher", "brief": "collect facts"},
            ],
        },
        max_workers_per_run=8,
    )

    assert isinstance(request, types_module.MultiAgentRequest)
    assert request.strategy is types_module.Strategy.FAN_OUT_FAN_IN
    assert len(request.tasks) == 1
    with pytest.raises(FrozenInstanceError):
        request.tasks[0].role = "changed"
    assert {field.name for field in fields(types_module.MultiAgentTask)} == {"role", "brief"}


# 验证 WorkerResult 严格区分成功和失败结果字段。
def test_worker_result_enforces_exact_status_content_error_invariants() -> None:
    assert {field.name for field in fields(types_module.WorkerResult)} == {"status", "content", "error"}
    assert types_module.WorkerResult(status="completed", content="done", error=None).content == "done"
    assert types_module.WorkerResult(status="failed", content=None, error="sanitized").error == "sanitized"
    with pytest.raises(ValueError):
        types_module.WorkerResult(status="completed", content="", error=None)
    with pytest.raises(ValueError):
        types_module.WorkerResult(status="failed", content="leaked", error="sanitized")
    with pytest.raises(ValueError):
        types_module.WorkerResult(status="failed", content=None, error=None)


# 验证 Run 和 Node 只保留执行所需的状态字段。
def test_multi_agent_state_types_exclude_redundant_ownership_and_partial_fields() -> None:
    node = types_module.MultiAgentNode(
        node_id="node-1",
        role="researcher",
        status="completed",
        duration_ms=12.5,
        result=types_module.WorkerResult(status="completed", content="done", error=None),
    )
    run = types_module.MultiAgentRun(
        run_id="run-1",
        parent_session_id="session-1",
        parent_request_id="request-1",
        strategy=types_module.Strategy.FAN_OUT_FAN_IN,
        status="completed",
        nodes=(node,),
        created_at="2026-08-11T00:00:00Z",
        started_at="2026-08-11T00:00:01Z",
        finished_at="2026-08-11T00:00:02Z",
        error=None,
    )
    assert {field.name for field in fields(types_module.MultiAgentRun)}.isdisjoint(
        {"mode", "partial", "parent_agent_id"}
    )
    assert {field.name for field in fields(types_module.MultiAgentNode)}.isdisjoint(
        {"task_id", "parent_agent_id", "parent_node_id"}
    )
    assert run.nodes[0].role == "researcher"
    assert run.nodes[0].duration_ms == 12.5


# 验证旧 task_id 被拒绝且剩余任务字段仍有长度边界。
def test_schema_rejects_obsolete_task_id_and_oversized_task_fields() -> None:
    base = {"strategy": "fan_out_fan_in", "tasks": [{"role": "r", "brief": "b"}, {"role": "r", "brief": "b"}]}
    with pytest.raises(schema_module.DelegateSchemaError):
        schema_module.validate_delegate_request(
            {**base, "tasks": [{"task_id": "obsolete", "role": "r", "brief": "b"}]}
        )
    for field, value in (("role", "r" * 129), ("brief", "b" * 12001)):
        payload = {**base, "tasks": [{"role": "r", "brief": "b"}, {"role": "r", "brief": "b"}]}
        payload["tasks"][0][field] = value
        with pytest.raises(schema_module.DelegateSchemaError):
            schema_module.validate_delegate_request(payload)


# 验证固定长度上限本身仍是合法的委派输入。
def test_schema_accepts_exact_task_field_length_limits() -> None:
    request = schema_module.validate_delegate_request(
        {
            "strategy": "fan_out_fan_in",
            "tasks": [
                {"role": "r" * 128, "brief": "b" * 12000},
                {"role": "reviewer", "brief": "check"},
            ],
        }
    )

    assert len(request.tasks[0].role) == 128
    assert len(request.tasks[0].brief) == 12000

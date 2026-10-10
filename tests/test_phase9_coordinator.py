from __future__ import annotations

import asyncio
import importlib
import json
from types import SimpleNamespace

import pytest


memory_module = importlib.import_module("Turning-Good-Agent.memory.long_term")


# 构造测试替身所需的空主体画像。
def _principal_context() -> SimpleNamespace:
    return SimpleNamespace(
        profile_memory=SimpleNamespace(read=lambda: memory_module.ProfileMemorySnapshot())
    )


# 构造可按任务角色返回固定结果的 Worker Runner。
def _worker_runner(results, calls, dependencies=None):
    types_module = importlib.import_module("Turning-Good-Agent.multi_agent.types")

    class Runner:
        # 记录实际启动顺序和 pipeline 依赖。
        async def run(self, task, dependency_content=None, *, run_id, node_id, profile_memory):
            assert run_id
            calls.append(task.role)
            if dependencies is not None:
                dependencies.append(dependency_content)
            value = results[task.role]
            if isinstance(value, Exception):
                raise value
            if isinstance(value, types_module.WorkerResult):
                return value
            return types_module.WorkerResult(status="completed", content=value)

    return Runner()


# 验证 Runner 签名检查异常时仍只使用带 run_id 的唯一契约。
def test_worker_runner_receives_run_id_without_signature_fallback(monkeypatch) -> None:
    coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
    schema_module = importlib.import_module("Turning-Good-Agent.multi_agent.schema")
    received_run_ids = []

    class Runner:
        async def run(self, task, dependency_content=None, *, run_id, node_id, profile_memory):
            del task, dependency_content, node_id
            received_run_ids.append(run_id)
            return importlib.import_module("Turning-Good-Agent.multi_agent.types").WorkerResult(
                status="completed", content="worker result"
            )

    def fail_signature(*args, **kwargs):
        del args, kwargs
        raise ValueError("signature unavailable")

    context = _parent_turn()
    request = schema_module.validate_delegate_request(
        {
            "strategy": "fan_out_fan_in",
            "tasks": [{ "role": "reviewer", "brief": "verify"}],
        }
    )
    monkeypatch.setattr(coordinator_module.inspect, "signature", fail_signature)
    run = asyncio.run(coordinator_module.MultiAgentCoordinator(worker_runner=Runner()).delegate(request, context))

    assert run.status == "running"
    assert received_run_ids == [run.run_id]


# 验证安全标签保留受限 Unicode，并拒绝路径、控制字符和超长值。
def test_safe_task_label_preserves_chinese_and_rejects_unsafe_values() -> None:
    coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")

    safe = coordinator_module.MultiAgentCoordinator._safe_task_label

    assert safe("验证") == "验证"
    assert safe("风险 审查-2") == "风险 审查-2"
    assert safe("验证/../../secret") == "multi_agent"
    assert safe("验证\n审查") == "multi_agent"
    assert safe("验" * 65) == "multi_agent"


# 构造 Coordinator 所需的最小父 Turn。
def _parent_turn(session_id="session-1", request_id="request-1", is_stop_requested=lambda: False):
    types_module = importlib.import_module("Turning-Good-Agent.multi_agent.types")
    return types_module.DelegateParentTurn(
        session_id=session_id,
        request_id=request_id,
        profile_memory=memory_module.ProfileMemorySnapshot(),
        initial_context_tokens=0,
        is_stop_requested=is_stop_requested,
    )


# 构造父回合唯一的专用委派对象。
def _invocation(coordinator, session_id="session-1", request_id="request-1", is_stop_requested=lambda: False):
    delegate_module = importlib.import_module("Turning-Good-Agent.multi_agent.delegate_tool")
    return delegate_module.DelegateMultiAgentInvocation(
        coordinator,
        _parent_turn(session_id, request_id, is_stop_requested),
        max_workers_per_run=8,
    )


# 验证固定任务顺序由 Runtime 生成稳定 Worker 标识。
def test_delegate_generates_worker_ids_without_model_task_ids() -> None:
    coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
    schema_module = importlib.import_module("Turning-Good-Agent.multi_agent.schema")
    types_module = importlib.import_module("Turning-Good-Agent.multi_agent.types")
    received = []

    class Runner:
        async def run(self, task, dependency_content=None, *, run_id, node_id, profile_memory):
            del dependency_content, profile_memory
            received.append((run_id, node_id, task.role))
            return types_module.WorkerResult(status="completed", content=task.role)

    request = schema_module.validate_delegate_request(
        {
            "strategy": "fan_out_fan_in",
            "tasks": [
                {"role": "reader", "brief": "collect facts"},
                {"role": "reviewer", "brief": "find risks"},
            ],
        }
    )
    run = asyncio.run(coordinator_module.MultiAgentCoordinator(worker_runner=Runner()).delegate(request, _parent_turn()))

    assert [node.node_id for node in run.nodes] == ["worker-1", "worker-2"]
    assert [item[1:] for item in received] == [("worker-1", "reader"), ("worker-2", "reviewer")]


# 验证 fan-out 的部分状态由终态节点结果派生。
def test_fanout_partial_state_is_derived_from_terminal_nodes() -> None:
    coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
    schema_module = importlib.import_module("Turning-Good-Agent.multi_agent.schema")
    types_module = importlib.import_module("Turning-Good-Agent.multi_agent.types")
    calls = []
    context = _parent_turn()
    request = schema_module.validate_delegate_request(
        {
            "strategy": "fan_out_fan_in",
            "tasks": [
                { "role": "reader", "brief": "collect facts"},
                { "role": "reviewer", "brief": "find risks"},
            ],
        }
    )
    runner = _worker_runner(
        {
            "reader": "facts found",
            "reviewer": types_module.WorkerResult(status="failed", content=None, error="safe failure"),
        },
        calls,
    )
    coordinator = coordinator_module.MultiAgentCoordinator(
        worker_runner=runner,
        max_concurrent_workers_per_run=2,
        max_concurrent_workers_global=2,
    )

    run = asyncio.run(coordinator.delegate(request, context))

    assert run.status == "running"
    assert [node.status for node in run.nodes] == ["completed", "failed"]
    assert sorted(calls) == ["reader", "reviewer"]


# 验证 pipeline 前序失败会取消所有未启动下游且不重试。
def test_pipeline_failure_cancels_downstream_without_retry() -> None:
    coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
    schema_module = importlib.import_module("Turning-Good-Agent.multi_agent.schema")
    types_module = importlib.import_module("Turning-Good-Agent.multi_agent.types")
    calls = []
    dependencies = []
    context = _parent_turn()
    request = schema_module.validate_delegate_request(
        {
            "strategy": "pipeline",
            "tasks": [
                { "role": "reader", "brief": "first"},
                { "role": "writer", "brief": "second"},
                { "role": "reviewer", "brief": "third"},
            ],
        }
    )
    runner = _worker_runner(
        {"reader": types_module.WorkerResult(status="failed", content=None, error="failed")},
        calls,
        dependencies,
    )
    coordinator = coordinator_module.MultiAgentCoordinator(worker_runner=runner)

    run = asyncio.run(coordinator.delegate(request, context))

    assert run.status == "failed"
    assert calls == ["reader"]
    assert dependencies == [None]
    assert [node.status for node in run.nodes] == ["failed", "cancelled", "cancelled"]


# 验证单 Worker fan-out 可以运行并保持父 Run 运行。
def test_fanout_accepts_one_worker_and_keeps_parent_running() -> None:
    coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
    schema_module = importlib.import_module("Turning-Good-Agent.multi_agent.schema")
    calls = []
    context = _parent_turn()
    request = schema_module.validate_delegate_request(
        {
            "strategy": "fan_out_fan_in",
            "tasks": [{ "role": "reviewer", "brief": "verify draft"}],
        }
    )
    coordinator = coordinator_module.MultiAgentCoordinator(
        worker_runner=_worker_runner({"reviewer": "verified"}, calls)
    )

    run = asyncio.run(coordinator.delegate(request, context))

    assert run.status == "running"
    assert calls == ["reviewer"]


# 验证同一父请求的第二次委派明确拒绝。
def test_duplicate_delegate_call_is_rejected() -> None:
    coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
    calls = []
    context = _parent_turn()
    coordinator = coordinator_module.MultiAgentCoordinator(
        worker_runner=_worker_runner({"reviewer": "verified"}, calls)
    )
    invocation = _invocation(coordinator)
    payload = {
        "strategy": "fan_out_fan_in",
        "tasks": [{ "role": "reviewer", "brief": "verify"}],
    }

    first = json.loads(asyncio.run(invocation.invoke(payload)))
    second = json.loads(asyncio.run(invocation.invoke(payload)))

    assert first["status"] == "running"
    assert second["status"] == "failed"
    assert "一次" in second["error"]
    assert calls == ["reviewer"]


# 验证真实父 synthesis 第二轮能看到安全的 Worker 结果而非附件错误。
def test_delegate_worker_evidence_reaches_real_parent_synthesis_tool_message() -> None:
    # 执行两轮父 AgentLoop 委派场景。
    async def run() -> None:
        agent_loop_module = importlib.import_module("Turning-Good-Agent.runtime.agent_loop")
        coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
        delegate_module = importlib.import_module("Turning-Good-Agent.multi_agent.delegate_tool")
        llm_types_module = importlib.import_module("Turning-Good-Agent.llm.types")
        registry_module = importlib.import_module("Turning-Good-Agent.tools.registry")
        settings_module = importlib.import_module("Turning-Good-Agent.config.settings")

        class Runner:
            # 返回父 synthesis 应当可见的受限 Worker 结论。
            async def run(self, task, dependency_content=None, *, run_id, node_id, profile_memory):
                del task, dependency_content, node_id
                return importlib.import_module("Turning-Good-Agent.multi_agent.types").WorkerResult(
                    status="completed", content="worker evidence"
                )

        class Llm:
            # 初始化委派和父 synthesis 的两轮响应。
            def __init__(self) -> None:
                self.calls = 0

            # 在第二轮断言模型输入保留 Worker evidence。
            async def complete(self, messages, tools):
                self.calls += 1
                if self.calls == 1:
                    return llm_types_module.LLMResponse(
                        "",
                        [
                            llm_types_module.ToolCall(
                                "delegate",
                                "delegate_multi_agent",
                                {
                                    "strategy": "fan_out_fan_in",
                                    "tasks": [
                                        {
                                            "role": "reviewer",
                                            "brief": "verify",
                                        }
                                    ],
                                },
                            )
                        ],
                        llm_types_module.LLMUsage(total_tokens=1),
                    )
                assert tools == []
                tool_contents = [
                    message["content"]
                    for message in messages
                    if message.get("role") == "tool"
                ]
                assert any("worker evidence" in content for content in tool_contents)
                assert all("附件被拒绝" not in content for content in tool_contents)
                return llm_types_module.LLMResponse(
                    "parent synthesis", [], llm_types_module.LLMUsage(total_tokens=1)
                )

        context = _parent_turn("session-c1", "request-c1")
        coordinator = coordinator_module.MultiAgentCoordinator(worker_runner=Runner())
        invocation = delegate_module.DelegateMultiAgentInvocation(coordinator, context, max_workers_per_run=8)
        loop = agent_loop_module.AgentLoop(
            Llm(), registry_module.ToolRegistry(), settings_module.RuntimeSettings(max_tool_rounds=2)
        )

        result = await loop.run(
            [{"role": "user", "content": "delegate"}],
            multi_agent_invocation=invocation,
        )

        assert result.final_content == "parent synthesis"
        assert result.tool_calls == []

    asyncio.run(run())


# 验证专用 invocation 独立持有父 Turn 生命周期。
def test_delegate_multi_agent_invocation_owns_one_parent_run() -> None:
    # 使用真实 Coordinator 执行最小 fan-out 委派。
    async def run() -> None:
        coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
        delegate_module = importlib.import_module("Turning-Good-Agent.multi_agent.delegate_tool")
        types_module = importlib.import_module("Turning-Good-Agent.multi_agent.types")

        parent = types_module.DelegateParentTurn(
            session_id="invocation-session",
            request_id="invocation-request",
            profile_memory=memory_module.ProfileMemorySnapshot(),
            initial_context_tokens=0,
            is_stop_requested=lambda: False,
        )
        coordinator = coordinator_module.MultiAgentCoordinator(
            worker_runner=_worker_runner({"reviewer": "worker evidence"}, [])
        )
        invocation = delegate_module.DelegateMultiAgentInvocation(
            coordinator, parent, max_workers_per_run=8
        )
        payload = {
            "strategy": "fan_out_fan_in",
            "tasks": [{ "role": "reviewer", "brief": "verify"}],
        }

        content = await invocation.invoke(payload)
        result = json.loads(content)

        assert result["status"] == "running"
        assert result["results"] == [
            {"node_id": "worker-1", "role": "reviewer", "content": "worker evidence"}
        ]
        assert invocation.run_id == result["run_id"]
        assert invocation.is_schema_visible is False
        await invocation.finish_parent_turn("completed")
        assert coordinator.run_for(parent.session_id, parent.request_id) is None

    asyncio.run(run())


# 验证父 synthesis 未结束时 Run 保持非终态，失败后由 AgentLoop 回写失败。
def test_real_parent_synthesis_finalizes_run_only_after_agent_loop_outcome() -> None:
    # 执行第二轮父模型失败场景。
    async def run() -> None:
        agent_loop_module = importlib.import_module("Turning-Good-Agent.runtime.agent_loop")
        coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
        delegate_module = importlib.import_module("Turning-Good-Agent.multi_agent.delegate_tool")
        llm_types_module = importlib.import_module("Turning-Good-Agent.llm.types")
        registry_module = importlib.import_module("Turning-Good-Agent.tools.registry")
        settings_module = importlib.import_module("Turning-Good-Agent.config.settings")

        class Runner:
            # 返回可完成的验证 Worker 结果。
            async def run(self, task, dependency_content=None, *, run_id, node_id, profile_memory):
                del task, dependency_content, node_id
                return importlib.import_module("Turning-Good-Agent.multi_agent.types").WorkerResult(
                    status="completed", content="verified"
                )

        context = _parent_turn("session-c2", "request-c2")
        events = []
        coordinator = coordinator_module.MultiAgentCoordinator(
            worker_runner=Runner(),
            event_sink=lambda event_type, payload: events.append((event_type, payload)),
        )
        invocation = delegate_module.DelegateMultiAgentInvocation(coordinator, context, max_workers_per_run=8)

        class Llm:
            # 在 synthesis 开始前检查 Run 仍非 completed，然后模拟父失败。
            async def complete(self, messages, tools):
                if tools:
                    return llm_types_module.LLMResponse(
                        "",
                        [
                            llm_types_module.ToolCall(
                                "delegate",
                                "delegate_multi_agent",
                                {
                                    "strategy": "fan_out_fan_in",
                                    "tasks": [
                                        {
                                            "role": "reviewer",
                                            "brief": "verify",
                                        }
                                    ],
                                },
                            )
                        ],
                        llm_types_module.LLMUsage(total_tokens=1),
                    )
                active = coordinator.run_for("session-c2", "request-c2")
                assert active is not None
                assert active.status != "completed"
                raise RuntimeError("synthesis failed")

        loop = agent_loop_module.AgentLoop(
            Llm(), registry_module.ToolRegistry(), settings_module.RuntimeSettings(max_tool_rounds=2)
        )
        with pytest.raises(RuntimeError, match="synthesis failed"):
            await loop.run(
                [{"role": "user", "content": "delegate"}],
                multi_agent_invocation=invocation,
            )
        assert coordinator.run_for("session-c2", "request-c2") is None
        assert events[-1][0] == "multi_agent.run.failed"

    asyncio.run(run())


# 验证 Stop 会取消正在运行的 Worker 且不发送迟到终态事件。
def test_stop_cancels_running_worker_without_late_terminal_event() -> None:
    # 运行被 Stop 中断的单 Worker 委派。
    async def run() -> None:
        coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
        schema_module = importlib.import_module("Turning-Good-Agent.multi_agent.schema")
        stopped = False
        started = asyncio.Event()
        cancelled = asyncio.Event()
        blocker = asyncio.Event()
        events = []

        class Runner:
            # 阻塞直到 coordinator 发出取消。
            async def run(self, task, dependency_content=None, *, run_id, node_id, profile_memory):
                del task, dependency_content, node_id
                started.set()
                try:
                    await blocker.wait()
                except asyncio.CancelledError:
                    cancelled.set()
                    raise

        context = _parent_turn("session-stop", "request-stop", lambda: stopped)
        request = schema_module.validate_delegate_request(
            {
                "strategy": "fan_out_fan_in",
                "tasks": [{ "role": "reviewer", "brief": "verify"}],
            }
        )
        coordinator = coordinator_module.MultiAgentCoordinator(
            worker_runner=Runner(),
            run_timeout_seconds=10,
            worker_timeout_seconds=10,
            event_sink=lambda event_type, payload: events.append((event_type, payload)),
        )
        delegation = asyncio.create_task(coordinator.delegate(request, context))
        await started.wait()
        stopped = True
        done, pending = await asyncio.wait({delegation}, timeout=0.5)
        if pending:
            delegation.cancel()
            with pytest.raises(asyncio.CancelledError):
                await delegation
            pytest.fail("Stop 未在运行中取消 Worker")
        result = done.pop().result()

        assert cancelled.is_set()
        assert result.status == "cancelled"
        assert [node.status for node in result.nodes] == ["cancelled"]
        terminal_events = [
            event_type
            for event_type, payload in events
            if payload.node_id == "worker-1" and event_type.startswith("multi_agent.node.")
            and payload.status in {"completed", "failed", "timed_out", "cancelled", "interrupted"}
        ]
        assert terminal_events == ["multi_agent.node.updated"]
        event_count = len(events)
        await asyncio.sleep(0.1)
        assert len(events) == event_count

    asyncio.run(run())


# 验证单个 Worker 超过 token 预算时转为受控失败且不泄露正文。
def test_worker_result_over_budget_is_controlled_failure() -> None:
    # 执行单 Worker 正文超限场景。
    async def run() -> None:
        coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
        delegate_module = importlib.import_module("Turning-Good-Agent.multi_agent.delegate_tool")
        context = _parent_turn("session-i2-worker", "request-i2-worker")

        class Runner:
            # 返回超过单 Worker 预算的正文。
            async def run(self, task, dependency_content=None, *, run_id, node_id, profile_memory):
                del task, dependency_content, node_id
                return importlib.import_module("Turning-Good-Agent.multi_agent.types").WorkerResult(
                    status="completed", content="oversized-worker-content"
                )

        coordinator = coordinator_module.MultiAgentCoordinator(
            worker_runner=Runner(), worker_result_token_limit=1
        )
        invocation = delegate_module.DelegateMultiAgentInvocation(coordinator, context, max_workers_per_run=8)
        payload = {"strategy": "fan_out_fan_in", "tasks": [{ "role": "reviewer", "brief": "verify"}]}
        result = json.loads(await invocation.invoke(payload))
        assert result["status"] == "failed"
        assert "oversized-worker-content" not in result["error"]
        assert result["error_code"] == "result_context_exceeded"
        await invocation.finish_parent_turn("failed")
        assert coordinator.run_for("session-i2-worker", "request-i2-worker") is None

    asyncio.run(run())


# 验证聚合 Worker 结果超过父预算时不进入 synthesis 且不泄露正文。
def test_parent_result_budget_overflow_is_controlled_failure() -> None:
    # 执行 fan-out 聚合结果超限场景。
    async def run() -> None:
        coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
        delegate_module = importlib.import_module("Turning-Good-Agent.multi_agent.delegate_tool")
        types_module = importlib.import_module("Turning-Good-Agent.multi_agent.types")
        context = _parent_turn("session-i2-parent", "request-i2-parent")

        class Runner:
            # 返回两个可聚合但总量超限的 Worker 结果。
            async def run(self, task, dependency_content=None, *, run_id, node_id, profile_memory):
                del dependency_content, node_id
                return types_module.WorkerResult(status="completed", content=task.brief * 8)

        coordinator = coordinator_module.MultiAgentCoordinator(
            worker_runner=Runner(), parent_result_token_limit=1
        )
        invocation = delegate_module.DelegateMultiAgentInvocation(coordinator, context, max_workers_per_run=8)
        payload = {"strategy": "fan_out_fan_in", "tasks": [
            { "role": "reader", "brief": "one"},
            { "role": "reader", "brief": "two"},
        ]}
        result = json.loads(await invocation.invoke(payload))
        assert result["status"] == "failed"
        assert "ononono" not in result["error"]
        assert "twotwotwo" not in result["error"]
        assert result["error_code"] == "result_context_exceeded"
        await invocation.finish_parent_turn("failed")
        assert coordinator.run_for("session-i2-parent", "request-i2-parent") is None

    asyncio.run(run())


# 验证 pipeline 依赖超限时转为受控失败而不抛出异常。
def test_pipeline_dependency_overflow_is_controlled_failure() -> None:
    # 执行前序依赖长度超限场景。
    async def run() -> None:
        coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
        schema_module = importlib.import_module("Turning-Good-Agent.multi_agent.schema")
        types_module = importlib.import_module("Turning-Good-Agent.multi_agent.types")
        context = _parent_turn("session-i2-pipeline", "request-i2-pipeline")

        class Runner:
            # 返回会触发紧邻依赖上限的前序正文。
            async def run(self, task, dependency_content=None, *, run_id, node_id, profile_memory):
                del dependency_content, node_id
                content = "x" * 12_001 if task.brief == "first" else "second"
                return types_module.WorkerResult(status="completed", content=content)

        request = schema_module.validate_delegate_request(
            {
                "strategy": "pipeline",
                "tasks": [
                    { "role": "reader", "brief": "first"},
                    { "role": "writer", "brief": "second"},
                ],
            }
        )
        coordinator = coordinator_module.MultiAgentCoordinator(worker_runner=Runner())
        result = await coordinator.delegate(request, context)
        assert result.status == "failed"
        assert [node.status for node in result.nodes] == ["completed", "failed"]
        assert result.error_code == "result_context_exceeded"

    asyncio.run(run())


# 验证特殊能力结果会移除常见 secret=value 片段。
def test_delegate_result_redacts_secret_values() -> None:
    # 执行 Worker 正文安全化场景。
    async def run() -> None:
        coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
        delegate_module = importlib.import_module("Turning-Good-Agent.multi_agent.delegate_tool")
        types_module = importlib.import_module("Turning-Good-Agent.multi_agent.types")
        context = _parent_turn("session-i2-redact", "request-i2-redact")

        class Runner:
            # 返回包含敏感键值的受限 Worker 正文。
            async def run(self, task, dependency_content=None, *, run_id, node_id, profile_memory):
                del task, dependency_content, node_id
                return types_module.WorkerResult(
                    status="completed", content="api_key=super-secret-value"
                )

        coordinator = coordinator_module.MultiAgentCoordinator(worker_runner=Runner())
        invocation = delegate_module.DelegateMultiAgentInvocation(coordinator, context, max_workers_per_run=8)
        payload = {"strategy": "fan_out_fan_in", "tasks": [{ "role": "reviewer", "brief": "verify"}]}
        result = json.loads(await invocation.invoke(payload))
        assert "super-secret-value" not in result["results"][0]["content"]

    asyncio.run(run())


# 验证 Multi-Agent 事件不再传输固定所有权和版本字段。
def test_multi_agent_events_follow_reduced_payload_contract() -> None:
    # 执行事件序列并完成父 Run。
    async def run() -> None:
        coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
        schema_module = importlib.import_module("Turning-Good-Agent.multi_agent.schema")
        events = []

        class Runner:
            # 返回短文本以覆盖 Worker 终态事件。
            async def run(self, task, dependency_content=None, *, run_id, node_id, profile_memory):
                del task, dependency_content, node_id
                return importlib.import_module("Turning-Good-Agent.multi_agent.types").WorkerResult(
                    status="completed", content="verified"
                )

        context = _parent_turn("session-i3-events", "request-i3-events")
        request = schema_module.validate_delegate_request(
            {
                "strategy": "fan_out_fan_in",
                "tasks": [{ "role": "reviewer", "brief": "verify"}],
            }
        )
        coordinator = coordinator_module.MultiAgentCoordinator(
            worker_runner=Runner(),
            event_sink=lambda event_type, payload: events.append((event_type, payload)),
        )
        collected = await coordinator.delegate(request, context)
        # Worker 收集完成后仍由父 Agent 继续汇总，不应伪装成审批等待。
        assert collected.status == "running"
        await coordinator.finalize(collected.run_id, "completed")

        event_types = [event_type for event_type, _ in events]
        assert event_types == [
            "multi_agent.run.started",
            "multi_agent.node.updated",
            "multi_agent.run.started",
            "multi_agent.node.updated",
            "multi_agent.node.updated",
            "multi_agent.run.completed",
        ]
        worker_payloads = [payload for event_type, payload in events if payload.node_id == "worker-1"]
        assert worker_payloads
        assert all(payload.usage is None for _, payload in events)
        terminal = [
            event_type
            for event_type, payload in events
            if payload.node_id == "worker-1" and payload.status == "completed"
        ]
        assert terminal == ["multi_agent.node.updated"]
        removed = {
            "schema_version",
            "agent_id",
            "parent_agent_id",
            "parent_node_id",
            "node_kind",
            "mode",
            "partial",
        }
        assert all(removed.isdisjoint(payload.as_dict()) for _, payload in events)

    asyncio.run(run())


# 验证拓扑失败后父 Agent 不再调用第二次模型并确定性收口。
def test_failed_worker_run_stops_parent_synthesis() -> None:
    async def run() -> None:
        agent_loop_module = importlib.import_module("Turning-Good-Agent.runtime.agent_loop")
        coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
        delegate_module = importlib.import_module("Turning-Good-Agent.multi_agent.delegate_tool")
        llm_types_module = importlib.import_module("Turning-Good-Agent.llm.types")
        registry_module = importlib.import_module("Turning-Good-Agent.tools.registry")
        settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
        types_module = importlib.import_module("Turning-Good-Agent.multi_agent.types")

        class Runner:
            # 返回确定性失败以阻断父 synthesis。
            async def run(self, task, dependency_content=None, *, run_id, node_id, profile_memory):
                del task, dependency_content, run_id, node_id
                return types_module.WorkerResult("failed", None, "worker failed")

        class Llm:
            # 第二次调用若发生则证明失败拓扑错误地进入 synthesis。
            def __init__(self) -> None:
                self.calls = 0

            async def complete(self, messages, tools):
                del messages
                self.calls += 1
                if self.calls == 1:
                    return llm_types_module.LLMResponse(
                        "",
                        [
                            llm_types_module.ToolCall(
                                "delegate", "delegate_multi_agent", {
                                    "strategy": "fan_out_fan_in",
                                    "tasks": [{ "role": "reviewer", "brief": "verify"}],
                                },
                            )
                        ],
                        llm_types_module.LLMUsage(total_tokens=1),
                    )
                return llm_types_module.LLMResponse(
                    "不应调用", [], llm_types_module.LLMUsage(total_tokens=1)
                )

        context = _parent_turn("failed-session", "failed-request")
        events = []
        coordinator = coordinator_module.MultiAgentCoordinator(
            worker_runner=Runner(),
            event_sink=lambda event_type, payload: events.append((event_type, payload)),
        )
        invocation = delegate_module.DelegateMultiAgentInvocation(coordinator, context, max_workers_per_run=8)
        llm = Llm()
        result = await agent_loop_module.AgentLoop(
            llm, registry_module.ToolRegistry(), settings_module.RuntimeSettings(max_tool_rounds=2)
        ).run(
            [{"role": "user", "content": "delegate"}],
            multi_agent_invocation=invocation,
        )

        assert llm.calls == 1
        assert coordinator.run_for("failed-session", "failed-request") is None
        assert events[-1][0] == "multi_agent.run.failed"
        assert "失败" in result.final_content or "未能" in result.final_content

    asyncio.run(run())


# 验证父 synthesis 不能越过 Multi-Agent Run 的总 deadline。
def test_parent_synthesis_is_bounded_by_run_deadline() -> None:
    async def run() -> None:
        agent_loop_module = importlib.import_module("Turning-Good-Agent.runtime.agent_loop")
        coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
        delegate_module = importlib.import_module("Turning-Good-Agent.multi_agent.delegate_tool")
        llm_types_module = importlib.import_module("Turning-Good-Agent.llm.types")
        registry_module = importlib.import_module("Turning-Good-Agent.tools.registry")
        settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
        types_module = importlib.import_module("Turning-Good-Agent.multi_agent.types")

        class Runner:
            # 立即完成 Worker 让剩余时间留给父 synthesis。
            async def run(self, task, dependency_content=None, *, run_id, node_id, profile_memory):
                del task, dependency_content, run_id, node_id
                return types_module.WorkerResult("completed", "verified")

        class Llm:
            # 故意让第二次父模型调用超过 Run deadline。
            def __init__(self) -> None:
                self.calls = 0

            async def complete(self, messages, tools):
                del messages
                self.calls += 1
                if self.calls == 1:
                    return llm_types_module.LLMResponse(
                        "",
                        [
                            llm_types_module.ToolCall(
                                "delegate", "delegate_multi_agent", {
                                    "strategy": "fan_out_fan_in",
                                    "tasks": [{ "role": "reviewer", "brief": "verify"}],
                                },
                            )
                        ],
                        llm_types_module.LLMUsage(total_tokens=1),
                    )
                await asyncio.sleep(0.15)
                return llm_types_module.LLMResponse(
                    "late synthesis", [], llm_types_module.LLMUsage(total_tokens=1)
                )

        context = _parent_turn("deadline-session", "deadline-request")
        events = []
        coordinator = coordinator_module.MultiAgentCoordinator(
            worker_runner=Runner(),
            run_timeout_seconds=0.05,
            worker_timeout_seconds=0.04,
            event_sink=lambda event_type, payload: events.append((event_type, payload)),
        )
        invocation = delegate_module.DelegateMultiAgentInvocation(coordinator, context, max_workers_per_run=8)
        llm = Llm()
        result = await agent_loop_module.AgentLoop(
            llm, registry_module.ToolRegistry(), settings_module.RuntimeSettings(max_tool_rounds=2)
        ).run(
            [{"role": "user", "content": "delegate"}],
            multi_agent_invocation=invocation,
        )

        assert llm.calls == 2
        assert coordinator.run_for("deadline-session", "deadline-request") is None
        assert events[-1][0] == "multi_agent.run.failed"
        assert events[-1][1].status == "timed_out"
        assert "超时" in result.final_content

    asyncio.run(run())


# 验证同轮委派后的普通工具审批也受新建 Run deadline 约束。
def test_mixed_delegate_and_approval_is_bounded_by_run_deadline() -> None:
    # 执行同一模型响应内先委派、再等待审批的场景。
    async def run() -> None:
        agent_loop_module = importlib.import_module("Turning-Good-Agent.runtime.agent_loop")
        coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
        delegate_module = importlib.import_module("Turning-Good-Agent.multi_agent.delegate_tool")
        hook_module = importlib.import_module("Turning-Good-Agent.hooks.manager")
        permission_module = importlib.import_module("Turning-Good-Agent.hooks.tool_permission")
        llm_types_module = importlib.import_module("Turning-Good-Agent.llm.types")
        registry_module = importlib.import_module("Turning-Good-Agent.tools.registry")
        settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
        tool_base_module = importlib.import_module("Turning-Good-Agent.tools.base")
        types_module = importlib.import_module("Turning-Good-Agent.multi_agent.types")

        class ApprovalTool:
            name = "danger"
            description = "approval tool"
            input_schema = {"type": "object", "properties": {}}
            parallel_safe = False
            approval_required = True

            # 返回审批通过后的确定性结果。
            async def run(self, args):
                del args
                return tool_base_module.ToolResult("approved")

        class Runner:
            # 立即完成 Worker 以启动父 Run deadline。
            async def run(self, task, dependency_content=None, *, run_id, node_id, profile_memory):
                del task, dependency_content, run_id, node_id
                return types_module.WorkerResult("completed", "verified")

        class Adapter:
            # 初始化永不主动完成的审批等待信号。
            def __init__(self) -> None:
                self.waiting = asyncio.Event()

            # 丢弃流式文本。
            async def on_delta(self, text):
                del text

            # 丢弃状态文本。
            async def on_status(self, text):
                del text

            # 丢弃工具开始事件。
            async def on_tool_started(self, tool_call_id, tool_name, args):
                del tool_call_id, tool_name, args

            # 丢弃工具结束事件。
            async def on_tool_finished(self, tool_call_id, tool_name, failed):
                del tool_call_id, tool_name, failed

            # 丢弃完成事件。
            async def on_completed(self, content):
                del content

            # 丢弃错误事件。
            async def on_error(self, content):
                del content

            # 丢弃 Multi-Agent 事件。
            async def on_multi_agent_event(self, event_type, payload):
                del event_type, payload

            # 保持审批挂起直到 Run deadline 取消等待。
            async def request_tool_approval(self, call):
                del call
                self.waiting.set()
                await asyncio.Future()

            # 返回空 guidance。
            async def consume_guidance(self):
                return []

            # 表示父 turn 未被用户停止。
            def is_stop_requested(self):
                return False

        class Llm:
            # 在唯一模型调用中混合委派和审批工具。
            async def complete(self, messages, tools):
                del messages, tools
                return llm_types_module.LLMResponse(
                    "",
                    [
                        llm_types_module.ToolCall(
                            "delegate",
                            "delegate_multi_agent",
                            {
                                "strategy": "fan_out_fan_in",
                                "tasks": [
                                    {
                                        "role": "reviewer",
                                        "brief": "verify",
                                    }
                                ],
                            },
                        ),
                        llm_types_module.ToolCall("danger", "danger", {}),
                    ],
                    llm_types_module.LLMUsage(total_tokens=1),
                )

        context = _parent_turn("mixed-deadline-session", "mixed-deadline-request")
        registry = registry_module.ToolRegistry()
        registry.register(ApprovalTool())
        hooks = hook_module.HookManager()
        hooks.register(permission_module.ToolPermissionHook(frozenset({"danger"}), registry))
        events = []
        coordinator = coordinator_module.MultiAgentCoordinator(
            worker_runner=Runner(),
            run_timeout_seconds=0.05,
            worker_timeout_seconds=0.04,
            event_sink=lambda event_type, payload: events.append((event_type, payload)),
        )
        invocation = delegate_module.DelegateMultiAgentInvocation(coordinator, context, max_workers_per_run=8)
        adapter = Adapter()

        result = await asyncio.wait_for(
            agent_loop_module.AgentLoop(
                Llm(), registry, settings_module.RuntimeSettings(max_tool_rounds=2), hooks=hooks
            ).run(
                [{"role": "user", "content": "delegate and approve"}],
                adapter,
                multi_agent_invocation=invocation,
            ),
            timeout=0.2,
        )

        assert adapter.waiting.is_set()
        assert coordinator.run_for("mixed-deadline-session", "mixed-deadline-request") is None
        assert events[-1][0] == "multi_agent.run.failed"
        assert events[-1][1].status == "timed_out"
        assert "超时" in result.final_content

    asyncio.run(run())


# 验证事件投影故障不会遗留 running Run。
def test_event_sink_failure_closes_run_deterministically() -> None:
    async def run() -> None:
        coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
        schema_module = importlib.import_module("Turning-Good-Agent.multi_agent.schema")
        types_module = importlib.import_module("Turning-Good-Agent.multi_agent.types")

        class Runner:
            # 事件投影失败时不应继续执行 Worker。
            async def run(self, task, dependency_content=None, *, run_id, node_id, profile_memory):
                del task, dependency_content, run_id, node_id
                return types_module.WorkerResult("completed", "unexpected")

        async def sink(event_type, payload):
            if event_type == "multi_agent.node.updated" and payload.status == "running":
                raise OSError("trace persistence failed")

        context = _parent_turn("sink-session", "sink-request")
        request = schema_module.validate_delegate_request(
            {
                "strategy": "fan_out_fan_in",
                "tasks": [{ "role": "reviewer", "brief": "verify"}],
            }
        )
        coordinator = coordinator_module.MultiAgentCoordinator(
            worker_runner=Runner(), event_sink=sink
        )
        result = await coordinator.delegate(request, context)

        assert result.status == "failed"
        assert result.error is not None

    asyncio.run(run())


# 验证父 Run 终态事件落盘失败时不能返回伪 completed。
def test_terminal_event_sink_failure_changes_finalize_result_to_failed() -> None:
    # 仅拒绝 completed 终态并观察协调器的受控失败结果。
    async def run() -> None:
        coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
        schema_module = importlib.import_module("Turning-Good-Agent.multi_agent.schema")
        types_module = importlib.import_module("Turning-Good-Agent.multi_agent.types")

        class Runner:
            # 返回可进入父 synthesis 的成功 Worker 结果。
            async def run(self, task, dependency_content=None, *, run_id, node_id, profile_memory):
                del task, dependency_content, run_id, node_id
                return types_module.WorkerResult("completed", "verified")

        events = []

        # 模拟 completed 终态持久化失败并保留其他事件。
        async def sink(event_type, payload):
            if event_type == "multi_agent.run.completed":
                raise OSError("terminal trace persistence failed")
            events.append((event_type, payload))

        context = _parent_turn("terminal-sink-session", "terminal-sink-request")
        request = schema_module.validate_delegate_request(
            {
                "strategy": "fan_out_fan_in",
                "tasks": [{ "role": "reviewer", "brief": "verify"}],
            }
        )
        coordinator = coordinator_module.MultiAgentCoordinator(
            worker_runner=Runner(), event_sink=sink
        )
        active = await coordinator.delegate(request, context)
        final = await coordinator.finalize(active.run_id, "completed")

        assert final.status == "failed"
        assert final.error_code == "event_persistence_failed"
        assert [event_type for event_type, _ in events][-1] == "multi_agent.run.failed"

    asyncio.run(run())


# 验证普通工具产生的大上下文会在委派前触发确定性预算失败。
def test_current_parent_context_budget_is_rechecked_before_delegate() -> None:
    async def run() -> None:
        agent_loop_module = importlib.import_module("Turning-Good-Agent.runtime.agent_loop")
        coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
        delegate_module = importlib.import_module("Turning-Good-Agent.multi_agent.delegate_tool")
        llm_types_module = importlib.import_module("Turning-Good-Agent.llm.types")
        registry_module = importlib.import_module("Turning-Good-Agent.tools.registry")
        settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
        tool_base_module = importlib.import_module("Turning-Good-Agent.tools.base")
        types_module = importlib.import_module("Turning-Good-Agent.multi_agent.types")

        class BlobTool:
            name = "read_blob"
            description = "read blob"
            input_schema = {"type": "object", "properties": {}}
            parallel_safe = False

            # 返回足以超过父上下文上限的普通工具结果。
            async def run(self, args):
                del args
                return tool_base_module.ToolResult("x" * 5_000)

        class Runner:
            # 预算失败时不应启动 Worker。
            async def run(self, task, dependency_content=None, *, run_id, node_id, profile_memory):
                del task, dependency_content, run_id, node_id
                return types_module.WorkerResult("completed", "unexpected")

        class Llm:
            # 同一规划轮先读取大结果再尝试委派，后续模型调用不应发生。
            def __init__(self) -> None:
                self.calls = 0

            async def complete(self, messages, tools):
                del messages
                self.calls += 1
                return llm_types_module.LLMResponse(
                    "",
                    [
                        llm_types_module.ToolCall("blob", "read_blob", {}),
                        llm_types_module.ToolCall(
                            "delegate", "delegate_multi_agent", {
                                "strategy": "fan_out_fan_in",
                                "tasks": [{ "role": "reviewer", "brief": "verify"}],
                            },
                        )
                    ],
                    llm_types_module.LLMUsage(total_tokens=1),
                )

        context = _parent_turn("budget-session", "budget-request")
        registry = registry_module.ToolRegistry()
        registry.register(BlobTool())
        events = []
        coordinator = coordinator_module.MultiAgentCoordinator(
            worker_runner=Runner(),
            max_context_tokens=100,
            event_sink=lambda event_type, payload: events.append((event_type, payload)),
        )
        invocation = delegate_module.DelegateMultiAgentInvocation(coordinator, context, max_workers_per_run=8)
        llm = Llm()
        result = await agent_loop_module.AgentLoop(
            llm, registry, settings_module.RuntimeSettings(max_tool_rounds=3)
        ).run(
            [{"role": "user", "content": "delegate"}],
            multi_agent_invocation=invocation,
        )

        assert llm.calls == 1
        assert coordinator.run_for("budget-session", "budget-request") is None
        assert events[-1][0] == "multi_agent.run.failed"
        assert "上下文" in (events[-1][1].error or "")
        assert "上下文" in result.final_content or "失败" in result.final_content

    asyncio.run(run())


# 验证父 synthesis 审批等待会投影为 waiting 并在恢复后回到完成。
def test_parent_approval_wait_projects_multi_agent_waiting() -> None:
    async def run() -> None:
        agent_loop_module = importlib.import_module("Turning-Good-Agent.runtime.agent_loop")
        coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
        delegate_module = importlib.import_module("Turning-Good-Agent.multi_agent.delegate_tool")
        hook_module = importlib.import_module("Turning-Good-Agent.hooks.manager")
        permission_module = importlib.import_module("Turning-Good-Agent.hooks.tool_permission")
        llm_types_module = importlib.import_module("Turning-Good-Agent.llm.types")
        registry_module = importlib.import_module("Turning-Good-Agent.tools.registry")
        settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
        tool_base_module = importlib.import_module("Turning-Good-Agent.tools.base")
        types_module = importlib.import_module("Turning-Good-Agent.multi_agent.types")

        class ApprovalTool:
            name = "danger"
            description = "approval tool"
            input_schema = {"type": "object", "properties": {}}
            parallel_safe = False
            approval_required = True

            # 返回审批后的确定性工具结果。
            async def run(self, args):
                del args
                return tool_base_module.ToolResult("approved")

        class Runner:
            # 立即完成只读验证 Worker。
            async def run(self, task, dependency_content=None, *, run_id, node_id, profile_memory):
                del task, dependency_content, run_id, node_id
                return types_module.WorkerResult("completed", "verified")

        class Adapter:
            # 保存父审批等待和放行信号。
            def __init__(self) -> None:
                self.waiting = asyncio.Event()
                self.release = asyncio.Event()

            async def on_delta(self, text):
                del text

            async def on_status(self, text):
                del text

            async def on_tool_started(self, tool_call_id, tool_name, args):
                del tool_call_id, tool_name, args

            async def on_tool_finished(self, tool_call_id, tool_name, failed):
                del tool_call_id, tool_name, failed

            async def on_completed(self, content):
                del content

            async def on_error(self, content):
                del content

            async def on_multi_agent_event(self, event_type, payload):
                del event_type, payload

            async def request_tool_approval(self, call):
                del call
                self.waiting.set()
                await self.release.wait()
                return None

            async def consume_guidance(self):
                return []

            def is_stop_requested(self):
                return False

        class Llm:
            # 先委派，再触发父工具审批，最后返回合成答案。
            def __init__(self) -> None:
                self.calls = 0

            async def complete(self, messages, tools):
                del messages
                self.calls += 1
                if self.calls == 1:
                    return llm_types_module.LLMResponse(
                        "",
                        [llm_types_module.ToolCall("delegate", "delegate_multi_agent", {
                            "strategy": "fan_out_fan_in",
                            "tasks": [{ "role": "reviewer", "brief": "verify"}],
                        })],
                        llm_types_module.LLMUsage(total_tokens=1),
                    )
                if self.calls == 2:
                    return llm_types_module.LLMResponse(
                        "",
                        [llm_types_module.ToolCall("danger", "danger", {})],
                        llm_types_module.LLMUsage(total_tokens=1),
                    )
                return llm_types_module.LLMResponse(
                    "done", [], llm_types_module.LLMUsage(total_tokens=1)
                )

        context = _parent_turn("approval-session", "approval-request")
        registry = registry_module.ToolRegistry()
        registry.register(ApprovalTool())
        hooks = hook_module.HookManager()
        hooks.register(permission_module.ToolPermissionHook(frozenset({"danger"}), registry))
        events = []
        coordinator = coordinator_module.MultiAgentCoordinator(
            worker_runner=Runner(),
            event_sink=lambda event_type, payload: events.append((event_type, payload)),
        )
        invocation = delegate_module.DelegateMultiAgentInvocation(coordinator, context, max_workers_per_run=8)
        adapter = Adapter()
        task = asyncio.create_task(
            agent_loop_module.AgentLoop(
                Llm(), registry, settings_module.RuntimeSettings(max_tool_rounds=3), hooks=hooks
            ).run(
                [{"role": "user", "content": "delegate"}],
                adapter,
                multi_agent_invocation=invocation,
            )
        )
        await asyncio.wait_for(adapter.waiting.wait(), timeout=1)
        active = coordinator.run_for("approval-session", "approval-request")
        assert active is not None and active.status == "waiting"
        adapter.release.set()
        result = await task
        assert result.final_content == "done"
        assert coordinator.run_for("approval-session", "approval-request") is None
        assert events[-1][0] == "multi_agent.run.completed"

    asyncio.run(run())

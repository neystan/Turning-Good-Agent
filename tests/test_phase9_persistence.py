from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient


store_module = importlib.import_module("Turning-Good-Agent.sessions.store")
trace_module = importlib.import_module("Turning-Good-Agent.observability.trace")
messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
llm_types_module = importlib.import_module("Turning-Good-Agent.llm.types")
runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
manager_module = importlib.import_module("Turning-Good-Agent.sessions.manager")
principal_module = importlib.import_module("Turning-Good-Agent.runtime.principal_context")

JsonlSessionStore = store_module.JsonlSessionStore
StateTrace = trace_module.StateTrace
ChannelRoute = messages_module.ChannelRoute
InboundMessage = messages_module.InboundMessage
LLMResponse = llm_types_module.LLMResponse
LLMUsage = llm_types_module.LLMUsage
ToolCall = llm_types_module.ToolCall
AgentRuntime = runtime_module.AgentRuntime
Settings = settings_module.Settings
SessionManager = manager_module.SessionManager
PrincipalSessionReader = principal_module.PrincipalSessionReader


# 构造父 Session 中可安全落盘的 Multi-Agent trace 事件。
def _multi_trace(
    session_id: str,
    event: str,
    *,
    run_id: str = "run-1",
    node_id: str | None = None,
    status: str = "completed",
    strategy: str = "fan_out_fan_in",
    content: str | None = None,
    usage: dict[str, int] | None = None,
    error: str | None = None,
) -> StateTrace:
    metadata: dict[str, Any] = {
        "run_id": run_id,
        "node_id": node_id,
        "task_label": node_id or "multi_agent",
        "strategy": strategy,
        "status": status,
        "duration_ms": 4.0,
        "usage": usage,
        "error": error,
        "content": content,
    }
    return StateTrace(
        turn_id="turn-1",
        session_id=session_id,
        state="MULTI_AGENT",
        duration_ms=4.0,
        event=event,
        error=error,
        metadata=metadata,
    )


# 创建一个父 Session 并写入一轮最终用户/assistant 消息。
async def _create_parent_session(store: JsonlSessionStore, session_id: str = "session-1") -> None:
    route = ChannelRoute("local-user", "web", "conversation-1", session_id)
    await store.create_session(session_id, route)
    await store.save_message(session_id, "user", "拆分任务", 3, message_id="user-1")
    await store.save_message(session_id, "assistant", "父 Agent 答复", 4, message_id="assistant-1")


# 写入一个已完成的单 Worker fan-out Run 及其 Worker 结果。
async def _seed_completed_run(
    store: JsonlSessionStore,
    session_id: str = "session-1",
) -> None:
    await store.save_trace(
        _multi_trace(session_id, "multi_agent.run.started", status="queued")
    )
    await store.save_trace(
        _multi_trace(
            session_id,
            "multi_agent.node.updated",
            node_id="verify",
            content="安全审阅结论",
            usage={"input_tokens": 2, "output_tokens": 3, "turn_total_tokens": 5},
        )
    )
    await store.save_trace(
        _multi_trace(
            session_id,
            "multi_agent.run.completed",
            status="completed",
        )
    )


# 运行真实父 AgentLoop 的一次 fan-out 委派，供目录与账本断言复用。
async def _run_one_multi_agent_turn(
    tmp_path: Path,
    *,
    mode: str = "auto",
) -> tuple[JsonlSessionStore, str, "ScriptedLLM"]:
    settings = Settings(data_dir=tmp_path / ".sessions")
    settings.multi_agent.enabled = True
    settings.llm.streaming_enabled = False

    class ScriptedLLM:
        # 保存父规划、Worker 和父 synthesis 的调用次数。
        def __init__(self) -> None:
            self.calls = 0

        # 按消息边界返回固定的委派回路响应。
        async def complete(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(
                    "",
                    [
                        ToolCall(
                            "delegate-1",
                            "delegate_multi_agent",
                            {
                                "strategy": "fan_out_fan_in",
                                "tasks": [
                                    {
                                        "role": "reviewer",
                                        "brief": "核对事实",
                                    }
                                ],
                            },
                        )
                    ],
                    LLMUsage(2, 2, 4),
                )
            if any(
                isinstance(item, dict)
                and str(item.get("content", "")).startswith("Worker role")
                for item in messages
            ):
                return LLMResponse("Worker 结论", [], LLMUsage(3, 2, 5))
            return LLMResponse("父 Agent 答复", [], LLMUsage(4, 3, 7))

    llm = ScriptedLLM()
    runtime = AgentRuntime.create_default(settings, llm)
    route = ChannelRoute("local-user", "web", "conversation-1", "session-1")
    inbound = InboundMessage.new(
        "拆分任务",
        route=route,
    )
    inbound.metadata["multi_agent_mode"] = mode
    await runtime.run_turn(inbound)
    return runtime.sessions.store, route.session_id, llm


# 验证 Multi-Agent turn 只使用父 Session 文件布局和父消息。
def test_multi_agent_turn_keeps_parent_session_layout(tmp_path: Path) -> None:
    # 执行真实父 turn 并检查父目录布局。
    async def run() -> None:
        store, session_id, _llm = await _run_one_multi_agent_turn(tmp_path)
        session_dirs = [path for path in (tmp_path / ".sessions").iterdir() if path.is_dir()]
        assert len(session_dirs) == 1
        session_dir = session_dirs[0]
        assert (session_dir / "messages.jsonl").exists()
        assert (session_dir / "turn_traces.jsonl").exists()
        assert (session_dir / "true_token_usage.jsonl").exists()
        assert not (session_dir / "multi_agent.jsonl").exists()
        assert len(list((tmp_path / ".sessions").rglob("messages.jsonl"))) == 1
        messages = await store.all_messages(session_id)
        assert [(item.role, item.content) for item in messages] == [
            ("user", "拆分任务"),
            ("assistant", "父 Agent 答复"),
        ]
        assert await store.all_tool_calls(session_id) == []

    asyncio.run(run())


# 验证 Multi-Agent trace 只落安全结构化字段且保留父 Session 终态。
def test_multi_agent_trace_is_safe_and_parent_scoped(tmp_path: Path) -> None:
    # 执行真实父 turn 并检查结构化 trace。
    async def run() -> None:
        store, session_id, _llm = await _run_one_multi_agent_turn(tmp_path)
        traces = [row for row in await store.all_turn_traces(session_id) if row["state"] == "MULTI_AGENT"]
        assert traces
        allowed = {
            "run_id",
            "node_id",
            "task_label",
            "strategy",
            "status",
            "duration_ms",
            "parent_request_id",
            "usage",
            "error_code",
            "error",
            "content",
        }
        assert all(set(row["metadata"]) <= allowed for row in traces)
        removed = {
            "schema_version",
            "agent_id",
            "parent_agent_id",
            "parent_node_id",
            "node_kind",
            "mode",
            "partial",
        }
        assert all(removed.isdisjoint(row["metadata"]) for row in traces)
        assert all("brief" not in row["metadata"] for row in traces)
        assert all("prompt" not in row["metadata"] for row in traces)
        assert all("reasoning" not in row["metadata"] for row in traces)
        assert all("raw_tool_output" not in row["metadata"] for row in traces)
        assert any(row["event"] == "multi_agent.run.completed" for row in traces)
        completed_node = next(
            row
            for row in traces
            if row["event"] == "multi_agent.node.updated"
            and row["metadata"].get("status") == "completed"
        )
        assert completed_node["metadata"]["content"] == "Worker 结论"

    asyncio.run(run())


# 验证真实父请求标识只进入父 trace 和安全 Run 读模型。
def test_multi_agent_parent_request_id_is_persisted_in_parent_projection(tmp_path: Path) -> None:
    # 执行真实父 turn，并以同一父 request_id 对照 trace 与摘要。
    async def run() -> None:
        store, session_id, _llm = await _run_one_multi_agent_turn(tmp_path)
        traces = [row for row in await store.all_turn_traces(session_id) if row["state"] == "MULTI_AGENT"]
        parent_request_ids = {
            row["metadata"].get("parent_request_id")
            for row in traces
            if isinstance(row.get("metadata"), dict)
        }
        assert len(parent_request_ids) == 1
        parent_request_id = next(iter(parent_request_ids))
        assert isinstance(parent_request_id, str) and parent_request_id
        run_view = (await store.read_multi_agent_runs(session_id))[0]
        assert run_view["parent_request_id"] == parent_request_id

    asyncio.run(run())


# 验证最终 Worker 结论只作为父 trace 的安全内部字段保存。
def test_completed_worker_content_is_available_to_parent_trace_read_model(tmp_path: Path) -> None:
    # 执行真实父 turn 并读取完成节点内容。
    async def run() -> None:
        store, session_id, _llm = await _run_one_multi_agent_turn(tmp_path)
        traces = await store.all_turn_traces(session_id)
        completed = next(
            row
            for row in traces
            if row["event"] == "multi_agent.node.updated"
            and row["metadata"].get("status") == "completed"
        )
        assert completed["metadata"]["content"] == "Worker 结论"
        run_view = (await store.read_multi_agent_runs(session_id))[0]
        assert "mode" not in run_view
        assert run_view["nodes"][0]["content"] == "Worker 结论"

    asyncio.run(run())


# 验证预算内的长 Worker 内容不会在 trace 或 Run 读模型中静默截断。
def test_long_valid_worker_content_is_preserved_in_parent_read_model(tmp_path: Path) -> None:
    # 写入超过旧字符切片但低于 Worker token 预算的安全正文。
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        await _create_parent_session(store)
        content = "x" * 13_000
        await store.save_trace(
            _multi_trace(
                "session-1",
                "multi_agent.node.updated",
                node_id="verify",
                content=content,
            )
        )

        traces = await store.all_turn_traces("session-1")
        run_view = (await store.read_multi_agent_runs("session-1"))[0]

        assert traces[0]["metadata"]["content"] == content
        assert run_view["nodes"][0]["content"] == content

    asyncio.run(run())


# 验证 Worker、Parent 账本标签和累计 total_tokens 单调递增。
def test_tagged_usage_is_monotonic_and_keeps_legacy_fields(tmp_path: Path) -> None:
    # 写入 Worker 与 Parent 两条账本记录并检查累计值。
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        await _create_parent_session(store)
        await store.save_true_token_usage(
            "worker-turn",
            "session-1",
            {
                "input_tokens": 2,
                "output_tokens": 3,
                "turn_total_tokens": 5,
                "total_tokens": 5,
                "compacted": 0,
                "run_id": "run-1",
                "node_id": "verify",
                "agent_id": "worker-run-1-verify",
                "scope": "worker",
            },
        )
        await store.save_true_token_usage(
            "parent-turn",
            "session-1",
            {
                "input_tokens": 4,
                "output_tokens": 3,
                "turn_total_tokens": 7,
                "total_tokens": 12,
                "compacted": 0,
                "run_id": "run-1",
                "node_id": None,
                "agent_id": "parent",
                "scope": "parent",
            },
        )
        rows = await store.all_true_token_usage("session-1")
        assert [row["scope"] for row in rows] == ["worker", "parent"]
        assert [row["total_tokens"] for row in rows] == [5, 12]
        for row in rows:
            assert {
                "input_tokens",
                "output_tokens",
                "turn_total_tokens",
                "total_tokens",
                "compacted",
            } <= row.keys()
        assert rows[0]["run_id"] == "run-1"
        assert rows[0]["node_id"] == "verify"
        assert rows[1]["agent_id"] == "parent"

    asyncio.run(run())


# 验证历史读取按 Run 聚合并限制结果规模，不暴露 Worker 提示词。
def test_read_multi_agent_runs_returns_bounded_parent_projection(tmp_path: Path) -> None:
    # 写入历史 Run 并读取有限摘要。
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        await _create_parent_session(store)
        await _seed_completed_run(store)
        runs = await store.read_multi_agent_runs("session-1", limit=1)
        assert len(runs) == 1
        run_view = runs[0]
        assert run_view["run_id"] == "run-1"
        assert run_view["status"] == "completed"
        assert "mode" not in run_view
        assert run_view["strategy"] == "fan_out_fan_in"
        assert run_view["nodes"][0]["node_id"] == "verify"
        assert run_view["nodes"][0]["content"] == "安全审阅结论"
        assert "brief" not in run_view
        assert "prompt" not in run_view
        assert "reasoning" not in run_view

    asyncio.run(run())


# 验证主体 Session Reader 透传 Multi-Agent 历史并保持主体隔离。
def test_principal_reader_scopes_multi_agent_history(tmp_path: Path) -> None:
    # 通过主体 Reader 读取自己的 Run 并拒绝其它主体。
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        manager = SessionManager(store)
        await store.create_session("session-1", ChannelRoute("owner", "web", "c1", "session-1"))
        await store.create_session("session-2", ChannelRoute("other", "web", "c2", "session-2"))
        await _seed_completed_run(store, "session-1")
        reader = PrincipalSessionReader(manager, "owner")
        runs = await reader.read_multi_agent_runs("session-1", limit=10)
        assert runs[0]["run_id"] == "run-1"
        with pytest.raises(PermissionError):
            await reader.read_multi_agent_runs("session-2", limit=10)

    asyncio.run(run())


# 验证重启恢复仅中断未终态 Run，且按 run_id 幂等保留已完成节点。
def test_recover_interrupted_runs_is_idempotent_and_does_not_replay_llm(tmp_path: Path) -> None:
    # 写入活跃 Run 两次恢复并检查唯一中断终态。
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        await _create_parent_session(store)
        await store.save_trace(
            _multi_trace("session-1", "multi_agent.run.started", run_id="run-restart", status="queued")
        )
        await store.save_trace(
            _multi_trace(
                "session-1",
                "multi_agent.node.updated",
                run_id="run-restart",
                node_id="done",
                status="completed",
                content="保留的结果",
            )
        )
        await store.save_trace(
            _multi_trace(
                "session-1",
                "multi_agent.node.updated",
                run_id="run-restart",
                node_id="active",
                status="running",
            )
        )
        await store.recover_interrupted_runs()
        await store.recover_interrupted_runs()
        traces = await store.all_turn_traces("session-1")
        events = [row["event"] for row in traces if row["metadata"].get("run_id") == "run-restart"]
        assert events.count("multi_agent.node.updated") == 3
        assert events.count("multi_agent.run.failed") == 1
        assert any(
            row["event"] == "multi_agent.run.failed"
            and row["metadata"]["status"] == "interrupted"
            for row in traces
        )
        assert "保留的结果" in str(traces)
        assert not (tmp_path / "multi_agent.jsonl").exists()

    asyncio.run(run())


# 验证真实委派把 Worker 和父 Agent 的 usage 分别写入同一父账本。
def test_real_multi_agent_usage_is_tagged_by_worker_and_parent(tmp_path: Path) -> None:
    # 运行真实委派回路并读取共享 token ledger。
    async def run() -> None:
        store, session_id, _llm = await _run_one_multi_agent_turn(tmp_path)
        rows = await store.all_true_token_usage(session_id)
        assert [row["scope"] for row in rows] == ["worker", "parent"]
        assert rows[0]["run_id"] == rows[1]["run_id"]
        assert rows[0]["node_id"] == "worker-1"
        assert rows[0]["agent_id"].startswith("worker-")
        assert rows[1]["node_id"] is None
        assert rows[1]["agent_id"] == "parent"
        assert [row["total_tokens"] for row in rows] == [5, 16]

    asyncio.run(run())


# 验证单独启动 Runtime 不会把热替换误判为 Gateway 重启。
def test_runtime_start_does_not_recover_interrupted_multi_agent_runs(tmp_path: Path) -> None:
    # 写入活跃 Run 后启动 Runtime，并保留恢复职责给 Gateway Host。
    async def run() -> None:
        settings = Settings(data_dir=tmp_path / ".sessions")
        runtime = AgentRuntime.create_default(settings, object())
        runtime.skills.scan = lambda: None

        async def start_without_mcp(*_args: object) -> None:
            return None

        runtime.mcp.start_background = start_without_mcp
        store = runtime.sessions.store
        await _create_parent_session(store)
        await store.save_trace(
            _multi_trace("session-1", "multi_agent.run.started", run_id="run-start", status="queued")
        )
        await runtime.start()
        traces = await store.all_turn_traces("session-1")
        events = [row["event"] for row in traces if row["metadata"].get("run_id") == "run-start"]
        assert events == ["multi_agent.run.started"]

    asyncio.run(run())


# 验证 Gateway 在取得实例锁后恢复未终态 Run。
def test_gateway_start_recovers_interrupted_multi_agent_runs(tmp_path: Path) -> None:
    # 通过真实 Host 生命周期检查恢复只发生在 Gateway 启动阶段。
    async def run() -> None:
        gateway_module = importlib.import_module("Turning-Good-Agent.gateway.host")
        settings = Settings(data_dir=tmp_path / ".sessions")
        settings.local_config_path = tmp_path / "settings.local.json"
        settings.proactive.enabled = False
        runtime = AgentRuntime.create_default(settings, object())
        runtime.skills.scan = lambda: None

        async def start_without_mcp(*_args: object) -> None:
            return None

        runtime.mcp.start_background = start_without_mcp
        store = runtime.sessions.store
        await _create_parent_session(store)
        await store.save_trace(
            _multi_trace("session-1", "multi_agent.run.started", run_id="run-host-start", status="queued")
        )
        host = gateway_module.GatewayHost(settings, runtime=runtime)
        await host.start()
        try:
            traces = await store.all_turn_traces("session-1")
            events = [
                row["event"]
                for row in traces
                if row["metadata"].get("run_id") == "run-host-start"
            ]
            assert events == ["multi_agent.run.started", "multi_agent.run.failed"]
        finally:
            await host.close()

    asyncio.run(run())


# 验证历史读取按 run_id 隔离 Worker、Parent 与总用量聚合。
def test_read_multi_agent_runs_scopes_usage_to_its_run(tmp_path: Path) -> None:
    # 写入两个 Run 的独立账本并比较各自聚合。
    async def run() -> None:
        store = JsonlSessionStore(tmp_path)
        await _create_parent_session(store)
        await _seed_completed_run(store)
        await store.save_trace(
            _multi_trace("session-1", "multi_agent.run.started", run_id="run-2", status="queued")
        )
        await store.save_trace(
            _multi_trace("session-1", "multi_agent.run.completed", run_id="run-2", status="completed")
        )
        for turn_id, run_id, scope, node_id, total in (
            ("worker-1", "run-1", "worker", "verify", 5),
            ("parent-1", "run-1", "parent", None, 7),
            ("worker-2", "run-2", "worker", "verify", 11),
            ("parent-2", "run-2", "parent", None, 13),
        ):
            await store.save_true_token_usage(
                turn_id,
                "session-1",
                {
                    "input_tokens": total - 1,
                    "output_tokens": 1,
                    "turn_total_tokens": total,
                    "total_tokens": total,
                    "compacted": 0,
                    "run_id": run_id,
                    "node_id": node_id,
                    "agent_id": f"{scope}-{run_id}",
                    "scope": scope,
                },
            )
        views = {item["run_id"]: item for item in await store.read_multi_agent_runs("session-1", limit=10)}
        assert set(views["run-1"]["usage"]) == {"worker", "total"}
        assert views["run-1"]["usage"]["worker"]["turn_total_tokens"] == 5
        assert views["run-1"]["usage"]["total"]["turn_total_tokens"] == 12
        assert views["run-2"]["usage"]["worker"]["turn_total_tokens"] == 11
        assert views["run-2"]["usage"]["total"]["turn_total_tokens"] == 24

    asyncio.run(run())


# 验证既有 observability 端点返回有界的父 Session Run 摘要。
def test_observability_includes_multi_agent_run_summaries(tmp_path: Path) -> None:
    # 创建 Web 父 Session 后通过真实 REST 端点读取摘要。
    async def seed() -> tuple[Settings, AgentRuntime]:
        settings = Settings(data_dir=tmp_path / ".sessions")
        settings.local_config_path = tmp_path / "settings.local.json"
        settings.local_config_path.write_text("{}", encoding="utf-8")
        settings.proactive.enabled = False
        runtime = AgentRuntime.create_default(settings, object())
        await _create_parent_session(runtime.sessions.store)
        await _seed_completed_run(runtime.sessions.store)
        return settings, runtime

    gateway_module = importlib.import_module("Turning-Good-Agent.gateway.host")
    app_module = importlib.import_module("Turning-Good-Agent.web.backend.app")
    settings, runtime = asyncio.run(seed())
    app = app_module.create_app(gateway_module.GatewayHost(settings, runtime=runtime))
    with TestClient(app) as client:
        response = client.get("/api/sessions/session-1/observability")
    assert response.status_code == 200
    runs = response.json()["multi_agent_runs"]
    assert len(runs) == 1
    assert runs[0]["run_id"] == "run-1"
    assert runs[0]["strategy"] == "fan_out_fan_in"
    assert {"mode", "partial", "parent_agent_id"}.isdisjoint(runs[0])
    assert {"task_id", "agent_id"}.isdisjoint(runs[0]["nodes"][0])
    assert runs[0]["error_code"] is None
    assert runs[0]["nodes"][0]["error_code"] is None

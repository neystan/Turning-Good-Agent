from __future__ import annotations

import asyncio
import importlib
from pathlib import Path


messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
llm_types_module = importlib.import_module("Turning-Good-Agent.llm.types")
runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
memory_module = importlib.import_module("Turning-Good-Agent.memory.long_term")
multi_agent_types_module = importlib.import_module("Turning-Good-Agent.multi_agent.types")

ChannelRoute = messages_module.ChannelRoute
InboundMessage = messages_module.InboundMessage
LLMResponse = llm_types_module.LLMResponse
LLMUsage = llm_types_module.LLMUsage
ToolCall = llm_types_module.ToolCall
AgentRuntime = runtime_module.AgentRuntime
Settings = settings_module.Settings
ProfileMemorySnapshot = memory_module.ProfileMemorySnapshot
DelegateParentTurn = multi_agent_types_module.DelegateParentTurn


# 构造固定的多 Worker 委派参数。
def _delegate_payload(strategy: str, tasks: list[tuple[str, str]]) -> dict[str, object]:
    return {
        "strategy": strategy,
        "tasks": [
            {"role": role, "brief": brief}
            for role, brief in tasks
        ],
    }


# 验证模式、全局开关和 IM 路由只在可信父入口暴露委派能力。
def test_mode_matrix_keeps_disabled_off_and_im_turns_single_agent(tmp_path: Path) -> None:
    # 逐项执行入口模式矩阵。
    async def run() -> None:
        observed: list[tuple[str, bool]] = []

        class Llm:
            # 保存每个父 turn 的特殊能力可见性。
            def __init__(self, label: str) -> None:
                self.label = label

            # 返回不调用委派工具的确定性父回复。
            async def complete(self, messages, tools):
                names = {
                    item["function"]["name"]
                    for item in tools
                    if isinstance(item, dict) and isinstance(item.get("function"), dict)
                }
                observed.append((self.label, "delegate_multi_agent" in names))
                return LLMResponse("single-agent", [], LLMUsage(1, 1, 2))

        cases = (
            ("enabled-auto", True, "web", "auto"),
            ("enabled-off", True, "web", "off"),
            ("disabled-auto", False, "web", "auto"),
            ("im-auto", True, "feishu", "auto"),
        )
        for label, enabled, channel, mode in cases:
            settings = Settings(data_dir=tmp_path / label / ".sessions")
            settings.multi_agent.enabled = enabled
            settings.llm.streaming_enabled = False
            runtime = AgentRuntime.create_default(settings, Llm(label))
            route = ChannelRoute("local-user", channel, label, f"session-{label}")
            inbound = InboundMessage.new("simple request", route=route)
            inbound.metadata["multi_agent_mode"] = mode
            outbound = await runtime.run_turn(inbound)
            assert outbound.content == "single-agent"
            assert await runtime.sessions.store.read_multi_agent_runs(route.session_id) == []

        settings = Settings(data_dir=tmp_path / "invalid" / ".sessions")
        settings.multi_agent.enabled = True
        settings.llm.streaming_enabled = False
        invalid_llm = Llm("invalid")
        runtime = AgentRuntime.create_default(settings, invalid_llm)
        route = ChannelRoute("local-user", "web", "invalid", "session-invalid")
        inbound = InboundMessage.new("invalid mode", route=route)
        for invalid_mode in ("invalid", "on"):
            inbound.metadata["multi_agent_mode"] = invalid_mode
            outbound = await runtime.run_turn(inbound)
            assert "multi_agent_mode 必须是 auto 或 off" in outbound.content
        assert all(label != "invalid" for label, _ in observed)

        assert observed == [
            ("enabled-auto", True),
            ("enabled-off", False),
            ("disabled-auto", False),
            ("im-auto", False),
        ]

    asyncio.run(run())


# 验证真实 Runtime 的 fan-out 部分成功只写父 Session、聚合安全事件和账本。
def test_real_fanout_turn_persists_only_parent_safe_projection(tmp_path: Path) -> None:
    # 运行包含一个成功和一个失败 Worker 的父 turn。
    async def run() -> None:
        private_parent_text = "parent-private-history-must-not-reach-worker"

        class ScriptedLlm:
            # 保存 Worker 收到的逻辑上下文。
            def __init__(self) -> None:
                self.worker_inputs: list[str] = []
                self.parent_calls = 0

            # 驱动父规划、两个 Worker 和最终 synthesis。
            async def complete(self, messages, tools):
                text = "\n".join(
                    str(item.get("content", ""))
                    for item in messages
                    if isinstance(item, dict)
                )
                if "Worker role：" in text:
                    self.worker_inputs.append(text)
                    assert private_parent_text not in text
                    if "failure brief" in text:
                        raise RuntimeError("deterministic worker failure")
                    return LLMResponse("worker evidence", [], LLMUsage(2, 1, 3))
                self.parent_calls += 1
                if self.parent_calls == 1:
                    return LLMResponse(
                        "",
                        [
                            ToolCall(
                                "delegate-fanout",
                                "delegate_multi_agent",
                                _delegate_payload(
                                    "fan_out_fan_in",
                                    [
                                        ("researcher", "safe brief"),
                                        ("reviewer", "failure brief"),
                                    ],
                                ),
                            )
                        ],
                        LLMUsage(2, 1, 3),
                    )
                assert "worker evidence" in text
                return LLMResponse("parent synthesis", [], LLMUsage(3, 2, 5))

        settings = Settings(data_dir=tmp_path / ".sessions")
        settings.multi_agent.enabled = True
        settings.llm.streaming_enabled = False
        llm = ScriptedLlm()
        runtime = AgentRuntime.create_default(settings, llm)
        route = ChannelRoute("local-user", "web", "analysis", "parent-session")
        inbound = InboundMessage.new(private_parent_text, route=route)
        inbound.metadata["multi_agent_mode"] = "auto"
        outbound = await runtime.run_turn(inbound)

        store = runtime.sessions.store
        assert outbound.content == "parent synthesis"
        assert len(llm.worker_inputs) == 2
        messages = await store.all_messages(route.session_id)
        assert [(item.role, item.content) for item in messages] == [
            ("user", private_parent_text),
            ("assistant", "parent synthesis"),
        ]
        assert await store.all_tool_calls(route.session_id) == []
        runs = await store.read_multi_agent_runs(route.session_id)
        assert len(runs) == 1
        run_view = runs[0]
        assert run_view["strategy"] == "fan_out_fan_in"
        assert run_view["status"] == "completed"
        assert "partial" not in run_view
        assert [node["status"] for node in run_view["nodes"]] == ["completed", "failed"]
        assert run_view["usage"]["worker"]["turn_total_tokens"] == 3
        assert run_view["usage"]["total"]["turn_total_tokens"] == 11
        coordinator_module = importlib.import_module("Turning-Good-Agent.web.backend.coordinator")
        bus_module = importlib.import_module("Turning-Good-Agent.bus.queue")

        # 接受该只读快照测试的虚拟请求。
        async def submit(*_args, **_kwargs):
            return True

        # 忽略该只读快照测试的虚拟完成回调。
        async def complete_route_turn(*_args):
            return None

        # 返回已完成真实父 turn 的 Runtime。
        def runtime_provider():
            return runtime

        web_coordinator = coordinator_module.WebSessionCoordinator(
            submit=submit,
            complete_route_turn=complete_route_turn,
            runtime_provider=runtime_provider,
            principal_id="local-user",
            event_buffer_size=16,
            bus=bus_module.AsyncMessageBus(),
        )
        snapshot = await web_coordinator.snapshot(route.session_id)
        assert "partial" not in snapshot["multi_agent_runs"][0]
        traces = await store.all_turn_traces(route.session_id)
        multi_agent_traces = [row for row in traces if row["state"] == "MULTI_AGENT"]
        assert any(row["event"] == "multi_agent.run.completed" for row in multi_agent_traces)
        assert private_parent_text not in str(multi_agent_traces)
        session_dir = next(path for path in settings.data_dir.iterdir() if path.is_dir())
        names = {path.name for path in session_dir.iterdir()}
        assert {"messages.jsonl", "turn_traces.jsonl", "true_token_usage.jsonl"} <= names
        assert not any("worker" in path.name or "multi_agent" in path.name for path in session_dir.rglob("*"))

    asyncio.run(run())


# 验证非 Owner 从 CLI 或 Web 委派时 Worker 只接收该主体的长期画像。
def test_non_owner_cli_and_web_workers_receive_only_principal_profile_memory(tmp_path: Path) -> None:
    # 分别执行两个可信父入口的真实委派。
    async def run() -> None:
        class ScriptedLlm:
            # 保存 Worker 实际收到的 LLM 输入。
            def __init__(self) -> None:
                self.worker_inputs: list[str] = []
                self.parent_calls: dict[str, int] = {}

            # 驱动父委派、Worker 和父汇总的固定交互。
            async def complete(self, messages, tools):
                await asyncio.sleep(0)
                text = "\n".join(
                    str(item.get("content", ""))
                    for item in messages
                    if isinstance(item, dict)
                )
                if "Worker role：" in text:
                    self.worker_inputs.append(text)
                    return LLMResponse("worker evidence", [], LLMUsage(2, 1, 3))
                channel = "cli" if "cli delegation" in text else "web"
                parent_calls = self.parent_calls.get(channel, 0)
                self.parent_calls[channel] = parent_calls + 1
                if parent_calls == 0:
                    return LLMResponse(
                        "",
                        [
                            ToolCall(
                                "delegate-fanout",
                                "delegate_multi_agent",
                                _delegate_payload(
                                    "fan_out_fan_in",
                                    [("reviewer", "verify profile isolation")],
                                ),
                            )
                        ],
                        LLMUsage(2, 1, 3),
                    )
                return LLMResponse("parent synthesis", [], LLMUsage(3, 2, 5))

        settings = Settings(data_dir=tmp_path / ".sessions")
        settings.gateway.principal_id = "owner"
        settings.multi_agent.enabled = True
        settings.llm.streaming_enabled = False
        llm = ScriptedLlm()
        runtime = AgentRuntime.create_default(settings, llm)
        owner_route = ChannelRoute("owner", "web", "owner", "owner-session")
        owner_context = runtime.resolve_principal_context(owner_route)
        owner_context.profile_memory.write(
            ProfileMemorySnapshot(soul="owner-soul", user="owner-user")
        )

        inbounds: list[InboundMessage] = []
        for channel in ("cli", "web"):
            guest_route = ChannelRoute(f"guest-{channel}", channel, channel, f"guest-{channel}-session")
            guest_context = runtime.resolve_principal_context(guest_route)
            guest_context.profile_memory.write(
                ProfileMemorySnapshot(soul=f"{channel}-guest-soul", user=f"{channel}-guest-user")
            )
            inbound = InboundMessage.new(f"{channel} delegation", route=guest_route)
            inbound.metadata["multi_agent_mode"] = "auto"
            inbounds.append(inbound)

        outbounds = await asyncio.gather(*(runtime.run_turn(inbound) for inbound in inbounds))
        assert [outbound.content for outbound in outbounds] == ["parent synthesis", "parent synthesis"]

        cli_input = next(worker_input for worker_input in llm.worker_inputs if "cli-guest-soul" in worker_input)
        web_input = next(worker_input for worker_input in llm.worker_inputs if "web-guest-soul" in worker_input)
        assert "cli-guest-user" in cli_input
        assert "web-guest-user" not in cli_input
        assert "web-guest-user" in web_input
        assert "cli-guest-user" not in web_input
        assert "owner-soul" not in cli_input + web_input
        assert "owner-user" not in cli_input + web_input

        assert len(llm.worker_inputs) == 2

    asyncio.run(run())


# 验证固定拓扑、依赖取消、并发上限和 Worker deadline 的组合边界。
def test_topology_resource_and_deadline_matrix() -> None:
    # 在内存 Coordinator 上执行确定性资源矩阵。
    async def run() -> None:
        coordinator_module = importlib.import_module("Turning-Good-Agent.multi_agent.coordinator")
        schema_module = importlib.import_module("Turning-Good-Agent.multi_agent.schema")
        types_module = importlib.import_module("Turning-Good-Agent.multi_agent.types")
        class FanoutRunner:
            # 记录 fan-out 活跃数并制造一个确定性失败。
            def __init__(self) -> None:
                self.active = 0
                self.maximum = 0

            # 并行完成第一个 Worker 并失败第二个 Worker。
            async def run(self, task, dependency_content=None, *, run_id, node_id, profile_memory):
                del run_id, node_id, profile_memory
                assert dependency_content is None
                self.active += 1
                self.maximum = max(self.maximum, self.active)
                try:
                    await asyncio.sleep(0.02)
                    if task.brief == "bad":
                        return types_module.WorkerResult("failed", None, "expected failure")
                    return types_module.WorkerResult("completed", f"{task.brief} result")
                finally:
                    self.active -= 1

        fanout_runner = FanoutRunner()
        fanout = coordinator_module.MultiAgentCoordinator(
            worker_runner=fanout_runner,
            max_concurrent_workers_per_run=2,
            max_concurrent_workers_global=2,
        )
        fanout_parent = DelegateParentTurn(
            session_id="fanout-session",
            request_id="fanout-request",
            profile_memory=ProfileMemorySnapshot(),
            initial_context_tokens=0,
            is_stop_requested=lambda: False,
        )
        fanout_request = schema_module.validate_delegate_request(
            _delegate_payload(
                "fan_out_fan_in",
                [("reader", "good"), ("reader", "bad")],
            )
        )
        fanout_run = await fanout.delegate(fanout_request, fanout_parent)
        assert fanout_runner.maximum == 2
        assert fanout_run.status == "running"
        assert [node.node_id for node in fanout_run.nodes] == ["worker-1", "worker-2"]
        assert [node.status for node in fanout_run.nodes] == ["completed", "failed"]

        class PipelineRunner:
            # 保存唯一相邻依赖并在第二个节点失败。
            def __init__(self) -> None:
                self.dependencies: list[tuple[str, str | None]] = []

            # 验证顺序依赖后返回受控结果。
            async def run(self, task, dependency_content=None, *, run_id, node_id, profile_memory):
                del run_id, node_id, profile_memory
                self.dependencies.append((task.brief, dependency_content))
                if task.brief == "second":
                    return types_module.WorkerResult("failed", None, "expected failure")
                return types_module.WorkerResult("completed", f"{task.brief} result")

        pipeline_runner = PipelineRunner()
        pipeline = coordinator_module.MultiAgentCoordinator(worker_runner=pipeline_runner)
        pipeline_parent = DelegateParentTurn(
            session_id="pipeline-session",
            request_id="pipeline-request",
            profile_memory=ProfileMemorySnapshot(),
            initial_context_tokens=0,
            is_stop_requested=lambda: False,
        )
        pipeline_request = schema_module.validate_delegate_request(
            _delegate_payload(
                "pipeline",
                [
                    ("reader", "first"),
                    ("writer", "second"),
                    ("reviewer", "third"),
                ],
            )
        )
        pipeline_run = await pipeline.delegate(pipeline_request, pipeline_parent)
        assert pipeline_run.status == "failed"
        assert [node.status for node in pipeline_run.nodes] == ["completed", "failed", "cancelled"]
        assert pipeline_runner.dependencies == [("first", None), ("second", "first result")]

        class SlowRunner:
            # 让 Worker 超过固定 deadline。
            async def run(self, task, dependency_content=None, *, run_id, node_id, profile_memory):
                del task, dependency_content, run_id, node_id, profile_memory
                await asyncio.sleep(0.1)
                return types_module.WorkerResult("completed", "late")

        deadline = coordinator_module.MultiAgentCoordinator(
            worker_runner=SlowRunner(),
            run_timeout_seconds=1,
            worker_timeout_seconds=0.01,
        )
        deadline_parent = DelegateParentTurn(
            session_id="deadline-session",
            request_id="deadline-request",
            profile_memory=ProfileMemorySnapshot(),
            initial_context_tokens=0,
            is_stop_requested=lambda: False,
        )
        deadline_request = schema_module.validate_delegate_request(
            _delegate_payload("fan_out_fan_in", [("reviewer", "verify")])
        )
        deadline_run = await deadline.delegate(deadline_request, deadline_parent)
        assert deadline_run.status == "failed"
        assert deadline_run.nodes[0].status == "timed_out"

    asyncio.run(run())


# 验证 Web/CLI 共享安全投影字段，IM 永久丢弃 Multi-Agent 中间事件。
def test_channel_projection_matrix_keeps_im_permanently_off() -> None:
    # 使用现有 Channel Adapter 投影同一个 Run 节点事件。
    async def run() -> None:
        bus_module = importlib.import_module("Turning-Good-Agent.bus.queue")
        cli_module = importlib.import_module("Turning-Good-Agent.channels.cli_gateway")
        im_module = importlib.import_module("Turning-Good-Agent.channels.im")
        web_module = importlib.import_module("Turning-Good-Agent.channels.web")

        bus = bus_module.AsyncMessageBus()
        route = ChannelRoute("local-user", "cli", "analysis", "cli-session")
        control = cli_module.CliTurnControl("cli-session", "cli-request", route)
        cli_adapter = cli_module.CliGatewayAdapter(bus, control)
        payload = {
            "run_id": "run-1",
            "node_id": "verify",
            "task_label": "reviewer",
            "strategy": "fan_out_fan_in",
            "status": "completed",
            "duration_ms": 3.0,
            "usage": {"turn_total_tokens": 5},
            "error_code": None,
            "error": None,
            "content": "must not project",
            "raw_prompt": "must not project",
        }
        await cli_adapter.on_multi_agent_event("multi_agent.node.updated", payload)
        cli_event = await asyncio.wait_for(bus.consume_outbound(), timeout=1)
        assert cli_event.event_type == "multi_agent.node.updated"
        assert "content" not in cli_event.metadata
        assert "raw_prompt" not in cli_event.metadata
        assert cli_event.metadata["run_id"] == "run-1"

        web_route = ChannelRoute("local-user", "web", "analysis", "web-session")
        web_control = web_module.WebTurnControl("web-session", "web-request", web_route)
        web_adapter = web_module.WebChannelAdapter(bus, web_control, route=web_route)
        await web_adapter.on_multi_agent_event("multi_agent.node.updated", payload)
        web_event = await asyncio.wait_for(bus.consume_outbound(), timeout=1)
        assert web_event.event_type == "multi_agent.node.updated"
        assert web_event.metadata == cli_event.metadata | {"request_id": "web-request"}

        im_route = ChannelRoute("local-user", "feishu", "analysis", "im-session")
        im_adapter = im_module.ImChannelAdapter(bus, im_module.ImTurnControl("im-request", im_route))
        await im_adapter.on_multi_agent_event("multi_agent.node.updated", payload)
        try:
            await asyncio.wait_for(bus.consume_outbound(), timeout=0.05)
        except TimeoutError:
            pass
        else:
            raise AssertionError("IM 不应投影 Multi-Agent 事件")

    asyncio.run(run())

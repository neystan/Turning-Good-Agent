from __future__ import annotations

import asyncio
import importlib
import inspect


# 验证 thinking 工具调用的内部字段会随下一次请求回传。
def test_agent_loop_returns_reasoning_content_for_the_next_tool_turn_only() -> None:
    async def run() -> None:
        agent_loop_module = importlib.import_module("Turning-Good-Agent.runtime.agent_loop")
        builtin_module = importlib.import_module("Turning-Good-Agent.tools.builtin_tools")
        llm_types_module = importlib.import_module("Turning-Good-Agent.llm.types")
        registry_module = importlib.import_module("Turning-Good-Agent.tools.registry")
        settings_module = importlib.import_module("Turning-Good-Agent.config.settings")

        class Llm:
            # 保存工具调用后的下一次模型请求。
            def __init__(self) -> None:
                self.requests: list[list[dict[str, object]]] = []
                self.responses = [
                    llm_types_module.LLMResponse(
                        "",
                        [llm_types_module.ToolCall("call-1", "now", {})],
                        llm_types_module.LLMUsage(total_tokens=1),
                        reasoning_content="internal thinking",
                    ),
                    llm_types_module.LLMResponse(
                        "已完成总结。",
                        [],
                        llm_types_module.LLMUsage(total_tokens=1),
                    ),
                ]

            # 返回固定的工具调用和最终文本。
            async def complete(self, messages, tools):
                del tools
                self.requests.append(list(messages))
                return self.responses.pop(0)

        registry = registry_module.ToolRegistry()
        registry.register(builtin_module.NowTool())
        llm = Llm()
        result = await agent_loop_module.AgentLoop(
            llm,
            registry,
            settings_module.RuntimeSettings(max_tool_rounds=2),
        ).run([{"role": "user", "content": "现在几点"}])

        assistant_message = next(
            message
            for message in llm.requests[1]
            if message["role"] == "assistant" and "tool_calls" in message
        )
        assert assistant_message["reasoning_content"] == "internal thinking"
        assert "reasoning_content" not in result.tool_calls[0]
        assert "internal thinking" not in result.final_content

    asyncio.run(run())


# 验证临时 Registry 和 Runner 既覆盖父工具面又不会持久化 Worker 调用。
def test_per_call_tool_surface_uses_temporary_runner_without_persisting_records() -> None:
    # 执行临时工具面场景。
    async def run() -> None:
        agent_loop_module = importlib.import_module("Turning-Good-Agent.runtime.agent_loop")
        hooks_module = importlib.import_module("Turning-Good-Agent.hooks.manager")
        llm_types_module = importlib.import_module("Turning-Good-Agent.llm.types")
        runner_module = importlib.import_module("Turning-Good-Agent.runtime.tool_call_runner")
        registry_module = importlib.import_module("Turning-Good-Agent.tools.registry")
        settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
        tool_base_module = importlib.import_module("Turning-Good-Agent.tools.base")
        executor_module = importlib.import_module("Turning-Good-Agent.tools.executor")

        class ParentTool:
            name = "parent_tool"
            description = "parent"
            input_schema = {"type": "object", "properties": {}}
            parallel_safe = False

            # 验证父工具不会被临时表调用。
            async def run(self, args: dict[str, object]) -> object:
                del args
                return tool_base_module.ToolResult("parent")

        class WorkerTool:
            name = "worker_tool"
            description = "worker"
            input_schema = {"type": "object", "properties": {}}
            parallel_safe = False

            # 验证临时工具会在无 Hook 的 Runner 中执行。
            async def run(self, args: dict[str, object]) -> object:
                del args
                return tool_base_module.ToolResult("worker")

        class Llm:
            # 初始化固定模型响应。
            def __init__(self) -> None:
                self.schemas: list[list[dict[str, object]]] = []
                self.responses = [
                    llm_types_module.LLMResponse(
                        "",
                        [llm_types_module.ToolCall("worker-call", "worker_tool", {})],
                        llm_types_module.LLMUsage(total_tokens=1),
                    ),
                    llm_types_module.LLMResponse("done", [], llm_types_module.LLMUsage(total_tokens=1)),
                ]

            # 记录模型实际接收到的临时 schema。
            async def complete(self, messages, tools):
                del messages
                self.schemas.append(list(tools))
                return self.responses.pop(0)

        parent_registry = registry_module.ToolRegistry()
        parent_registry.register(ParentTool())
        temporary_registry = registry_module.ToolRegistry()
        temporary_registry.register(WorkerTool())
        settings = settings_module.RuntimeSettings(max_tool_rounds=2, parallel_tool_calls_enabled=False)
        llm = Llm()
        loop = agent_loop_module.AgentLoop(llm, parent_registry, settings)
        temporary_runner = runner_module.ToolCallRunner(
            temporary_registry, executor_module.ToolExecutor(), hooks_module.HookManager(), settings
        )

        result = await loop.run(
            [{"role": "user", "content": "worker"}],
            allowed_tool_names=frozenset({"worker_tool"}),
            tool_registry=temporary_registry,
            tool_runner=temporary_runner,
            persist_tool_calls=False,
            streaming_enabled=False,
        )

        assert result.final_content == "done"
        assert result.tool_calls == []
        assert [schema["function"]["name"] for schema in llm.schemas[0]] == ["worker_tool"]
        assert result.messages[0]["content"] == "worker"

    asyncio.run(run())


# 验证隐藏的 IM 工具调用不会进入审批或执行路径。
def test_im_allowlist_hides_and_rejects_a_forged_approval_tool() -> None:
    # 执行伪造审批工具调用场景。
    async def run() -> None:
        agent_loop_module = importlib.import_module("Turning-Good-Agent.runtime.agent_loop")
        hooks_module = importlib.import_module("Turning-Good-Agent.hooks.manager")
        llm_types_module = importlib.import_module("Turning-Good-Agent.llm.types")
        registry_module = importlib.import_module("Turning-Good-Agent.tools.registry")
        settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
        tool_base_module = importlib.import_module("Turning-Good-Agent.tools.base")

        class ApprovalTool:
            name = "write_file"
            description = "approval tool"
            input_schema = {"type": "object", "properties": {}}
            parallel_safe = False
            approval_required = True

            # 初始化调用计数器。
            def __init__(self) -> None:
                self.calls = 0

            # 记录不应发生的工具执行。
            async def run(self, args: dict[str, object]) -> object:
                del args
                self.calls += 1
                return tool_base_module.ToolResult("executed")

        class ForgingLlm:
            # 初始化伪造模型响应。
            def __init__(self) -> None:
                self.tools: list[list[dict[str, object]]] = []
                self.responses = [
                    llm_types_module.LLMResponse(
                        "",
                        [llm_types_module.ToolCall("call-1", "write_file", {})],
                        llm_types_module.LLMUsage(input_tokens=1, output_tokens=1, total_tokens=2),
                    ),
                    llm_types_module.LLMResponse(
                        "blocked", [], llm_types_module.LLMUsage(input_tokens=1, output_tokens=1, total_tokens=2)
                    ),
                ]

            # 返回隐藏工具调用和最终文本。
            async def complete(self, messages, tools):
                del messages
                self.tools.append(list(tools))
                return self.responses.pop(0)

        registry = registry_module.ToolRegistry()
        tool = ApprovalTool()
        registry.register(tool)
        llm = ForgingLlm()
        loop = agent_loop_module.AgentLoop(
            llm,
            registry,
            settings_module.RuntimeSettings(max_tool_rounds=2),
            hooks=hooks_module.HookManager(),
        )

        result = await loop.run(
            [{"role": "user", "content": "try it"}],
            auto_approve_tools=True,
            allowed_tool_names=frozenset(),
        )

        assert llm.tools[0] == []
        assert tool.calls == 0
        assert result.tool_calls[0]["error"] == "当前 Channel 不允许调用该工具"

    asyncio.run(run())


# 验证没有 invocation 的伪造委派不会进入普通 Tool 记录。
def test_forged_delegate_without_invocation_is_rejected_ephemerally() -> None:
    # 执行无委派对象的伪造调用。
    async def run() -> None:
        agent_loop_module = importlib.import_module("Turning-Good-Agent.runtime.agent_loop")
        llm_types_module = importlib.import_module("Turning-Good-Agent.llm.types")
        registry_module = importlib.import_module("Turning-Good-Agent.tools.registry")
        settings_module = importlib.import_module("Turning-Good-Agent.config.settings")

        class Llm:
            # 初始化伪造调用和最终答复。
            def __init__(self) -> None:
                self.schemas: list[list[dict[str, object]]] = []
                self.responses = [
                    llm_types_module.LLMResponse(
                        "",
                        [llm_types_module.ToolCall("delegate", "delegate_multi_agent", {})],
                        llm_types_module.LLMUsage(total_tokens=1),
                    ),
                    llm_types_module.LLMResponse("done", [], llm_types_module.LLMUsage(total_tokens=1)),
                ]

            # 保存每轮父模型可见的 schema。
            async def complete(self, messages, tools):
                del messages
                self.schemas.append(list(tools))
                return self.responses.pop(0)

        llm = Llm()
        result = await agent_loop_module.AgentLoop(
            llm, registry_module.ToolRegistry(), settings_module.RuntimeSettings(max_tool_rounds=2)
        ).run([{"role": "user", "content": "delegate"}])

        assert llm.schemas == [[], []]
        assert result.final_content == "done"
        assert result.tool_calls == []
        assert "不允许 delegate_multi_agent" in result.messages[-1]["content"]

    asyncio.run(run())


# 验证父 Agent 只通过命名委派对象使用一次 Multi-Agent Tool。
def test_named_multi_agent_invocation_is_exposed_once_and_finalized() -> None:
    # 执行父规划、委派和汇总的最小交互。
    async def run() -> None:
        agent_loop_module = importlib.import_module("Turning-Good-Agent.runtime.agent_loop")
        llm_types_module = importlib.import_module("Turning-Good-Agent.llm.types")
        registry_module = importlib.import_module("Turning-Good-Agent.tools.registry")
        settings_module = importlib.import_module("Turning-Good-Agent.config.settings")

        class Invocation:
            # 初始化仅供本父 Turn 使用的委派状态。
            def __init__(self) -> None:
                self.is_schema_visible = True
                self.calls = 0
                self.outcomes: list[str] = []

            # 返回唯一委派工具的模型 schema。
            def openai_schema(self) -> dict[str, object]:
                return {
                    "type": "function",
                    "function": {
                        "name": "delegate_multi_agent",
                        "description": "delegate",
                        "parameters": {"type": "object", "properties": {}},
                    },
                }

            # 受理委派并提供父 synthesis 可消费的有界结果。
            async def invoke(self, args) -> str:
                del args
                self.calls += 1
                self.is_schema_visible = False
                return "worker summary"

            # 声明本测试不施加额外 Run deadline。
            def deadline_monotonic(self):
                return None

            # 声明本测试的父 synthesis 上下文有效。
            async def check_parent_synthesis(self, parent_messages, parent_tools):
                del parent_messages, parent_tools
                return None

            # 记录父 Turn 的唯一收口结果。
            async def finish_parent_turn(self, outcome: str) -> None:
                self.outcomes.append(outcome)

            # 保持测试 ChannelAdapter 原样。
            def wrap_parent_approval(self, adapter):
                return adapter

        class Llm:
            # 初始化一次委派和一次父汇总响应。
            def __init__(self) -> None:
                self.schemas: list[list[dict[str, object]]] = []
                self.responses = [
                    llm_types_module.LLMResponse(
                        "",
                        [llm_types_module.ToolCall("delegate", "delegate_multi_agent", {})],
                        llm_types_module.LLMUsage(total_tokens=1),
                    ),
                    llm_types_module.LLMResponse(
                        "parent synthesis", [], llm_types_module.LLMUsage(total_tokens=1)
                    ),
                ]

            # 记录每轮父模型可见的 schema。
            async def complete(self, messages, tools):
                del messages
                self.schemas.append(list(tools))
                return self.responses.pop(0)

        invocation = Invocation()
        llm = Llm()
        loop = agent_loop_module.AgentLoop(
            llm, registry_module.ToolRegistry(), settings_module.RuntimeSettings(max_tool_rounds=2)
        )
        result = await loop.run(
            [{"role": "user", "content": "delegate"}],
            multi_agent_invocation=invocation,
        )

        assert result.final_content == "parent synthesis"
        assert result.tool_calls == []
        assert invocation.calls == 1
        assert invocation.outcomes == ["completed"]
        assert "worker summary" in result.messages[-1]["content"]
        assert [item["function"]["name"] for item in llm.schemas[0]] == ["delegate_multi_agent"]
        assert llm.schemas[1] == []

    asyncio.run(run())


# 验证 AgentLoop 不再保留泛化 capability 参数。
def test_agent_loop_exposes_only_named_multi_agent_invocation_api() -> None:
    agent_loop_module = importlib.import_module("Turning-Good-Agent.runtime.agent_loop")

    parameters = inspect.signature(agent_loop_module.AgentLoop.run).parameters

    assert "multi_agent_invocation" in parameters
    assert "special_capabilities" not in parameters
    assert "allowed_special_capability_names" not in parameters

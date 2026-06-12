import asyncio
from importlib import import_module

RuntimeSettings = import_module("Turning-Good-Agent.config.settings").RuntimeSettings
FakeLLM = import_module("Turning-Good-Agent.llm.fake").FakeLLM
AgentLoop = import_module("Turning-Good-Agent.runtime.agent_loop").AgentLoop
tools_module = import_module("Turning-Good-Agent.tools.builtin_tools")
EchoTool = tools_module.EchoTool
NowTool = tools_module.NowTool
ToolRegistry = import_module("Turning-Good-Agent.tools.registry").ToolRegistry


def test_fake_llm_returns_plain_response():
    async def run() -> None:
        registry = ToolRegistry()
        loop = AgentLoop(FakeLLM(), registry, RuntimeSettings())

        result = await loop.run([{"role": "user", "content": "hello"}])

        assert "hello" in result.final_content
        assert result.tool_calls == []

    asyncio.run(run())


def test_fake_llm_can_call_echo_tool():
    async def run() -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())
        loop = AgentLoop(FakeLLM(), registry, RuntimeSettings())

        result = await loop.run([{"role": "user", "content": "echo: abc"}])

        assert "abc" in result.final_content
        assert result.tool_calls[0]["tool_name"] == "echo"

    asyncio.run(run())


def test_fake_llm_can_call_now_tool():
    async def run() -> None:
        registry = ToolRegistry()
        registry.register(NowTool())
        loop = AgentLoop(FakeLLM(), registry, RuntimeSettings())

        result = await loop.run([{"role": "user", "content": "what time is it"}])

        assert result.final_content.startswith("工具结果：")
        assert result.tool_calls[0]["tool_name"] == "now"

    asyncio.run(run())

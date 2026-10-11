import asyncio
from importlib import import_module


Settings = import_module("Turning-Good-Agent.config.settings").Settings
LLMResponse = import_module("Turning-Good-Agent.llm.types").LLMResponse
LLMUsage = import_module("Turning-Good-Agent.llm.types").LLMUsage
AgentRuntime = import_module("Turning-Good-Agent.runtime.runtime").AgentRuntime
InboundMessage = import_module("Turning-Good-Agent.bus.messages").InboundMessage


class Llm:
    async def complete(self, messages, tools):
        del messages, tools
        return LLMResponse("ok", usage=LLMUsage(2, 1, 3))


class Manager:
    def __init__(self):
        self.start_count = 0
        self.closed = False

    async def start_background(self, registry):
        self.start_count += 1

    async def close(self):
        self.closed = True


def test_runtime_starts_mcp_once_at_lifecycle_start_and_closes(tmp_path):
    async def run_test():
        runtime = AgentRuntime.create_default(Settings(data_dir=tmp_path), Llm())
        manager = Manager()
        runtime.mcp = manager
        await runtime.start()
        await runtime.start()
        await runtime.run_turn(InboundMessage.new("hello", "demo", "user", "silent"))
        await runtime.run_turn(InboundMessage.new("again", "demo", "user", "silent"))
        await runtime.close()
        return manager

    manager = asyncio.run(run_test())

    assert manager.start_count == 1
    assert manager.closed is True


def test_runtime_start_is_independent_from_slash_command(tmp_path):
    """验证 slash command 不再决定 MCP 生命周期。"""

    async def run_test():
        runtime = AgentRuntime.create_default(Settings(data_dir=tmp_path), Llm())
        manager = Manager()
        runtime.mcp = manager
        await runtime.start()
        await runtime.run_turn(InboundMessage.new("/exit", "demo", "user", "silent"))
        await runtime.close()
        return manager

    manager = asyncio.run(run_test())

    assert manager.start_count == 1
    assert manager.closed is True

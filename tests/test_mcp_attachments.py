import asyncio
from importlib import import_module

import pytest


McpManager = import_module("Turning-Good-Agent.mcp.manager").McpManager
McpServerSettings = import_module("Turning-Good-Agent.config.settings").McpServerSettings
McpSettings = import_module("Turning-Good-Agent.config.settings").McpSettings
ContextAttachment = import_module("Turning-Good-Agent.tools.context_attachment").ContextAttachment
validate_context_attachment = import_module(
    "Turning-Good-Agent.tools.context_attachment"
).validate_context_attachment
AgentLoop = import_module("Turning-Good-Agent.runtime.agent_loop").AgentLoop
RuntimeSettings = import_module("Turning-Good-Agent.config.settings").RuntimeSettings
ToolRegistry = import_module("Turning-Good-Agent.tools.registry").ToolRegistry
ToolResult = import_module("Turning-Good-Agent.tools.base").ToolResult
LLMResponse = import_module("Turning-Good-Agent.llm.types").LLMResponse
LLMUsage = import_module("Turning-Good-Agent.llm.types").LLMUsage
ToolCall = import_module("Turning-Good-Agent.llm.types").ToolCall
count_content_tokens = import_module("Turning-Good-Agent.sessions.token_counter").count_content_tokens


def test_context_attachment_is_tool_generic_and_validated():
    """验证任意工具都可提供受限的本轮上下文附件。"""
    attachment = ContextAttachment("test", [{"role": "user", "content": "temporary"}], 1)

    assert validate_context_attachment(attachment, 0, 3) is None
    assert validate_context_attachment(
        ContextAttachment("test", [{"role": "system", "content": "invalid"}], 1), 0, 3
    ) == "本轮上下文附件只允许 user 或 assistant 文本消息"
    assert validate_context_attachment(
        ContextAttachment("test", [{"role": "user", "content": "temporary"}], 99), 0, 100
    ) == "本轮上下文附件 token 计数不一致"
    assert validate_context_attachment(
        ContextAttachment("test", [{"role": "user", "content": "temporary"}], count_content_tokens("temporary")),
        3,
        3,
    ) == "本轮上下文附件总量超过 3 tokens 限制"


class Client:
    async def read_resource(self, uri):
        return "start " + "x " * 80 + "end"

    async def get_prompt(self, name, arguments):
        return [{"role": "user", "content": "too many prompt tokens for the configured limit"}]


def test_resource_truncates_and_prompt_rejects_over_limit():
    async def run_test():
        manager = McpManager(
            McpSettings(
                resource_context_token_limit=12,
                prompt_context_token_limit=3,
                attachment_context_token_limit=20,
                servers={"demo": McpServerSettings(command="python")},
            )
        )
        manager.workers["demo"] = Client()
        attachment = await manager.attach_resource("demo", "file:///x", {})
        with pytest.raises(RuntimeError, match="Prompt 超过 3 tokens"):
            await manager.apply_prompt("demo", "review", {})
        return attachment

    attachment = asyncio.run(run_test())

    assert attachment.token_count <= 12
    assert attachment.messages[0]["role"] == "user"
    assert "start" in attachment.messages[0]["content"]
    assert "end" in attachment.messages[0]["content"]


def test_attachment_only_enters_current_working_messages_within_budget():
    class AttachmentTool:
        name = "attach"
        description = "attach"
        input_schema = {"type": "object", "properties": {}}
        parallel_safe = False

        async def run(self, args):
            del args
            return ToolResult(
                "attached",
                context_attachment=ContextAttachment(
                    "demo/resource", [{"role": "user", "content": "temporary resource"}], 2
                ),
            )

    class Llm:
        def __init__(self):
            self.calls = []

        async def complete(self, messages, tools):
            self.calls.append(messages)
            if len(self.calls) == 1:
                return LLMResponse("", [ToolCall("call-1", "attach", {})], LLMUsage(2, 1, 3))
            return LLMResponse("done", usage=LLMUsage(4, 1, 5))

    async def run_test():
        registry = ToolRegistry()
        registry.register(AttachmentTool())
        llm = Llm()
        loop = AgentLoop(llm, registry, RuntimeSettings(), attachment_context_token_limit=3)
        result = await loop.run([{"role": "user", "content": "request"}])
        return llm, result

    llm, result = asyncio.run(run_test())

    assert llm.calls[1][-1] == {"role": "user", "content": "temporary resource"}
    assert result.tool_calls[0].get("context_attachment") is None

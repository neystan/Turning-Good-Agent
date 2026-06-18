import json
import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..config.settings import RuntimeSettings
from ..llm.client import LLMProvider
from ..llm.types import LLMResponse, LLMUsage
from ..tools.executor import ToolExecutor
from ..tools.registry import ToolRegistry


@dataclass(slots=True)
class AgentLoopResult:
    """保存 AgentLoop 的最终回复和工具记录。"""

    final_content: str
    messages: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: LLMUsage | None = None


class AgentLoop:
    """执行 LLM 对话与工具调用循环。"""

    def __init__(
        self,
        llm: LLMProvider,
        tools: ToolRegistry,
        runtime: RuntimeSettings,
        streaming_enabled: bool = False,
    ) -> None:
        self.llm = llm
        self.tools = tools
        self.runtime = runtime
        self.executor = ToolExecutor(tools)
        self.streaming_enabled = streaming_enabled

    async def run(
        self,
        messages: list[dict[str, Any]],
        on_delta: Callable[[str], Any] | None = None,
    ) -> AgentLoopResult:
        """运行模型调用和工具循环直到得到最终文本。"""
        working = list(messages)
        tool_records: list[dict[str, Any]] = []
        usage = LLMUsage()
        for _ in range(self.runtime.max_tool_rounds):
            response = await self._complete(working, self.tools.openai_tools(), on_delta)
            usage = usage.add(response.usage)
            if not response.tool_calls:
                return AgentLoopResult(response.content, working, tool_records, usage)
            working.append(
                {
                    "role": "assistant",
                    "content": response.content,
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(call.args, ensure_ascii=False),
                            },
                        }
                        for call in response.tool_calls[: self.runtime.max_tool_calls_per_round]
                    ],
                }
            )
            for call in response.tool_calls[: self.runtime.max_tool_calls_per_round]:
                record = await self.executor.run(call.name, call.args)
                tool_records.append(record)
                working.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "name": call.name,
                        "content": record["content"],
                    }
                )
        return AgentLoopResult("工具调用轮数已达到上限。", working, tool_records, usage)

    async def _complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        on_delta: Callable[[str], Any] | None,
    ) -> LLMResponse:
        """按配置选择非流式或流式模型调用。"""
        if not self.streaming_enabled or not hasattr(self.llm, "stream"):
            return await self.llm.complete(messages, tools)

        content_parts: list[str] = []
        tool_calls = []
        usage = LLMUsage()
        async for chunk in self.llm.stream(messages, tools):
            usage = usage.add(chunk.usage)
            if chunk.delta_text:
                content_parts.append(chunk.delta_text)
                if on_delta is not None:
                    emitted = on_delta(chunk.delta_text)
                    if inspect.isawaitable(emitted):
                        await emitted
            if chunk.tool_calls:
                tool_calls = chunk.tool_calls
        return LLMResponse(content="".join(content_parts), tool_calls=tool_calls, usage=usage)

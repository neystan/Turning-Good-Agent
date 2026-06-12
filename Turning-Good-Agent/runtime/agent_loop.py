from dataclasses import dataclass, field
from typing import Any

from ..config.settings import RuntimeSettings
from ..llm.client import LLMProvider
from ..tools.executor import ToolExecutor
from ..tools.registry import ToolRegistry


@dataclass(slots=True)
class AgentLoopResult:
    """保存 AgentLoop 的最终回复和工具记录。"""

    final_content: str
    messages: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)


class AgentLoop:
    """执行 LLM 对话与工具调用循环。"""

    def __init__(self, llm: LLMProvider, tools: ToolRegistry, runtime: RuntimeSettings) -> None:
        self.llm = llm
        self.tools = tools
        self.runtime = runtime
        self.executor = ToolExecutor(tools)

    async def run(self, messages: list[dict[str, Any]]) -> AgentLoopResult:
        """运行模型调用和工具循环直到得到最终文本。"""
        working = list(messages)
        tool_records: list[dict[str, Any]] = []
        for _ in range(self.runtime.max_tool_rounds):
            response = await self.llm.complete(working, self.tools.schemas())
            if not response.tool_calls:
                return AgentLoopResult(response.content, working, tool_records)
            for call in response.tool_calls[: self.runtime.max_tool_calls_per_round]:
                record = await self.executor.run(call.name, call.args)
                tool_records.append(record)
                working.append({"role": "tool", "name": call.name, "content": record["content"]})
        return AgentLoopResult("工具调用轮数已达到上限。", working, tool_records)

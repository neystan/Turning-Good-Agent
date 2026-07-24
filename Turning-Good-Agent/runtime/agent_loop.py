import json
from dataclasses import dataclass, field
from typing import Any

from ..channels.base import ChannelAdapter, SilentChannelAdapter
from ..config.settings import RuntimeSettings
from ..config.settings import SkillsSettings
from ..context.tool_round_limit_prompt import TOOL_ROUND_LIMIT_SUMMARY_PROMPT
from ..hooks.manager import HookManager
from ..llm.client import LLMProvider
from ..llm.types import LLMResponse, LLMUsage, ToolCall
from ..sessions.token_counter import count_content_tokens
from ..tools.context_attachment import ContextAttachment, validate_context_attachment
from ..tools.executor import ToolExecutor
from ..tools.registry import ToolRegistry
from .tool_call_runner import ToolCallRunner

@dataclass(slots=True)
class AgentLoopResult:
    """保存 AgentLoop 的最终回复和工具记录。"""

    final_content: str
    messages: list[dict[str, Any]]
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    usage: LLMUsage | None = None
    loaded_skill_names: list[str] = field(default_factory=list)
    loaded_skill_token_count: int = 0


class AgentLoop:
    """执行 LLM 对话与工具调用循环。"""

    def __init__(
        self,
        llm: LLMProvider,
        tools: ToolRegistry,
        runtime: RuntimeSettings,
        streaming_enabled: bool = False,
        hooks: HookManager | None = None,
        attachment_context_token_limit: int = 12_000,
        skills: SkillsSettings | None = None,
    ) -> None:
        """初始化模型、工具 Runner 和 Hook 管理器。"""
        self.llm = llm
        self.tools = tools
        self.runtime = runtime
        self.executor = ToolExecutor()
        self.streaming_enabled = streaming_enabled
        self.hooks = hooks or HookManager()
        self.tool_call_runner = ToolCallRunner(tools, self.executor, self.hooks, runtime)
        self.attachment_context_token_limit = attachment_context_token_limit
        self.skills = skills or SkillsSettings()

    async def run(
        self,
        messages: list[dict[str, Any]],
        channel_adapter: ChannelAdapter | None = None,
        auto_approve_tools: bool = False,
    ) -> AgentLoopResult:
        """运行模型调用和工具循环直到得到最终文本。"""
        channel_adapter = channel_adapter or SilentChannelAdapter()
        working = list(messages)
        tool_records: list[dict[str, Any]] = []
        usage = LLMUsage()
        attachment_tokens = 0
        loaded_skill_names: list[str] = []
        loaded_skill_token_count = 0
        for _ in range(self.runtime.max_tool_rounds):
            response = await self._complete(working, self.tools.openai_tools(), channel_adapter)
            usage = usage.add(response.usage)
            if not response.tool_calls:
                return AgentLoopResult(
                    response.content,
                    working,
                    tool_records,
                    usage,
                    loaded_skill_names,
                    loaded_skill_token_count,
                )
            calls = response.tool_calls[: self.runtime.max_tool_calls_per_round]
            working.append(self._assistant_tool_message(response.content, calls))
            records = await self.tool_call_runner.execute_calls(calls, channel_adapter, auto_approve_tools)
            for call, record in zip(calls, records, strict=True):
                tool_records.append(record)
                attachment = record.pop("context_attachment", None)
                attachment_error = validate_context_attachment(
                    attachment,
                    attachment_tokens if getattr(attachment, "kind", "mcp") == "mcp" else 0,
                    self.attachment_context_token_limit
                    if getattr(attachment, "kind", "mcp") == "mcp"
                    else self.runtime.max_context_tokens,
                )
                if attachment_error is None and attachment is not None:
                    attachment_error = self._validate_skill_attachment(
                        attachment,
                        record,
                        loaded_skill_names,
                        loaded_skill_token_count,
                        working,
                    )
                if attachment_error is not None:
                    record["error"] = attachment_error
                    record["content"] = f"本轮上下文附件被拒绝：{attachment_error}"
                working.append(self._tool_result_message(call, record))
                if attachment_error is None and attachment is not None:
                    assert isinstance(attachment, ContextAttachment)
                    if attachment.kind == "mcp":
                        attachment_tokens += attachment.token_count
                    else:
                        name = str(record["metadata"]["loaded_skill_name"])
                        loaded_skill_names.append(name)
                        loaded_skill_token_count += int(record["metadata"]["loaded_skill_token_count"])
                    working.extend(attachment.messages)
        working.append({"role": "system", "content": TOOL_ROUND_LIMIT_SUMMARY_PROMPT})
        summary = await self._complete(working, [], channel_adapter)
        usage = usage.add(summary.usage)
        content = summary.content.strip()
        if summary.protocol_error or summary.tool_calls or not content:
            content = self._tool_round_limit_fallback(tool_records)
        await channel_adapter.on_delta(content)
        return AgentLoopResult(
            content,
            working,
            tool_records,
            usage,
            loaded_skill_names,
            loaded_skill_token_count,
        )

    def _validate_skill_attachment(
        self,
        attachment: ContextAttachment,
        record: dict[str, Any],
        names: list[str],
        used_tokens: int,
        working: list[dict[str, Any]],
    ) -> str | None:
        """校验 Skill 专属数量、正文和总上下文预算。"""
        if attachment.kind != "skill":
            return self._validate_context_budget(attachment, working)
        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            return "Skill 附件缺少加载元数据"
        name = metadata.get("loaded_skill_name")
        body_tokens = metadata.get("loaded_skill_token_count")
        if not isinstance(name, str) or not attachment.source == f"skill:{name}":
            return "Skill 附件来源无效"
        if not isinstance(body_tokens, int) or body_tokens < 0:
            return "Skill 附件 token 元数据无效"
        if len(names) >= self.skills.max_loaded_skills_per_turn:
            return f"本轮最多加载 {self.skills.max_loaded_skills_per_turn} 个 Skill"
        if body_tokens > self.skills.max_skill_tokens:
            return f"单个 Skill 超过 {self.skills.max_skill_tokens} tokens 限制"
        if used_tokens + body_tokens > self.skills.max_loaded_skill_tokens_per_turn:
            return f"本轮已加载 Skill 总量超过 {self.skills.max_loaded_skill_tokens_per_turn} tokens 限制"
        return self._validate_context_budget(attachment, working)

    def _validate_context_budget(
        self,
        attachment: ContextAttachment,
        working: list[dict[str, Any]],
    ) -> str | None:
        """确保追加附件后的下一次模型请求不超过总上下文上限。"""
        message_tokens = sum(count_content_tokens(str(message.get("content", ""))) for message in working)
        tool_tokens = count_content_tokens(json.dumps(self.tools.openai_tools(), ensure_ascii=False, sort_keys=True))
        if message_tokens + tool_tokens + attachment.token_count > self.runtime.max_context_tokens:
            return f"追加附件后上下文超过 {self.runtime.max_context_tokens} tokens 限制"
        return None

    @staticmethod
    def _assistant_tool_message(content: str, calls: list[ToolCall]) -> dict[str, Any]:
        """构造包含工具请求的 assistant 消息。"""
        return {
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.args, ensure_ascii=False),
                    },
                }
                for call in calls
            ],
        }

    @staticmethod
    def _tool_result_message(call: ToolCall, record: dict[str, Any]) -> dict[str, Any]:
        """构造注入下一轮模型调用的工具结果消息。"""
        return {
            "role": "tool",
            "tool_call_id": call.id,
            "name": call.name,
            "content": record["content"],
        }

    @staticmethod
    def _tool_round_limit_fallback(tool_records: list[dict[str, Any]]) -> str:
        """返回工具上限后的确定性降级结果。"""
        tool_names = ", ".join(record["tool_name"] for record in tool_records) or "无"
        return (
            f"工具调用轮数已达到上限，已完成 {len(tool_records)} 次工具调用（{tool_names}）。"
            "模型未能生成最终总结，可使用 /tools 查看本轮完整工具结果。"
        )

    async def _complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        channel_adapter: ChannelAdapter,
    ) -> LLMResponse:
        """按配置选择非流式或流式模型调用。"""
        if not self.streaming_enabled or not hasattr(self.llm, "stream"):
            return await self.llm.complete(messages, tools)

        content_parts: list[str] = []
        tool_calls = []
        usage = LLMUsage()
        protocol_error: str | None = None
        async for chunk in self.llm.stream(messages, tools):
            usage = usage.add(chunk.usage)
            if chunk.delta_text:
                content_parts.append(chunk.delta_text)
                await channel_adapter.on_delta(chunk.delta_text)
            if chunk.tool_calls:
                tool_calls = chunk.tool_calls
            if chunk.protocol_error:
                protocol_error = chunk.protocol_error
        if usage.total_tokens <= 0:
            raise RuntimeError("流式 LLM 响应缺少 usage，无法保存本轮结果。")
        return LLMResponse(
            content="".join(content_parts),
            tool_calls=tool_calls,
            usage=usage,
            protocol_error=protocol_error,
        )

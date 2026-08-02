import json
from dataclasses import dataclass, field
from typing import Any

from ..channels.base import ChannelAdapter, SilentChannelAdapter
from ..config.settings import RuntimeSettings, SkillsSettings
from ..context.tool_round_limit_prompt import TOOL_ROUND_LIMIT_SUMMARY_PROMPT
from ..hooks.manager import HookManager
from ..llm.client import LLMProvider
from ..llm.types import LLMResponse, LLMUsage, ToolCall
from ..tools.executor import ToolExecutor
from ..tools.registry import ToolRegistry
from .attachment_manager import AttachmentManager
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
    consumed_guidance: list[str] = field(default_factory=list)
    cancelled: bool = False


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
        *,
        allowed_tool_names: frozenset[str] | None = None,
    ) -> AgentLoopResult:
        """运行模型调用和工具循环直到得到最终文本。"""
        channel_adapter = channel_adapter or SilentChannelAdapter()
        working = list(messages)
        openai_tools = self.tools.openai_tools(allowed_tool_names)
        attachments = AttachmentManager(
            self.runtime,
            self.skills,
            self.attachment_context_token_limit,
            openai_tools,
        )
        tool_records: list[dict[str, Any]] = []
        usage = LLMUsage()
        consumed_guidance: list[str] = []

        def finish(final_content: str, *, cancelled: bool = False) -> AgentLoopResult:
            """封装本轮最终结果。"""
            return AgentLoopResult(
                final_content=final_content,
                messages=working,
                tool_calls=tool_records,
                usage=usage,
                loaded_skill_names=attachments.skill_names,
                loaded_skill_token_count=attachments.skill_tokens,
                consumed_guidance=consumed_guidance,
                cancelled=cancelled,
            )

        for _ in range(self.runtime.max_tool_rounds):
            if await self._apply_turn_control(working, channel_adapter, consumed_guidance):
                return finish("", cancelled=True)
            response = await self._complete(working, openai_tools, channel_adapter)
            usage = usage.add(response.usage)
            if not response.tool_calls:
                if channel_adapter.is_stop_requested():
                    return finish(response.content, cancelled=True)
                if not response.content.strip():
                    raise RuntimeError("模型返回了空响应。")
                return finish(response.content)
            calls = response.tool_calls[: self.runtime.max_tool_calls_per_round]
            working.append(self._assistant_tool_message(response.content, calls))
            if channel_adapter.is_stop_requested():
                return finish(response.content, cancelled=True)
            records = await self.tool_call_runner.execute_calls(
                calls,
                channel_adapter,
                auto_approve_tools,
                allowed_tool_names,
            )
            tool_messages: list[dict[str, Any]] = []
            pending_attachments: list[tuple[int, ToolCall, dict[str, Any], object | None]] = []
            for call, record in zip(calls, records, strict=True):
                tool_records.append(record)
                attachment = record.pop("context_attachment", None)
                tool_message = self._tool_result_message(call, record)
                pending_attachments.append((len(tool_messages), call, record, attachment))
                tool_messages.append(tool_message)
            working.extend(tool_messages)
            tool_message_start = len(working) - len(tool_messages)
            for message_index, call, record, attachment in pending_attachments:
                attachment_error = attachments.check(attachment, record, working)
                if attachment_error is not None:
                    record["error"] = attachment_error
                    record["content"] = f"本轮上下文附件被拒绝：{attachment_error}"
                    working[tool_message_start + message_index] = self._tool_result_message(
                        call,
                        record,
                    )
                if attachment_error is None:
                    attachments.commit(attachment, record, working)
            if await self._apply_turn_control(working, channel_adapter, consumed_guidance):
                return finish(response.content, cancelled=True)
        working.append({"role": "system", "content": TOOL_ROUND_LIMIT_SUMMARY_PROMPT})
        summary = await self._complete(working, [], channel_adapter, emit_deltas=False)
        usage = usage.add(summary.usage)
        if channel_adapter.is_stop_requested():
            return finish("", cancelled=True)
        content = summary.content.strip()
        if summary.protocol_error or summary.tool_calls or not content:
            content = self._tool_round_limit_fallback(tool_records)
        await channel_adapter.on_delta(content)
        return finish(content)

    async def _apply_turn_control(
        self,
        working: list[dict[str, Any]],
        channel_adapter: ChannelAdapter,
        consumed_guidance: list[str],
    ) -> bool:
        """在安全检查点追加引导或确认协作式停止。"""
        if channel_adapter.is_stop_requested():
            return True
        guidance = await channel_adapter.consume_guidance()
        for content in guidance:
            text = content.strip()
            if not text:
                continue
            consumed_guidance.append(text)
            working.append(
                {
                    "role": "user",
                    "content": "【用户正在引导当前任务】\n"
                    "用户在任务执行中补充了以下方向。请在系统约束和安全审批不变的前提下调整后续计划；"
                    "不要重复已经完成的操作。\n\n"
                    f"{text}",
                }
            )
        return False

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
        emit_deltas: bool = True,
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
                if emit_deltas:
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

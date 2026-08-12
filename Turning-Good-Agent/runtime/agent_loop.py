import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from ..channels.base import ChannelAdapter, SilentChannelAdapter
from ..config.settings import RuntimeSettings, SkillsSettings
from ..context.tool_round_limit_prompt import TOOL_ROUND_LIMIT_SUMMARY_PROMPT
from ..hooks.manager import HookManager
from ..llm.client import LLMProvider
from ..llm.types import LLMResponse, LLMUsage, ToolCall
from ..multi_agent.delegate_tool import DELEGATE_MULTI_AGENT_NAME, DelegateMultiAgentInvocation
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

    # 初始化模型、工具 Runner 和 Hook 管理器。
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

    # 运行模型调用、普通工具和唯一委派 Tool 直到得到最终文本。
    async def run(
        self,
        messages: list[dict[str, Any]],
        channel_adapter: ChannelAdapter | None = None,
        auto_approve_tools: bool = False,
        *,
        allowed_tool_names: frozenset[str] | None = None,
        llm_override: LLMProvider | None = None,
        streaming_enabled: bool | None = None,
        tool_registry: ToolRegistry | None = None,
        tool_runner: ToolCallRunner | None = None,
        multi_agent_invocation: DelegateMultiAgentInvocation | None = None,
        persist_tool_calls: bool = True,
    ) -> AgentLoopResult:
        """运行模型调用和工具循环直到得到最终文本。"""
        channel_adapter = channel_adapter or SilentChannelAdapter()
        llm = self.llm if llm_override is None else llm_override
        use_streaming = self.streaming_enabled if streaming_enabled is None else streaming_enabled
        active_tools = tool_registry if tool_registry is not None else self.tools
        active_runner = tool_runner or (
            ToolCallRunner(active_tools, self.executor, self.hooks, self.runtime)
            if tool_registry is not None
            else self.tool_call_runner
        )
        working = list(messages)
        openai_tools = self._openai_tools(
            active_tools,
            allowed_tool_names,
            multi_agent_invocation,
        )
        attachments = AttachmentManager(
            self.runtime,
            self.skills,
            self.attachment_context_token_limit,
            openai_tools,
        )
        tool_records: list[dict[str, Any]] = []
        usage = LLMUsage()
        consumed_guidance: list[str] = []
        invocation_finished = False

        # 只在已受理委派后向 Coordinator 交付一次父回合结局。
        async def finish_multi_agent(outcome: str) -> None:
            nonlocal invocation_finished
            if invocation_finished or multi_agent_invocation is None:
                return
            invocation_finished = True
            await multi_agent_invocation.finish_parent_turn(outcome)

        # 统一构造结果并收口本父回合的专用委派。
        async def finish(
            final_content: str,
            *,
            cancelled: bool = False,
            outcome: str | None = None,
        ) -> AgentLoopResult:
            await finish_multi_agent(outcome or ("cancelled" if cancelled else "completed"))
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

        # 在已创建 Run 后以其绝对 deadline 约束父模型和普通 Tool 等待。
        async def await_multi_agent_deadline(awaitable: Any) -> Any:
            if multi_agent_invocation is None:
                return await awaitable
            deadline = multi_agent_invocation.deadline_monotonic()
            if deadline is None:
                return await awaitable
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                if asyncio.iscoroutine(awaitable):
                    awaitable.close()
                raise TimeoutError
            return await asyncio.wait_for(awaitable, timeout=remaining)

        for _ in range(self.runtime.max_tool_rounds):
            if await self._apply_turn_control(working, channel_adapter, consumed_guidance):
                return await finish("", cancelled=True)
            if self._multi_agent_deadline_expired(multi_agent_invocation):
                return await finish("Multi-Agent 协作已超时。", outcome="timed_out")
            context_error = await self._check_multi_agent_invocation(
                multi_agent_invocation,
                working,
                openai_tools,
            )
            if context_error is not None:
                return await finish(context_error, outcome="failed")
            try:
                response = await await_multi_agent_deadline(
                    self._complete(
                        working,
                        openai_tools,
                        channel_adapter,
                        llm=llm,
                        streaming_enabled=use_streaming,
                    )
                )
            except asyncio.CancelledError:
                await finish_multi_agent("cancelled")
                raise
            except TimeoutError:
                return await finish("Multi-Agent 协作已超时。", outcome="timed_out")
            except Exception:
                await finish_multi_agent("failed")
                raise
            usage = usage.add(response.usage)
            if not response.tool_calls:
                if channel_adapter.is_stop_requested():
                    return await finish(response.content, cancelled=True)
                if not response.content.strip():
                    await finish_multi_agent("failed")
                    raise RuntimeError("模型返回了空响应。")
                return await finish(response.content)
            calls = response.tool_calls[: self.runtime.max_tool_calls_per_round]
            working.append(
                self._assistant_tool_message(
                    response.content,
                    calls,
                    reasoning_content=response.reasoning_content,
                )
            )
            if channel_adapter.is_stop_requested():
                return await finish(response.content, cancelled=True)
            try:
                executed, channel_adapter = await self._execute_parent_calls(
                    calls,
                    channel_adapter,
                    auto_approve_tools,
                    allowed_tool_names,
                    active_runner,
                    multi_agent_invocation,
                )
            except asyncio.CancelledError:
                await finish_multi_agent("cancelled")
                raise
            except TimeoutError:
                return await finish("Multi-Agent 协作已超时。", outcome="timed_out")
            except Exception:
                await finish_multi_agent("failed")
                raise
            tool_messages: list[dict[str, Any]] = []
            pending_attachments: list[tuple[int, ToolCall, dict[str, Any], object | None]] = []
            for call, (record, persist_record) in zip(calls, executed, strict=True):
                if persist_tool_calls and persist_record:
                    tool_records.append(record)
                attachment = record.pop("context_attachment", None)
                tool_messages.append(self._tool_result_message(call, record))
                pending_attachments.append(
                    (len(tool_messages) - 1, call, record, attachment)
                )
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
                return await finish(response.content, cancelled=True)
            openai_tools = self._openai_tools(
                active_tools,
                allowed_tool_names,
                multi_agent_invocation,
            )
            if self._multi_agent_deadline_expired(multi_agent_invocation):
                return await finish("Multi-Agent 协作已超时。", outcome="timed_out")
            context_error = await self._check_multi_agent_invocation(
                multi_agent_invocation,
                working,
                openai_tools,
            )
            if context_error is not None:
                return await finish(context_error, outcome="failed")
        working.append({"role": "system", "content": TOOL_ROUND_LIMIT_SUMMARY_PROMPT})
        if self._multi_agent_deadline_expired(multi_agent_invocation):
            return await finish("Multi-Agent 协作已超时。", outcome="timed_out")
        try:
            summary = await await_multi_agent_deadline(
                self._complete(
                    working,
                    [],
                    channel_adapter,
                    llm=llm,
                    streaming_enabled=use_streaming,
                    emit_deltas=False,
                )
            )
        except asyncio.CancelledError:
            await finish_multi_agent("cancelled")
            raise
        except TimeoutError:
            return await finish("Multi-Agent 协作已超时。", outcome="timed_out")
        except Exception:
            await finish_multi_agent("failed")
            raise
        usage = usage.add(summary.usage)
        if channel_adapter.is_stop_requested():
            return await finish("", cancelled=True)
        content = summary.content.strip()
        if summary.protocol_error or summary.tool_calls or not content:
            content = self._tool_round_limit_fallback(tool_records)
        await channel_adapter.on_delta(content)
        return await finish(content)

    # 返回普通 Tool 与当前唯一委派 Tool 合并后的模型 schema。
    def openai_tools(
        self,
        allowed_tool_names: frozenset[str] | None = None,
        *,
        tool_registry: ToolRegistry | None = None,
        multi_agent_invocation: DelegateMultiAgentInvocation | None = None,
    ) -> list[dict[str, Any]]:
        active_tools = tool_registry if tool_registry is not None else self.tools
        return self._openai_tools(active_tools, allowed_tool_names, multi_agent_invocation)

    # 仅在父回合允许时追加唯一委派 Tool schema。
    @staticmethod
    def _openai_tools(
        tool_registry: ToolRegistry,
        allowed_tool_names: frozenset[str] | None,
        multi_agent_invocation: DelegateMultiAgentInvocation | None,
    ) -> list[dict[str, Any]]:
        schemas = list(tool_registry.openai_tools(allowed_tool_names))
        names = {schema["function"]["name"] for schema in schemas}
        if (
            multi_agent_invocation is not None
            and multi_agent_invocation.is_schema_visible
            and DELEGATE_MULTI_AGENT_NAME not in names
        ):
            schemas.append(multi_agent_invocation.openai_schema())
        return schemas

    # 按调用顺序分发普通 Tool 和唯一的委派 Tool。
    async def _execute_parent_calls(
        self,
        calls: list[ToolCall],
        channel_adapter: ChannelAdapter,
        auto_approve_tools: bool,
        allowed_tool_names: frozenset[str] | None,
        tool_runner: ToolCallRunner,
        multi_agent_invocation: DelegateMultiAgentInvocation | None,
    ) -> tuple[list[tuple[dict[str, Any], bool]], ChannelAdapter]:
        results: list[tuple[dict[str, Any], bool]] = []
        ordinary: list[ToolCall] = []
        active_adapter = channel_adapter
        approval_wrapped = False

        # 在 Run 已建立后以它的绝对 deadline 限制剩余调用。
        async def await_multi_agent_deadline(awaitable: Any) -> Any:
            if multi_agent_invocation is None:
                return await awaitable
            deadline = multi_agent_invocation.deadline_monotonic()
            if deadline is None:
                return await awaitable
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                if asyncio.iscoroutine(awaitable):
                    awaitable.close()
                raise TimeoutError
            return await asyncio.wait_for(awaitable, timeout=remaining)

        # 刷新连续普通工具批次并保持原始调用顺序。
        async def flush_ordinary() -> None:
            if not ordinary:
                return
            records = await await_multi_agent_deadline(
                tool_runner.execute_calls(
                    list(ordinary),
                    active_adapter,
                    auto_approve_tools,
                    allowed_tool_names,
                )
            )
            results.extend((record, True) for record in records)
            ordinary.clear()

        for call in calls:
            if call.name != DELEGATE_MULTI_AGENT_NAME:
                ordinary.append(call)
                continue
            await flush_ordinary()
            if multi_agent_invocation is None or not multi_agent_invocation.is_schema_visible:
                results.append(
                    (
                        {
                            "tool_name": call.name,
                            "args": dict(call.args),
                            "content": "当前父回合不允许 delegate_multi_agent 调用。",
                            "duration_ms": 0.0,
                            "error": "当前父回合不允许委派",
                            "tool_call_id": call.id,
                        },
                        False,
                    )
                )
                continue
            content = await multi_agent_invocation.invoke(dict(call.args))
            results.append(
                (
                    {
                        "tool_name": call.name,
                        "args": dict(call.args),
                        "content": content,
                        "duration_ms": 0.0,
                        "error": None,
                        "tool_call_id": call.id,
                    },
                    False,
                )
            )
            if not multi_agent_invocation.is_schema_visible and not approval_wrapped:
                active_adapter = multi_agent_invocation.wrap_parent_approval(active_adapter)
                approval_wrapped = True
        await flush_ordinary()
        return results, active_adapter

    # 在父 synthesis 安全点统一检查本次委派的终态和上下文。
    @staticmethod
    async def _check_multi_agent_invocation(
        multi_agent_invocation: DelegateMultiAgentInvocation | None,
        working: list[dict[str, Any]],
        openai_tools: list[dict[str, Any]],
    ) -> str | None:
        if multi_agent_invocation is None:
            return None
        return await multi_agent_invocation.check_parent_synthesis(working, openai_tools)

    # 判断已受理委派是否已经越过 Coordinator 的总 deadline。
    @staticmethod
    def _multi_agent_deadline_expired(
        multi_agent_invocation: DelegateMultiAgentInvocation | None,
    ) -> bool:
        if multi_agent_invocation is None:
            return False
        deadline = multi_agent_invocation.deadline_monotonic()
        return deadline is not None and deadline <= asyncio.get_running_loop().time()

    # 在安全检查点追加引导或确认协作式停止。
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
    def _assistant_tool_message(
        content: str,
        calls: list[ToolCall],
        *,
        reasoning_content: str | None = None,
    ) -> dict[str, Any]:
        """构造包含工具请求的 assistant 消息。"""
        message = {
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
        if reasoning_content:
            message["reasoning_content"] = reasoning_content
        return message

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
        *,
        llm: LLMProvider,
        streaming_enabled: bool,
        emit_deltas: bool = True,
    ) -> LLMResponse:
        """按配置选择非流式或流式模型调用。"""
        if not streaming_enabled or not hasattr(llm, "stream"):
            return await llm.complete(messages, tools)

        content_parts: list[str] = []
        tool_calls = []
        usage = LLMUsage()
        protocol_error: str | None = None
        async for chunk in llm.stream(messages, tools):
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

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..channels.base import SilentChannelAdapter
from ..llm.types import LLMUsage
from .types import new_id, utc_now_iso


class BackgroundAgentLoop(Protocol):
    """后台入口需要的 AgentLoop 最小接口。"""

    async def run(
        self,
        messages: list[dict[str, Any]],
        channel_adapter: SilentChannelAdapter,
        auto_approve_tools: bool,
        *,
        allow_tools: bool = True,
        excluded_tool_names: frozenset[str] = frozenset(),
    ) -> Any:
        """运行既有模型和 Tool 执行链。"""


class ExecutionAuditStore(Protocol):
    """保存不含 prompt、内容或 Tool 参数的后台执行审计。"""

    def append_jsonl(self, name: str, payload: dict[str, Any]) -> None:
        """追加一条 durable JSONL 审计记录。"""


class BackgroundChannelAdapter(SilentChannelAdapter):
    """禁止后台执行等待用户审批。"""

    async def request_tool_approval(self, call: object) -> str:
        """转发工具审批请求。"""
        del call
        return "后台任务不允许审批 Tool。"


@dataclass(frozen=True, slots=True)
class ProactiveExecutionContext:
    """描述一次脱离普通聊天 turn 的后台执行。"""

    capability: str
    job_id: str
    source_session_ids: tuple[str, ...] = ()
    output_token_limit: int | None = None


@dataclass(slots=True)
class ProactiveExecutionResult:
    """保存后台模型调用的可审计结果。"""

    success: bool
    content: str
    usage: LLMUsage
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    cancelled: bool = False


class ProactiveExecutor:
    """唯一后台模型调用边界，不进入 AgentRuntime.run_turn。"""

    def __init__(
        self,
        agent_loop: BackgroundAgentLoop,
        *,
        audit_store: ExecutionAuditStore | None = None,
        excluded_tool_names: frozenset[str] = frozenset(),
    ) -> None:
        """初始化对象状态。"""
        self._agent_loop = agent_loop
        self._audit_store = audit_store
        self._configured_excluded_tool_names = excluded_tool_names

    async def run(
        self,
        context: ProactiveExecutionContext,
        prompt: str,
        *,
        allow_tools: bool = True,
    ) -> ProactiveExecutionResult:
        """用现有 AgentLoop 执行后台任务并默认拒绝审批 Tool。"""
        started_at = utc_now_iso()
        execution_id = new_id("execution")
        self._append_audit(execution_id, context, "started", started_at=started_at)
        messages = [
            {
                "role": "system",
                "content": self._system_prompt(context, allow_tools),
            },
            {"role": "user", "content": prompt},
        ]
        try:
            loop_result = await self._agent_loop.run(
                messages,
                BackgroundChannelAdapter(),
                auto_approve_tools=False,
                allow_tools=allow_tools,
                excluded_tool_names=self._excluded_tool_names(),
            )
        except asyncio.CancelledError:
            self._append_audit(
                execution_id,
                context,
                "cancelled",
                started_at=started_at,
                result=ProactiveExecutionResult(False, "", LLMUsage(), error="后台任务已取消", cancelled=True),
            )
            raise
        except Exception as exc:
            result = ProactiveExecutionResult(
                False,
                "",
                LLMUsage(),
                error=f"后台执行失败：{type(exc).__name__}",
            )
            self._append_audit(execution_id, context, "failed", started_at=started_at, result=result)
            return result
        tool_calls = [dict(record) for record in getattr(loop_result, "tool_calls", [])]
        tool_errors = [str(record["error"]) for record in tool_calls if record.get("error")]
        cancelled = bool(getattr(loop_result, "cancelled", False))
        error = "后台 Tool 调用失败" if tool_errors else None
        if cancelled and error is None:
            error = "后台任务已取消"
        result = ProactiveExecutionResult(
            success=not cancelled and error is None,
            content=str(getattr(loop_result, "final_content", "")),
            usage=getattr(loop_result, "usage", None) or LLMUsage(),
            tool_calls=tool_calls,
            error=error,
            cancelled=cancelled,
        )
        self._append_audit(
            execution_id,
            context,
            "completed" if result.success else "failed",
            started_at=started_at,
            result=result,
        )
        return result

    def _append_audit(
        self,
        execution_id: str,
        context: ProactiveExecutionContext,
        state: str,
        *,
        started_at: str,
        result: ProactiveExecutionResult | None = None,
    ) -> None:
        """追加任务审计记录。"""
        if self._audit_store is None:
            return
        payload: dict[str, Any] = {
            "id": execution_id,
            "created_at": utc_now_iso(),
            "capability": context.capability,
            "job_id": context.job_id,
            "source_session_ids": list(context.source_session_ids),
            "state": state,
            "started_at": started_at,
        }
        if result is not None:
            payload["finished_at"] = utc_now_iso()
            payload["usage"] = {
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "total_tokens": result.usage.total_tokens,
            }
            payload["tool_call_count"] = len(result.tool_calls)
            if result.error:
                payload["error"] = result.error
        self._audit_store.append_jsonl("executions.jsonl", payload)

    @staticmethod
    def _system_prompt(context: ProactiveExecutionContext, allow_tools: bool) -> str:
        """构造审阅提示词。"""
        tool_rule = (
            "可在既有安全链路内调用工具；任何需要审批的工具都会被拒绝。"
            if allow_tools
            else "本次审阅不得调用工具。"
        )
        return (
            "你正在执行 Turning Good Agent 的后台主动任务。"
            f"能力：{context.capability}；任务 ID：{context.job_id}。"
            "不要展示推理过程，不要把结果写入普通聊天历史。"
            f"{tool_rule}"
        )

    def _excluded_tool_names(self) -> frozenset[str]:
        """返回后台不可见且不可执行的审批/主动控制 Tool。"""
        names = set(_PROACTIVE_CONTROL_TOOL_NAMES)
        names.update(self._configured_excluded_tool_names)
        registry = getattr(self._agent_loop, "tools", None)
        if registry is not None:
            for name in getattr(registry, "tool_names", []):
                tool = registry.get(name)
                if bool(getattr(tool, "approval_required", False)):
                    names.add(name)
        return frozenset(names)


_PROACTIVE_CONTROL_TOOL_NAMES = frozenset(
    {
        "create_cron",
        "list_crons",
        "delete_cron",
        "run_breakbeat",
        "list_breakbeat",
        "complete_breakbeat",
        "delete_breakbeat",
        "run_dream",
        "read_profile_memory",
        "run_skill_evolution",
        "list_skill_drafts",
        "delete_skill_draft",
        "list_incidents",
    }
)

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Callable, Protocol

from ..channels.base import SilentChannelAdapter
from ..llm.client import LLMProvider
from ..llm.types import LLMUsage


class BackgroundAgentLoop(Protocol):
    """后台入口需要的 AgentLoop 最小接口。"""

    async def run(
        self,
        messages: list[dict[str, Any]],
        channel_adapter: SilentChannelAdapter,
        auto_approve_tools: bool,
        *,
        allowed_tool_names: frozenset[str] | None = None,
        llm_override: LLMProvider | None = None,
        streaming_enabled: bool | None = None,
    ) -> Any:
        """运行既有模型和 Tool 执行链。"""


@dataclass(slots=True)
class ProactiveExecutionResult:
    """保存后台模型调用的最小结果。"""

    success: bool
    content: str
    usage: LLMUsage
    error: str | None = None
    cancelled: bool = False


class ProactiveExecutor:
    """唯一后台模型调用边界，不进入 AgentRuntime.run_turn。"""

    def __init__(
        self,
        agent_loop: BackgroundAgentLoop,
        *,
        approval_required_tool_names: frozenset[str] = frozenset(),
        proactive_tool_names: Callable[[], frozenset[str]] | None = None,
        review_llm: LLMProvider | None = None,
    ) -> None:
        """初始化模型入口和配置审批名单。"""
        self._agent_loop = agent_loop
        self._approval_required_tool_names = approval_required_tool_names
        self._proactive_tool_names = proactive_tool_names
        self._review_llm = review_llm

    async def run(
        self,
        capability: str,
        prompt: str,
        *,
        allowed_tool_names: frozenset[str] | None = None,
        use_review_model: bool = False,
    ) -> ProactiveExecutionResult:
        """以后台安全白名单执行一次主动任务。"""
        safe_tool_names = self._safe_tool_names(allowed_tool_names)
        messages = [
            {"role": "system", "content": self._system_prompt(capability)},
            {"role": "user", "content": prompt},
        ]
        try:
            if use_review_model and self._review_llm is not None:
                loop_result = await self._agent_loop.run(
                    messages,
                    SilentChannelAdapter(),
                    auto_approve_tools=False,
                    allowed_tool_names=safe_tool_names,
                    llm_override=self._review_llm,
                    streaming_enabled=False,
                )
            else:
                loop_result = await self._agent_loop.run(
                    messages,
                    SilentChannelAdapter(),
                    auto_approve_tools=False,
                    allowed_tool_names=safe_tool_names,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            return ProactiveExecutionResult(
                False,
                "",
                LLMUsage(),
                error=f"后台执行失败：{type(exc).__name__}",
            )
        tool_errors = [
            str(record["error"])
            for record in getattr(loop_result, "tool_calls", [])
            if isinstance(record, dict) and record.get("error")
        ]
        cancelled = bool(getattr(loop_result, "cancelled", False))
        error = "后台 Tool 调用失败" if tool_errors else None
        if cancelled and error is None:
            error = "后台任务已取消"
        return ProactiveExecutionResult(
            success=not cancelled and error is None,
            content=str(getattr(loop_result, "final_content", "")),
            usage=getattr(loop_result, "usage", None) or LLMUsage(),
            error=error,
            cancelled=cancelled,
        )

    def _safe_tool_names(self, requested: frozenset[str] | None) -> frozenset[str]:
        """计算后台可见且可执行的 Tool 交集。"""
        registry = getattr(self._agent_loop, "tools", None)
        if registry is None:
            return frozenset()
        names = set(requested or ())
        names.intersection_update(getattr(registry, "tool_names", []))
        names.difference_update(_PROACTIVE_CONTROL_TOOL_NAMES)
        if self._proactive_tool_names is not None:
            names.difference_update(self._proactive_tool_names())
        names.difference_update(self._approval_required_tool_names)
        for name in tuple(names):
            tool = registry.get(name)
            if bool(getattr(tool, "approval_required", False)):
                names.discard(name)
        return frozenset(names)

    @staticmethod
    def _system_prompt(capability: str) -> str:
        """构造最小后台任务边界。"""
        return (
            "你正在执行 Turning Good Agent 的后台主动任务。"
            f"能力：{capability}。"
            "不要展示推理过程，不要把结果写入普通聊天历史。"
        )


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

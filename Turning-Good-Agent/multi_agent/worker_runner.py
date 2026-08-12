from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from ..channels.base import SilentChannelAdapter
from ..context.builder import ContextBuilder
from ..hooks.manager import HookManager
from ..hooks.tool_result_truncation import ToolResultTruncationHook
from ..memory.long_term import ProfileMemorySnapshot
from ..multi_agent.types import MultiAgentTask, WorkerResult
from ..tools.registry import ToolRegistry
from .worker_profile import WorkerToolProfile, _build_worker_registry, build_worker_profile


if TYPE_CHECKING:
    from ..runtime.runtime import AgentRuntime


_TOOL_ROUND_LIMIT_FALLBACK_PREFIX = "工具调用轮数已达到上限，"
_TOOL_ROUND_LIMIT_ERROR = "Worker 已达到工具调用轮数上限"
_EVIDENCE_PREVIEW_MAX_CHARS = 1_000


class WorkerRunner:
    """在固定 Profile 和无历史上下文中执行一次 Worker。"""

    # 绑定 active Runtime、上下文构建器和固定 Profile。
    def __init__(
        self,
        runtime: "AgentRuntime",
        context_builder: ContextBuilder | None = None,
    ) -> None:
        self.runtime = runtime
        self.profile = build_worker_profile(
            runtime.agent_loop.tools,
            runtime.settings.data_dir,
            skills_manager=runtime.skills,
            approval_required_tool_names=frozenset(
                runtime.settings.tool_permissions.approval_required_tools
            ),
        )
        self.context_builder = context_builder or ContextBuilder()
        self._execute_worker = self._build_execution()
        self._usage_by_run_task: dict[tuple[str, str], dict[str, int]] = {}

    # 为当前 WorkerRunner 闭包固定独立的串行工具执行面。
    def _build_execution(self) -> Callable[[list[dict[str, Any]]], Awaitable[Any]]:
        from ..runtime.agent_loop import AgentLoop

        registry = _build_worker_registry(
            self.runtime.agent_loop.tools,
            self.profile.path_policy,
            skills_manager=self.runtime.skills,
            allowed_names=self.profile.tool_names,
        )
        settings = replace(
            self.runtime.settings.runtime,
            parallel_tool_calls_enabled=False,
            max_parallel_tool_calls=1,
            max_tool_calls_per_round=1,
        )
        hooks = HookManager()
        hooks.register(ToolResultTruncationHook(settings.max_tool_result_tokens))
        worker_loop = AgentLoop(
            self.runtime.agent_loop.llm,
            registry,
            settings,
            streaming_enabled=False,
            hooks=hooks,
            attachment_context_token_limit=self.runtime.agent_loop.attachment_context_token_limit,
            skills=self.runtime.agent_loop.skills,
        )

        # 在闭包中保留临时 Registry 和 Worker Loop，避免向 Profile 公开。
        async def execute(messages: list[dict[str, Any]]) -> Any:
            return await worker_loop.run(
                messages,
                SilentChannelAdapter(),
                auto_approve_tools=False,
                allowed_tool_names=self.profile.tool_names,
                persist_tool_calls=True,
                streaming_enabled=False,
            )

        return execute

    # 只接受协调器传入的紧邻前序有界内容。
    async def run(
        self,
        task: MultiAgentTask,
        dependency_content: str | None = None,
        *,
        run_id: str,
        node_id: str,
        profile_memory: ProfileMemorySnapshot,
    ) -> WorkerResult:
        try:
            messages = self.context_builder.build_worker(
                self.profile.system_prompt,
                profile_memory,
                list(self.profile.skill_catalog),
                task.role,
                task.brief,
                dependency_content,
            )
            result = await self._execute_worker(messages)
            usage = result.usage
            if result.cancelled:
                self._remember_usage(run_id, node_id, usage)
                return WorkerResult(status="cancelled", content=None, error="Worker 已取消")
            if result.final_content.startswith(_TOOL_ROUND_LIMIT_FALLBACK_PREFIX):
                content = self._evidence_handoff(result.tool_calls)
                self._remember_usage(run_id, node_id, usage)
                if content is not None:
                    return WorkerResult(status="completed", content=content, error=None)
                return WorkerResult(status="failed", content=None, error=_TOOL_ROUND_LIMIT_ERROR)
            self._remember_usage(run_id, node_id, usage)
            return WorkerResult(status="completed", content=result.final_content, error=None)
        except Exception:
            return WorkerResult(status="failed", content=None, error="Worker 执行失败")

    # 将成功工具结果整理为父 Agent 可消费的有界证据。
    @staticmethod
    def _evidence_handoff(records: list[dict[str, Any]]) -> str | None:
        evidence: list[str] = []
        for record in records:
            if record.get("error"):
                continue
            tool_name = str(record.get("tool_name", "未知工具"))
            content = str(record.get("content", "")).strip()
            if not content:
                continue
            preview = content[:_EVIDENCE_PREVIEW_MAX_CHARS]
            if len(content) > len(preview):
                preview += "\n[结果摘录已截断]"
            evidence.append(f"[{tool_name}]\n{preview}")
        if not evidence:
            return None
        return (
            "Worker 工具调用预算已用尽。以下是已获得的受控证据摘录；"
            "请基于这些证据完成汇总，并明确仍未验证的缺口。\n\n"
            + "\n\n".join(evidence)
        )

    # 保存当前 Run/节点的最小真实 Worker usage，避免扩展 WorkerResult 契约。
    def _remember_usage(self, run_id: str, node_id: str, usage: object) -> None:
        input_tokens = getattr(usage, "input_tokens", None)
        output_tokens = getattr(usage, "output_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        if not all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in (input_tokens, output_tokens, total_tokens)
        ):
            return
        if input_tokens < 0 or output_tokens < 0 or total_tokens <= 0:
            return
        self._usage_by_run_task[(run_id, node_id)] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "turn_total_tokens": total_tokens,
        }

    # 返回指定 Worker usage 的副本，供 Coordinator 生成安全事件。
    def usage_for(self, run_id: str, node_id: str) -> dict[str, int] | None:
        usage = self._usage_by_run_task.get((run_id, node_id))
        return dict(usage) if usage is not None else None

    # 删除指定终态 Run 的 Worker usage 缓存。
    def release_run(self, run_id: str) -> None:
        self._usage_by_run_task = {
            key: value for key, value in self._usage_by_run_task.items() if key[0] != run_id
        }

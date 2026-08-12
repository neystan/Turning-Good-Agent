from __future__ import annotations

import asyncio
import inspect
import json
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from ..context.builder import WORKER_DEPENDENCY_MAX_CHARS, WORKER_DEPENDENCY_MAX_TOKENS
from ..memory.long_term import ProfileMemorySnapshot
from .events import MULTI_AGENT_EVENT_TYPES, MultiAgentEventPayload
from ..sessions.token_counter import count_content_tokens
from .types import (
    DelegateParentTurn,
    MultiAgentNode,
    MultiAgentRequest,
    MultiAgentRun,
    MultiAgentTask,
    Strategy,
    WorkerResult,
)
from .worker_runner import WorkerRunner


EventSink = Callable[[str, MultiAgentEventPayload], Awaitable[None] | None]

RESULT_CONTEXT_EXCEEDED_CODE = "result_context_exceeded"
RESULT_CONTEXT_EXCEEDED_ERROR = "Worker 结果超出上下文限制"
INVALID_RESULT_ERROR = "Worker 结果格式无效"
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(\b(?:api[_-]?key|access[_-]?token|auth(?:orization)?|password|passwd|secret|"
    r"client[_-]?secret|private[_-]?key)\b\s*[:=]\s*)(?:\"[^\"]*\"|'[^']*'|[^\s,;}\]]+)"
)
_BEARER_VALUE = re.compile(r"(?i)(\bBearer\s+)([^\s,;}'\"]+)")
_TOKEN_VALUE = re.compile(r"\b(?:sk|rk|xox[baprs])-[-A-Za-z0-9_]{12,}\b")
_RUN_EVENT_BY_STATUS = {
    "queued": "multi_agent.run.started",
    "running": "multi_agent.run.started",
    "waiting": "multi_agent.run.started",
    "completed": "multi_agent.run.completed",
    "failed": "multi_agent.run.failed",
    "timed_out": "multi_agent.run.failed",
    "cancelled": "multi_agent.run.cancelled",
    "interrupted": "multi_agent.run.failed",
}
_NODE_UPDATED_EVENT = "multi_agent.node.updated"


class MultiAgentCoordinator:
    """在父 turn 内运行固定深度、受限并发的 Worker 图。"""

    # 绑定固定 Worker Runner、并发上限、deadline 和事件出口。
    def __init__(
        self,
        runtime: Any | None = None,
        *,
        worker_runner: Any | None = None,
        max_concurrent_workers_per_run: int | None = None,
        max_concurrent_workers_global: int | None = None,
        run_timeout_seconds: float | None = None,
        worker_timeout_seconds: float | None = None,
        worker_result_token_limit: int | None = None,
        parent_result_token_limit: int | None = None,
        max_context_tokens: int | None = None,
        event_sink: EventSink | None = None,
        global_semaphore: asyncio.Semaphore | None = None,
    ) -> None:
        self.runtime = runtime
        settings = getattr(getattr(runtime, "settings", None), "multi_agent", None)
        self.worker_runner = worker_runner or WorkerRunner(runtime)
        self.max_concurrent_workers_per_run = int(
            getattr(settings, "max_concurrent_workers_per_run", 4)
            if max_concurrent_workers_per_run is None
            else max_concurrent_workers_per_run
        )
        global_limit = int(
            getattr(settings, "max_concurrent_workers_global", 8)
            if max_concurrent_workers_global is None
            else max_concurrent_workers_global
        )
        if self.max_concurrent_workers_per_run <= 0 or global_limit <= 0:
            raise ValueError("Worker 并发上限必须为正整数")
        self.run_timeout_seconds = float(
            getattr(settings, "run_timeout_seconds", 3600)
            if run_timeout_seconds is None
            else run_timeout_seconds
        )
        self.worker_timeout_seconds = float(
            getattr(settings, "worker_timeout_seconds", 900)
            if worker_timeout_seconds is None
            else worker_timeout_seconds
        )
        if self.run_timeout_seconds <= 0 or self.worker_timeout_seconds <= 0:
            raise ValueError("Worker deadline 必须为正数")
        if self.worker_timeout_seconds > self.run_timeout_seconds:
            raise ValueError("Worker deadline 不能超过 Run deadline")
        runtime_settings = getattr(getattr(runtime, "settings", None), "runtime", None)
        configured_context_limit = getattr(runtime_settings, "max_context_tokens", 300_000)
        self.max_context_tokens = int(
            configured_context_limit if max_context_tokens is None else max_context_tokens
        )
        multi_agent_settings = getattr(getattr(runtime, "settings", None), "multi_agent", None)
        self.worker_result_token_limit = int(
            getattr(multi_agent_settings, "worker_result_token_limit", 60_000)
            if worker_result_token_limit is None
            else worker_result_token_limit
        )
        self.parent_result_token_limit = int(
            getattr(multi_agent_settings, "parent_result_token_limit", 120_000)
            if parent_result_token_limit is None
            else parent_result_token_limit
        )
        if (
            self.max_context_tokens <= 0
            or self.worker_result_token_limit <= 0
            or self.parent_result_token_limit <= 0
        ):
            raise ValueError("Multi-Agent token 预算必须为正整数")
        self._global_semaphore = global_semaphore or asyncio.Semaphore(global_limit)
        self._event_sink = event_sink
        self._runs_by_request: dict[tuple[str, str], MultiAgentRun] = {}
        self._run_keys: dict[str, tuple[str, str]] = {}
        self._run_deadlines: dict[str, float] = {}
        self._event_errors: dict[str, str] = {}
        self._terminal_nodes: set[tuple[str, str]] = set()
        self._terminal_runs: set[str] = set()

    # 执行一个父请求唯一的固定拓扑 Run。
    async def delegate(
        self,
        request: MultiAgentRequest,
        parent: DelegateParentTurn,
    ) -> MultiAgentRun:
        profile_memory = parent.profile_memory
        session_id = parent.session_id
        request_id = parent.request_id
        key = (session_id, request_id)
        if key in self._runs_by_request:
            raise RuntimeError("每个父请求只能创建一次 Multi-Agent Run")
        now = self._now_iso()
        started = time.monotonic()
        deadline = started + self.run_timeout_seconds
        run = MultiAgentRun(
            run_id=str(uuid4()),
            parent_session_id=session_id,
            parent_request_id=request_id,
            strategy=request.strategy,
            status="queued",
            nodes=tuple(
                MultiAgentNode(
                    node_id=f"worker-{index}",
                    role=task.role,
                    status="queued",
                )
                for index, task in enumerate(request.tasks, start=1)
            ),
            created_at=now,
        )
        self._runs_by_request[key] = run
        self._run_keys[run.run_id] = key
        self._run_deadlines[run.run_id] = deadline
        await self._emit(_RUN_EVENT_BY_STATUS[run.status], run, None)
        if self._event_error(run.run_id) is not None:
            return await self._finish_event_failure(key, run)
        for node in run.nodes:
            await self._emit(_NODE_UPDATED_EVENT, run, node)
        if self._event_error(run.run_id) is not None:
            return await self._finish_event_failure(key, run)
        run = replace(run, status="running", started_at=self._now_iso())
        self._remember(key, run)
        await self._emit(_RUN_EVENT_BY_STATUS[run.status], run, None)
        if self._event_error(run.run_id) is not None:
            return await self._finish_event_failure(key, run)
        per_run = asyncio.Semaphore(self.max_concurrent_workers_per_run)

        if request.strategy is Strategy.FAN_OUT_FAN_IN:
            nodes = await self._run_fanout(
                run, request.tasks, parent, per_run, deadline, profile_memory
            )
        else:
            nodes = await self._run_sequential(
                run, request.tasks, parent, per_run, deadline, profile_memory
            )
        run = replace(run, nodes=nodes)
        if self._event_error(run.run_id) is not None:
            return await self._finish_event_failure(key, run)
        budget_error_code, budget_error = self._result_budget_error(run, parent)
        if budget_error_code is not None:
            run = replace(run, error_code=budget_error_code, error=budget_error)
        successful = [node for node in nodes if node.status == "completed"]
        failed = [node for node in nodes if node.status != "completed"]
        stop_requested = self._stop_requested(parent)
        deadline_expired = time.monotonic() >= deadline

        if stop_requested:
            run = self._finish(run, "cancelled", "用户已停止协作")
        elif deadline_expired:
            run = self._finish(run, "timed_out", "Multi-Agent Run 已超时")
        elif budget_error_code is not None:
            run = self._finish(run, "failed", budget_error)
        elif not successful or (
            request.strategy is not Strategy.FAN_OUT_FAN_IN and failed
        ):
            run = replace(
                run,
                error_code=self._first_error_code(nodes) or run.error_code,
            )
            run = self._finish(run, "failed", self._first_error(nodes))
        else:
            # Worker 收集完成后仍由父 Agent 继续汇总，保持运行状态。
            self._remember(key, run)
            return run
        self._remember(key, run)
        persisted = await self._emit(_RUN_EVENT_BY_STATUS[run.status], run, None)
        if not persisted:
            return await self._finish_event_failure(key, run)
        return run

    # 返回同一父请求当前保存的 Run 快照。
    def run_for(self, parent_session_id: str, parent_request_id: str) -> MultiAgentRun | None:
        return self._runs_by_request.get((parent_session_id, parent_request_id))

    # 返回仍由父 AgentLoop 共同遵守的 Run 绝对 deadline。
    def deadline_for(self, run_id: str) -> float | None:
        return self._run_deadlines.get(run_id)

    # 根据真实父 AgentLoop 结果一次性收口已完成 Worker 收集的 Run。
    async def finalize(self, run_id: str, outcome: str) -> MultiAgentRun:
        key = self._run_keys.get(run_id)
        if key is None:
            raise ValueError("未知 Multi-Agent Run")
        run = self._runs_by_request[key]
        if run.status in {"completed", "failed", "timed_out", "cancelled", "interrupted"}:
            self._release_run_runtime_state(run_id)
            return run
        deadline = self._run_deadlines.get(run_id)
        if outcome == "completed" and deadline is not None and time.monotonic() >= deadline:
            outcome = "timed_out"
        mapping = {
            "completed": ("completed", None),
            "failed": ("failed", "父 Agent synthesis 失败"),
            "cancelled": ("cancelled", "用户已停止协作"),
            "timed_out": ("timed_out", "Multi-Agent Run 已超时"),
        }
        if outcome not in mapping:
            raise ValueError("不支持的父 Run 收口结果")
        status, error = mapping[outcome]
        final = self._finish(run, status, error)
        self._remember(key, final)
        persisted = await self._emit(_RUN_EVENT_BY_STATUS[final.status], final, None)
        if not persisted:
            return await self._finish_event_failure(key, final)
        self._release_run_runtime_state(run_id)
        return final

    # 在父审批等待期间投影 waiting，恢复后投影 running。
    async def set_waiting(self, run_id: str, waiting: bool) -> MultiAgentRun | None:
        key = self._run_keys.get(run_id)
        if key is None:
            return None
        run = self._runs_by_request[key]
        if run.status in {"completed", "failed", "timed_out", "cancelled", "interrupted"}:
            return run
        status = "waiting" if waiting else "running"
        if run.status == status:
            return run
        updated = replace(run, status=status)
        self._remember(key, updated)
        await self._emit(_RUN_EVENT_BY_STATUS[updated.status], updated, None)
        return updated

    # 在进入父 synthesis 前以实际消息和工具 schema 复核上下文上限。
    async def check_parent_context(
        self,
        run_id: str,
        messages: list[dict[str, Any]],
        openai_tools: list[dict[str, Any]],
    ) -> str | None:
        key = self._run_keys.get(run_id)
        if key is None:
            return None
        run = self._runs_by_request[key]
        if run.status in {"completed", "failed", "timed_out", "cancelled", "interrupted"}:
            return None
        if self._actual_parent_context_tokens(messages, openai_tools) <= self.max_context_tokens:
            return None
        failed = replace(run, error_code=RESULT_CONTEXT_EXCEEDED_CODE)
        failed = self._finish(failed, "failed", RESULT_CONTEXT_EXCEEDED_ERROR)
        self._remember(key, failed)
        await self._emit("multi_agent.run.failed", failed, None)
        return f"Multi-Agent 协作失败：{RESULT_CONTEXT_EXCEEDED_ERROR}"

    # 并行启动 fan-out Worker 并按原任务顺序归并结果。
    async def _run_fanout(
        self,
        run: MultiAgentRun,
        tasks: tuple[MultiAgentTask, ...],
        parent: DelegateParentTurn,
        per_run: asyncio.Semaphore,
        deadline: float,
        profile_memory: ProfileMemorySnapshot,
    ) -> tuple[MultiAgentNode, ...]:
        executions = [
            asyncio.create_task(
                self._run_worker(
                    run,
                    task,
                    f"worker-{index}",
                    parent,
                    per_run,
                    deadline,
                    None,
                    profile_memory,
                ),
                name=f"multi-agent-{run.run_id}-worker-{index}",
            )
            for index, task in enumerate(tasks, start=1)
        ]
        return tuple(await asyncio.gather(*executions))

    # 顺序运行 pipeline Worker，并仅传紧邻成功前序。
    async def _run_sequential(
        self,
        run: MultiAgentRun,
        tasks: tuple[MultiAgentTask, ...],
        parent: DelegateParentTurn,
        per_run: asyncio.Semaphore,
        deadline: float,
        profile_memory: ProfileMemorySnapshot,
    ) -> tuple[MultiAgentNode, ...]:
        nodes: list[MultiAgentNode] = []
        dependency_content: str | None = None
        blocked = False
        skip_next = False
        for index, task in enumerate(tasks, start=1):
            node_id = f"worker-{index}"
            if skip_next:
                skip_next = False
                continue
            if blocked:
                cancelled_node = self._cancelled_node(node_id, task, "前序 Worker 未成功")
                nodes.append(cancelled_node)
                await self._emit(_NODE_UPDATED_EVENT, run, cancelled_node)
                continue
            node = await self._run_worker(
                run,
                task,
                node_id,
                parent,
                per_run,
                deadline,
                dependency_content,
                profile_memory,
            )
            nodes.append(node)
            if node.status != "completed" or node.result is None or node.result.content is None:
                blocked = True
                continue
            if index >= len(tasks):
                continue
            dependency_content, dependency_error = self._build_dependency(node)
            if dependency_error is not None:
                blocked = True
                failed_node = self._failed_node(
                    f"worker-{index + 1}",
                    tasks[index],
                    dependency_error,
                    RESULT_CONTEXT_EXCEEDED_CODE,
                )
                nodes.append(failed_node)
                await self._emit(_NODE_UPDATED_EVENT, run, failed_node)
                skip_next = True
                continue
        return tuple(nodes)

    # 在全局和每 Run semaphore 内执行一次有 deadline 的 Worker。
    async def _run_worker(
        self,
        run: MultiAgentRun,
        task: MultiAgentTask,
        node_id: str,
        parent: DelegateParentTurn,
        per_run: asyncio.Semaphore,
        deadline: float,
        dependency_content: str | None,
        profile_memory: ProfileMemorySnapshot,
    ) -> MultiAgentNode:
        acquired_run = False
        acquired_global = False
        started = time.monotonic()
        try:
            event_error = self._event_error(run.run_id)
            if event_error is not None:
                return self._event_failure_node(node_id, task, event_error)
            acquired_run = await self._acquire(per_run, parent, deadline)
            if not acquired_run:
                node = self._terminal_before_start(node_id, task, parent, deadline)
                await self._emit(_NODE_UPDATED_EVENT, run, node)
                return node
            acquired_global = await self._acquire(self._global_semaphore, parent, deadline)
            if not acquired_global:
                node = self._terminal_before_start(node_id, task, parent, deadline)
                await self._emit(_NODE_UPDATED_EVENT, run, node)
                return node
            await self._emit(
                _NODE_UPDATED_EVENT,
                run,
                MultiAgentNode(node_id, task.role, "running"),
            )
            event_error = self._event_error(run.run_id)
            if event_error is not None:
                return self._event_failure_node(node_id, task, event_error)
            remaining = max(0.0, deadline - time.monotonic())
            timeout = min(self.worker_timeout_seconds, remaining)
            if timeout <= 0:
                node = self._timed_out_node(node_id, task, started)
                await self._emit(_NODE_UPDATED_EVENT, run, node)
                return node
            try:
                result = await self._await_worker_result(
                    run.run_id,
                    node_id,
                    task,
                    dependency_content,
                    parent,
                    timeout,
                    profile_memory,
                )
            except asyncio.CancelledError:
                result = WorkerResult(status="cancelled", content=None, error="Worker 已取消")
            result, error_code = self._normalize_worker_result(result)
            if self._stop_requested(parent) and result.status == "completed":
                result = WorkerResult(
                    status="cancelled", content=None, error="用户已停止协作"
                )
                error_code = None
            node = MultiAgentNode(
                node_id=node_id,
                role=task.role,
                status=result.status,
                duration_ms=(time.monotonic() - started) * 1000,
                result=result,
                error=result.error,
                error_code=error_code,
            )
            await self._emit(
                _NODE_UPDATED_EVENT,
                run,
                node,
                usage=self._worker_usage(run.run_id, node.node_id),
            )
            return node
        finally:
            if acquired_global:
                self._global_semaphore.release()
            if acquired_run:
                per_run.release()

    # 在 Worker 运行期间轮询 Stop 和 deadline，并收口子任务结果。
    async def _await_worker_result(
        self,
        run_id: str,
        node_id: str,
        task: MultiAgentTask,
        dependency_content: str | None,
        parent: DelegateParentTurn,
        timeout: float,
        profile_memory: ProfileMemorySnapshot,
    ) -> WorkerResult:
        worker_task = asyncio.create_task(
            self.worker_runner.run(
                task,
                dependency_content=dependency_content,
                run_id=run_id,
                node_id=node_id,
                profile_memory=profile_memory,
            ),
            name=f"multi-agent-worker-{node_id}",
        )
        worker_deadline = time.monotonic() + timeout
        try:
            while True:
                if self._stop_requested(parent):
                    await self._cancel_worker_task(worker_task)
                    return WorkerResult(status="cancelled", content=None, error="用户已停止协作")
                remaining = worker_deadline - time.monotonic()
                if remaining <= 0:
                    await self._cancel_worker_task(worker_task)
                    return WorkerResult(status="timed_out", content=None, error="Worker 执行超时")
                done, _ = await asyncio.wait(
                    {worker_task},
                    timeout=min(0.05, remaining),
                )
                if not done:
                    continue
                try:
                    result = worker_task.result()
                except asyncio.CancelledError:
                    return WorkerResult(status="cancelled", content=None, error="Worker 已取消")
                except Exception:
                    return WorkerResult(status="failed", content=None, error="Worker 执行失败")
                return result
        except asyncio.CancelledError:
            await self._cancel_worker_task(worker_task)
            raise

    # 有界等待已取消 Worker 清理，避免 Stop 被拖到完整 deadline。
    async def _cancel_worker_task(self, worker_task: asyncio.Task[Any]) -> None:
        if not worker_task.done():
            worker_task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(worker_task), timeout=0.2)
        except (asyncio.CancelledError, TimeoutError, Exception):
            return

    # 读取并校验 Worker Runner 保留的最小 usage 字段。
    def _worker_usage(self, run_id: str, node_id: str) -> dict[str, int] | None:
        getter = getattr(self.worker_runner, "usage_for", None)
        if not callable(getter):
            return None
        value = getter(run_id, node_id)
        if not isinstance(value, dict):
            return None
        clean: dict[str, int] = {}
        for field in ("input_tokens", "output_tokens", "turn_total_tokens"):
            item = value.get(field)
            if not isinstance(item, int) or isinstance(item, bool) or item < 0:
                return None
            clean[field] = item
        return clean if clean["turn_total_tokens"] > 0 else None

    # 统一校验并脱敏 Worker 返回，拒绝任何超预算或非契约结果。
    def _normalize_worker_result(
        self,
        result: object,
    ) -> tuple[WorkerResult, str | None]:
        if not isinstance(result, WorkerResult):
            return self._controlled_failure(INVALID_RESULT_ERROR), RESULT_CONTEXT_EXCEEDED_CODE
        if result.status != "completed":
            return (
                WorkerResult(
                    status=result.status,
                    content=None,
                    error=self._safe_error(result.error),
                ),
                None,
            )
        content = self._sanitize_content(result.content)
        if not content.strip():
            return self._controlled_failure(INVALID_RESULT_ERROR), RESULT_CONTEXT_EXCEEDED_CODE
        if count_content_tokens(content) > self.worker_result_token_limit:
            return self._controlled_failure(RESULT_CONTEXT_EXCEEDED_ERROR), RESULT_CONTEXT_EXCEEDED_CODE
        return WorkerResult(status="completed", content=content), None

    # 生成不会携带 Worker 原文的固定失败结果。
    @staticmethod
    def _controlled_failure(error: str) -> WorkerResult:
        return WorkerResult(status="failed", content=None, error=error)

    # 将常见凭据值替换为固定占位符后再进入父上下文。
    @staticmethod
    def _sanitize_content(content: object) -> str:
        if not isinstance(content, str):
            return ""
        redacted = _BEARER_VALUE.sub(r"\1[REDACTED]", content)
        redacted = _SECRET_ASSIGNMENT.sub(r"\1[REDACTED]", redacted)
        return _TOKEN_VALUE.sub("[REDACTED]", redacted)

    # 保证事件和 Run 摘要只携带固定长度的脱敏错误。
    @classmethod
    def _safe_error(cls, error: object) -> str:
        value = cls._sanitize_content(error)
        return " ".join(value.split())[:256] or "Worker 执行失败"

    # 在 Coordinator 边界验证紧邻前序的有界结果。
    def _build_dependency(
        self,
        node: MultiAgentNode,
    ) -> tuple[str | None, str | None]:
        if node.result is None or node.result.content is None:
            return None, RESULT_CONTEXT_EXCEEDED_ERROR
        content = node.result.content
        if (
            len(content) > WORKER_DEPENDENCY_MAX_CHARS
            or count_content_tokens(content) > WORKER_DEPENDENCY_MAX_TOKENS
        ):
            return None, RESULT_CONTEXT_EXCEEDED_ERROR
        return content, None

    # 汇总成功 Worker 的实际 token 并检查父结果和 Runtime 上下文预算。
    def _result_budget_error(
        self,
        run: MultiAgentRun,
        parent: DelegateParentTurn,
    ) -> tuple[str | None, str | None]:
        contents = [
            node.result.content
            for node in run.nodes
            if node.status == "completed"
            and node.result is not None
            and isinstance(node.result.content, str)
        ]
        result_tokens = sum(count_content_tokens(content) for content in contents)
        if result_tokens > self.parent_result_token_limit:
            return RESULT_CONTEXT_EXCEEDED_CODE, RESULT_CONTEXT_EXCEEDED_ERROR
        if parent.initial_context_tokens + result_tokens > self.max_context_tokens:
            return RESULT_CONTEXT_EXCEEDED_CODE, RESULT_CONTEXT_EXCEEDED_ERROR
        return None, None

    # 序列化实际模型消息与 schema，避免复用过期 BUILD 估算。
    @staticmethod
    def _actual_parent_context_tokens(
        messages: list[dict[str, Any]],
        openai_tools: list[dict[str, Any]],
    ) -> int:
        try:
            return count_content_tokens(
                json.dumps(
                    {"messages": messages, "tools": openai_tools},
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        except (TypeError, ValueError):
            return 0

    # 轮询取得 semaphore，以便等待期间及时观察 Stop 和 Run deadline。
    async def _acquire(
        self,
        semaphore: asyncio.Semaphore,
        parent: DelegateParentTurn,
        deadline: float,
    ) -> bool:
        while not self._stop_requested(parent):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            try:
                await asyncio.wait_for(semaphore.acquire(), timeout=min(0.05, remaining))
                return True
            except TimeoutError:
                continue
        return False

    # 发布固定安全事件且兼容同步和异步 sink。
    async def _emit(
        self,
        event_type: str,
        run: MultiAgentRun,
        node: MultiAgentNode | None,
        *,
        usage: dict[str, int] | None = None,
    ) -> bool:
        if event_type not in MULTI_AGENT_EVENT_TYPES:
            raise ValueError("不支持的 Multi-Agent 事件类型")
        if self._event_sink is None:
            return True
        terminal_statuses = {"completed", "failed", "timed_out", "cancelled", "interrupted"}
        node_terminal_key: tuple[str, str] | None = None
        run_terminal = False
        if node is not None and node.status in terminal_statuses:
            node_terminal_key = (run.run_id, node.node_id)
            if node_terminal_key in self._terminal_nodes:
                return True
        if node is None and run.status in terminal_statuses:
            if run.run_id in self._terminal_runs:
                return True
            run_terminal = True
        payload = MultiAgentEventPayload(
            run_id=run.run_id,
            node_id=node.node_id if node is not None else None,
            task_label=self._safe_task_label(node.role if node is not None else "multi_agent"),
            strategy=run.strategy.value,
            status=node.status if node is not None else run.status,
            duration_ms=node.duration_ms if node is not None else self._run_duration_ms(run),
            usage=usage,
            error_code=node.error_code if node is not None else run.error_code,
            error=self._safe_error(node.error if node is not None else run.error)
            if (node.error if node is not None else run.error)
            else None,
            content=(
                node.result.content
                if node is not None
                and node.status == "completed"
                and node.result is not None
                and isinstance(node.result.content, str)
                else None
            ),
        )
        try:
            result = self._event_sink(event_type, payload)
            if inspect.isawaitable(result):
                await result
        except asyncio.CancelledError:
            raise
        except Exception:
            self._event_errors.setdefault(run.run_id, "Multi-Agent 事件持久化失败")
            return False
        if node_terminal_key is not None:
            self._terminal_nodes.add(node_terminal_key)
        if run_terminal:
            self._terminal_runs.add(run.run_id)
        return True

    # 将用户或模型提供的角色标签限制为可安全投影的短文本。
    @staticmethod
    def _safe_task_label(value: object) -> str:
        raw = str(value or "")
        if any(ord(char) < 32 or ord(char) == 127 for char in raw):
            return "multi_agent"
        text = " ".join(raw.split())
        if len(text) > 64:
            return "multi_agent"
        if not text or any(not (char.isalnum() or char in " _-") for char in text):
            return "multi_agent"
        return text

    # 计算当前 Run 从启动到投影时刻的安全耗时。
    @staticmethod
    def _run_duration_ms(run: MultiAgentRun) -> float | None:
        if run.started_at is None:
            return None
        try:
            started_at = datetime.fromisoformat(run.started_at)
            finished_at = datetime.fromisoformat(run.finished_at) if run.finished_at else datetime.now(UTC)
        except ValueError:
            return None
        return max(0.0, (finished_at - started_at).total_seconds() * 1000)

    # 返回本 Run 第一次事件落盘故障的脱敏摘要。
    def _event_error(self, run_id: str) -> str | None:
        return self._event_errors.get(run_id)

    # 将事件持久化故障确定性收口，避免遗留活跃 Run。
    async def _finish_event_failure(
        self,
        key: tuple[str, str],
        run: MultiAgentRun,
    ) -> MultiAgentRun:
        failed = replace(run, error_code="event_persistence_failed")
        failed = self._finish(failed, "failed", self._event_error(run.run_id) or "Multi-Agent 事件持久化失败")
        self._remember(key, failed)
        await self._emit("multi_agent.run.failed", failed, None)
        return failed

    # 释放已持久化终态 Run 的全部进程内生命周期状态。
    def _release_run_runtime_state(self, run_id: str) -> None:
        key = self._run_keys.pop(run_id, None)
        if key is not None:
            self._runs_by_request.pop(key, None)
        self._run_deadlines.pop(run_id, None)
        self._event_errors.pop(run_id, None)
        self._terminal_runs.discard(run_id)
        self._terminal_nodes = {
            key for key in self._terminal_nodes if key[0] != run_id
        }
        release_worker_run = getattr(self.worker_runner, "release_run", None)
        if callable(release_worker_run):
            release_worker_run(run_id)

    # 保存当前父请求的最新 Run 快照。
    def _remember(self, key: tuple[str, str], run: MultiAgentRun) -> None:
        self._runs_by_request[key] = run

    # 构造事件持久化失败后不启动 Worker 的受控节点结果。
    @staticmethod
    def _event_failure_node(node_id: str, task: MultiAgentTask, error: str) -> MultiAgentNode:
        return MultiAgentCoordinator._failed_node(node_id, task, error, "event_persistence_failed")

    # 在父 Channel 查询异常时保守停止当前协作。
    @staticmethod
    def _stop_requested(parent: DelegateParentTurn) -> bool:
        try:
            return bool(parent.is_stop_requested())
        except Exception:
            return True

    # 生成当前 UTC 时间字符串。
    @staticmethod
    def _now_iso() -> str:
        return datetime.now(UTC).isoformat()

    # 构造尚未启动节点的取消结果。
    @staticmethod
    def _cancelled_node(node_id: str, task: MultiAgentTask, error: str) -> MultiAgentNode:
        result = WorkerResult(status="cancelled", content=None, error=error)
        return MultiAgentNode(node_id, task.role, "cancelled", result=result, error=error)

    # 构造带固定错误码的受控失败节点。
    @staticmethod
    def _failed_node(
        node_id: str,
        task: MultiAgentTask,
        error: str,
        error_code: str,
    ) -> MultiAgentNode:
        result = WorkerResult(status="failed", content=None, error=error)
        return MultiAgentNode(
            node_id,
            task.role,
            "failed",
            result=result,
            error=error,
            error_code=error_code,
        )

    # 根据 Stop 或 deadline 构造未启动节点终态。
    def _terminal_before_start(
        self,
        node_id: str,
        task: MultiAgentTask,
        parent: DelegateParentTurn,
        deadline: float,
    ) -> MultiAgentNode:
        if self._stop_requested(parent):
            return self._cancelled_node(node_id, task, "用户已停止协作")
        if time.monotonic() >= deadline:
            return self._timed_out_node(node_id, task, time.monotonic())
        return self._cancelled_node(node_id, task, "Worker 未取得执行槽位")

    # 构造 Worker 超时节点。
    @staticmethod
    def _timed_out_node(node_id: str, task: MultiAgentTask, started: float) -> MultiAgentNode:
        result = WorkerResult(status="timed_out", content=None, error="Worker 执行超时")
        return MultiAgentNode(
            node_id,
            task.role,
            "timed_out",
            duration_ms=max(0.0, (time.monotonic() - started) * 1000),
            result=result,
            error=result.error,
        )

    # 收口 Run 终态。
    @staticmethod
    def _finish(
        run: MultiAgentRun,
        status: str,
        error: str | None,
    ) -> MultiAgentRun:
        return replace(
            run,
            status=status,
            finished_at=datetime.now(UTC).isoformat(),
            error=error,
        )

    # 返回第一个安全 Worker 错误作为 Run 摘要。
    @staticmethod
    def _first_error(nodes: tuple[MultiAgentNode, ...]) -> str:
        return next((node.error for node in nodes if node.error), "Multi-Agent Worker 全部失败")

    # 将受控节点错误码提升到失败 Run，供 trace 和事件消费者稳定识别。
    @staticmethod
    def _first_error_code(nodes: tuple[MultiAgentNode, ...]) -> str | None:
        return next((node.error_code for node in nodes if node.error_code), None)

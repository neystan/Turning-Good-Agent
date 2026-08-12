from __future__ import annotations

import copy
import json
from typing import Any

from ..channels.base import ChannelAdapter
from ..llm.types import ToolCall
from .coordinator import MultiAgentCoordinator
from .schema import DELEGATE_MULTI_AGENT_SCHEMA, DelegateSchemaError, validate_delegate_request
from .types import DelegateParentTurn, MultiAgentRun


DELEGATE_MULTI_AGENT_NAME = "delegate_multi_agent"
DELEGATE_MULTI_AGENT_DESCRIPTION = "使用固定拓扑运行受限只读 Worker，并由父 Agent 汇总结果。"


class DelegateMultiAgentInvocation:
    """持有单个父回合唯一的 Multi-Agent 委派生命周期。"""

    # 初始化本父回合的专用委派状态。
    def __init__(
        self,
        coordinator: MultiAgentCoordinator,
        parent: DelegateParentTurn,
        *,
        max_workers_per_run: int,
    ) -> None:
        if max_workers_per_run <= 0:
            raise ValueError("max_workers_per_run 必须是正整数")
        self.coordinator = coordinator
        self.parent = parent
        self.max_workers_per_run = max_workers_per_run
        self._accepted = False
        self._run_id: str | None = None
        self._schema_visible = True
        self._finished = False

    # 返回已受理委派的稳定 Run 标识。
    @property
    def run_id(self) -> str | None:
        return self._run_id

    # 返回当前父模型回合是否仍可看到委派 schema。
    @property
    def is_schema_visible(self) -> bool:
        return self._schema_visible

    # 构造不进入 ToolRegistry 的唯一委派 schema。
    def openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": DELEGATE_MULTI_AGENT_NAME,
                "description": DELEGATE_MULTI_AGENT_DESCRIPTION,
                "parameters": copy.deepcopy(DELEGATE_MULTI_AGENT_SCHEMA),
            },
        }

    # 执行本次委派并返回父模型可消费的安全 JSON 结果。
    async def invoke(self, args: dict[str, Any]) -> str:
        if self._accepted:
            return self._json_result(
                status="failed",
                error_code="duplicate_delegate",
                error="每个父请求只能受理一次 delegate_multi_agent 调用。",
            )
        try:
            request = validate_delegate_request(args, self.max_workers_per_run)
        except DelegateSchemaError as exc:
            return self._json_result(
                status="failed",
                error_code="invalid_delegate_request",
                error=f"委派请求无效：{exc}",
            )
        self._accepted = True
        self._schema_visible = False
        try:
            run = await self.coordinator.delegate(request, self.parent)
        except Exception:
            active = self.coordinator.run_for(self.parent.session_id, self.parent.request_id)
            if active is not None:
                self._run_id = active.run_id
            return self._json_result(
                status="failed",
                error_code="multi_agent_runtime_error",
                error="Multi-Agent 协作失败：运行时未能建立协作状态。",
            )
        self._run_id = run.run_id
        return self._run_content(run)

    # 在父 synthesis 前复核 deadline、终态和上下文预算。
    async def check_parent_synthesis(
        self,
        parent_messages: list[dict[str, Any]],
        parent_tools: list[dict[str, Any]],
    ) -> str | None:
        if self._run_id is None:
            return None
        run = self.coordinator.run_for(self.parent.session_id, self.parent.request_id)
        if run is not None and run.status in {
            "failed",
            "timed_out",
            "cancelled",
            "interrupted",
        }:
            return self._terminal_content(run)
        return await self.coordinator.check_parent_context(
            self._run_id,
            parent_messages,
            parent_tools,
        )

    # 只将当前父回合的最终结果交给 Coordinator 收口一次。
    async def finish_parent_turn(self, outcome: str) -> None:
        if self._finished or self._run_id is None:
            return
        self._finished = True
        try:
            await self.coordinator.finalize(self._run_id, outcome)
        except ValueError:
            return

    # 返回 Coordinator 保存的绝对 Run deadline。
    def deadline_monotonic(self) -> float | None:
        if self._run_id is None:
            return None
        return self.coordinator.deadline_for(self._run_id)

    # 将父审批调用包装为该委派 Run 的 waiting 状态投影。
    def wrap_parent_approval(self, adapter: ChannelAdapter) -> ChannelAdapter:
        if self._run_id is None:
            return adapter
        return _DelegateApprovalAdapter(adapter, self)

    # 在父审批开始或结束时更新专用 Run 状态。
    async def _set_waiting(self, waiting: bool) -> None:
        if self._run_id is not None:
            await self.coordinator.set_waiting(self._run_id, waiting)

    # 将 Run 状态转换为不携带 Worker 原文的安全父消息。
    @classmethod
    def _run_content(cls, run: MultiAgentRun) -> str:
        partial = run.strategy.value == "fan_out_fan_in" and any(
            node.status == "completed" for node in run.nodes
        ) and any(node.status != "completed" for node in run.nodes)
        completed = [
            {
                "node_id": node.node_id,
                "role": node.role,
                "content": node.result.content,
            }
            for node in run.nodes
            if node.status == "completed" and node.result is not None
        ]
        failures = [
            {
                "node_id": node.node_id,
                "status": node.status,
                "error_code": node.error_code,
                "error": node.error,
            }
            for node in run.nodes
            if node.status != "completed"
        ]
        if run.status in {"failed", "timed_out", "cancelled", "interrupted"}:
            completed = []
        return cls._json_result(
            status=run.status,
            run_id=run.run_id,
            partial=partial,
            error_code=run.error_code,
            error=run.error,
            results=completed,
            failures=failures,
        )

    # 生成父模型不应继续调用的固定终态文本。
    @staticmethod
    def _terminal_content(run: MultiAgentRun) -> str:
        if run.status == "timed_out":
            return "Multi-Agent 协作已超时。"
        if run.status == "cancelled":
            return "Multi-Agent 协作已停止。"
        if run.status == "interrupted":
            return "Multi-Agent 协作已中断，请重新提交任务。"
        return f"Multi-Agent 协作失败：{run.error or 'Worker 未能完成'}"

    # 生成固定字段的 JSON Tool 结果，避免回传任意内部对象。
    @staticmethod
    def _json_result(**fields: object) -> str:
        return json.dumps(fields, ensure_ascii=False, separators=(",", ":"))


class _DelegateApprovalAdapter:
    """只为已受理委派同步父工具审批 waiting 状态。"""

    # 绑定原始 Channel 与当前 invocation。
    def __init__(self, adapter: ChannelAdapter, invocation: DelegateMultiAgentInvocation) -> None:
        self._adapter = adapter
        self._invocation = invocation

    # 转发不需要委派状态的 Channel 方法。
    def __getattr__(self, name: str) -> Any:
        return getattr(self._adapter, name)

    # 在审批 Future 前后投影 Multi-Agent waiting 状态。
    async def request_tool_approval(self, call: ToolCall) -> str | None:
        await self._invocation._set_waiting(True)
        try:
            return await self._adapter.request_tool_approval(call)
        finally:
            await self._invocation._set_waiting(False)

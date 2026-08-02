from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from ..tools.base import ToolResult
from .executor import ProactiveExecutionContext, ProactiveExecutionResult
from .scheduler import local_run_at_after_delay, next_job_fire, validate_cron
from .store import ProactiveStore
from .types import CronJob, new_id, utc_now_iso


class CronExecutor(Protocol):
    """Cron 所需的后台执行接口。"""

    async def run(
        self,
        context: ProactiveExecutionContext,
        prompt: str,
        *,
        allow_tools: bool = True,
        on_started: Callable[[], Awaitable[None]] | None = None,
    ) -> ProactiveExecutionResult:
        """执行后台任务。"""


class CronDeliveryGate(Protocol):
    """Cron 所需的 durable 投递接口。"""

    async def enqueue(self, **payload: Any) -> Any:
        """写入待投递记录。"""


class CronIncidentReporter(Protocol):
    """Cron 所需的异常边沿接口。"""

    async def report_failure(self, *, fingerprint: str, source: str, message: str) -> bool:
        """记录失败边沿。"""

    async def report_recovery(self, *, fingerprint: str, source: str, message: str) -> bool:
        """记录恢复边沿。"""


class CronManager:
    """维护 Cron durable 状态及每个 Job 的下一触发时刻。"""

    def __init__(
        self,
        store: ProactiveStore,
        executor: CronExecutor,
        delivery_gate: CronDeliveryGate,
        proactive_settings: Any,
        *,
        target_channel: str,
        incidents: CronIncidentReporter | None = None,
        clock: Callable[[], datetime] | None = None,
        schedule_changed: Callable[[], None] | None = None,
    ) -> None:
        """恢复状态，并从当前时刻重算未来 deadline。"""
        self._store = store
        self._executor = executor
        self._delivery_gate = delivery_gate
        self._timezone = ZoneInfo(str(proactive_settings.timezone))
        self._target_channel = target_channel
        self._incidents = incidents
        self._clock = clock or (lambda: datetime.now(UTC))
        self._schedule_changed = schedule_changed
        self._jobs = {job.id: job for job in store.load_cron_jobs()}
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._running_ids: set[str] = set()
        self._next_fire: dict[str, datetime] = {}
        self._deleting: set[str] = set()
        self._lock = asyncio.Lock()
        self._rebuild_deadlines(self._now())

    def create_job(
        self,
        prompt: str,
        *,
        recurring: bool,
        cron: str | None = None,
        run_at: str | None = None,
        delivery_channels: Iterable[str] | None = None,
    ) -> CronJob:
        """验证并保存一条周期或一次性 Cron Job。"""
        if cron is not None:
            error = validate_cron(cron)
            if error:
                raise ValueError(error)
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt 不能为空")
        channels = [str(channel) for channel in (delivery_channels or ["all"])]
        if not channels or any(not channel.strip() for channel in channels):
            raise ValueError("delivery_channels 至少需要一个非空 channel")
        now = self._now()
        now_iso = now.isoformat()
        job = CronJob(
            id=new_id("cron"),
            cron=cron,
            run_at=run_at,
            created_at=now_iso,
            prompt=prompt,
            recurring=recurring,
            delivery_channels=channels,
            updated_at=now_iso,
        )
        deadline = next_job_fire(job, now, self._timezone)
        if deadline is None:
            raise ValueError("run_at 必须晚于当前时间")
        self._jobs[job.id] = job
        self._next_fire[job.id] = deadline.astimezone(UTC)
        self._save_jobs()
        self._notify_schedule_changed()
        return job

    def run_at_after_delay(self, delay_seconds: float) -> str:
        """使用与触发器相同的时钟和时区解析相对延迟。"""
        return local_run_at_after_delay(delay_seconds, self._now(), self._timezone)

    def list_jobs(self) -> list[CronJob]:
        """按创建时间返回全部现存 Cron Job。"""
        return sorted(self._jobs.values(), key=lambda job: (job.created_at, job.id))

    def get_job(self, job_id: str) -> CronJob:
        """读取一个 Job，不存在时返回明确错误。"""
        try:
            return self._jobs[job_id]
        except KeyError as exc:
            raise ValueError(f"Cron Job 不存在：{job_id}") from exc

    def runtime_state(self, job_id: str) -> str:
        """从当前进程的 Task 集合投影 active/queued/running。"""
        self.get_job(job_id)
        if job_id in self._running_ids:
            return "running"
        if job_id in self._tasks:
            return "queued"
        return "active"

    async def delete_job(self, job_id: str) -> CronJob:
        """取消运行任务并完全删除该 Cron 的所有持久化记录。"""
        async with self._lock:
            job = self.get_job(job_id)
            self._deleting.add(job_id)
            task = self._tasks.get(job_id)
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        async with self._lock:
            self._jobs.pop(job_id, None)
            self._next_fire.pop(job_id, None)
            self._tasks.pop(job_id, None)
            self._running_ids.discard(job_id)
            self._save_jobs()
            self._purge_related_records(job_id)
            self._deleting.discard(job_id)
        self._notify_schedule_changed()
        return job

    def next_deadline(self, now: datetime | None = None) -> datetime | None:
        """返回最早的 UTC deadline；已到期值由 run_due 立即消费。"""
        del now
        return min(self._next_fire.values(), default=None)

    async def run_due(self, now: datetime | None = None) -> int:
        """仅启动当前已到期的 Job，并推进其下一 deadline。"""
        moment = _as_utc(now or self._now())
        created = 0
        changed = False
        async with self._lock:
            due = sorted(
                (
                    (deadline, job_id)
                    for job_id, deadline in self._next_fire.items()
                    if deadline <= moment
                ),
                key=lambda item: (item[0], item[1]),
            )
            for scheduled_at, job_id in due:
                job = self._jobs.get(job_id)
                if job is None or job_id in self._deleting:
                    self._next_fire.pop(job_id, None)
                    changed = True
                    continue
                if job_id in self._tasks:
                    self._append_audit(job, "skipped_overlap", scheduled_at=scheduled_at)
                    self._advance_deadline(job, scheduled_at)
                    changed = True
                    continue
                self._append_audit(job, "queued", scheduled_at=scheduled_at)
                self._advance_deadline(job, scheduled_at)
                task = asyncio.create_task(
                    self._run_job(job.id, scheduled_at),
                    name=f"proactive-cron-{job.id}",
                )
                self._tasks[job.id] = task
                created += 1
                changed = True
        if changed:
            self._notify_schedule_changed()
        return created

    def update_timezone(self, name: str) -> None:
        """切换全局 IANA 时区并立即重算所有未完成 Job。"""
        self._timezone = ZoneInfo(name)
        self._rebuild_deadlines(self._now())
        self._notify_schedule_changed()

    async def wait_for_jobs(self) -> None:
        """等待当前已启动的后台 Job 完成，供关闭和确定性测试使用。"""
        while self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)

    async def close(self) -> None:
        """取消尚未结束的 Cron 任务并完成 durable 取消收尾。"""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_job(self, job_id: str, scheduled_at: datetime) -> None:
        """执行 Cron 后台任务，并在所有出口清理 Task 表。"""
        try:
            job = self.get_job(job_id)
            result = await self._executor.run(
                ProactiveExecutionContext(capability="cron", job_id=job.id),
                job.prompt,
                on_started=lambda: self._mark_running(job_id, scheduled_at),
            )
            await self._finish_job(job_id, result)
        except asyncio.CancelledError:
            await self._cancel_job(job_id)
            raise
        except Exception:
            await self._finish_job(
                job_id,
                ProactiveExecutionResult(False, "", usage=_empty_usage(), error="后台任务执行失败"),
            )
        finally:
            self._tasks.pop(job_id, None)
            self._running_ids.discard(job_id)
            self._notify_schedule_changed()

    async def _mark_running(self, job_id: str, scheduled_at: datetime) -> None:
        """仅在共享 semaphore 已取得后记录内存 running 与 started 审计。"""
        async with self._lock:
            if job_id in self._deleting or job_id not in self._jobs:
                raise asyncio.CancelledError
            job = self._jobs[job_id]
            self._running_ids.add(job_id)
            self._append_audit(job, "started", scheduled_at=scheduled_at)

    async def _cancel_job(self, job_id: str) -> None:
        """把 queued/running 的取消恢复为可解释的 durable 终态。"""
        async with self._lock:
            if job_id in self._deleting:
                return
            job = self._jobs.get(job_id)
            if job is None:
                return
            self._append_audit(job, "cancelled", error="后台任务已取消")
            if not job.recurring:
                self._jobs.pop(job_id, None)
                self._next_fire.pop(job_id, None)
                self._save_jobs()

    async def _finish_job(self, job_id: str, result: ProactiveExecutionResult) -> None:
        """保存 Cron 结果，并仅为仍存在且未删除的 Job 投递。"""
        async with self._lock:
            if job_id in self._deleting or job_id not in self._jobs:
                return
            job = self._jobs[job_id]
            if result.success:
                self._append_audit(job, "completed", result=result)
                targets = self._delivery_targets(job)
            else:
                self._append_audit(
                    job,
                    "failed",
                    result=result,
                    error=_safe_failure_message(result.error),
                )
                targets = []
            if not job.recurring:
                self._jobs.pop(job_id, None)
                self._next_fire.pop(job_id, None)
                self._save_jobs()
        if result.success:
            for channel in targets:
                if job_id in self._deleting:
                    return
                await self._delivery_gate.enqueue(
                    session_id="proactive",
                    target_channel=channel,
                    event_type="proactive.cron.completed",
                    content=f"定时任务已完成：{result.content}",
                    metadata={"cron_id": job.id, "recurring": job.recurring},
                )
            if self._incidents is not None and job_id not in self._deleting:
                await self._incidents.report_recovery(
                    fingerprint=f"proactive:cron:{job.id}",
                    source="cron",
                    message=f"定时任务 {job.id} 已恢复。",
                )
            return
        if self._incidents is not None and job_id not in self._deleting:
            await self._incidents.report_failure(
                fingerprint=f"proactive:cron:{job.id}",
                source="cron",
                message=f"定时任务 {job.id} 执行失败。",
            )

    def _advance_deadline(self, job: CronJob, scheduled_at: datetime) -> None:
        """消费本次 deadline，并只为周期 Job 计算下一次。"""
        if not job.recurring:
            self._next_fire.pop(job.id, None)
            return
        next_fire = next_job_fire(job, scheduled_at, self._timezone)
        if next_fire is None:
            self._next_fire.pop(job.id, None)
        else:
            self._next_fire[job.id] = next_fire.astimezone(UTC)

    def _rebuild_deadlines(self, now: datetime) -> None:
        """从当前时刻重建未完成 Job 的内存 next-fire 表。"""
        self._next_fire.clear()
        expired_one_shots: list[str] = []
        for job in list(self._jobs.values()):
            if job.id in self._tasks and not job.recurring:
                continue
            next_fire = next_job_fire(job, now, self._timezone)
            if next_fire is not None:
                self._next_fire[job.id] = next_fire.astimezone(UTC)
            elif not job.recurring:
                expired_one_shots.append(job.id)
        if expired_one_shots:
            for job_id in expired_one_shots:
                self._jobs.pop(job_id, None)
            self._save_jobs()

    def _delivery_targets(self, job: CronJob) -> list[str]:
        """解析任务投递渠道。"""
        if "all" in job.delivery_channels or self._target_channel in job.delivery_channels:
            return [self._target_channel]
        return []

    def _append_audit(
        self,
        job: CronJob,
        status: str,
        *,
        scheduled_at: datetime | None = None,
        result: ProactiveExecutionResult | None = None,
        error: str | None = None,
    ) -> None:
        """追加任务审计记录。"""
        payload: dict[str, Any] = {
            "id": new_id("cron_audit"),
            "created_at": self._now().isoformat(),
            "cron_id": job.id,
            "status": status,
        }
        if scheduled_at is not None:
            payload["scheduled_at"] = scheduled_at.astimezone(UTC).isoformat()
        if result is not None:
            payload["usage"] = {
                "input_tokens": result.usage.input_tokens,
                "output_tokens": result.usage.output_tokens,
                "total_tokens": result.usage.total_tokens,
            }
            payload["tool_call_count"] = len(result.tool_calls)
        if error:
            payload["error"] = error
        self._store.append_jsonl("cron_audit.jsonl", payload)

    def _purge_related_records(self, job_id: str) -> None:
        """硬删除 Cron 在所有主动审计/投递文件中的关联行。"""
        fingerprint = f"proactive:cron:{job_id}"
        self._store.filter_jsonl("cron_audit.jsonl", lambda row: row.get("cron_id") != job_id)
        self._store.filter_jsonl("executions.jsonl", lambda row: row.get("job_id") != job_id)

        def keep_delivery(row: dict[str, Any]) -> bool:
            metadata = row.get("metadata")
            if not isinstance(metadata, dict):
                return True
            return metadata.get("cron_id") != job_id and metadata.get("fingerprint") != fingerprint

        self._store.filter_jsonl("deliveries.jsonl", keep_delivery)
        self._store.filter_jsonl("incidents.jsonl", lambda row: row.get("fingerprint") != fingerprint)

    def _save_jobs(self) -> None:
        """保存 Cron 任务状态。"""
        self._store.save_cron_jobs(self._jobs.values())

    def _now(self) -> datetime:
        """读取可测试的 aware UTC 时钟。"""
        return _as_utc(self._clock())

    def _notify_schedule_changed(self) -> None:
        """唤醒 Service 重新读取最早 deadline。"""
        if self._schedule_changed is not None:
            self._schedule_changed()


class CreateCronTool:
    """创建一个由 CronManager 调度的后台任务。"""

    name = "create_cron"
    description = "创建周期 Cron 或一次性本地时间/相对延迟任务。"
    parallel_safe = False
    input_schema = {
        "type": "object",
        "properties": {
            "cron": {"type": ["string", "null"], "description": "周期任务的五字段 Cron 表达式"},
            "run_at": {
                "type": ["string", "null"],
                "description": "一次性任务在 proactive.timezone 中的不含 offset ISO 本地时间",
            },
            "delay_seconds": {
                "type": ["number", "null"],
                "minimum": 0.001,
                "description": "一次性任务从当前时刻起的延迟秒数",
            },
            "prompt": {"type": "string", "description": "到时交给后台 Agent 的完整任务提示词"},
            "recurring": {"type": "boolean", "description": "是否重复执行"},
            "delivery_channels": {
                "type": "array",
                "items": {"type": "string"},
                "description": "通知渠道，默认 all",
            },
        },
        "required": ["prompt", "recurring"],
    }

    def __init__(self, manager: CronManager) -> None:
        self._manager = manager

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """验证互斥计划参数并创建任务。"""
        cron = _optional_arg(args.get("cron"))
        run_at = _optional_arg(args.get("run_at"))
        delay_seconds = args.get("delay_seconds")
        if delay_seconds is not None:
            if cron is not None or run_at is not None:
                raise ValueError("delay_seconds 不能与 cron 或 run_at 同时设置")
            run_at = self._manager.run_at_after_delay(delay_seconds)
        job = self._manager.create_job(
            str(args["prompt"]),
            cron=cron,
            run_at=run_at,
            recurring=bool(args["recurring"]),
            delivery_channels=args.get("delivery_channels"),
        )
        mode = "周期性" if job.recurring else "一次性"
        schedule = job.cron if job.recurring else job.run_at
        return ToolResult(
            f"已创建{mode}定时任务：{job.id}（{schedule}）。",
            metadata={"cron_id": job.id},
        )


class ListCronsTool:
    """列出当前 Cron Job 摘要。"""

    name = "list_crons"
    description = "列出当前 Cron 定时任务及其状态。"
    parallel_safe = True
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, manager: CronManager) -> None:
        self._manager = manager

    async def run(self, args: dict[str, Any]) -> ToolResult:
        del args
        jobs = self._manager.list_jobs()
        if not jobs:
            return ToolResult("暂无 Cron 定时任务。")
        lines = [
            (
                f"{job.id} status={self._manager.runtime_state(job.id)} "
                f"schedule={job.cron or job.run_at} "
                f"recurring={job.recurring} prompt={job.prompt}"
            )
            for job in jobs
        ]
        return ToolResult("\n".join(lines))


class DeleteCronTool:
    """完全删除一个 Cron Job 及其相关记录。"""

    name = "delete_cron"
    description = "完全删除一个唯一定位的 Cron 定时任务及其相关记录。"
    parallel_safe = False
    input_schema = {
        "type": "object",
        "properties": {"id": {"type": "string", "description": "Cron Job ID"}},
        "required": ["id"],
    }

    def __init__(self, manager: CronManager) -> None:
        self._manager = manager

    async def run(self, args: dict[str, Any]) -> ToolResult:
        job = await self._manager.delete_job(str(args["id"]))
        return ToolResult(f"已删除 Cron 定时任务：{job.id}")


def _empty_usage():
    """返回空的用量统计。"""
    from ..llm.types import LLMUsage

    return LLMUsage()


def _safe_failure_message(error: str | None) -> str:
    """生成安全的失败摘要。"""
    return "后台任务未完成" if not error else "后台任务执行失败"


def _optional_arg(value: object) -> str | None:
    """读取 Tool 的可选非空字符串。"""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _as_utc(moment: datetime) -> datetime:
    """把注入时钟或调用参数归一为 aware UTC。"""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from ..tools.base import ToolResult
from .executor import ProactiveExecutionResult
from .scheduler import next_cron_fire, next_job_fire, next_run_at_after_delay, validate_cron
from .store import ProactiveStore
from .types import CronJob, UsageTotals, new_id


class CronExecutor(Protocol):
    """Cron 所需的后台执行接口。"""

    async def run(
        self,
        capability: str,
        prompt: str,
        *,
        allowed_tool_names: frozenset[str] | None = None,
        on_started: Callable[[], Awaitable[None]] | None = None,
    ) -> ProactiveExecutionResult:
        """执行后台任务。"""


class CronNotificationPublisher(Protocol):
    """Cron 所需的 Gateway 主动结果发布接口。"""

    async def enqueue(self, **payload: Any) -> Any:
        """发布一次结果事件，由 Gateway 决定 Fanout 与投递。"""


class CronIncidentReporter(Protocol):
    """Cron 所需的异常边沿接口。"""

    async def report_failure(self, *, fingerprint: str, source: str, message: str) -> bool:
        """记录失败边沿。"""

    async def report_recovery(self, *, fingerprint: str, source: str, message: str) -> bool:
        """记录恢复边沿。"""

    async def delete_by_fingerprint(self, fingerprint: str) -> int:
        """硬删除来源关联的异常记录。"""


class CronManager:
    """维护 Cron 快照及每个 Job 的下一触发时刻。"""

    def __init__(
        self,
        store: ProactiveStore,
        executor: CronExecutor,
        notification_publisher: CronNotificationPublisher,
        proactive_settings: Any,
        *,
        incidents: CronIncidentReporter | None = None,
        clock: Callable[[], datetime] | None = None,
        schedule_changed: Callable[[], None] | None = None,
        idle_changed: Callable[[], None] | None = None,
        available_tool_names: Callable[[], frozenset[str]] | None = None,
    ) -> None:
        """恢复 Cron 快照并建立内存 deadline 索引。"""
        self._store = store
        self._executor = executor
        self._notification_publisher = notification_publisher
        self._timezone = ZoneInfo(str(proactive_settings.timezone))
        self._incidents = incidents
        self._clock = clock or (lambda: datetime.now(UTC))
        self._schedule_changed = schedule_changed
        self._idle_changed = idle_changed
        self._available_tool_names = available_tool_names or frozenset
        self._jobs, self._usage = self._load_state()
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._running_ids: set[str] = set()
        self._next_fire: dict[str, datetime] = {}
        self._deleting: set[str] = set()
        self._lock = asyncio.Lock()
        self._rebuild_deadlines(self._now(), persist=False)

    def initialize_schedule(self, now: datetime | None = None) -> None:
        """在所属 Gateway 已激活后刷新快照并持久化可恢复的 deadline。"""
        self._jobs, self._usage = self._load_state()
        self._rebuild_deadlines(now or self._now(), persist=True)
        self._notify_schedule_changed()

    def create_job(
        self,
        prompt: str,
        *,
        recurring: bool,
        cron: str | None = None,
        next_run_at: str | None = None,
        delivery_channels: Iterable[str] | None = None,
    ) -> CronJob:
        """验证并保存一条周期或一次性 Cron Job。"""
        prompt = prompt.strip()
        if not prompt:
            raise ValueError("prompt 不能为空")
        channels = [str(channel).strip() for channel in (delivery_channels or ["all"])]
        if not channels or any(not channel for channel in channels):
            raise ValueError("delivery_channels 至少需要一个非空 channel")
        now = self._now()
        if recurring:
            if cron is None:
                raise ValueError("周期 Cron Job 必须设置 cron")
            error = validate_cron(cron)
            if error:
                raise ValueError(error)
            if next_run_at is not None:
                raise ValueError("周期 Cron Job 不能设置 next_run_at")
            deadline = next_cron_fire(cron, now, self._timezone)
            next_run_at = deadline.isoformat(timespec="seconds")
        else:
            if next_run_at is None:
                raise ValueError("一次性 Cron Job 必须设置 next_run_at")
            deadline = datetime.fromisoformat(next_run_at)
            if deadline.tzinfo is None or deadline.utcoffset() is None:
                raise ValueError("next_run_at 必须包含 UTC offset")
            if deadline.astimezone(UTC) <= now:
                raise ValueError("next_run_at 必须晚于当前时间")
        now_iso = now.isoformat()
        job = CronJob(
            id=new_id("cron"),
            cron=cron,
            created_at=now_iso,
            prompt=prompt,
            recurring=recurring,
            delivery_channels=channels,
            updated_at=now_iso,
            next_run_at=next_run_at,
        )
        self._jobs[job.id] = job
        self._next_fire[job.id] = deadline.astimezone(UTC)
        self._save_state()
        self._notify_schedule_changed()
        return job

    def next_run_at_after_delay(self, delay_seconds: float) -> str:
        """使用全局时区把相对延迟转换为固定时刻。"""
        return next_run_at_after_delay(delay_seconds, self._now(), self._timezone)

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
        """从当前进程的 Task 集合投影 active、queued 或 running。"""
        self.get_job(job_id)
        if job_id in self._running_ids:
            return "running"
        if job_id in self._tasks:
            return "queued"
        return "active"

    @property
    def is_idle(self) -> bool:
        """报告当前没有排队或运行中的 Cron Job。"""
        return not self._tasks

    async def delete_job(self, job_id: str) -> CronJob:
        """取消执行并硬删除 Cron 及其关联主动记录。"""
        async with self._lock:
            job = self.get_job(job_id)
            self._deleting.add(job_id)
            task = self._tasks.get(job_id)
        try:
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            await self._purge_related_records(job_id)
            async with self._lock:
                self._jobs.pop(job_id, None)
                self._next_fire.pop(job_id, None)
                self._tasks.pop(job_id, None)
                self._running_ids.discard(job_id)
                self._save_state()
                self._deleting.discard(job_id)
        except BaseException:
            async with self._lock:
                self._deleting.discard(job_id)
            self._notify_schedule_changed()
            raise
        self._notify_schedule_changed()
        return job

    def next_deadline(self, now: datetime | None = None) -> datetime | None:
        """返回最早的 UTC deadline。"""
        del now
        return min(
            (deadline for job_id, deadline in self._next_fire.items() if job_id not in self._deleting),
            default=None,
        )

    async def run_due(self, now: datetime | None = None) -> int:
        """启动所有到期 Job，并先持久化它们的下次运行时刻。"""
        moment = _as_utc(now or self._now())
        created = 0
        changed = False
        async with self._lock:
            due = sorted(
                ((deadline, job_id) for job_id, deadline in self._next_fire.items() if deadline <= moment),
                key=lambda item: (item[0], item[1]),
            )
            for scheduled_at, job_id in due:
                job = self._jobs.get(job_id)
                if job is None:
                    self._next_fire.pop(job_id, None)
                    changed = True
                    continue
                if job_id in self._deleting:
                    continue
                self._advance_deadline(job, scheduled_at)
                changed = True
                if job_id in self._tasks:
                    continue
                task = asyncio.create_task(
                    self._run_job(job_id, scheduled_at),
                    name=f"proactive-cron-{job_id}",
                )
                self._tasks[job_id] = task
                created += 1
            if changed:
                self._save_state()
        if changed:
            self._notify_schedule_changed()
        return created

    def update_timezone(self, name: str) -> None:
        """切换全局 IANA 时区并重算周期任务的下次运行。"""
        self._timezone = ZoneInfo(name)
        self._rebuild_deadlines(self._now(), persist=True)
        self._notify_schedule_changed()

    async def wait_for_jobs(self) -> None:
        """等待当前已启动的后台 Job 完成。"""
        while self._tasks:
            await asyncio.gather(*list(self._tasks.values()), return_exceptions=True)

    async def close(self) -> None:
        """取消尚未结束的 Cron 任务。"""
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _run_job(self, job_id: str, scheduled_at: datetime) -> None:
        """执行单个 Cron，并在任何出口清理内存 Task 状态。"""
        try:
            job = self.get_job(job_id)
            try:
                available_tool_names = frozenset(self._available_tool_names())
                result = await self._executor.run(
                    "cron",
                    job.prompt,
                    allowed_tool_names=available_tool_names,
                    on_started=lambda: self._mark_running(job_id, scheduled_at),
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                result = ProactiveExecutionResult(False, "", usage=_empty_usage(), error="后台任务执行失败")
            await self._finish_job(job_id, result)
        except asyncio.CancelledError:
            await self._cancel_job(job_id)
            raise
        finally:
            self._tasks.pop(job_id, None)
            self._running_ids.discard(job_id)
            self._notify_schedule_changed()
            self._notify_idle_changed()

    async def _mark_running(self, job_id: str, scheduled_at: datetime) -> None:
        """仅在共享后台容量取得后标记为运行中。"""
        del scheduled_at
        async with self._lock:
            if job_id in self._deleting or job_id not in self._jobs:
                raise asyncio.CancelledError
            self._running_ids.add(job_id)

    async def _cancel_job(self, job_id: str) -> None:
        """取消一次性任务时直接删除它；周期任务保留已推进的下次时间。"""
        async with self._lock:
            if job_id in self._deleting:
                return
            job = self._jobs.get(job_id)
            if job is None or job.recurring:
                return
            self._jobs.pop(job_id, None)
            self._next_fire.pop(job_id, None)
            self._save_state()

    async def _finish_job(self, job_id: str, result: ProactiveExecutionResult) -> None:
        """累计用量、删除已结束一次性任务，并投递成功输出。"""
        async with self._lock:
            if job_id in self._deleting or job_id not in self._jobs:
                return
            job = self._jobs[job_id]
            self._add_usage(result)
            if not job.recurring:
                self._jobs.pop(job_id, None)
                self._next_fire.pop(job_id, None)
            self._save_state()
        fingerprint = f"proactive:cron:{job.id}"
        if result.success:
            if job_id in self._deleting:
                return
            await self._notification_publisher.enqueue(
                event_type="proactive.cron.completed",
                content=f"定时任务已完成：{result.content}",
                source_id=job.id,
                notification_scope="all_subscribed",
            )
            if self._incidents is not None and job_id not in self._deleting:
                await self._incidents.report_recovery(
                    fingerprint=fingerprint,
                    source="cron",
                    message=f"定时任务 {job.id} 已恢复。",
                )
            return
        if self._incidents is not None and job_id not in self._deleting:
            await self._incidents.report_failure(
                fingerprint=fingerprint,
                source="cron",
                message=f"定时任务 {job.id} 执行失败。",
            )

    def _advance_deadline(self, job: CronJob, scheduled_at: datetime) -> None:
        """消费本次 deadline；周期任务立即计算并持久化下一次。"""
        if not job.recurring:
            self._next_fire.pop(job.id, None)
            self._replace_job(job, None)
            return
        next_fire = next_job_fire(job, scheduled_at, self._timezone)
        if next_fire is None:
            self._next_fire.pop(job.id, None)
            self._replace_job(job, None)
            return
        self._next_fire[job.id] = next_fire.astimezone(UTC)
        self._replace_job(job, next_fire)

    def _rebuild_deadlines(self, now: datetime, *, persist: bool) -> None:
        """从快照恢复 deadline；仅激活后的服务可以改写 durable state。"""
        self._next_fire.clear()
        changed = False
        for job in list(self._jobs.values()):
            if job.recurring:
                deadline = next_job_fire(job, now, self._timezone)
                if deadline is None:
                    if persist:
                        self._jobs.pop(job.id, None)
                        changed = True
                    continue
                self._next_fire[job.id] = deadline.astimezone(UTC)
                if persist:
                    self._replace_job(job, deadline)
                    changed = True
                continue
            deadline = next_job_fire(job, now, self._timezone)
            if deadline is None:
                if persist:
                    self._jobs.pop(job.id, None)
                    changed = True
                continue
            self._next_fire[job.id] = deadline.astimezone(UTC)
        if persist and changed:
            self._save_state()

    def _replace_job(self, job: CronJob, deadline: datetime | None) -> CronJob:
        """以新的下次运行时刻替换 Job 并刷新更新时间。"""
        next_run_at = None if deadline is None else deadline.isoformat(timespec="seconds")
        updated = replace(job, next_run_at=next_run_at, updated_at=self._now().isoformat())
        self._jobs[job.id] = updated
        return updated

    async def _purge_related_records(self, job_id: str) -> None:
        """硬删除关联异常记录；通知从不作为 durable 状态保存。"""
        fingerprint = f"proactive:cron:{job_id}"
        if self._incidents is not None:
            delete_incident = getattr(self._incidents, "delete_by_fingerprint", None)
            if callable(delete_incident):
                await delete_incident(fingerprint)

    def _load_state(self) -> tuple[dict[str, CronJob], UsageTotals]:
        """读取最小 Cron 快照。"""
        payload = self._store.read_json(
            "cron.json",
            {"jobs": [], "usage": UsageTotals().to_dict()},
        )
        if not isinstance(payload, dict) or set(payload) != {"jobs", "usage"}:
            raise ValueError(f"{self._store.root / 'cron.json'} 字段无效")
        rows = payload["jobs"]
        if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
            raise ValueError(f"{self._store.root / 'cron.json'} 的 jobs 必须是数组")
        usage = payload["usage"]
        required_usage = {"calls", "input_tokens", "output_tokens", "total_tokens"}
        if not isinstance(usage, dict) or set(usage) != required_usage:
            raise ValueError(f"{self._store.root / 'cron.json'} 的 usage 无效")
        jobs = [CronJob.from_dict(row) for row in rows]
        job_map = {job.id: job for job in jobs}
        if len(job_map) != len(jobs):
            raise ValueError(f"{self._store.root / 'cron.json'} 包含重复 Cron Job ID")
        return job_map, UsageTotals.from_dict(usage)

    def _save_state(self) -> None:
        """保存 Cron Job 与累计模型用量。"""
        self._store.write_json(
            "cron.json",
            {
                "jobs": [job.to_dict() for job in self.list_jobs()],
                "usage": self._usage.to_dict(),
            },
        )

    def _add_usage(self, result: ProactiveExecutionResult) -> None:
        """累计一次后台模型尝试的 Token 用量。"""
        self._usage = self._usage.add(
            input_tokens=result.usage.input_tokens,
            output_tokens=result.usage.output_tokens,
            total_tokens=result.usage.total_tokens,
        )

    def _now(self) -> datetime:
        """读取可测试的 aware UTC 时钟。"""
        return _as_utc(self._clock())

    def _notify_schedule_changed(self) -> None:
        """唤醒 Service 重新读取最早 deadline。"""
        if self._schedule_changed is not None:
            self._schedule_changed()

    def _notify_idle_changed(self) -> None:
        """在任务已从运行时投影移除后通知 Gateway 复查全局空闲。"""
        if self._idle_changed is not None:
            self._idle_changed()


class CreateCronTool:
    """创建一个由 CronManager 调度的后台任务。"""

    name = "create_cron"
    description = "创建周期 Cron 或一次性固定时刻/相对延迟任务。"
    parallel_safe = False
    input_schema = {
        "type": "object",
        "properties": {
            "cron": {"type": ["string", "null"], "description": "周期任务的五字段 Cron 表达式"},
            "next_run_at": {
                "type": ["string", "null"],
                "description": "一次性任务的带 UTC offset ISO 时刻",
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
        """初始化对象状态。"""
        self._manager = manager

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """验证互斥计划参数并创建任务。"""
        cron = _optional_arg(args.get("cron"))
        next_run_at = _optional_arg(args.get("next_run_at"))
        delay_seconds = args.get("delay_seconds")
        if delay_seconds is not None:
            if cron is not None or next_run_at is not None:
                raise ValueError("delay_seconds 不能与 cron 或 next_run_at 同时设置")
            next_run_at = self._manager.next_run_at_after_delay(delay_seconds)
        job = self._manager.create_job(
            str(args["prompt"]),
            cron=cron,
            next_run_at=next_run_at,
            recurring=bool(args["recurring"]),
            delivery_channels=args.get("delivery_channels"),
        )
        mode = "周期性" if job.recurring else "一次性"
        schedule = job.cron if job.recurring else job.next_run_at
        return ToolResult(f"已创建{mode}定时任务：{job.id}（下次运行：{schedule}）。", metadata={"cron_id": job.id})


class ListCronsTool:
    """列出当前 Cron Job 摘要。"""

    name = "list_crons"
    description = "列出当前 Cron 定时任务及其状态。"
    parallel_safe = True
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, manager: CronManager) -> None:
        """初始化对象状态。"""
        self._manager = manager

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """列出定时任务。"""
        del args
        jobs = self._manager.list_jobs()
        if not jobs:
            return ToolResult("暂无 Cron 定时任务。")
        lines = [
            (
                f"{job.id} status={self._manager.runtime_state(job.id)} "
                f"next_run_at={job.next_run_at} "
                f"schedule={job.cron or 'one-shot'} recurring={job.recurring} prompt={job.prompt}"
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
        """初始化对象状态。"""
        self._manager = manager

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """删除定时任务。"""
        job = await self._manager.delete_job(str(args["id"]))
        return ToolResult(f"已删除 Cron 定时任务：{job.id}")


def _empty_usage():
    """返回空的模型用量。"""
    from ..llm.types import LLMUsage

    return LLMUsage()


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

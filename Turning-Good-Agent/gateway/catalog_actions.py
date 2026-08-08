from __future__ import annotations

import asyncio
import inspect
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal
from uuid import uuid4

from ..context.session_context import build_session_context
from ..memory.short_term import ShortTermMemory
from ..observability.trace import StateTrace
from ..runtime.turn_context import reset_current_route, set_current_route


RuntimeProvider = Callable[[], Any | Awaitable[Any]]
ProactiveProvider = Callable[[], Any]
CatalogAction = Literal[
    "compact",
    "run_skill_evolution",
    "run_dream:session",
    "run_dream:global",
    "run_breakbeat:session",
    "run_breakbeat:global",
]


@dataclass(frozen=True, slots=True)
class CompactionResult:
    summary: str
    compacted_message_count: int
    compacted_token_count: int
    raw_window_message_count: int
    raw_window_token_count: int
    compacted: bool


class CatalogActionExecutor:
    """执行不产生聊天消息的 Web Catalog 控制动作。"""

    def __init__(
        self,
        *,
        runtime_provider: RuntimeProvider,
        proactive_provider: ProactiveProvider,
        web_coordinator: Any,
    ) -> None:
        self._runtime_provider = runtime_provider
        self._proactive_provider = proactive_provider
        self._web_coordinator = web_coordinator
        self._tasks: set[asyncio.Task[None]] = set()
        self._active_abilities: set[str] = set()
        self._lock = asyncio.Lock()
        self._closed = False

    async def execute(self, session_id: str, action: CatalogAction) -> str:
        """受理一个 Catalog 动作并立即返回可关联的 request id。"""
        if self._closed:
            raise RuntimeError("Gateway 正在关闭，无法执行 Catalog 动作")
        action_name, scope = _parse_action(action)
        request_id = str(uuid4())
        exclusive = scope == "session"
        async with self._lock:
            if action_name != "compact" and action_name in self._active_abilities:
                raise RuntimeError(f"{_display_name(action_name)} 正在运行")
            await self._web_coordinator.begin_catalog_action(
                session_id,
                request_id,
                exclusive=exclusive,
            )
            if action_name != "compact":
                self._active_abilities.add(action_name)
            task = asyncio.create_task(
                self._run(session_id, request_id, action_name, scope),
                name=f"catalog-{action_name}-{request_id}",
            )
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return request_id

    async def close(self) -> None:
        """取消尚未完成的 Catalog 控制任务。"""
        self._closed = True
        for task in tuple(self._tasks):
            task.cancel()
        if self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def wait_for_idle(self) -> None:
        """等待当前受理的控制任务结束，供生命周期与定向测试使用。"""
        while self._tasks:
            await asyncio.gather(*tuple(self._tasks), return_exceptions=True)

    async def _run(
        self,
        session_id: str,
        request_id: str,
        action_name: str,
        scope: str,
    ) -> None:
        hub = self._web_coordinator.hub
        tool_started = False
        try:
            await hub.publish(
                session_id,
                request_id,
                "task.queued",
                {"kind": "catalog", "catalog_action": _catalog_action(action_name, scope)},
            )
            runtime = await _resolve(self._runtime_provider())
            timeout = max(1, int(runtime.settings.runtime.turn_timeout_seconds))
            if action_name == "compact":
                await hub.publish(
                    session_id,
                    request_id,
                    "task.status",
                    {"content": "正在压缩会话上下文..."},
                )
                result = await asyncio.wait_for(
                    compact_session(runtime, session_id, request_id),
                    timeout=timeout,
                )
                await hub.publish(
                    session_id,
                    request_id,
                    "task.status",
                    {"content": _compaction_complete_message(result)},
                )
            else:
                args = {} if action_name == "run_skill_evolution" else {"scope": scope}
                await hub.publish(
                    session_id,
                    request_id,
                    "tool.started",
                    {
                        "tool_call_id": request_id,
                        "tool_name": action_name,
                        "args": args,
                    },
                )
                tool_started = True
                route = await _route_for(self._web_coordinator, session_id)
                route_token = set_current_route(route) if route is not None else None
                try:
                    result = await asyncio.wait_for(
                        self._proactive_provider().run_catalog_action(
                            action_name,
                            scope,
                            session_id,
                            route,
                        ),
                        timeout=timeout,
                    )
                finally:
                    if route_token is not None:
                        reset_current_route(route_token)
                await hub.publish(
                    session_id,
                    request_id,
                    "tool.finished",
                    {
                        "tool_call_id": request_id,
                        "tool_name": action_name,
                        "failed": False,
                    },
                )
            await hub.publish(session_id, request_id, "task.completed", {"content": str(result)})
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            if tool_started:
                await hub.publish(
                    session_id,
                    request_id,
                    "tool.finished",
                    {
                        "tool_call_id": request_id,
                        "tool_name": action_name,
                        "failed": True,
                    },
                )
            await hub.publish(
                session_id,
                request_id,
                "task.failed",
                {"content": _failure_message(action_name, exc)},
            )
        finally:
            async with self._lock:
                if action_name != "compact":
                    self._active_abilities.discard(action_name)
            await self._web_coordinator.finish_catalog_action(session_id, request_id)


async def compact_session(runtime: Any, session_id: str, turn_id: str) -> CompactionResult:
    """压缩已有会话，不增加任何普通聊天消息或工具调用记录。"""
    started = time.perf_counter()
    store = runtime.sessions.store
    stats: dict[str, int] = {
        "compacted": 0,
        "compacted_message_count": 0,
        "compacted_token_count": 0,
        "raw_window_message_count": 0,
        "raw_window_token_count": 0,
    }
    try:
        session = await store.load_session(session_id)
        if session is None:
            raise ValueError("会话不存在")
        history = build_session_context(session, await runtime.sessions.all_messages(session_id)).uncompacted_history
        memory = ShortTermMemory(
            compact_token_threshold=runtime.settings.memory.compact_token_threshold,
            recent_window_token_limit=runtime.settings.memory.recent_window_token_limit,
        )
        plan = memory.plan_compaction(history, force=True)
        stats.update(
            {
                "compacted_message_count": plan.compacted_message_count,
                "compacted_token_count": plan.compacted_token_count,
                "raw_window_message_count": plan.raw_window_message_count,
                "raw_window_token_count": plan.raw_window_token_count,
            }
        )
        if not plan.compact_source:
            await store.update_uncompacted_history(session_id, history)
            result = CompactionResult(
                session.summary,
                plan.compacted_message_count,
                plan.compacted_token_count,
                plan.raw_window_message_count,
                plan.raw_window_token_count,
                False,
            )
        else:
            summary, usage = await memory.compact(session.summary, plan.compact_source, runtime.agent_loop.llm)
            token_usage = runtime.token_monitor.record_llm_usage(
                usage,
                previous_total_tokens=await store.last_total_tokens(session_id),
                compacted=True,
            )
            stats["compacted"] = 1
            await store.update_summary(session_id, summary)
            await store.update_uncompacted_history(session_id, plan.recent_window)
            await store.save_true_token_usage(turn_id, session_id, token_usage)
            result = CompactionResult(
                summary,
                plan.compacted_message_count,
                plan.compacted_token_count,
                plan.raw_window_message_count,
                plan.raw_window_token_count,
                True,
            )
    except Exception as exc:
        await store.save_trace(
            StateTrace(
                turn_id,
                session_id,
                "COMPACT",
                (time.perf_counter() - started) * 1000,
                "error",
                str(exc),
                stats,
            )
        )
        raise
    await store.save_trace(
        StateTrace(
            turn_id,
            session_id,
            "COMPACT",
            (time.perf_counter() - started) * 1000,
            "ok",
            metadata=stats,
        )
    )
    return result


def _parse_action(action: CatalogAction) -> tuple[str, str]:
    allowed = {
        "compact": ("compact", "session"),
        "run_skill_evolution": ("run_skill_evolution", "global"),
        "run_dream:session": ("run_dream", "session"),
        "run_dream:global": ("run_dream", "global"),
        "run_breakbeat:session": ("run_breakbeat", "session"),
        "run_breakbeat:global": ("run_breakbeat", "global"),
    }
    if action not in allowed:
        raise ValueError("不支持的 Catalog 动作")
    return allowed[action]


def _catalog_action(action_name: str, scope: str) -> str:
    if action_name == "compact":
        return "compact"
    return action_name if action_name == "run_skill_evolution" else f"{action_name}:{scope}"


async def _resolve(value: Any | Awaitable[Any]) -> Any:
    return await value if inspect.isawaitable(value) else value


async def _route_for(coordinator: Any, session_id: str) -> Any | None:
    route_for_session = getattr(coordinator, "route_for_session", None)
    if route_for_session is None:
        return None
    return await route_for_session(session_id)


def _compaction_complete_message(result: CompactionResult) -> str:
    if not result.compacted:
        return "压缩完成：当前没有可压缩的完整历史。"
    return (
        "压缩完成："
        f"已压缩 {result.compacted_message_count} 条消息，"
        f"压缩 {result.compacted_token_count} tokens，"
        f"保留最近 {result.raw_window_token_count} tokens 原文。"
    )


def _display_name(action_name: str) -> str:
    return {
        "run_dream": "Dream",
        "run_breakbeat": "Breakbeat",
        "run_skill_evolution": "Skill 演进",
    }.get(action_name, "该操作")


def _failure_message(action_name: str, error: Exception) -> str:
    label = "上下文压缩" if action_name == "compact" else _display_name(action_name)
    if isinstance(error, TimeoutError):
        return f"{label} 超时，未完成本次操作。"
    return f"{label} 失败：{str(error)[:200]}"

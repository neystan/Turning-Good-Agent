from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from ..memory.long_term import ProfileMemory, ProfileMemorySnapshot
from ..sessions.types import MessageRecord, Session
from ..tools.base import ToolResult
from .executor import ProactiveExecutionContext, ProactiveExecutionResult
from .review_window import ReviewCursor, select_review_window
from .store import ProactiveStore
from .types import new_id


_SENSITIVE_PATTERNS = (
    re.compile(r"(?i)\bapi[_-]?key\s*[:=]"),
    re.compile(r"(?i)\bbearer\s+[a-z0-9._~+/=-]+"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
)


class DreamSessions(Protocol):
    """Dream 回读原始事实所需的会话接口。"""

    async def list_sessions(self) -> list[Session]:
        """列出可审阅会话。"""

    async def all_messages(self, session_id: str) -> list[MessageRecord]:
        """读取原始 user/assistant 消息。"""


class DreamExecutor(Protocol):
    """Dream 单阶段审阅所需的模型入口。"""

    async def run(
        self,
        context: ProactiveExecutionContext,
        prompt: str,
        *,
        allow_tools: bool = True,
    ) -> ProactiveExecutionResult:
        """执行不带工具的结构化审阅。"""


@dataclass(frozen=True, slots=True)
class DreamOutcome:
    """记录一次 Dream 审阅结果。"""

    changed: bool
    memory_count: int
    added_memories: tuple[tuple[str, str], ...]
    errors: int
    summary: str


class DreamManager:
    """从有界原文中单阶段提炼并追加 USER/SOUL 长期记忆。"""

    def __init__(
        self,
        store: ProactiveStore,
        sessions: DreamSessions,
        executor: DreamExecutor,
        profile_memory: ProfileMemory,
        proactive_settings: Any,
        *,
        schedule_changed: Callable[[], None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """初始化对象状态。"""
        self.store = store
        for name in ("dream_evidence.jsonl", "dream_memories.jsonl", "dream_revisions.jsonl"):
            if (store.root / name).exists():
                store.raise_legacy_data(name)
        self._sessions = sessions
        self._executor = executor
        self._profile_memory = profile_memory
        self._refresh = timedelta(hours=int(proactive_settings.dream_refresh_hours))
        self._token_limit = int(proactive_settings.review_window_token_limit)
        self._schedule_changed = schedule_changed or (lambda: None)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = asyncio.Lock()

    @property
    def running(self) -> bool:
        """返回能力当前是否正在审阅。"""
        return self._lock.locked()

    @property
    def next_run_at(self) -> datetime | None:
        """返回持久化的下一次 UTC 运行时间。"""
        value = self._state().get("next_run_at")
        if not isinstance(value, str):
            return None
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            return None
        return parsed.astimezone(UTC)

    def initialize_schedule(self, now: datetime) -> None:
        """保留未来 deadline；启动时跳过已错过的 deadline。"""
        current = now.astimezone(UTC)
        deadline = self.next_run_at
        if deadline is not None and deadline > current:
            return
        state = self._state()
        state["next_run_at"] = (current + self._refresh).isoformat()
        self._save_state(state)
        self._schedule_changed()

    async def run(
        self,
        *,
        manual: bool,
        scope: str,
        session_id: str | None = None,
        current_message: MessageRecord | None = None,
        now: datetime | None = None,
    ) -> DreamOutcome:
        """逐批提炼长期记忆，并在每批完整校验后成对写入画像。"""
        if scope not in {"global", "session"}:
            raise ValueError("Dream scope 必须为 global 或 session")
        if scope == "session" and not session_id:
            raise ValueError("session scope 需要当前 session_id")
        current_time = now or datetime.now(UTC)
        async with self._lock:
            state = self._state()
            changed = False
            errors = 0
            added_memories: list[tuple[str, str]] = []
            sessions = await self._sessions.list_sessions()
            if scope == "session":
                sessions = [session for session in sessions if session.id == session_id]
            for session in sessions:
                if session.archived:
                    continue
                messages = await self._sessions.all_messages(session.id)
                if current_message is not None and current_message.session_id == session.id:
                    if all(message.id != current_message.id for message in messages):
                        messages.append(current_message)
                selection = select_review_window(
                    messages,
                    cursor=self._cursor_for(state, session.id),
                    now=current_time,
                    refresh=self._refresh,
                    token_limit=self._token_limit,
                )
                for batch in selection.batches:
                    outcome = await self._review_batch(session.id, list(batch.messages))
                    if outcome is None:
                        errors += 1
                        break
                    batch_changed, batch_memories = outcome
                    changed = changed or batch_changed
                    added_memories.extend(batch_memories)
                    last = batch.messages[-1]
                    state["cursors"][session.id] = {
                        "message_id": last.id,
                        "created_at": last.created_at,
                    }
                    self._save_state(state)
            if errors == 0:
                finished_at = self._clock().astimezone(UTC)
                state["last_successful_run_at"] = finished_at.isoformat()
                state["next_run_at"] = (finished_at + self._refresh).isoformat()
                self._save_state(state)
                self._schedule_changed()
        if manual:
            summary = "Dream 审阅完成：已更新长期记忆。" if changed else "Dream 审阅完成：无记忆变更。"
            if errors:
                summary += f" {errors} 个审阅批次失败。"
        elif changed:
            summary = "Dream 已更新长期记忆。"
        else:
            summary = ""
        return DreamOutcome(changed, len(added_memories), tuple(added_memories), errors, summary)

    async def _review_batch(
        self,
        session_id: str,
        messages: list[MessageRecord],
    ) -> tuple[bool, tuple[tuple[str, str], ...]] | None:
        """用一次模型调用解析并直接追加一批长期记忆。"""
        snapshot = self._profile_memory.read()
        result = await self._executor.run(
            ProactiveExecutionContext(
                capability="dream",
                job_id=new_id("dream"),
                source_session_ids=(session_id,),
            ),
            _dream_prompt(session_id, messages, snapshot),
            allow_tools=False,
        )
        if not result.success:
            return None
        try:
            memories = _parse_memories(result.content)
            candidate, added = _append_memories(snapshot, memories)
            if added:
                self._profile_memory.write(candidate)
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return None
        return bool(added), added

    def _state(self) -> dict[str, Any]:
        """读取当前持久化状态。"""
        default = {"cursors": {}, "last_successful_run_at": None, "next_run_at": None}
        payload = self.store.read_json("dream_state.json", default)
        if not isinstance(payload, dict):
            return dict(default)
        cursors = payload.get("cursors")
        return {
            "cursors": dict(cursors) if isinstance(cursors, dict) else {},
            "last_successful_run_at": payload.get("last_successful_run_at"),
            "next_run_at": payload.get("next_run_at"),
        }

    def _cursor_for(self, state: dict[str, Any], session_id: str) -> ReviewCursor | None:
        """生成消息游标。"""
        payload = state["cursors"].get(session_id)
        if not isinstance(payload, dict):
            return None
        message_id = payload.get("message_id")
        created_at = payload.get("created_at")
        if not isinstance(message_id, str) or not isinstance(created_at, str):
            return None
        return ReviewCursor(message_id, created_at)

    def _save_state(self, state: dict[str, Any]) -> None:
        """保存当前持久化状态。"""
        self.store.write_json("dream_state.json", state)


class RunDreamTool:
    """立即执行内置 Dream 单例，不创建新的 Dream 实例。"""

    name = "run_dream"
    description = "当用户明确要求运行 Dream 或总结长期记忆时，立即执行一次 Dream 审阅。"
    parallel_safe = False
    input_schema = {
        "type": "object",
        "properties": {
            "scope": {
                "type": "string",
                "enum": ["global", "session"],
                "description": "全局审阅，或只审阅当前会话",
            }
        },
        "required": ["scope"],
    }

    def __init__(
        self,
        manager: DreamManager,
        *,
        active_session_id: Callable[[], str | None] | None = None,
        current_message: Callable[[], MessageRecord | None] | None = None,
    ) -> None:
        """初始化对象状态。"""
        self._manager = manager
        self._active_session_id = active_session_id or (lambda: None)
        self._current_message = current_message or (lambda: None)

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """执行当前任务。"""
        scope = args.get("scope")
        if scope not in {"global", "session"}:
            raise ValueError("run_dream.scope 必须为 global 或 session")
        session_id = self._active_session_id() if scope == "session" else None
        current_message = self._current_message()
        if scope == "session" and (
            current_message is None or current_message.session_id != session_id
        ):
            raise ValueError("session scope 需要当前消息")
        outcome = await self._manager.run(
            manual=True,
            scope=scope,
            session_id=session_id,
            current_message=current_message,
        )
        lines = [
            f"Dream 审阅完成（scope={scope}）。",
            f"错误数：{outcome.errors}",
        ]
        if outcome.added_memories:
            lines.append(f"本次追加 {len(outcome.added_memories)} 条长期记忆：")
            lines.extend(
                f"- {target.upper()}: {content}"
                for target, content in outcome.added_memories
            )
        else:
            lines.append("本次未追加长期记忆。")
        return ToolResult("\n".join(lines))


class ReadProfileMemoryTool:
    """直接读取当前部署级 USER/SOUL 画像。"""

    name = "read_profile_memory"
    description = "读取当前长期记忆文件 USER.md 与 SOUL.md 的实际内容。"
    parallel_safe = True
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, profile_memory: ProfileMemory) -> None:
        """初始化画像读取入口。"""
        self._profile_memory = profile_memory

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """返回当前 USER/SOUL 内容。"""
        del args
        snapshot = self._profile_memory.read()
        return ToolResult(
            f"USER：{snapshot.user or '(空)'}\n"
            f"SOUL：{snapshot.soul or '(空)'}"
        )


def _dream_prompt(
    session_id: str,
    messages: list[MessageRecord],
    snapshot: ProfileMemorySnapshot,
) -> str:
    """构造单阶段 Dream 提示词。"""
    source = "\n".join(f"{item.role}: {item.content}" for item in messages)
    return (
        "从以下单个会话原文提炼稳定、长期、非秘密的记忆。忽略临时任务、闲聊、情绪和猜测。"
        "仅返回 JSON：{\"memories\":[{\"target\":\"user|soul\",\"content\":\"长期记忆正文\"}]}。"
        "每项只能包含 target、content；不要输出 operation、session 或消息 ID。"
        f"\n会话：{session_id}"
        f"\n当前 USER：\n{snapshot.user or '(空)'}"
        f"\n当前 SOUL：\n{snapshot.soul or '(空)'}"
        f"\n原始消息：\n{source}"
    )


def _parse_memories(content: str) -> list[tuple[str, str]]:
    """严格解析模型返回的最小 Dream 协议。"""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.endswith("```"):
            text = text[:-3]
    payload = json.loads(text)
    if not isinstance(payload, dict) or set(payload) != {"memories"}:
        raise ValueError("Dream 输出必须只包含 memories")
    entries = payload["memories"]
    if not isinstance(entries, list) or not all(isinstance(item, dict) for item in entries):
        raise ValueError("Dream memories 必须是对象数组")
    memories: list[tuple[str, str]] = []
    for entry in entries:
        if set(entry) != {"target", "content"}:
            raise ValueError("Dream memory 字段无效")
        target = entry["target"]
        if target not in {"user", "soul"}:
            raise ValueError("Dream target 必须为 user 或 soul")
        value = entry["content"]
        if not isinstance(value, str) or not value.strip() or len(value.strip()) > 4_000:
            raise ValueError("Dream content 无效")
        normalized = value.strip()
        if any(pattern.search(normalized) for pattern in _SENSITIVE_PATTERNS):
            raise ValueError("Dream content 包含敏感信息")
        memories.append((target, normalized))
    return memories


def _append_memories(
    snapshot: ProfileMemorySnapshot,
    memories: list[tuple[str, str]],
) -> tuple[ProfileMemorySnapshot, tuple[tuple[str, str], ...]]:
    """构造一次成对提交的 USER/SOUL 候选快照。"""
    user = snapshot.user
    soul = snapshot.soul
    added: list[tuple[str, str]] = []
    for target, content in memories:
        existing = user if target == "user" else soul
        if content in existing:
            continue
        updated = "\n".join(part for part in (existing.strip(), content) if part)
        if target == "user":
            user = updated
        else:
            soul = updated
        added.append((target, content))
    return ProfileMemorySnapshot(soul=soul, user=user), tuple(added)

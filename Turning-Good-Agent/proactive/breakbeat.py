from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from collections.abc import Callable
from typing import Any, Protocol

from ..sessions.types import MessageRecord, Session
from ..tools.base import ToolResult
from .executor import ProactiveExecutionContext, ProactiveExecutionResult
from .types import BreakbeatItem, new_id, utc_now_iso
from .review_window import ReviewCursor, select_review_window
from .store import ProactiveStore


class BreakbeatSessions(Protocol):
    """Breakbeat 回读原始会话所需的最小接口。"""

    async def list_sessions(self) -> list[Session]:
        """列出可审阅会话。"""

    async def all_messages(self, session_id: str) -> list[MessageRecord]:
        """读取未压缩的原始消息事实。"""


class BreakbeatExecutor(Protocol):
    """Breakbeat 所需的无工具审阅入口。"""

    async def run(
        self,
        context: ProactiveExecutionContext,
        prompt: str,
        *,
        allow_tools: bool = True,
    ) -> ProactiveExecutionResult:
        """执行有界审阅。"""


@dataclass(frozen=True, slots=True)
class BreakbeatOutcome:
    """记录一次审阅对事项的可见影响。"""

    created: int = 0
    updated: int = 0
    completed: int = 0
    errors: int = 0
    summary: str = ""

    @property
    def should_notify(self) -> bool:
        """判断是否需要通知用户。"""
        return self.created + self.updated + self.completed > 0


class BreakbeatManager:
    """按会话游标从原始消息中提炼可追溯未完成事项。"""

    def __init__(
        self,
        store: ProactiveStore,
        sessions: BreakbeatSessions,
        executor: BreakbeatExecutor,
        proactive_settings: Any,
        *,
        schedule_changed: Callable[[], None] | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        """初始化对象状态。"""
        self.store = store
        self._sessions = sessions
        self._executor = executor
        self._refresh = timedelta(minutes=int(proactive_settings.breakbeat_refresh_minutes))
        self._token_limit = int(proactive_settings.review_window_token_limit)
        self._lock = asyncio.Lock()
        self._schedule_changed = schedule_changed or (lambda: None)
        self._clock = clock or (lambda: datetime.now(UTC))

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
    ) -> BreakbeatOutcome:
        """冻结每个会话的有界原文并应用通过校验的事项变更。"""
        if scope not in {"global", "session"}:
            raise ValueError("Breakbeat scope 必须为 global 或 session")
        if scope == "session" and not session_id:
            raise ValueError("session scope 需要当前 session_id")
        current_time = now or datetime.now(UTC)
        async with self._lock:
            state = self._state()
            created = updated = completed = errors = 0
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
                    batch_created, batch_updated, batch_completed = outcome
                    created += batch_created
                    updated += batch_updated
                    completed += batch_completed
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
        changed = created + updated + completed
        if manual:
            summary = (
                f"Breakbeat 审阅完成：新增 {created} 项，更新 {updated} 项，完成 {completed} 项。"
                if changed
                else "Breakbeat 审阅完成：无变更。"
            )
            if errors:
                summary += f" {errors} 个会话批次审阅失败。"
        elif changed:
            summary = f"Breakbeat 已更新：新增 {created} 项，更新 {updated} 项，完成 {completed} 项。"
        else:
            summary = ""
        return BreakbeatOutcome(created, updated, completed, errors, summary)

    def list_items(self) -> list[BreakbeatItem]:
        """从 append-only 审计重建当前事项视图。"""
        current: dict[str, BreakbeatItem] = {}
        path = self.store.root / "breakbeat_items.jsonl"
        for row in self.store.read_jsonl("breakbeat_items.jsonl"):
            if not isinstance(row, dict):
                raise ValueError(f"{path} 中的每条 Breakbeat 事项必须是 object")
            if _is_legacy_item(row):
                self.store.raise_legacy_data("breakbeat_items.jsonl")
            try:
                item = _item_from_dict(row)
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError(f"{path} 包含无效的 Breakbeat 事项：{exc}") from exc
            current[item.id] = item
        items = sorted(current.values(), key=lambda item: (item.updated_at, item.id), reverse=True)
        return items

    def delete_item(self, item_id: str) -> BreakbeatItem:
        """完全删除一个事项的全部历史版本。"""
        item = self._item_by_id(item_id)
        self.store.filter_jsonl("breakbeat_items.jsonl", lambda row: row.get("id") != item_id)
        return item

    def complete_item(self, item_id: str) -> BreakbeatItem:
        """确定性完成事项；重复完成不追加新版本。"""
        item = self._item_by_id(item_id)
        if item.status == "completed":
            return item
        completed = replace(item, status="completed", updated_at=utc_now_iso())
        self.store.append_jsonl("breakbeat_items.jsonl", completed.to_dict())
        return completed

    async def _review_batch(
        self,
        session_id: str,
        messages: list[MessageRecord],
    ) -> tuple[int, int, int] | None:
        """审阅一批原始消息。"""
        existing = [item for item in self.list_items() if item.source_session_id == session_id]
        prompt = _review_prompt(session_id, messages, existing)
        result = await self._executor.run(
            ProactiveExecutionContext(capability="breakbeat", job_id=new_id("breakbeat"), source_session_ids=(session_id,)),
            prompt,
            allow_tools=False,
        )
        if not result.success:
            return None
        try:
            actions = _parse_actions(result.content)
            return self._apply_actions(session_id, messages, actions)
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    def _apply_actions(
        self,
        session_id: str,
        messages: list[MessageRecord],
        actions: list[dict[str, Any]],
    ) -> tuple[int, int, int]:
        """应用审阅产生的事项变更。"""
        del messages
        projected = {item.id: item for item in self.list_items()}
        staged: list[BreakbeatItem] = []
        created = updated = completed = 0
        for action in actions:
            kind = _action_kind(action)
            if kind == "no_change":
                continue
            if kind == "create":
                _require_keys(action, required={"action", "todo", "deadline"})
                timestamp = utc_now_iso()
                item = BreakbeatItem(
                    id=new_id("breakbeat"),
                    todo=_bounded_text(action.get("todo"), "todo", 1_000),
                    deadline=_deadline(action.get("deadline")),
                    source_session_id=session_id,
                    status="in_progress",
                    created_at=timestamp,
                    updated_at=timestamp,
                )
                projected[item.id] = item
                staged.append(item)
                created += 1
                continue
            if kind == "update":
                _require_keys(action, required={"action", "id"}, optional={"todo", "deadline"})
                if not {"todo", "deadline"}.intersection(action):
                    raise ValueError("Breakbeat update 必须修改 todo 或 deadline")
            else:
                _require_keys(action, required={"action", "id"})
            item_id = _bounded_text(action.get("id"), "id", 200)
            item = projected.get(item_id)
            if item is None:
                raise ValueError(f"Breakbeat 事项不存在：{item_id}")
            if item.source_session_id != session_id:
                raise ValueError("Breakbeat 事项来源无效")
            if kind == "update":
                if item.status != "in_progress":
                    raise ValueError("已完成的 Breakbeat 事项不能更新")
                item = replace(
                    item,
                    todo=_bounded_text(action["todo"], "todo", 1_000) if "todo" in action else item.todo,
                    deadline=_deadline(action["deadline"]) if "deadline" in action else item.deadline,
                    updated_at=utc_now_iso(),
                )
                updated += 1
            else:
                if item.status == "completed":
                    continue
                item = replace(item, status="completed", updated_at=utc_now_iso())
                completed += 1
            projected[item.id] = item
            staged.append(item)
        self.store.append_jsonl_many("breakbeat_items.jsonl", [item.to_dict() for item in staged])
        return created, updated, completed

    def _item_by_id(self, item_id: str) -> BreakbeatItem:
        """按 ID 查找事项。"""
        for item in self.list_items():
            if item.id == item_id:
                return item
        raise ValueError(f"Breakbeat 事项不存在：{item_id}")

    def _state(self) -> dict[str, Any]:
        """读取当前持久化状态。"""
        default = {"cursors": {}, "last_successful_run_at": None, "next_run_at": None}
        payload = self.store.read_json("breakbeat_state.json", default)
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
        self.store.write_json("breakbeat_state.json", state)


class RunBreakbeatTool:
    """仅允许模型在用户明确要求执行 Breakbeat 时调用。"""

    name = "run_breakbeat"
    description = "仅当用户明确要求“执行 Breakbeat”或同等明确措辞时，立即审阅未完成事项。"
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
        manager: BreakbeatManager,
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
            raise ValueError("run_breakbeat.scope 必须为 global 或 session")
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
        return ToolResult(outcome.summary)


class ListBreakbeatTool:
    """列出当前 Breakbeat 事项。"""

    name = "list_breakbeat"
    description = "列出当前 Breakbeat 未完成事项及其状态。"
    parallel_safe = True
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, manager: BreakbeatManager) -> None:
        """初始化对象状态。"""
        self._manager = manager

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """执行当前任务。"""
        del args
        items = self._manager.list_items()
        if not items:
            return ToolResult("暂无 Breakbeat 事项。")
        return ToolResult(
            "\n".join(
                f"{item.id} status={item.status} todo={item.todo}"
                + (f" deadline={item.deadline}" if item.deadline is not None else "")
                for item in items
            )
        )


class CompleteBreakbeatTool:
    """由用户显式确认完成一个 Breakbeat 事项。"""

    name = "complete_breakbeat"
    description = "将一个唯一定位的 Breakbeat 事项标记为已完成。"
    parallel_safe = False
    input_schema = {
        "type": "object",
        "properties": {"id": {"type": "string", "description": "Breakbeat 事项 ID"}},
        "required": ["id"],
    }

    def __init__(self, manager: BreakbeatManager) -> None:
        """初始化对象状态。"""
        self._manager = manager

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """完成当前事项。"""
        item = self._manager.complete_item(str(args["id"]))
        return ToolResult(f"已完成 Breakbeat 事项：{item.id}")


class DeleteBreakbeatTool:
    """完全删除一个 Breakbeat 事项。"""

    name = "delete_breakbeat"
    description = "删除一个唯一定位的 Breakbeat 事项。"
    parallel_safe = False
    input_schema = {
        "type": "object",
        "properties": {"id": {"type": "string", "description": "Breakbeat 事项 ID"}},
        "required": ["id"],
    }

    def __init__(self, manager: BreakbeatManager) -> None:
        """初始化对象状态。"""
        self._manager = manager

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """执行当前任务。"""
        item = self._manager.delete_item(str(args["id"]))
        return ToolResult(f"已删除 Breakbeat 事项：{item.id}")


def _review_prompt(
    session_id: str,
    messages: list[MessageRecord],
    existing_items: list[BreakbeatItem],
) -> str:
    """构造审阅提示词。"""
    source = "\n".join(f"[{item.id}] {item.role}: {item.content}" for item in messages)
    existing = "\n".join(
        f"[{item.id}] status={item.status} todo={item.todo} deadline={item.deadline or '(空)'}"
        for item in existing_items
    )
    return (
        "审阅以下单个会话的原始消息，只识别有明确证据的未完成事项。"
        "不要把建议、猜测、闲聊或普通一次性内容升级为事项。"
        "仅返回 JSON：{\"actions\":[...]}; action 只能为 create、update、complete、no_change。"
        "create 必须精确给出 todo 和可空 deadline；update 必须给出 id 并修改 todo 或 deadline；"
        "complete 必须给出 id。不要输出 source_session_id 或消息 ID。"
        f"\n当前事项：\n{existing or '(无)'}"
        f"\n会话：{session_id}\n原始消息：\n{source}"
    )


def _parse_actions(content: str) -> list[dict[str, Any]]:
    """解析并校验输入内容。"""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.endswith("```"):
            text = text[:-3]
    payload = json.loads(text)
    if not isinstance(payload, dict) or not isinstance(payload.get("actions"), list):
        raise ValueError("Breakbeat 输出必须包含 actions 数组")
    if not all(isinstance(action, dict) for action in payload["actions"]):
        raise ValueError("Breakbeat action 必须是对象")
    return [dict(action) for action in payload["actions"]]


def _action_kind(action: dict[str, Any]) -> str:
    """读取受控动作名。"""
    kind = action.get("action")
    if kind not in {"create", "update", "complete", "no_change"}:
        raise ValueError("Breakbeat action 非法")
    if kind == "no_change":
        _require_keys(action, required={"action"})
    return kind


def _require_keys(
    payload: dict[str, Any],
    *,
    required: set[str],
    optional: set[str] | None = None,
) -> None:
    """拒绝缺失字段及由模型伪造的协议外字段。"""
    allowed = required | (optional or set())
    if set(payload) != required | set(payload).intersection(optional or set()):
        raise ValueError("Breakbeat action 字段无效")
    if not required.issubset(payload) or not set(payload).issubset(allowed):
        raise ValueError("Breakbeat action 字段无效")


def _bounded_text(value: object, label: str, maximum: int) -> str:
    """校验并限制文本内容。"""
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f"{label} 无效")
    return value.strip()


def _deadline(value: object) -> str | None:
    """校验可空 ISO-8601 date/datetime。"""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("deadline 无效")
    text = value.strip()
    try:
        if len(text) == 10:
            date.fromisoformat(text)
        else:
            datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError("deadline 无效") from exc
    return text


def _item_from_dict(payload: dict[str, Any]) -> BreakbeatItem:
    """从字典恢复事项。"""
    if _is_legacy_item(payload):
        raise ValueError("检测到旧版 Breakbeat 结构")
    required = {
        "id",
        "todo",
        "deadline",
        "source_session_id",
        "status",
        "created_at",
        "updated_at",
    }
    if set(payload) != required:
        raise ValueError("Breakbeat 事项字段无效")
    status = str(payload["status"])
    if status not in {"in_progress", "completed"}:
        raise ValueError("Breakbeat status 无效")
    return BreakbeatItem(
        id=_bounded_text(payload["id"], "id", 200),
        todo=_bounded_text(payload["todo"], "todo", 1_000),
        deadline=_deadline(payload.get("deadline")),
        source_session_id=_bounded_text(payload["source_session_id"], "source_session_id", 200),
        status=status,
        created_at=_bounded_text(payload["created_at"], "created_at", 100),
        updated_at=_bounded_text(payload["updated_at"], "updated_at", 100),
    )


def _is_legacy_item(payload: dict[str, Any]) -> bool:
    """识别已废弃的 Breakbeat 字段与状态。"""
    legacy_fields = {"title", "next_action", "due_hint", "source_" "message_ids"}
    if set(payload).intersection(legacy_fields):
        return True
    return payload.get("status") in {"open", "closed", "deleted"}

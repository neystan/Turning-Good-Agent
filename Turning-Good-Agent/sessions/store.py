import asyncio
import json
import re
import shutil
from collections.abc import Callable
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from ..bus.messages import ChannelRoute, utc_now_iso
from ..multi_agent.events import MULTI_AGENT_EVENT_TYPES
from .token_counter import count_content_tokens
from .types import MessageRecord, Session, ToolCallRecord


BEIJING_TZ = timezone(timedelta(hours=8))


class JsonlSessionStore:
    """使用 JSONL 文件保存会话、消息、trace 和 token 记录。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir = self.data_dir
        self._write_locks: dict[str, asyncio.Lock] = {}

    async def load_session(self, session_id: str) -> Session | None:
        """按 ID 加载最新会话记录。"""
        path = self._session_file(session_id)
        return self._read_session_file(path)

    async def create_session(
        self,
        session_id: str,
        user_id: str | ChannelRoute,
        channel: str | None = None,
        conversation_id: str | None = None,
    ) -> Session:
        """创建新会话并写入独立目录。"""
        route = self._coerce_route(session_id, user_id, channel, conversation_id)
        now = utc_now_iso()
        session = Session(
            session_id,
            route.principal_id,
            route.channel,
            session_id,
            "",
            False,
            False,
            [],
            now,
            now,
            principal_id=route.principal_id,
            conversation_id=route.conversation_id,
        )
        session_dir = self._new_session_dir(session_id, now)
        session_dir.mkdir(parents=True, exist_ok=True)
        self._write_session(session, session_dir)
        return session

    async def clear_session(self, session_id: str) -> None:
        """删除指定会话目录。"""
        session_dir = self._find_session_dir(session_id)
        if session_dir.exists():
            shutil.rmtree(session_dir)

    async def clear_matching_sessions(
        self,
        predicate: Callable[[Session], bool],
    ) -> list[str]:
        """删除所有满足谓词的完整会话目录，并返回其 Session ID。"""
        if not callable(predicate):
            raise TypeError("predicate 必须可调用")
        matched: list[tuple[str, Path]] = []
        for session_dir in self._all_session_dirs():
            session = self._read_session_file(session_dir / "session.json")
            if session is not None and predicate(session):
                matched.append((session.id, session_dir))
        for _session_id, session_dir in matched:
            shutil.rmtree(session_dir)
        return [session_id for session_id, _session_dir in matched]

    async def cleanup_expired_sessions(self, retention_days: int) -> int:
        """删除超出保留期的会话目录。"""
        if retention_days <= 0:
            return 0
        deadline = datetime.now(UTC) - timedelta(days=retention_days)
        removed = 0
        for session_dir in self._all_session_dirs():
            session_file = session_dir / "session.json"
            if not session_file.exists():
                continue
            session = self._read_session_file(session_file)
            if session is None:
                continue
            expired = datetime.fromisoformat(session.updated_at) < deadline
            if expired:
                shutil.rmtree(session_dir)
                removed += 1
        return removed

    async def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        token_count: int = 0,
        name: str | None = None,
        tool_call_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        message_id: str | None = None,
        created_at: str | None = None,
    ) -> MessageRecord:
        """保存一条会话消息。"""
        now = created_at or utc_now_iso()
        metadata = metadata or {}
        record = MessageRecord(
            id=message_id or str(uuid4()),
            session_id=session_id,
            role=role,
            content=content,
            name=name,
            tool_call_id=tool_call_id,
            token_count=token_count,
            created_at=now,
            metadata=metadata,
        )
        self._append_jsonl(self._messages_file(session_id), self._message_to_dict(record))
        await self._touch_session(session_id)
        return record

    async def recent_messages(self, session_id: str, limit: int) -> list[MessageRecord]:
        """读取最近 N 条消息并按写入顺序返回。"""
        rows = self._read_jsonl(self._messages_file(session_id))
        return [self._dict_to_message(row) for row in rows[-limit:]]

    async def all_messages(self, session_id: str) -> list[MessageRecord]:
        """读取指定会话的全部消息。"""
        return [self._dict_to_message(row) for row in self._read_jsonl(self._messages_file(session_id))]

    async def update_summary(self, session_id: str, summary: str) -> None:
        """更新会话摘要。"""
        session = await self.load_session(session_id)
        if session is None:
            return
        session.summary = summary
        session.updated_at = utc_now_iso()
        self._write_session(session)

    async def list_sessions(
        self,
        archived: bool | None = None,
        *,
        channel: str | None = None,
        principal_id: str | None = None,
    ) -> list[Session]:
        """读取会话列表并按置顶和最后活动时间排序。"""
        sessions = [
            session
            for path in self._all_session_dirs()
            if (session := self._read_session_file(path / "session.json")) is not None
        ]
        if archived is not None:
            sessions = [item for item in sessions if item.archived is archived]
        if channel is not None:
            sessions = [item for item in sessions if item.channel == channel]
        if principal_id is not None:
            sessions = [item for item in sessions if item.principal_id == principal_id]
        sessions.sort(key=lambda item: item.updated_at, reverse=True)
        sessions.sort(key=lambda item: not item.pinned)
        return sessions

    async def update_session_metadata(
        self,
        session_id: str,
        *,
        title: str | None = None,
        pinned: bool | None = None,
        archived: bool | None = None,
    ) -> Session | None:
        """更新会话可管理元数据。"""
        session = await self.load_session(session_id)
        if session is None:
            return None
        if title is not None:
            session.title = title
        if pinned is not None:
            session.pinned = pinned
        if archived is not None:
            session.archived = archived
        session.updated_at = utc_now_iso()
        self._write_session(session)
        return session

    async def update_uncompacted_history(self, session_id: str, history: list[MessageRecord]) -> None:
        """更新会话未压缩上下文窗口。"""
        session = await self.load_session(session_id)
        if session is None:
            return
        session.uncompacted_history = history
        session.updated_at = utc_now_iso()
        self._write_session(session)

    async def save_trace(self, trace: Any) -> None:
        """保存单条状态 trace。"""
        await self.save_turn_traces([trace])

    async def save_turn_traces(self, traces: list[Any]) -> None:
        """批量保存同一轮状态 trace。"""
        if not traces:
            return
        session_id = traces[0].session_id
        rows = [self._trace_to_dict(trace) for trace in traces]
        async with self._write_lock(session_id):
            self._append_jsonl_rows(self._traces_file(session_id), rows)

    async def save_true_token_usage(self, turn_id: str, session_id: str, usage: dict[str, Any]) -> None:
        """保存单轮 token 使用量。"""
        row = {
            "turn_id": turn_id,
            "input_tokens": int(usage["input_tokens"]),
            "output_tokens": int(usage["output_tokens"]),
            "turn_total_tokens": int(usage["turn_total_tokens"]),
            "total_tokens": int(usage["total_tokens"]),
            "compacted": int(usage["compacted"]),
        }
        scope = usage.get("scope")
        if scope is not None:
            if scope not in {"worker", "parent"}:
                raise ValueError("token usage scope 必须是 worker 或 parent")
            row.update(
                {
                    "run_id": self._safe_identifier(usage.get("run_id"), "run_id"),
                    "node_id": self._safe_optional_identifier(usage.get("node_id")),
                    "agent_id": self._safe_identifier(usage.get("agent_id"), "agent_id"),
                    "scope": scope,
                }
            )
        async with self._write_lock(session_id):
            if scope is not None:
                previous = self._read_jsonl(self._true_tokens_file(session_id))
                previous_total = int(previous[-1].get("total_tokens", 0)) if previous else 0
                row["total_tokens"] = previous_total + row["turn_total_tokens"]
            self._append_jsonl(self._true_tokens_file(session_id), row)

    async def save_tool_calls(
        self,
        turn_id: str,
        session_id: str,
        tool_calls: list[dict[str, Any]],
    ) -> None:
        """批量保存单轮工具调用明细。"""
        if not tool_calls:
            return
        now = utc_now_iso()
        rows = [
            self._tool_call_to_dict(
                ToolCallRecord(
                    turn_id=turn_id,
                    tool_call_id=str(record.get("tool_call_id", "")),
                    tool_name=str(record["tool_name"]),
                    args=dict(record.get("args", {})),
                    content=str(record.get("content", "")),
                    error=record.get("error"),
                    duration_ms=float(record.get("duration_ms", 0.0)),
                    created_at=now,
                )
            )
            for record in tool_calls
        ]
        self._append_jsonl_rows(self._tool_calls_file(session_id), rows)

    async def all_tool_calls(self, session_id: str) -> list[ToolCallRecord]:
        """读取指定会话的全部工具调用记录。"""
        return [self._dict_to_tool_call(row) for row in self._read_jsonl(self._tool_calls_file(session_id))]

    async def all_turn_traces(self, session_id: str) -> list[dict[str, Any]]:
        """读取指定会话的全部状态追踪。"""
        return self._read_jsonl(self._traces_file(session_id))

    async def all_true_token_usage(self, session_id: str) -> list[dict[str, Any]]:
        """读取指定会话的全部真实 token 用量。"""
        return self._read_jsonl(self._true_tokens_file(session_id))

    # 从父 Session trace 生成有界的 Multi-Agent Run 摘要。
    async def read_multi_agent_runs(self, session_id: str, limit: int | None = 20) -> list[dict[str, Any]]:
        if limit is not None and limit <= 0:
            return []
        traces = await self.all_turn_traces(session_id)
        runs: dict[str, dict[str, Any]] = {}
        terminal_runs: set[str] = set()
        terminal_nodes: set[tuple[str, str]] = set()
        run_terminal_events = {
            "multi_agent.run.completed",
            "multi_agent.run.failed",
            "multi_agent.run.cancelled",
        }
        run_terminal_statuses = {
            "completed",
            "failed",
            "timed_out",
            "cancelled",
            "interrupted",
        }
        node_terminal_statuses = set(run_terminal_statuses)
        for row in traces:
            if row.get("state") != "MULTI_AGENT":
                continue
            metadata = row.get("metadata")
            if not isinstance(metadata, dict):
                continue
            event = row.get("event")
            if event not in MULTI_AGENT_EVENT_TYPES:
                continue
            run_id = metadata.get("run_id")
            if not isinstance(run_id, str) or not run_id:
                continue
            if run_id not in runs:
                parent_request_id = metadata.get("parent_request_id")
                run_view = {
                    "run_id": run_id,
                    "status": "queued",
                    "strategy": metadata.get("strategy", ""),
                    "nodes": [],
                    "error_code": None,
                    "error": None,
                    "usage": None,
                    "duration_ms": None,
                }
                if isinstance(parent_request_id, str) and parent_request_id:
                    run_view["parent_request_id"] = parent_request_id
                runs[run_id] = run_view
            run_view = runs[run_id]
            status = metadata.get("status")
            if event == "multi_agent.run.started" and run_id not in terminal_runs:
                if isinstance(status, str) and status:
                    run_view["status"] = status
                if isinstance(metadata.get("strategy"), str):
                    run_view["strategy"] = metadata["strategy"]
                if isinstance(metadata.get("duration_ms"), (int, float)):
                    run_view["duration_ms"] = metadata["duration_ms"]
            if event == "multi_agent.node.updated":
                node_id = metadata.get("node_id")
                if not isinstance(node_id, str) or not node_id:
                    continue
                node_key = (run_id, node_id)
                if node_key in terminal_nodes:
                    continue
                node_view = next(
                    (item for item in run_view["nodes"] if item["node_id"] == node_id),
                    None,
                )
                if node_view is None:
                    node_view = {
                        "node_id": node_id,
                        "role": metadata.get("task_label", node_id),
                        "status": "queued",
                        "duration_ms": None,
                        "content": None,
                        "error_code": None,
                        "error": None,
                    }
                    run_view["nodes"].append(node_view)
                if isinstance(status, str) and status:
                    node_view["status"] = status
                if isinstance(metadata.get("task_label"), str):
                    node_view["role"] = metadata["task_label"]
                if isinstance(metadata.get("duration_ms"), (int, float)):
                    node_view["duration_ms"] = metadata["duration_ms"]
                if isinstance(metadata.get("content"), str):
                    node_view["content"] = metadata["content"]
                if isinstance(metadata.get("error_code"), str):
                    node_view["error_code"] = metadata["error_code"]
                if isinstance(metadata.get("error"), str):
                    node_view["error"] = metadata["error"][:256]
                if status in node_terminal_statuses:
                    terminal_nodes.add(node_key)
            if event in run_terminal_events and run_id not in terminal_runs:
                if status in run_terminal_statuses:
                    if isinstance(status, str) and status:
                        run_view["status"] = status
                    run_view["error_code"] = metadata.get("error_code")
                    run_view["error"] = metadata.get("error")
                    if isinstance(metadata.get("duration_ms"), (int, float)):
                        run_view["duration_ms"] = metadata["duration_ms"]
                    terminal_runs.add(run_id)
        ordered = list(runs.values()) if limit is None else list(runs.values())[-limit:]
        for run_view in ordered:
            run_view["usage"] = self._multi_agent_usage_summary(session_id, run_view["run_id"])
        return ordered

    # 在 Gateway 重启后将没有终态的父 Run 和节点一次标记为 interrupted。
    async def recover_interrupted_runs(self) -> list[str]:
        recovered: list[str] = []
        terminal_statuses = {
            "completed",
            "failed",
            "timed_out",
            "cancelled",
            "interrupted",
        }
        for session_dir in self._all_session_dirs():
            session_file = session_dir / "session.json"
            session = self._read_session_file(session_file)
            if session is None:
                continue
            session_id = session.id
            async with self._write_lock(session_id):
                traces = self._read_jsonl(session_dir / "turn_traces.jsonl")
                grouped: dict[str, list[dict[str, Any]]] = {}
                for row in traces:
                    if row.get("state") != "MULTI_AGENT":
                        continue
                    if row.get("event") not in MULTI_AGENT_EVENT_TYPES:
                        continue
                    metadata = row.get("metadata")
                    run_id = metadata.get("run_id") if isinstance(metadata, dict) else None
                    if isinstance(run_id, str) and run_id:
                        grouped.setdefault(run_id, []).append(row)
                additions: list[dict[str, Any]] = []
                for run_id, run_rows in grouped.items():
                    if any(
                        row.get("event") in {
                            "multi_agent.run.completed",
                            "multi_agent.run.failed",
                            "multi_agent.run.cancelled",
                        }
                        and isinstance(row.get("metadata"), dict)
                        and row["metadata"].get("status") in terminal_statuses
                        for row in run_rows
                    ):
                        continue
                    latest_nodes: dict[str, dict[str, Any]] = {}
                    for row in run_rows:
                        metadata = row.get("metadata")
                        if not isinstance(metadata, dict):
                            continue
                        node_id = metadata.get("node_id")
                        if not isinstance(node_id, str) or not node_id:
                            continue
                        latest_nodes[node_id] = metadata
                    for node_id, metadata in latest_nodes.items():
                        if metadata.get("status") in terminal_statuses:
                            continue
                        additions.append(
                            self._recovery_trace_row(
                                session_id,
                                run_id,
                                node_id,
                                metadata,
                            )
                        )
                    additions.append(
                        self._recovery_trace_row(
                            session_id,
                            run_id,
                            None,
                            next(
                                (
                                    row.get("metadata", {})
                                    for row in reversed(run_rows)
                                    if isinstance(row.get("metadata"), dict)
                                ),
                                {},
                            ),
                        )
                    )
                    recovered.append(run_id)
                self._append_jsonl_rows(session_dir / "turn_traces.jsonl", additions)
        return recovered

    async def last_total_tokens(self, session_id: str) -> int:
        """读取当前会话最后一条累计 token。"""
        rows = self._read_jsonl(self._true_tokens_file(session_id))
        if not rows:
            return 0
        return int(rows[-1].get("total_tokens", 0))

    async def count_rows(self, table: str) -> int:
        """读取指定 JSONL 文件的行数，供行为验证使用。"""
        paths = {
            "sessions": "session.json",
            "messages": "messages.jsonl",
            "turn_traces": "turn_traces.jsonl",
            "true_token_usage": "true_token_usage.jsonl",
            "tool_calls": "tool_calls.jsonl",
        }
        if table not in paths:
            raise ValueError(f"不支持的表：{table}")
        if table == "sessions":
            return sum(
                1
                for session_dir in self._all_session_dirs()
                if self._read_session_file(session_dir / "session.json") is not None
            )
        return sum(len(self._read_jsonl(session_dir / paths[table])) for session_dir in self._all_session_dirs())

    # 返回每个 Session 独立的异步写锁，避免 Worker 账本累计竞争。
    def _write_lock(self, session_id: str) -> asyncio.Lock:
        return self._write_locks.setdefault(session_id, asyncio.Lock())

    # 生成安全的持久化标识符。
    @staticmethod
    def _safe_identifier(value: object, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} 必须是非空字符串")
        text = " ".join(value.split())
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", text):
            raise ValueError(f"{field} 包含不安全字符")
        return text

    # 生成允许为空的安全节点标识符。
    @classmethod
    def _safe_optional_identifier(cls, value: object) -> str | None:
        if value is None:
            return None
        return cls._safe_identifier(value, "node_id")

    # 构造重启恢复所需的最小安全 trace 行。
    @staticmethod
    def _recovery_trace_row(
        session_id: str,
        run_id: str,
        node_id: str | None,
        previous: dict[str, Any],
    ) -> dict[str, Any]:
        metadata = {
            "run_id": run_id,
            "node_id": node_id,
            "task_label": previous.get("task_label") if node_id else "multi_agent",
            "strategy": previous.get("strategy", ""),
            "status": "interrupted",
            "duration_ms": previous.get("duration_ms"),
            "usage": None,
            "error_code": "gateway_restart",
            "error": "Gateway 重启导致 Run 中断",
        }
        return {
            "turn_id": f"recovery-{run_id}",
            "state": "MULTI_AGENT",
            "duration_ms": 0.0,
            "event": "multi_agent.node.updated" if node_id else "multi_agent.run.failed",
            "error": "Gateway 重启导致 Run 中断",
            "metadata": metadata,
        }

    # 汇总父 Session 中 Worker 与 Run 总 token 用量。
    def _multi_agent_usage_summary(
        self,
        session_id: str,
        run_id: str,
    ) -> dict[str, dict[str, int]]:
        fields = ("input_tokens", "output_tokens", "turn_total_tokens")
        summary = {
            "worker": {field: 0 for field in fields},
            "total": {field: 0 for field in fields},
        }
        for row in self._read_jsonl(self._true_tokens_file(session_id)):
            if row.get("run_id") != run_id:
                continue
            scope = row.get("scope")
            if scope not in {"worker", "parent"}:
                continue
            for field in fields:
                value = int(row.get(field, 0))
                if scope == "worker":
                    summary["worker"][field] += value
                summary["total"][field] += value
        return summary

    async def _touch_session(self, session_id: str) -> None:
        """更新会话的 updated_at。"""
        session = await self.load_session(session_id)
        if session is None:
            return
        session.updated_at = utc_now_iso()
        self._write_session(session)

    def _append_jsonl(self, path: Path, row: dict[str, Any]) -> None:
        """向 JSONL 文件追加一行。"""
        self._append_jsonl_rows(path, [row])

    def _append_jsonl_rows(self, path: Path, rows: list[dict[str, Any]]) -> None:
        """向 JSONL 文件批量追加多行。"""
        if not rows:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as file:
            for row in rows:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _read_jsonl(self, path: Path) -> list[dict[str, Any]]:
        """读取 JSONL 文件，不存在时返回空列表。"""
        if not path.exists():
            return []
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8") as file:
            for line in file:
                text = line.strip()
                if text:
                    rows.append(json.loads(text))
        return rows

    def _write_jsonl(self, path: Path, rows: list[dict[str, Any]]) -> None:
        """重写 JSONL 文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as file:
            for row in rows:
                file.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _all_session_dirs(self) -> list[Path]:
        """返回所有会话目录。"""
        return [path for path in self.sessions_dir.iterdir() if path.is_dir()]

    def _session_dir(self, session_id: str) -> Path:
        """返回单个会话目录路径。"""
        return self._find_session_dir(session_id)

    def has_session_record(self, session_id: str) -> bool:
        """返回是否存在指定 ID 的原始会话记录（包括已拒绝的旧 schema）。"""
        for session_dir in self._all_session_dirs():
            path = session_dir / "session.json"
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if isinstance(payload, dict) and payload.get("id") == session_id:
                return True
        return False

    def _new_session_dir(self, session_id: str, created_at: str) -> Path:
        """返回新会话目录路径。"""
        stamp = datetime.fromisoformat(created_at).astimezone(BEIJING_TZ).strftime("%Y%m%d_%H%M%S")
        return self.sessions_dir / f"{stamp}_{quote(session_id, safe='')}"

    def _session_file(self, session_id: str) -> Path:
        """返回会话信息文件路径。"""
        return self._session_dir(session_id) / "session.json"

    def _messages_file(self, session_id: str) -> Path:
        """返回消息文件路径。"""
        return self._session_dir(session_id) / "messages.jsonl"

    def _traces_file(self, session_id: str) -> Path:
        """返回状态追踪文件路径。"""
        return self._session_dir(session_id) / "turn_traces.jsonl"

    def _true_tokens_file(self, session_id: str) -> Path:
        """返回 token 记录文件路径。"""
        return self._session_dir(session_id) / "true_token_usage.jsonl"

    def _tool_calls_file(self, session_id: str) -> Path:
        """返回工具调用记录文件路径。"""
        return self._session_dir(session_id) / "tool_calls.jsonl"

    def _write_session(self, session: Session, session_dir: Path | None = None) -> None:
        """写入单个会话信息文件。"""
        path = (session_dir or self._session_dir(session.id)) / "session.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self._session_to_dict(session), ensure_ascii=False), encoding="utf-8")

    def _find_session_dir(self, session_id: str) -> Path:
        """按 session_id 查找真实目录，不存在时返回兼容路径。"""
        legacy_dir = self.sessions_dir / quote(session_id, safe="")
        legacy_session = self._read_session_file(legacy_dir / "session.json")
        if legacy_session is not None and legacy_session.id == session_id:
            return legacy_dir
        for session_dir in self._all_session_dirs():
            session_file = session_dir / "session.json"
            session = self._read_session_file(session_file)
            if session is not None and session.id == session_id:
                return session_dir
        return legacy_dir

    def _read_session_file(self, path: Path) -> Session | None:
        """安全读取有效 Session；未知或损坏目录不会中断扫描。"""
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return None
            string_fields = (
                "id",
                "user_id",
                "channel",
                "principal_id",
                "conversation_id",
                "title",
                "summary",
                "created_at",
                "updated_at",
            )
            if any(not isinstance(payload.get(field), str) for field in string_fields):
                return None
            if not payload["id"]:
                return None
            if not isinstance(payload.get("pinned"), bool) or not isinstance(
                payload.get("archived"), bool
            ):
                return None
            history = payload.get("uncompacted_history")
            if not isinstance(history, list) or any(
                not isinstance(item, dict)
                or not isinstance(item.get("role"), str)
                or not isinstance(item.get("content"), str)
                for item in history
            ):
                return None
            for field in ("created_at", "updated_at"):
                timestamp = datetime.fromisoformat(payload[field])
                if timestamp.tzinfo is None:
                    return None
            return self._dict_to_session(payload)
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return None

    def _dict_to_session(self, row: dict[str, Any]) -> Session:
        """将字典转换为 Session。"""
        uncompacted_history = [
            self._dict_to_message(item)
            for item in row.get("uncompacted_history", [])
        ]
        return Session(
            id=row["id"],
            user_id=row["user_id"],
            channel=row["channel"],
            title=row["title"],
            summary=row["summary"],
            pinned=bool(row.get("pinned", False)),
            archived=bool(row.get("archived", False)),
            uncompacted_history=uncompacted_history,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            principal_id=row["principal_id"],
            conversation_id=row["conversation_id"],
        )

    def _session_to_dict(self, session: Session) -> dict[str, Any]:
        """将 Session 转换为可持久化字典。"""
        return {
            "id": session.id,
            "user_id": session.user_id,
            "channel": session.channel,
            "principal_id": session.principal_id,
            "conversation_id": session.conversation_id,
            "title": session.title,
            "summary": session.summary,
            "pinned": session.pinned,
            "archived": session.archived,
            "uncompacted_history": [
                self._context_message_to_dict(record)
                for record in session.uncompacted_history
            ],
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }

    def _coerce_route(
        self,
        session_id: str,
        user_id: str | ChannelRoute,
        channel: str | None,
        conversation_id: str | None,
    ) -> ChannelRoute:
        """将旧创建参数归一化为带完整身份的路由。"""
        if isinstance(user_id, ChannelRoute):
            if user_id.session_id != session_id:
                raise ValueError("session_id 必须与 route 一致")
            return user_id
        if channel is None:
            raise TypeError("channel 为必填")
        return ChannelRoute(user_id, channel, conversation_id or session_id, session_id)

    def _context_message_to_dict(self, record: MessageRecord) -> dict[str, str]:
        """将上下文消息转换为最小可读字典。"""
        return {
            "role": record.role,
            "content": record.content,
        }

    def _message_to_dict(self, record: MessageRecord) -> dict[str, Any]:
        """将 MessageRecord 转换为可写入 JSONL 的字典。"""
        return {
            "id": record.id,
            "role": record.role,
            "content": record.content,
            "token_count": record.token_count,
            "created_at": record.created_at,
            "metadata": record.metadata,
        }

    def _trace_to_dict(self, trace: Any) -> dict[str, Any]:
        """将状态 trace 转换为可写入 JSONL 的字典。"""
        metadata = getattr(trace, "metadata", {})
        if getattr(trace, "state", None) == "MULTI_AGENT":
            metadata = self._safe_multi_agent_metadata(metadata)
        return {
            "turn_id": trace.turn_id,
            "state": trace.state,
            "duration_ms": trace.duration_ms,
            "event": trace.event,
            "error": trace.error,
            "metadata": metadata,
        }

    # 过滤 Multi-Agent trace 中不允许持久化的提示词和原始调用字段。
    @staticmethod
    def _safe_multi_agent_metadata(metadata: object) -> dict[str, Any]:
        if not isinstance(metadata, dict):
            return {}
        allowed = {
            "run_id",
            "node_id",
            "parent_request_id",
            "task_label",
            "strategy",
            "status",
            "duration_ms",
            "usage",
            "error_code",
            "error",
            "content",
        }
        clean: dict[str, Any] = {}
        for key in allowed:
            value = metadata.get(key)
            if key == "parent_request_id":
                if isinstance(value, str):
                    text = " ".join(value.split())
                    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", text):
                        clean[key] = text
            elif key in {"run_id", "task_label", "strategy", "status", "error_code"}:
                if isinstance(value, str):
                    clean[key] = " ".join(value.split())[:256]
            elif key == "node_id":
                if value is None or isinstance(value, str):
                    clean[key] = value[:128] if isinstance(value, str) else value
            elif key in {"error", "content"}:
                if isinstance(value, str):
                    text = " ".join(value.split())
                    clean[key] = text if key == "content" else text[:256]
            elif key == "usage":
                if isinstance(value, dict):
                    clean[key] = {
                        name: int(value.get(name, 0))
                        for name in ("input_tokens", "output_tokens", "turn_total_tokens")
                        if isinstance(value.get(name), (int, float))
                        and not isinstance(value.get(name), bool)
                    }
            elif key == "duration_ms":
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    clean[key] = max(0.0, float(value))
            else:
                clean[key] = value
        return clean

    def _tool_call_to_dict(self, record: ToolCallRecord) -> dict[str, Any]:
        """将 ToolCallRecord 转换为可写入 JSONL 的字典。"""
        return {
            "turn_id": record.turn_id,
            "tool_call_id": record.tool_call_id,
            "tool_name": record.tool_name,
            "args": record.args,
            "content": record.content,
            "error": record.error,
            "duration_ms": record.duration_ms,
            "created_at": record.created_at,
        }

    def _dict_to_message(self, row: dict[str, Any]) -> MessageRecord:
        """将字典转换为 MessageRecord。"""
        return MessageRecord(
            id=row.get("id", ""),
            session_id=row.get("session_id", ""),
            role=row["role"],
            content=row["content"],
            name=row.get("name"),
            tool_call_id=row.get("tool_call_id"),
            token_count=int(row.get("token_count", count_content_tokens(row["content"]))),
            created_at=row.get("created_at", ""),
            metadata=row.get("metadata", {}),
        )

    def _dict_to_tool_call(self, row: dict[str, Any]) -> ToolCallRecord:
        """将字典转换为 ToolCallRecord。"""
        return ToolCallRecord(
            turn_id=row["turn_id"],
            tool_call_id=row["tool_call_id"],
            tool_name=row["tool_name"],
            args=row.get("args", {}),
            content=row.get("content", ""),
            error=row.get("error"),
            duration_ms=float(row.get("duration_ms", 0.0)),
            created_at=row.get("created_at", ""),
        )

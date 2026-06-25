import json
import shutil
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from ..bus.messages import utc_now_iso
from .token_counter import count_content_tokens
from .types import MessageRecord, Session, ToolCallRecord


BEIJING_TZ = timezone(timedelta(hours=8))


class JsonlSessionStore:
    """使用 JSONL 文件保存会话、消息、trace 和 token 记录。"""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.sessions_dir = self.data_dir

    async def load_session(self, session_id: str) -> Session | None:
        """按 ID 加载最新会话记录。"""
        path = self._session_file(session_id)
        if not path.exists():
            return None
        return self._dict_to_session(json.loads(path.read_text(encoding="utf-8")))

    async def create_session(self, session_id: str, user_id: str, channel: str) -> Session:
        """创建新会话并写入独立目录。"""
        now = utc_now_iso()
        session = Session(session_id, user_id, channel, session_id, "", [], now, now)
        session_dir = self._new_session_dir(session_id, now)
        session_dir.mkdir(parents=True, exist_ok=True)
        self._write_session(session, session_dir)
        return session

    async def clear_session(self, session_id: str) -> None:
        """删除指定会话目录。"""
        session_dir = self._find_session_dir(session_id)
        if session_dir.exists():
            shutil.rmtree(session_dir)

    async def cleanup_expired_sessions(self, retention_days: int) -> int:
        """删除超出保留期的会话目录。"""
        if retention_days <= 0:
            return 0
        deadline = datetime.now(UTC) - timedelta(days=retention_days)
        removed = 0
        for session_dir in self._all_session_dirs():
            session_file = session_dir / "session.json"
            if not session_file.exists():
                shutil.rmtree(session_dir)
                removed += 1
                continue
            payload = json.loads(session_file.read_text(encoding="utf-8"))
            updated_at = payload.get("updated_at") or payload.get("created_at")
            if not updated_at:
                continue
            if datetime.fromisoformat(updated_at) < deadline:
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
    ) -> MessageRecord:
        """保存一条会话消息。"""
        now = utc_now_iso()
        metadata = metadata or {}
        record = MessageRecord(
            id=str(uuid4()),
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
        self._append_jsonl_rows(self._traces_file(session_id), rows)

    async def save_token_usage(self, turn_id: str, session_id: str, usage: dict[str, Any]) -> None:
        """保存单轮 token 使用量。"""
        self._append_jsonl(
            self._tokens_file(session_id),
            {
                "turn_id": turn_id,
                "input_tokens": usage["input_tokens"],
                "output_tokens": usage["output_tokens"],
                "turn_total_tokens": usage["turn_total_tokens"],
                "total_tokens": usage["total_tokens"],
                "compacted": usage["compacted"],
            },
        )

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

    async def last_total_tokens(self, session_id: str) -> int:
        """读取当前会话最后一条累计 token。"""
        rows = self._read_jsonl(self._tokens_file(session_id))
        if not rows:
            return 0
        return int(rows[-1].get("total_tokens", 0))

    async def count_rows(self, table: str) -> int:
        """读取指定 JSONL 文件的行数，供行为验证使用。"""
        paths = {
            "sessions": "session.json",
            "messages": "messages.jsonl",
            "turn_traces": "turn_traces.jsonl",
            "token_usage": "token_usage.jsonl",
            "tool_calls": "tool_calls.jsonl",
        }
        if table not in paths:
            raise ValueError(f"不支持的表：{table}")
        if table == "sessions":
            return sum(1 for session_dir in self._all_session_dirs() if (session_dir / "session.json").exists())
        return sum(len(self._read_jsonl(session_dir / paths[table])) for session_dir in self._all_session_dirs())

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

    def _tokens_file(self, session_id: str) -> Path:
        """返回 token 记录文件路径。"""
        return self._session_dir(session_id) / "token_usage.jsonl"

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
        if (legacy_dir / "session.json").exists():
            return legacy_dir
        for session_dir in self._all_session_dirs():
            session_file = session_dir / "session.json"
            if not session_file.exists():
                continue
            payload = json.loads(session_file.read_text(encoding="utf-8"))
            if payload.get("id") == session_id:
                return session_dir
        return legacy_dir

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
            uncompacted_history=uncompacted_history,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _session_to_dict(self, session: Session) -> dict[str, Any]:
        """将 Session 转换为可持久化字典。"""
        return {
            "id": session.id,
            "user_id": session.user_id,
            "channel": session.channel,
            "title": session.title,
            "summary": session.summary,
            "uncompacted_history": [
                self._context_message_to_dict(record)
                for record in session.uncompacted_history
            ],
            "created_at": session.created_at,
            "updated_at": session.updated_at,
        }

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
        return {
            "turn_id": trace.turn_id,
            "state": trace.state,
            "duration_ms": trace.duration_ms,
            "event": trace.event,
            "error": trace.error,
            "metadata": getattr(trace, "metadata", {}),
        }

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

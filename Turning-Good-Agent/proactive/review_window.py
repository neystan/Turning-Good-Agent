from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..sessions.types import MessageRecord


@dataclass(frozen=True, slots=True)
class ReviewCursor:
    """标记某项主动审阅已处理到的原始消息。"""

    message_id: str
    created_at: str


@dataclass(frozen=True, slots=True)
class ReviewBatch:
    """保持消息完整性的一个有界审阅批次。"""

    messages: tuple[MessageRecord, ...]


@dataclass(frozen=True, slots=True)
class ReviewSelection:
    """一次冻结快照得到的审阅范围和后续游标。"""

    batches: tuple[ReviewBatch, ...]
    advance_cursor: ReviewCursor | None
    skipped_before: bool


def select_review_window(
    messages: list[MessageRecord],
    *,
    cursor: ReviewCursor | None,
    now: datetime,
    refresh: timedelta,
    token_limit: int,
) -> ReviewSelection:
    """按 Phase 7 的游标、刷新窗口和 token 上限选择完整原文。"""
    if token_limit <= 0:
        raise ValueError("token_limit 必须大于 0")
    if refresh <= timedelta(0):
        raise ValueError("refresh 必须大于 0")

    snapshot_time = _as_utc(now)
    source = sorted(
        (
            record
            for record in messages
            if record.role in {"user", "assistant"} and _parse_time(record.created_at) <= snapshot_time
        ),
        key=lambda record: (_parse_time(record.created_at), record.id),
    )
    if not source:
        return ReviewSelection((), cursor, False)

    unread_start = _unread_start(source, cursor)
    if unread_start >= len(source):
        return ReviewSelection((), cursor, False)

    window_start = snapshot_time - refresh
    cursor_is_fresh = cursor is not None and _parse_time(cursor.created_at) >= window_start
    skipped_before = False
    if cursor_is_fresh:
        selected = source[unread_start:]
    else:
        current_start = _first_at_or_after(source, window_start)
        selected_start = max(unread_start, current_start)
        selected = source[selected_start:]
        skipped_before = selected_start > unread_start

    if not selected:
        return ReviewSelection((), _cursor_for(source[-1]), not cursor_is_fresh)
    batches = tuple(ReviewBatch(tuple(batch)) for batch in _batches(selected, token_limit))
    advance_record = source[-1] if skipped_before else selected[-1]
    return ReviewSelection(batches, _cursor_for(advance_record), skipped_before)


def _unread_start(messages: list[MessageRecord], cursor: ReviewCursor | None) -> int:
    """返回 cursor 之后的第一条消息位置，兼容已不存在的旧消息。"""
    if cursor is None:
        return 0
    for index, record in enumerate(messages):
        if record.id == cursor.message_id:
            return index + 1
    cursor_time = _parse_time(cursor.created_at)
    return _first_at_or_after(messages, cursor_time, strict=True)


def _first_at_or_after(
    messages: list[MessageRecord],
    threshold: datetime,
    *,
    strict: bool = False,
) -> int:
    """查找第一个位于时间阈值之后的消息。"""
    for index, record in enumerate(messages):
        timestamp = _parse_time(record.created_at)
        if timestamp > threshold if strict else timestamp >= threshold:
            return index
    return len(messages)


def _batches(messages: list[MessageRecord], token_limit: int) -> list[list[MessageRecord]]:
    """按上限分批，但绝不拆分一条原始消息。"""
    batches: list[list[MessageRecord]] = []
    current: list[MessageRecord] = []
    current_tokens = 0
    for record in messages:
        if record.token_count > token_limit:
            raise ValueError("单条消息超过主动审阅 token 上限")
        if current and current_tokens + record.token_count > token_limit:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(record)
        current_tokens += record.token_count
    if current:
        batches.append(current)
    return batches


def _cursor_for(record: MessageRecord) -> ReviewCursor:
    """生成消息游标。"""
    return ReviewCursor(record.id, record.created_at)


def _parse_time(value: str) -> datetime:
    """解析时间字符串。"""
    timestamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return _as_utc(timestamp)


def _as_utc(value: datetime) -> datetime:
    """转换为 UTC 时间。"""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)

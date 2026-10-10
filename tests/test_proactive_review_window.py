from __future__ import annotations

import importlib
from datetime import UTC, datetime, timedelta

import pytest


review_module = importlib.import_module("Turning-Good-Agent.proactive.review_window")
types_module = importlib.import_module("Turning-Good-Agent.sessions.types")

MessageRecord = types_module.MessageRecord
ReviewCursor = review_module.ReviewCursor
select_review_window = review_module.select_review_window


NOW = datetime(2026, 7, 31, 11, 0, tzinfo=UTC)


def message(identifier: str, minutes_before: int, tokens: int) -> MessageRecord:
    return MessageRecord(
        id=identifier,
        session_id="session-1",
        role="user",
        content=identifier,
        name=None,
        tool_call_id=None,
        token_count=tokens,
        created_at=(NOW - timedelta(minutes=minutes_before)).isoformat(),
        metadata={},
    )


def test_stale_cursor_never_backfills_before_refresh_window() -> None:
    messages = [
        message("old-1", 180, 30),
        message("old-2", 120, 30),
        message("recent-1", 30, 20),
        message("recent-2", 15, 20),
    ]

    selection = select_review_window(
        messages,
        cursor=ReviewCursor("old-1", messages[0].created_at),
        now=NOW,
        refresh=timedelta(hours=1),
        token_limit=80,
    )

    assert [[item.id for item in batch.messages] for batch in selection.batches] == [
        ["recent-1", "recent-2"]
    ]
    assert selection.skipped_before is True
    assert selection.advance_cursor.message_id == "recent-2"


def test_missing_cursor_never_backfills_before_refresh_window() -> None:
    messages = [
        message("old", 120, 10),
        message("recent", 15, 10),
    ]

    selection = select_review_window(
        messages,
        cursor=None,
        now=NOW,
        refresh=timedelta(hours=1),
        token_limit=100,
    )

    assert [[item.id for item in batch.messages] for batch in selection.batches] == [
        ["recent"]
    ]
    assert selection.skipped_before is True


def test_stale_cursor_skips_old_history_when_current_window_exceeds_token_cap() -> None:
    messages = [
        message("cursor", 180, 10),
        message("old-unread", 120, 10),
        message("recent-1", 45, 60),
        message("recent-2", 15, 60),
    ]

    selection = select_review_window(
        messages,
        cursor=ReviewCursor("cursor", messages[0].created_at),
        now=NOW,
        refresh=timedelta(hours=1),
        token_limit=100,
    )

    assert [[item.id for item in batch.messages] for batch in selection.batches] == [
        ["recent-1"],
        ["recent-2"],
    ]
    assert selection.skipped_before is True
    assert selection.advance_cursor.message_id == "recent-2"


def test_fresh_cursor_reads_everything_after_cursor_even_across_batches() -> None:
    messages = [
        message("before", 70, 10),
        message("cursor", 50, 10),
        message("after-1", 40, 60),
        message("after-2", 10, 60),
    ]

    selection = select_review_window(
        messages,
        cursor=ReviewCursor("cursor", messages[1].created_at),
        now=NOW,
        refresh=timedelta(hours=1),
        token_limit=100,
    )

    assert [[item.id for item in batch.messages] for batch in selection.batches] == [
        ["after-1"],
        ["after-2"],
    ]
    assert selection.skipped_before is False
    assert selection.advance_cursor.message_id == "after-2"


def test_single_oversized_message_is_rejected_before_building_a_batch() -> None:
    oversized = message("oversized", 10, 101)

    with pytest.raises(ValueError, match="单条消息超过主动审阅 token 上限"):
        select_review_window(
            [oversized],
            cursor=None,
            now=NOW,
            refresh=timedelta(hours=1),
            token_limit=100,
        )

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace
from typing import Any


state_module = importlib.import_module("Turning-Good-Agent.runtime.state")
llm_types_module = importlib.import_module("Turning-Good-Agent.llm.types")

LLMUsage = llm_types_module.LLMUsage


def cancelled_context() -> SimpleNamespace:
    return SimpleNamespace(
        inbound=SimpleNamespace(session_id="session-1", content="start"),
        turn_id="turn-1",
        session=None,
        uncompacted_history=[],
        consumed_guidance=[],
        final_content="",
        cancelled=True,
        llm_usage=LLMUsage(),
        true_token_usage={},
        tool_calls=[],
    )


class RecordingStore:
    async def save_tool_calls(
        self,
        turn_id: str,
        session_id: str,
        tool_calls: list[dict[str, Any]],
    ) -> None:
        del turn_id, session_id, tool_calls


class RecordingSessions:
    def __init__(self) -> None:
        self.store = RecordingStore()
        self.user_messages: list[str] = []
        self.assistant_messages: list[str] = []

    async def save_user_message(self, session_id: str, content: str, token_count: int) -> None:
        del session_id, token_count
        self.user_messages.append(content)

    async def save_assistant_message(
        self,
        session_id: str,
        content: str,
        token_count: int,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        del session_id, token_count, metadata
        self.assistant_messages.append(content)


class RecordingProactive:
    async def emit(self, event: str, payload: dict[str, str]) -> None:
        del event, payload


def test_cancelled_empty_turn_omits_assistant_from_virtual_history() -> None:
    records = state_module.build_virtual_history(cancelled_context())

    assert [record.role for record in records] == ["user"]


def test_cancelled_empty_turn_does_not_save_assistant_message() -> None:
    async def run() -> None:
        sessions = RecordingSessions()
        runtime = SimpleNamespace(sessions=sessions, proactive=RecordingProactive())

        await state_module.save(runtime, cancelled_context())

        assert sessions.user_messages == ["start"]
        assert sessions.assistant_messages == []

    asyncio.run(run())

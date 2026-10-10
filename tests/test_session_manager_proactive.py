from __future__ import annotations

import asyncio
import importlib


manager_module = importlib.import_module("Turning-Good-Agent.sessions.manager")

SessionManager = manager_module.SessionManager


class RecordingStore:
    def __init__(self) -> None:
        self.archived = None

    async def list_sessions(self, archived):
        self.archived = archived
        return ["session-1"]


def test_session_manager_exposes_session_list_for_proactive_reviewers() -> None:
    async def run() -> None:
        manager = SessionManager.__new__(SessionManager)
        manager.store = RecordingStore()

        assert await manager.list_sessions(archived=False) == ["session-1"]
        assert manager.store.archived is False

    asyncio.run(run())

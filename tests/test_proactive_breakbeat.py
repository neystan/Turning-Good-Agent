from __future__ import annotations

import asyncio
import importlib
import json
from datetime import UTC, datetime, timedelta

import pytest


breakbeat_module = importlib.import_module("Turning-Good-Agent.proactive.breakbeat")
executor_module = importlib.import_module("Turning-Good-Agent.proactive.executor")
settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
store_module = importlib.import_module("Turning-Good-Agent.proactive.store")
types_module = importlib.import_module("Turning-Good-Agent.proactive.types")
llm_types_module = importlib.import_module("Turning-Good-Agent.llm.types")
session_types_module = importlib.import_module("Turning-Good-Agent.sessions.types")

BreakbeatManager = breakbeat_module.BreakbeatManager
CompleteBreakbeatTool = breakbeat_module.CompleteBreakbeatTool
DeleteBreakbeatTool = breakbeat_module.DeleteBreakbeatTool
ProactiveExecutionResult = executor_module.ProactiveExecutionResult
ProactiveSettings = settings_module.ProactiveSettings
ProactiveStore = store_module.ProactiveStore
LLMUsage = llm_types_module.LLMUsage
MessageRecord = session_types_module.MessageRecord
Session = session_types_module.Session
CronJob = types_module.CronJob

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def record(
    identifier: str,
    session_id: str,
    content: str,
    *,
    minutes_before: int = 5,
    role: str = "user",
) -> MessageRecord:
    return MessageRecord(
        id=identifier,
        session_id=session_id,
        role=role,
        content=content,
        name=None,
        tool_call_id=None,
        token_count=10,
        created_at=(NOW - timedelta(minutes=minutes_before)).isoformat(),
        metadata={},
    )


class Sessions:
    def __init__(self, records: dict[str, list[MessageRecord]]) -> None:
        self.records = records

    async def list_sessions(self) -> list[Session]:
        return [
            Session(
                id=session_id,
                user_id="user-1",
                channel="cli",
                title=session_id,
                summary="",
                pinned=False,
                archived=False,
                uncompacted_history=[],
                created_at=(NOW - timedelta(hours=1)).isoformat(),
                updated_at=NOW.isoformat(),
            )
            for session_id in self.records
        ]

    async def all_messages(self, session_id: str) -> list[MessageRecord]:
        return list(self.records[session_id])


class Executor:
    def __init__(self, *responses: str, usage: LLMUsage | None = None) -> None:
        self.responses = list(responses)
        self.usage = usage or LLMUsage(input_tokens=2, output_tokens=3, total_tokens=5)
        self.calls: list[tuple[str, str, frozenset[str] | None]] = []

    async def run(
        self,
        capability: str,
        prompt: str,
        *,
        allowed_tool_names: frozenset[str] | None = None,
    ) -> ProactiveExecutionResult:
        self.calls.append((capability, prompt, allowed_tool_names))
        return ProactiveExecutionResult(True, self.responses.pop(0), self.usage)


class ResultExecutor(Executor):
    def __init__(self, *results: ProactiveExecutionResult) -> None:
        super().__init__()
        self.results = list(results)

    async def run(
        self,
        capability: str,
        prompt: str,
        *,
        allowed_tool_names: frozenset[str] | None = None,
    ) -> ProactiveExecutionResult:
        self.calls.append((capability, prompt, allowed_tool_names))
        return self.results.pop(0)


class BlockingExecutor(Executor):
    def __init__(self, *responses: str) -> None:
        super().__init__(*responses)
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def run(self, capability, prompt, *, allowed_tool_names=None) -> ProactiveExecutionResult:
        self.started.set()
        await self.release.wait()
        return await super().run(capability, prompt, allowed_tool_names=allowed_tool_names)


class FailingSessions(Sessions):
    async def list_sessions(self) -> list[Session]:
        raise OSError("session index unavailable")


class FailingBreakbeatStore(ProactiveStore):
    def write_json(self, name: str, payload: object) -> None:
        if name == "breakbeat.json":
            raise OSError("snapshot unavailable")
        super().write_json(name, payload)


def manager_for(
    tmp_path,
    executor: Executor,
    records: dict[str, list[MessageRecord]] | None = None,
    cron_jobs=None,
) -> tuple[BreakbeatManager, ProactiveStore]:
    store = ProactiveStore(tmp_path)
    manager = BreakbeatManager(
        store,
        Sessions(records or {"session-1": [record("message-1", "session-1", "明天要提交文档。")] }),
        executor,
        ProactiveSettings(),
        cron_jobs=cron_jobs,
        clock=lambda: NOW,
    )
    return manager, store


def test_breakbeat_writes_minimal_snapshot_and_keeps_relative_deadline(tmp_path) -> None:
    async def run() -> None:
        executor = Executor(
            json.dumps(
                {"actions": [{"action": "create", "todo": "提交文档", "deadline": "明天"}]},
                ensure_ascii=False,
            )
        )
        manager, store = manager_for(tmp_path, executor)

        outcome = await manager.run(manual=True, scope="global", now=NOW)

        assert outcome.created == 1
        assert outcome.status == "success"
        assert outcome.successful_batches == 1
        assert outcome.failure_categories == ()
        snapshot = store.read_json("breakbeat.json", {})
        assert set(snapshot) == {"items", "cursors", "next_run_at", "usage"}
        assert snapshot["items"][0]["deadline"] == "明天"
        assert snapshot["items"][0]["status"] == "in_progress"
        assert snapshot["usage"] == {
            "calls": 1,
            "input_tokens": 2,
            "output_tokens": 3,
            "total_tokens": 5,
        }
        assert snapshot["cursors"]["session-1"]["message_id"] == "message-1"
        prompt = executor.calls[0][1]
        assert "会话：session-1" in prompt
        assert "领域状态：" in prompt
        assert "会话消息：" in prompt
        assert "user: 明天要提交文档。" in prompt
        assert "根据会话消息更新待办。只记录用户明确需要完成且尚未完成的事项。" in prompt
        assert "deadline 原样保留；用户未提供则为 null，不推算相对日期。" in prompt
        assert "{\"actions\":[]}" in prompt
        assert "action 只能是 create。" in prompt
        assert executor.calls[0][2] == frozenset()

    asyncio.run(run())


def test_breakbeat_supplies_global_items_and_deterministically_skips_duplicate(tmp_path) -> None:
    async def run() -> None:
        executor = Executor(
            json.dumps(
                {"actions": [{"action": "create", "todo": "提交文档", "deadline": None}]},
                ensure_ascii=False,
            )
        )
        manager, store = manager_for(tmp_path, executor)
        existing = {
            "id": "breakbeat-existing",
            "todo": "提交文档",
            "deadline": "明天",
            "source_session_id": "session-other",
            "status": "in_progress",
            "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
        }
        store.write_json(
            "breakbeat.json",
            {"items": [existing], "cursors": {}, "next_run_at": None, "usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}},
        )

        outcome = await manager.run(manual=True, scope="global", now=NOW)

        assert outcome.created == 0
        assert outcome.status == "success"
        assert outcome.successful_batches == 1
        assert outcome.failure_categories == ()
        assert [item.id for item in manager.list_items()] == ["breakbeat-existing"]
        assert "breakbeat-existing" in executor.calls[0][1]
        assert "提交文档" in executor.calls[0][1]

    asyncio.run(run())


def test_breakbeat_rejects_automatic_update_and_records_its_usage(tmp_path) -> None:
    async def run() -> None:
        executor = Executor('{"actions":[{"action":"update","id":"breakbeat-existing"}]}')
        manager, store = manager_for(tmp_path, executor)

        outcome = await manager.run(manual=True, scope="global", now=NOW)

        assert outcome.errors == 1
        snapshot = store.read_json("breakbeat.json", {})
        assert snapshot["items"] == []
        assert snapshot["cursors"] == {}
        assert snapshot["usage"]["calls"] == 1
        assert snapshot["usage"]["total_tokens"] == 5

    asyncio.run(run())


def test_breakbeat_rejects_legacy_no_change_action(tmp_path) -> None:
    async def run() -> None:
        executor = Executor('{"actions":[{"action":"no_change"}]}')
        manager, store = manager_for(tmp_path, executor)

        outcome = await manager.run(manual=True, scope="global", now=NOW)

        assert outcome.failure_categories == ("action_validation_failed",)
        assert store.read_json("breakbeat.json", {})["items"] == []
        assert store.read_json("breakbeat.json", {})["cursors"] == {}

    asyncio.run(run())


def test_breakbeat_receives_cron_as_non_todo_domain_state(tmp_path) -> None:
    async def run() -> None:
        cron = CronJob(
            id="cron-gym",
            cron="10 16 * * *",
            created_at=NOW.isoformat(),
            prompt="每天下午 16:10 提醒去健身。",
            recurring=True,
            delivery_channels=["all"],
            updated_at=NOW.isoformat(),
            next_run_at=(NOW + timedelta(days=1)).isoformat(),
        )
        executor = Executor('{"actions":[]}')
        manager, _ = manager_for(
            tmp_path,
            executor,
            {
                "session-1": [
                    record("user-1", "session-1", "每天下午 16:10 提醒我去健身。"),
                    record(
                        "assistant-1",
                        "session-1",
                        "已创建定时提醒。",
                        role="assistant",
                    ),
                ]
            },
            cron_jobs=lambda: [cron],
        )

        outcome = await manager.run(manual=True, scope="global", now=NOW)

        assert outcome.created == 0
        prompt = executor.calls[0][1]
        assert "现有 Cron 计划（不可转为待办）" in prompt
        assert "cron-gym" in prompt
        assert "每天下午 16:10 提醒去健身。" in prompt
        assert "提醒、周期计划和 Cron 投递文本不得创建待办" in prompt

    asyncio.run(run())


def test_breakbeat_records_usage_on_invalid_json_without_advancing_cursor(tmp_path) -> None:
    async def run() -> None:
        executor = Executor("not json")
        manager, store = manager_for(tmp_path, executor)

        outcome = await manager.run(manual=True, scope="global", now=NOW)

        assert outcome.errors == 1
        snapshot = store.read_json("breakbeat.json", {})
        assert snapshot["usage"]["calls"] == 1
        assert snapshot["cursors"] == {}

    asyncio.run(run())


@pytest.mark.parametrize(
    ("result", "expected_category"),
    [
        (ProactiveExecutionResult(False, "", LLMUsage(), error="hidden"), "llm_call_failed"),
        (ProactiveExecutionResult(True, "not json", LLMUsage()), "invalid_json"),
        (ProactiveExecutionResult(True, '{"actions":[{"action":"update"}]}', LLMUsage()), "action_validation_failed"),
    ],
)
def test_breakbeat_classifies_failed_batches_without_leaking_model_details(
    tmp_path,
    result: ProactiveExecutionResult,
    expected_category: str,
) -> None:
    async def run() -> None:
        manager, store = manager_for(tmp_path, ResultExecutor(result))

        outcome = await manager.run(manual=False, scope="global", now=NOW)

        assert outcome.status == "failed"
        assert outcome.created == 0
        assert outcome.successful_batches == 0
        assert outcome.failure_categories == (expected_category,)
        assert "hidden" not in outcome.summary
        assert "not json" not in outcome.summary
        assert store.read_json("breakbeat.json", {})["next_run_at"] == "2026-08-02T13:00:00+00:00"

    asyncio.run(run())


def test_breakbeat_continues_after_failed_batch_and_keeps_prior_created_item_and_cursor(tmp_path) -> None:
    async def run() -> None:
        created = ProactiveExecutionResult(
            True,
            json.dumps({"actions": [{"action": "create", "todo": "提交文档", "deadline": None}]}, ensure_ascii=False),
            LLMUsage(input_tokens=2, output_tokens=3, total_tokens=5),
        )
        failed = ProactiveExecutionResult(False, "", LLMUsage(), error="hidden")
        manager, store = manager_for(
            tmp_path,
            ResultExecutor(created, failed),
            {
                "session-1": [record("message-1", "session-1", "提交文档")],
                "session-2": [record("message-2", "session-2", "安排会议")],
            },
        )

        outcome = await manager.run(manual=False, scope="global", now=NOW)

        assert outcome.status == "partial"
        assert outcome.created == 1
        assert outcome.successful_batches == 1
        assert outcome.failure_categories == ("llm_call_failed",)
        snapshot = store.read_json("breakbeat.json", {})
        assert [item["todo"] for item in snapshot["items"]] == ["提交文档"]
        assert snapshot["cursors"] == {
            "session-1": {"message_id": "message-1", "created_at": "2026-08-02T11:55:00+00:00"}
        }
        assert snapshot["next_run_at"] == "2026-08-02T13:00:00+00:00"

    asyncio.run(run())


def test_breakbeat_unexpected_session_failure_becomes_one_backed_off_outcome(tmp_path) -> None:
    async def run() -> None:
        manager = BreakbeatManager(
            ProactiveStore(tmp_path),
            FailingSessions({}),
            Executor('{"actions":[]}'),
            ProactiveSettings(),
            clock=lambda: NOW,
        )

        outcome = await manager.run(manual=False, scope="global", now=NOW)

        assert outcome.status == "failed"
        assert outcome.failure_categories == ("execution_failed",)
        assert "session index unavailable" not in outcome.summary
        assert manager.next_run_at == NOW + timedelta(hours=1)

    asyncio.run(run())


def test_breakbeat_snapshot_write_failure_uses_in_memory_backoff(tmp_path) -> None:
    async def run() -> None:
        manager = BreakbeatManager(
            FailingBreakbeatStore(tmp_path),
            Sessions({"session-1": [record("message-1", "session-1", "提交文档")]}),
            Executor('{"actions":[]}'),
            ProactiveSettings(),
            clock=lambda: NOW,
        )

        outcome = await manager.run(manual=False, scope="global", now=NOW)

        assert outcome.status == "failed"
        assert outcome.failure_categories == ("execution_failed",)
        assert "snapshot unavailable" not in outcome.summary
        assert manager.next_run_at == NOW + timedelta(hours=1)

    asyncio.run(run())


def test_breakbeat_empty_window_advances_cursor_without_model_call(tmp_path) -> None:
    async def run() -> None:
        old = record("old-message", "session-1", "很久以前的消息", minutes_before=120)
        executor = Executor('{"actions":[]}')
        manager, store = manager_for(tmp_path, executor, {"session-1": [old]})

        await manager.run(manual=False, scope="global", now=NOW)

        snapshot = store.read_json("breakbeat.json", {})
        assert executor.calls == []
        assert snapshot["cursors"]["session-1"] == {
            "message_id": "old-message",
            "created_at": old.created_at,
        }
        assert snapshot["usage"]["calls"] == 0

    asyncio.run(run())


def test_breakbeat_complete_and_delete_replace_the_snapshot_items(tmp_path) -> None:
    async def run() -> None:
        executor = Executor('{"actions":[]}')
        manager, store = manager_for(tmp_path, executor)
        item = {
            "id": "breakbeat-existing",
            "todo": "提交文档",
            "deadline": None,
            "source_session_id": "session-1",
            "status": "in_progress",
            "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
        }
        store.write_json(
            "breakbeat.json",
            {"items": [item], "cursors": {}, "next_run_at": None, "usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}},
        )

        await CompleteBreakbeatTool(manager).run({"id": item["id"]})
        assert store.read_json("breakbeat.json", {})["items"][0]["status"] == "completed"
        await DeleteBreakbeatTool(manager).run({"id": item["id"]})
        assert store.read_json("breakbeat.json", {})["items"] == []

    asyncio.run(run())


def test_breakbeat_completion_waits_for_running_review_and_is_not_overwritten(tmp_path) -> None:
    async def run() -> None:
        executor = BlockingExecutor('{"actions":[]}')
        manager, store = manager_for(tmp_path, executor)
        item = {
            "id": "breakbeat-existing",
            "todo": "提交文档",
            "deadline": None,
            "source_session_id": "session-1",
            "status": "in_progress",
            "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
        }
        store.write_json(
            "breakbeat.json",
            {"items": [item], "cursors": {}, "next_run_at": None, "usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}},
        )

        review = asyncio.create_task(manager.run(manual=True, scope="global", now=NOW))
        await executor.started.wait()
        completion = asyncio.create_task(CompleteBreakbeatTool(manager).run({"id": item["id"]}))
        await asyncio.sleep(0)
        assert not completion.done()

        executor.release.set()
        await review
        await completion

        assert store.read_json("breakbeat.json", {})["items"][0]["status"] == "completed"

    asyncio.run(run())

from __future__ import annotations

import asyncio
import importlib
import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest


evolution_module = importlib.import_module("Turning-Good-Agent.proactive.skill_evolution")
executor_module = importlib.import_module("Turning-Good-Agent.proactive.executor")
llm_types_module = importlib.import_module("Turning-Good-Agent.llm.types")
skills_module = importlib.import_module("Turning-Good-Agent.skills.manager")
store_module = importlib.import_module("Turning-Good-Agent.proactive.store")
types_module = importlib.import_module("Turning-Good-Agent.proactive.types")
session_types_module = importlib.import_module("Turning-Good-Agent.sessions.types")

SkillEvolutionManager = evolution_module.SkillEvolutionManager
ProactiveExecutionResult = executor_module.ProactiveExecutionResult
LLMUsage = llm_types_module.LLMUsage
SkillManager = skills_module.SkillManager
SkillsSettings = importlib.import_module("Turning-Good-Agent.config.settings").SkillsSettings
ProactiveStore = store_module.ProactiveStore
Observation = types_module.Observation
OBSERVATION_KINDS = types_module.OBSERVATION_KINDS
MessageRecord = session_types_module.MessageRecord

NOW = datetime(2026, 8, 2, 12, 0, tzinfo=UTC)


def settings(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "skill_observation_turn_interval": 10,
        "skill_observation_token_limit": 160,
        "review_window_token_limit": 100_000,
        "skill_evolution_batch_token_limit": 100_000,
        "skill_evolution_batches_per_kind": 3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def message(
    message_id: str,
    session_id: str,
    role: str,
    content: str,
    created_at: datetime,
    *,
    token_count: int = 4,
    incomplete: bool = False,
) -> MessageRecord:
    return MessageRecord(
        id=message_id,
        session_id=session_id,
        role=role,
        content=content,
        name=None,
        tool_call_id=None,
        token_count=token_count,
        created_at=created_at.isoformat(),
        metadata={"incomplete": True} if incomplete else {},
    )


def completed_turns(session_id: str, count: int) -> list[MessageRecord]:
    records: list[MessageRecord] = []
    for index in range(count):
        started = NOW + timedelta(minutes=index * 2)
        records.extend(
            (
                message(f"user-{index}", session_id, "user", f"用户确认做法 {index}", started),
                message(
                    f"assistant-{index}",
                    session_id,
                    "assistant",
                    f"助手验证做法 {index}",
                    started + timedelta(seconds=1),
                ),
            )
        )
    return records


class Sessions:
    def __init__(self, records: list[MessageRecord]) -> None:
        self.records = records

    async def all_messages(self, session_id: str) -> list[MessageRecord]:
        return [record for record in self.records if record.session_id == session_id]


def result(payload: object, *, usage: tuple[int, int, int] = (2, 3, 5)) -> ProactiveExecutionResult:
    return ProactiveExecutionResult(
        True,
        json.dumps(payload, ensure_ascii=False),
        LLMUsage(input_tokens=usage[0], output_tokens=usage[1], total_tokens=usage[2]),
    )


class BoundaryExecutor:
    def __init__(self) -> None:
        self.capabilities: list[str] = []

    async def run(
        self,
        capability: str,
        prompt: str,
        *,
        allowed_tool_names: frozenset[str] | None = None,
    ) -> ProactiveExecutionResult:
        assert allowed_tool_names == frozenset()
        self.capabilities.append(capability)
        if capability == "skill_observation_signal":
            return result({"explicit_learning_signal": False})
        assert capability == "skill_observation"
        source_ids = re.findall(r"\[([^]]+)] (?:user|assistant):", prompt)
        return result(
            {
                "observation": {
                    "kind": "tool_procedure",
                    "observation": "已验证的工具操作步骤可稳定复用。",
                    "source_message_ids": source_ids[-2:],
                }
            }
        )


def test_observation_uses_minimal_snapshot_at_each_tenth_completed_turn(tmp_path: Path) -> None:
    async def run() -> None:
        session_id = "session-boundary"
        sessions = Sessions(completed_turns(session_id, 10))
        executor = BoundaryExecutor()
        store = ProactiveStore(tmp_path)
        manager = SkillEvolutionManager(
            store,
            sessions,
            executor,
            executor,
            SkillManager(tmp_path / ".skills", SkillsSettings()),
            settings(),
        )

        first = await manager.maybe_observe(session_id)
        repeated = await manager.maybe_observe(session_id)
        sessions.records.extend(completed_turns(session_id, 10)[-20:])
        for index, record in enumerate(sessions.records[-20:], start=10):
            record.id = record.id.replace("-", f"-next-{index}-", 1)
        second = await manager.maybe_observe(session_id)

        assert first is not None
        assert repeated is None
        assert second is not None
        assert executor.capabilities == ["skill_observation", "skill_observation"]
        snapshot = store.read_json("skill.json", {})
        assert set(snapshot) == {"observations", "next_run_at", "usage"}
        assert len(snapshot["observations"]) == 2
        assert snapshot["observations"][0]["source_session_id"] == session_id
        assert snapshot["observations"][0]["source_message_ids"] == ["user-9", "assistant-9"]
        assert snapshot["usage"] == {
            "calls": 2,
            "input_tokens": 4,
            "output_tokens": 6,
            "total_tokens": 10,
        }
        assert not (tmp_path / "proactive" / "observations.jsonl").exists()
        assert not (tmp_path / "proactive" / "skill_evolution_state.json").exists()

    asyncio.run(run())


def test_skill_evolution_failure_defers_next_run_after_unexpected_error(tmp_path: Path) -> None:
    async def run() -> None:
        manager = SkillEvolutionManager(
            ProactiveStore(tmp_path),
            Sessions([]),
            BoundaryExecutor(),
            BoundaryExecutor(),
            SkillManager(tmp_path / ".skills", SkillsSettings()),
            settings(),
            clock=lambda: NOW,
        )
        observation = Observation(
            id="obs-failure",
            created_at=NOW.isoformat(),
            kind="workflow",
            observation="已验证的工作流。",
            source_session_id="session-1",
            source_message_ids=["message-1"],
        )
        manager.store.write_json(
            "skill.json",
            {
                "observations": [observation.to_dict()],
                "next_run_at": (NOW - timedelta(minutes=1)).isoformat(),
                "usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            },
        )
        manager._select_candidates = _raise_unexpected  # type: ignore[method-assign]

        with pytest.raises(RuntimeError, match="unexpected review failure"):
            await manager.run_evolution(manual=False, now=NOW)

        assert manager.next_run_at == NOW + timedelta(days=1)

    asyncio.run(run())


async def _raise_unexpected(*_args, **_kwargs):
    raise RuntimeError("unexpected review failure")


def test_observation_uses_completed_event_boundary_when_later_turn_is_already_saved(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        session_id = "session-event-boundary"
        executor = BoundaryExecutor()
        manager = SkillEvolutionManager(
            ProactiveStore(tmp_path),
            Sessions(completed_turns(session_id, 11)),
            executor,
            executor,
            SkillManager(tmp_path / ".skills", SkillsSettings()),
            settings(),
        )

        observation = await manager.maybe_observe(
            session_id,
            completed_assistant_message_id="assistant-9",
        )

        assert observation is not None
        assert observation.source_message_ids == ["user-9", "assistant-9"]
        assert executor.capabilities == ["skill_observation"]

    asyncio.run(run())


def test_explicit_learning_signal_observes_before_interval_without_durable_session_cursor(
    tmp_path: Path,
) -> None:
    class ExplicitExecutor(BoundaryExecutor):
        async def run(
            self,
            capability: str,
            prompt: str,
            *,
            allowed_tool_names: frozenset[str] | None = None,
        ) -> ProactiveExecutionResult:
            if capability == "skill_observation_signal":
                self.capabilities.append(capability)
                return result({"explicit_learning_signal": True})
            return await super().run(capability, prompt, allowed_tool_names=allowed_tool_names)

    async def run() -> None:
        session_id = "session-signal"
        executor = ExplicitExecutor()
        store = ProactiveStore(tmp_path)
        manager = SkillEvolutionManager(
            store,
            Sessions(completed_turns(session_id, 1)),
            executor,
            executor,
            SkillManager(tmp_path / ".skills", SkillsSettings()),
            settings(),
        )

        observation = await manager.maybe_observe(session_id)

        assert observation is not None
        assert observation.source_session_id == session_id
        assert executor.capabilities == ["skill_observation_signal", "skill_observation"]
        snapshot = store.read_json("skill.json", {})
        assert "sessions" not in snapshot
        assert "drafted_observation_ids" not in snapshot

    asyncio.run(run())


def test_observation_keeps_completed_batches_when_a_later_batch_fails(tmp_path: Path) -> None:
    class PartialFailureExecutor:
        def __init__(self) -> None:
            self.calls = 0

        async def run(
            self,
            capability: str,
            prompt: str,
            *,
            allowed_tool_names: frozenset[str] | None = None,
        ) -> ProactiveExecutionResult:
            assert capability == "skill_observation"
            assert allowed_tool_names == frozenset()
            self.calls += 1
            if self.calls == 2:
                return ProactiveExecutionResult(False, "", LLMUsage(total_tokens=5))
            source_id = re.findall(r"\[([^]]+)]", prompt)[-1]
            return result(
                {
                    "observation": {
                        "kind": "workflow",
                        "observation": "第一批已验证的工作流。",
                        "source_message_ids": [source_id],
                    }
                }
            )

    async def run() -> None:
        session_id = "session-partial"
        store = ProactiveStore(tmp_path)
        executor = PartialFailureExecutor()
        manager = SkillEvolutionManager(
            store,
            Sessions(completed_turns(session_id, 1)),
            executor,
            executor,
            SkillManager(tmp_path / ".skills", SkillsSettings()),
            settings(skill_observation_turn_interval=1, review_window_token_limit=4),
        )

        assert await manager.maybe_observe(session_id) is None
        assert await manager.maybe_observe(session_id) is not None

        snapshot = store.read_json("skill.json", {})
        assert [entry["source_message_ids"] for entry in snapshot["observations"]] == [
            ["user-0"],
            ["assistant-0"],
        ]
        assert snapshot["usage"]["calls"] == 3

    asyncio.run(run())


def test_observation_write_failure_leaves_completed_boundary_retryable(
    tmp_path: Path,
    monkeypatch,
) -> None:
    async def run() -> None:
        session_id = "session-write-failure"
        store = ProactiveStore(tmp_path)
        executor = BoundaryExecutor()
        manager = SkillEvolutionManager(
            store,
            Sessions(completed_turns(session_id, 1)),
            executor,
            executor,
            SkillManager(tmp_path / ".skills", SkillsSettings()),
            settings(skill_observation_turn_interval=1),
        )
        original_write = store.write_json
        writes = 0

        def fail_observation_write(name: str, payload: object) -> None:
            nonlocal writes
            writes += 1
            if name == "skill.json" and writes == 2:
                raise OSError("simulated observation write failure")
            original_write(name, payload)

        monkeypatch.setattr(store, "write_json", fail_observation_write)
        with pytest.raises(OSError, match="simulated observation write failure"):
            await manager.maybe_observe(session_id)

        monkeypatch.setattr(store, "write_json", original_write)
        assert await manager.maybe_observe(session_id) is not None
        assert executor.capabilities == ["skill_observation", "skill_observation"]

    asyncio.run(run())


def test_evolution_deletes_only_successfully_consumed_observations_and_reschedules(
    tmp_path: Path,
) -> None:
    class EvolutionExecutor:
        async def run(
            self,
            capability: str,
            prompt: str,
            *,
            allowed_tool_names: frozenset[str] | None = None,
        ) -> ProactiveExecutionResult:
            assert allowed_tool_names == frozenset()
            if capability == "skill_evolution_select":
                return result({"candidates": [{"observation_ids": ["obs-consumed"]}]})
            assert capability == "skill_evolution_draft"
            return result(
                {
                    "draft": {
                        "name": "safe-tool-procedure",
                        "description": "验证后执行工具的可复用步骤。",
                        "instructions": "先验证输入，再执行工具并检查输出。",
                    }
                }
            )

    async def run() -> None:
        session_id = "session-evolution"
        records = completed_turns(session_id, 2)
        store = ProactiveStore(tmp_path)
        store.write_json(
            "skill.json",
            {
                "observations": [
                    Observation(
                        "obs-consumed",
                        NOW.isoformat(),
                        "tool_procedure",
                        "可复用的工具验证步骤。",
                        session_id,
                        ["user-0", "assistant-0"],
                    ).to_dict(),
                    Observation(
                        "obs-retained",
                        (NOW + timedelta(seconds=1)).isoformat(),
                        "tool_procedure",
                        "尚未沉淀的另一条工具步骤。",
                        session_id,
                        ["user-1", "assistant-1"],
                    ).to_dict(),
                ],
                "next_run_at": None,
                "usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            },
        )
        manager = SkillEvolutionManager(
            store,
            Sessions(records),
            EvolutionExecutor(),
            EvolutionExecutor(),
            SkillManager(tmp_path / ".skills", SkillsSettings()),
            settings(),
            clock=lambda: NOW,
        )

        manager.initialize_schedule(NOW)
        outcome = await manager.run_evolution(manual=True, now=NOW + timedelta(hours=1))

        assert outcome.created_count == 1
        assert (tmp_path / ".skills" / ".drafts" / "safe-tool-procedure" / "SKILL.md").is_file()
        snapshot = store.read_json("skill.json", {})
        assert [entry["id"] for entry in snapshot["observations"]] == ["obs-retained"]
        assert snapshot["usage"] == {
            "calls": 2,
            "input_tokens": 4,
            "output_tokens": 6,
            "total_tokens": 10,
        }
        assert manager.next_run_at == NOW + timedelta(hours=1, days=1)
        assert snapshot["next_run_at"] == (NOW + timedelta(hours=1, days=1)).isoformat()

    asyncio.run(run())


def test_evolution_reschedules_from_finished_time_when_now_is_not_injected(tmp_path: Path) -> None:
    async def run() -> None:
        moments = iter((NOW, NOW + timedelta(hours=2)))
        manager = SkillEvolutionManager(
            ProactiveStore(tmp_path),
            Sessions([]),
            BoundaryExecutor(),
            BoundaryExecutor(),
            SkillManager(tmp_path / ".skills", SkillsSettings()),
            settings(),
            clock=lambda: next(moments),
        )

        await manager.run_evolution(manual=True)

        assert manager.next_run_at == NOW + timedelta(days=1, hours=2)

    asyncio.run(run())


def test_evolution_limits_each_observation_kind_to_configured_batches(tmp_path: Path) -> None:
    class SelectionExecutor:
        def __init__(self) -> None:
            self.selection_calls = 0

        async def run(
            self,
            capability: str,
            prompt: str,
            *,
            allowed_tool_names: frozenset[str] | None = None,
        ) -> ProactiveExecutionResult:
            del prompt
            assert allowed_tool_names == frozenset()
            assert capability == "skill_evolution_select"
            self.selection_calls += 1
            return result({"candidates": []})

    async def run() -> None:
        session_id = "session-batch-cap"
        records = [
            message(f"message-{index}", session_id, "user", "来源消息。", NOW + timedelta(seconds=index))
            for index in range(4)
        ]
        observations = [
            Observation(
                f"obs-{index}",
                (NOW + timedelta(seconds=index)).isoformat(),
                "workflow",
                "每条 Observation 都需要单独成批。",
                session_id,
                [f"message-{index}"],
            ).to_dict()
            for index in range(4)
        ]
        store = ProactiveStore(tmp_path)
        store.write_json(
            "skill.json",
            {
                "observations": observations,
                "next_run_at": None,
                "usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            },
        )
        executor = SelectionExecutor()
        manager = SkillEvolutionManager(
            store,
            Sessions(records),
            executor,
            executor,
            SkillManager(tmp_path / ".skills", SkillsSettings()),
            settings(skill_evolution_batch_token_limit=1, skill_evolution_batches_per_kind=2),
        )

        await manager.run_evolution(manual=True, now=NOW)

        assert executor.selection_calls == 2

    asyncio.run(run())


def test_invalid_model_json_still_accumulates_all_skill_call_usage(tmp_path: Path) -> None:
    class InvalidJsonExecutor:
        def __init__(self) -> None:
            self.responses = {
                "skill_observation_signal": ("not-json", (1, 2, 3)),
                "skill_observation": ("not-json", (4, 5, 6)),
                "skill_evolution_select": (
                    json.dumps({"candidates": [{"observation_ids": ["obs-keep"]}]}),
                    (7, 8, 9),
                ),
                "skill_evolution_draft": ("not-json", (10, 11, 12)),
            }

        async def run(
            self,
            capability: str,
            prompt: str,
            *,
            allowed_tool_names: frozenset[str] | None = None,
        ) -> ProactiveExecutionResult:
            del prompt
            assert allowed_tool_names == frozenset()
            content, usage = self.responses[capability]
            return ProactiveExecutionResult(
                True,
                content,
                LLMUsage(input_tokens=usage[0], output_tokens=usage[1], total_tokens=usage[2]),
            )

    async def run() -> None:
        session_id = "session-usage"
        records = completed_turns(session_id, 1)
        store = ProactiveStore(tmp_path)
        store.write_json(
            "skill.json",
            {
                "observations": [
                    Observation(
                        "obs-keep",
                        NOW.isoformat(),
                        "workflow",
                        "需要保留的已验证工作流。",
                        session_id,
                        ["user-0", "assistant-0"],
                    ).to_dict()
                ],
                "next_run_at": None,
                "usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            },
        )
        executor = InvalidJsonExecutor()
        manager = SkillEvolutionManager(
            store,
            Sessions(records),
            executor,
            executor,
            SkillManager(tmp_path / ".skills", SkillsSettings()),
            settings(),
        )

        assert await manager.maybe_observe(session_id) is None
        assert await manager.maybe_observe(session_id, explicit_signal=True) is None
        outcome = await manager.run_evolution(manual=True, now=NOW)

        assert outcome.created_count == 0
        snapshot = store.read_json("skill.json", {})
        assert snapshot["usage"] == {
            "calls": 4,
            "input_tokens": 22,
            "output_tokens": 26,
            "total_tokens": 30,
        }
        assert [entry["id"] for entry in snapshot["observations"]] == ["obs-keep"]
        assert not (tmp_path / ".skills" / ".drafts").exists()

    asyncio.run(run())


def test_evolution_fails_closed_when_any_referenced_source_message_is_missing(
    tmp_path: Path,
) -> None:
    class NoCallExecutor:
        async def run(self, *args: object, **kwargs: object) -> ProactiveExecutionResult:
            raise AssertionError("来源不完整时不应调用模型")

    async def run() -> None:
        session_id = "session-missing"
        store = ProactiveStore(tmp_path)
        store.write_json(
            "skill.json",
            {
                "observations": [
                    Observation(
                        "obs-missing",
                        NOW.isoformat(),
                        "workflow",
                        "原始证据缺失时不能沉淀。",
                        session_id,
                        ["present", "missing"],
                    ).to_dict()
                ],
                "next_run_at": None,
                "usage": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            },
        )
        manager = SkillEvolutionManager(
            store,
            Sessions([message("present", session_id, "user", "存在的原文。", NOW)]),
            NoCallExecutor(),
            NoCallExecutor(),
            SkillManager(tmp_path / ".skills", SkillsSettings()),
            settings(),
        )

        outcome = await manager.run_evolution(manual=True, now=NOW)

        assert outcome.created_count == 0
        snapshot = store.read_json("skill.json", {})
        assert [entry["id"] for entry in snapshot["observations"]] == ["obs-missing"]
        assert snapshot["usage"]["calls"] == 0

    asyncio.run(run())


@pytest.mark.parametrize("kind", sorted(OBSERVATION_KINDS))
def test_observation_parser_accepts_only_current_kinds_and_system_owned_session(kind: str) -> None:
    observation = evolution_module._parse_observation(
        json.dumps(
            {
                "observation": {
                    "kind": kind,
                    "observation": "经过验证的可复用做法。",
                    "source_message_ids": ["message-1"],
                }
            },
            ensure_ascii=False,
        ),
        "session-owned",
        {"message-1"},
        160,
    )

    assert observation is not None
    assert observation.source_session_id == "session-owned"


def test_observation_parser_rejects_model_owned_source_session_id() -> None:
    with pytest.raises(ValueError, match="Observation"):
        evolution_module._parse_observation(
            json.dumps(
                {
                    "observation": {
                        "kind": "workflow",
                        "observation": "经过验证的可复用做法。",
                        "source_message_ids": ["message-1"],
                        "source_session_id": "forged-session",
                    }
                },
                ensure_ascii=False,
            ),
            "session-owned",
            {"message-1"},
            160,
        )


def test_observation_prompt_keeps_evidence_ids_and_explicit_null_boundary() -> None:
    prompt = evolution_module._observation_prompt(
        "session-1",
        [
            message("user-1", "session-1", "user", "请记住这个临时安排。", NOW),
            message("assistant-1", "session-1", "assistant", "好的。", NOW),
        ],
    )

    assert "[user-1] user: 请记住这个临时安排。" in prompt
    assert "source_message_ids 必须原样选自消息 ID" in prompt
    assert "临时偏好、单次任务和普通事实不是 Observation" in prompt
    assert '{"observation":null}' in prompt

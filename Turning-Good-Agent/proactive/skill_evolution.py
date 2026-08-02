from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from ..sessions.token_counter import count_content_tokens
from ..sessions.types import MessageRecord, Session
from ..skills.manager import SkillManager
from ..tools.base import ToolResult
from .executor import ProactiveExecutionContext, ProactiveExecutionResult
from .types import OBSERVATION_KINDS, Observation, new_id, utc_now_iso
from .store import ProactiveStore


class EvolutionSessions(Protocol):
    """Observation 和 Draft 回读会话所需的接口。"""

    async def all_messages(self, session_id: str) -> list[MessageRecord]:
        """读取原始消息。"""

    async def list_sessions(self) -> list[Session]:
        """列出会话。"""


class EvolutionExecutor(Protocol):
    """Skill 自进化的无工具模型入口。"""

    async def run(
        self,
        context: ProactiveExecutionContext,
        prompt: str,
        *,
        allow_tools: bool = True,
    ) -> ProactiveExecutionResult:
        """执行 Observation、选择或 Draft 生成。"""


@dataclass(frozen=True, slots=True)
class SkillEvolutionOutcome:
    """记录一次按 kind 沉淀产生的 Draft 数量。"""

    created_count: int
    summary: str


class SkillEvolutionManager:
    """从严格 Observation 生成待审批的 Skill Draft。"""

    def __init__(
        self,
        store: ProactiveStore,
        sessions: EvolutionSessions,
        review_executor: EvolutionExecutor,
        draft_executor: EvolutionExecutor,
        skills: SkillManager,
        proactive_settings: Any,
    ) -> None:
        """初始化对象状态。"""
        self.store = store
        self._sessions = sessions
        self._review_executor = review_executor
        self._draft_executor = draft_executor
        self._skills = skills
        self._turn_interval = int(proactive_settings.skill_observation_turn_interval)
        self._observation_limit = int(proactive_settings.skill_observation_token_limit)
        self._review_window_limit = int(proactive_settings.review_window_token_limit)
        self._batch_limit = int(proactive_settings.skill_evolution_batch_token_limit)
        self._batches_per_kind = int(proactive_settings.skill_evolution_batches_per_kind)
        self._daily_limit = int(proactive_settings.skill_evolution_daily_draft_limit)
        self._timezone = ZoneInfo(str(proactive_settings.timezone))
        self._observe_lock = asyncio.Lock()
        self._evolution_lock = asyncio.Lock()

    async def maybe_observe(self, session_id: str, *, explicit_signal: bool = False) -> Observation | None:
        """每 N 个成功保存轮次或显式学习信号时提炼一条严格 Observation。"""
        async with self._observe_lock:
            messages = [
                message
                for message in await self._sessions.all_messages(session_id)
                if message.role in {"user", "assistant"}
            ]
            turns = _completed_turns(messages)
            completed_turns = len(turns)
            state = self._state()
            session_state = state["sessions"].get(session_id, {})
            last_turn = int(session_state.get("last_observation_turn", 0))
            due_by_interval = completed_turns - last_turn >= self._turn_interval
            if not explicit_signal and not due_by_interval:
                last_signal_check_turn = int(session_state.get("last_signal_check_turn", 0))
                if completed_turns <= last_signal_check_turn:
                    return None
                signal_messages = [
                    message
                    for turn in turns[last_signal_check_turn:completed_turns]
                    for message in turn
                    if message.role == "user"
                ]
                explicit_signal = await self._has_explicit_learning_signal(session_id, signal_messages)
                session_state["last_signal_check_turn"] = completed_turns
                state["sessions"][session_id] = session_state
                self._save_state(state)
                if not explicit_signal:
                    return None
            source = [message for turn in turns[max(0, last_turn) :] for message in turn]
            if not source:
                return None
            observations: list[Observation] = []
            for batch in _message_batches(source, self._review_window_limit):
                result = await self._review_executor.run(
                    ProactiveExecutionContext(
                        capability="skill_observation",
                        job_id=new_id("observation"),
                        source_session_ids=(session_id,),
                    ),
                    _observation_prompt(session_id, batch),
                    allow_tools=False,
                )
                if not result.success:
                    return None
                try:
                    observation = _parse_observation(
                        result.content,
                        session_id,
                        {message.id for message in batch},
                        self._observation_limit,
                    )
                except (TypeError, ValueError, json.JSONDecodeError):
                    return None
                if observation is not None:
                    observations.append(observation)
            session_state["last_observation_turn"] = completed_turns
            session_state["last_signal_check_turn"] = completed_turns
            state["sessions"][session_id] = session_state
            if not observations:
                self.store.append_jsonl(
                    "observations.jsonl",
                    {
                        "id": new_id("observation_audit"),
                        "created_at": utc_now_iso(),
                        "session_id": session_id,
                        "status": "no_observation",
                    },
                )
                self._save_state(state)
                return None
            self.store.append_jsonl_many(
                "observations.jsonl",
                [observation.to_dict() for observation in observations],
            )
            self._save_state(state)
            return observations[-1]

    async def _has_explicit_learning_signal(
        self,
        session_id: str,
        messages: list[MessageRecord],
    ) -> bool:
        """判断是否存在明确学习信号。"""
        if not messages:
            return False
        for batch in _message_batches(messages, self._review_window_limit):
            result = await self._review_executor.run(
                ProactiveExecutionContext(
                    capability="skill_observation_signal",
                    job_id=new_id("observation_signal"),
                    source_session_ids=(session_id,),
                ),
                _explicit_signal_prompt(session_id, batch),
                allow_tools=False,
            )
            if not result.success:
                return False
            try:
                if _parse_explicit_signal(result.content):
                    return True
            except (TypeError, ValueError, json.JSONDecodeError):
                return False
        return False

    async def run_evolution(self, *, manual: bool, now: datetime | None = None) -> SkillEvolutionOutcome:
        """按 kind 并行、kind 内串行地将 Observation 沉淀为 Draft。"""
        del manual
        current_time = now or datetime.now(UTC)
        async with self._evolution_lock:
            state = self._state()
            self._reset_daily_state(state, current_time)
            consumed = set(state["drafted_observation_ids"])
            groups: dict[str, list[Observation]] = {}
            for observation in self._observations():
                if observation.id not in consumed:
                    groups.setdefault(observation.kind, []).append(observation)
            if not groups or state["daily_draft_count"] >= self._daily_limit:
                self._save_state(state)
                return SkillEvolutionOutcome(0, "未生成新的 Skill Draft。")
            reservation = asyncio.Lock()
            created: list[str] = []

            async def process_kind(kind: str, observations: list[Observation]) -> None:
                """处理同一类型的 Observation。"""
                for batch in _observation_batches(observations, self._batch_limit)[: self._batches_per_kind]:
                    selected = await self._select_candidates(kind, batch)
                    for candidate in selected:
                        async with reservation:
                            if state["daily_draft_count"] >= self._daily_limit:
                                return
                        draft = await self._generate_draft(kind, batch, candidate)
                        if draft is None:
                            continue
                        try:
                            await self._skills.create_draft(
                                draft["name"], draft["description"], draft["instructions"]
                            )
                        except (OSError, RuntimeError, ValueError):
                            continue
                        ids = candidate["observation_ids"]
                        async with reservation:
                            if state["daily_draft_count"] >= self._daily_limit:
                                await self._skills.delete_draft(draft["name"])
                                return
                            state["daily_draft_count"] += 1
                            state["drafted_observation_ids"] = sorted(
                                set(state["drafted_observation_ids"]).union(ids)
                            )
                            created.append(draft["name"])

            await asyncio.gather(*(process_kind(kind, observations) for kind, observations in groups.items()))
            self._save_state(state)
        if not created:
            return SkillEvolutionOutcome(0, "未生成新的 Skill Draft。")
        return SkillEvolutionOutcome(len(created), f"已生成 {len(created)} 个待审批 Skill Draft：{', '.join(sorted(created))}。")

    def list_drafts(self) -> list[str]:
        """列出当前仍未发布的 Draft 名称。"""
        return self._skills.list_drafts()

    async def _select_candidates(self, kind: str, batch: list[Observation]) -> list[dict[str, list[str]]]:
        """筛选 Skill 候选证据。"""
        try:
            source_token_counts = await self._source_token_counts(batch)
        except ValueError:
            return []
        result = await self._review_executor.run(
            ProactiveExecutionContext(capability="skill_evolution_select", job_id=new_id("skill_select")),
            _selection_prompt(kind, batch, source_token_counts, self._batch_limit),
            allow_tools=False,
        )
        if not result.success:
            return []
        try:
            payload = _json_object(result.content)
            candidates = payload.get("candidates")
            if not isinstance(candidates, list):
                return []
            allowed = {observation.id for observation in batch}
            normalized: list[dict[str, list[str]]] = []
            for candidate in candidates:
                if not isinstance(candidate, dict):
                    continue
                raw_ids = candidate.get("observation_ids")
                if not isinstance(raw_ids, list) or not raw_ids or not all(isinstance(item, str) for item in raw_ids):
                    continue
                ids = list(dict.fromkeys(raw_ids))
                selected = [observation for observation in batch if observation.id in set(ids)]
                source_messages = await self._source_messages(selected)
                if (
                    all(item in allowed for item in ids)
                    and source_messages
                    and _messages_fit_token_limit(source_messages, self._batch_limit)
                ):
                    normalized.append({"observation_ids": ids})
            return normalized
        except (TypeError, ValueError, json.JSONDecodeError):
            return []

    async def _generate_draft(
        self,
        kind: str,
        batch: list[Observation],
        candidate: dict[str, list[str]],
    ) -> dict[str, str] | None:
        """生成 Skill 草稿内容。"""
        selected = [observation for observation in batch if observation.id in set(candidate["observation_ids"])]
        try:
            source_messages = await self._source_messages(selected)
        except ValueError:
            return None
        result = await self._draft_executor.run(
            ProactiveExecutionContext(capability="skill_evolution_draft", job_id=new_id("skill_draft")),
            _draft_prompt(kind, selected, source_messages),
            allow_tools=False,
        )
        if not result.success:
            return None
        try:
            payload = _json_object(result.content)
            draft = payload.get("draft")
            if not isinstance(draft, dict):
                return None
            name = _draft_text(draft.get("name"), "name", 80)
            description = _draft_text(draft.get("description"), "description", 240)
            instructions = _draft_text(draft.get("instructions"), "instructions", 16_000)
            return {"name": name, "description": description, "instructions": instructions}
        except (TypeError, ValueError, json.JSONDecodeError):
            return None

    async def _source_messages(self, observations: list[Observation]) -> list[MessageRecord]:
        """读取 Observation 的原始消息。"""
        sessions: dict[str, dict[str, MessageRecord]] = {}
        selected: dict[str, MessageRecord] = {}
        for observation in observations:
            if observation.source_session_id not in sessions:
                sessions[observation.source_session_id] = {
                    message.id: message for message in await self._sessions.all_messages(observation.source_session_id)
                }
            requested = set(observation.source_message_ids)
            found = requested.intersection(sessions[observation.source_session_id])
            if found != requested:
                missing = ", ".join(sorted(requested - found))
                raise ValueError(
                    f"Observation {observation.id} 的来源消息不完整：{missing}"
                )
            for message_id in observation.source_message_ids:
                message = sessions[observation.source_session_id][message_id]
                selected[message.id] = message
        return sorted(selected.values(), key=lambda message: (message.created_at, message.id))

    async def _source_token_counts(self, observations: list[Observation]) -> dict[str, int]:
        """统计来源消息 token 数。"""
        counts: dict[str, int] = {}
        for observation in observations:
            messages = await self._source_messages([observation])
            counts[observation.id] = sum(max(0, message.token_count) for message in messages)
        return counts

    def _observations(self) -> list[Observation]:
        """读取已保存的 Observation。"""
        values: list[Observation] = []
        for row in self.store.read_jsonl("observations.jsonl"):
            if not {"id", "kind", "observation", "source_session_id", "source_message_ids"}.issubset(row):
                continue
            try:
                values.append(
                    Observation(
                        id=str(row["id"]),
                        created_at=str(row["created_at"]),
                        kind=str(row["kind"]),
                        observation=str(row["observation"]),
                        source_session_id=str(row["source_session_id"]),
                        source_message_ids=[str(item) for item in row["source_message_ids"]],
                    )
                )
            except (KeyError, TypeError, ValueError):
                continue
        return sorted(values, key=lambda observation: (observation.created_at, observation.id))

    def _state(self) -> dict[str, Any]:
        """读取当前持久化状态。"""
        payload = self.store.read_json(
            "skill_evolution_state.json",
            {"sessions": {}, "drafted_observation_ids": [], "daily_date": None, "daily_draft_count": 0},
        )
        if not isinstance(payload, dict):
            payload = {}
        sessions = payload.get("sessions")
        drafted = payload.get("drafted_observation_ids")
        return {
            "sessions": dict(sessions) if isinstance(sessions, dict) else {},
            "drafted_observation_ids": [str(item) for item in drafted] if isinstance(drafted, list) else [],
            "daily_date": payload.get("daily_date"),
            "daily_draft_count": int(payload.get("daily_draft_count", 0)),
        }

    def _reset_daily_state(self, state: dict[str, Any], current_time: datetime) -> None:
        """按日期重置沉淀状态。"""
        date = current_time.astimezone(self._timezone).date().isoformat()
        if state["daily_date"] != date:
            state["daily_date"] = date
            state["daily_draft_count"] = 0

    def _save_state(self, state: dict[str, Any]) -> None:
        """保存当前持久化状态。"""
        self.store.write_json("skill_evolution_state.json", state)


class RunSkillEvolutionTool:
    """手动运行一次按 kind 的 Skill Draft 沉淀。"""

    name = "run_skill_evolution"
    description = "当用户明确要求审阅学习内容并生成候选 Skill 时运行；不会自动发布。"
    parallel_safe = False
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, manager: SkillEvolutionManager) -> None:
        """初始化对象状态。"""
        self._manager = manager

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """执行当前任务。"""
        del args
        outcome = await self._manager.run_evolution(manual=True)
        return ToolResult(outcome.summary)


class ListSkillDraftsTool:
    """列出尚未发布的 Draft Skill。"""

    name = "list_skill_drafts"
    description = "列出当前待用户审阅、尚未发布的 Skill Draft。"
    parallel_safe = True
    input_schema = {"type": "object", "properties": {}}

    def __init__(self, manager: SkillEvolutionManager) -> None:
        """初始化对象状态。"""
        self._manager = manager

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """执行当前任务。"""
        del args
        drafts = self._manager.list_drafts()
        return ToolResult("暂无 Skill Draft。" if not drafts else "\n".join(drafts))


class DeleteSkillDraftTool:
    """删除一个 Draft，始终走现有审批链。"""

    name = "delete_skill_draft"
    description = "删除一个尚未发布的 Skill Draft；该操作需要用户审批。"
    parallel_safe = False
    approval_required = True
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Skill Draft 名称"}},
        "required": ["name"],
    }

    def __init__(self, skills: SkillManager) -> None:
        """初始化对象状态。"""
        self._skills = skills

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """执行当前任务。"""
        name = str(args["name"])
        await self._skills.delete_draft(name)
        return ToolResult(f"已删除 Skill Draft：{name}")


def _observation_prompt(session_id: str, messages: list[MessageRecord]) -> str:
    """构造审阅提示词。"""
    source = "\n".join(f"[{message.id}] {message.role}: {message.content}" for message in messages)
    return (
        "从下列会话原文中判断是否存在一个严格、具体、可复用的学习模式。"
        "只接受纠正、验证或重复证据；不要从普通聊天、一次性任务或猜测生成 Observation。"
        "仅返回 JSON：{\"observation\":null} 或 {\"observation\":{\"kind\":...,\"observation\":...,"
        "\"source_message_ids\":[...]}}。kind 只能是 "
        f"{', '.join(sorted(OBSERVATION_KINDS))}；内容 1-3 句且不超过 160 tokens。"
        f"\n会话：{session_id}\n原始消息：\n{source}"
    )


def _explicit_signal_prompt(session_id: str, messages: list[MessageRecord]) -> str:
    """构造审阅提示词。"""
    source = "\n".join(f"[{message.id}] {message.content}" for message in messages)
    return (
        "判断用户是否明确要求将本轮或近期已验证经验沉淀为可复用的 Skill 学习内容。"
        "只有直接、明确的学习或沉淀要求才为 true；不要从一般任务、建议或猜测推断。"
        "仅返回 JSON：{\"explicit_learning_signal\":true|false}。"
        f"\n会话：{session_id}\n用户消息：\n{source}"
    )


def _selection_prompt(
    kind: str,
    observations: list[Observation],
    source_token_counts: dict[str, int],
    token_limit: int,
) -> str:
    """构造审阅提示词。"""
    payload = json.dumps([item.to_dict() for item in observations], ensure_ascii=False)
    counts = json.dumps(source_token_counts, ensure_ascii=False, sort_keys=True)
    return (
        f"审阅同一 kind={kind} 的 Observation，只有存在一致且可复用的真实证据组时才选择。"
        "同 kind 本身不是生成 Skill 的理由。候选关联原文的去重 token 总量不得超过 "
        f"{token_limit}。仅返回 JSON：{{\"candidates\":[{{\"observation_ids\":[...]}}]}}。"
        f"\nObservation：{payload}\n每条 Observation 的原文 token 数：{counts}"
    )


def _draft_prompt(kind: str, observations: list[Observation], source_messages: list[MessageRecord]) -> str:
    """构造审阅提示词。"""
    observation_json = json.dumps([item.to_dict() for item in observations], ensure_ascii=False)
    source = "\n".join(f"[{message.id}] {message.role}: {message.content}" for message in source_messages)
    return (
        f"基于同类 kind={kind} 的 Observation 和关联原文生成一个高质量、可复用的 Draft Skill。"
        "不要自动发布。仅返回 JSON：{\"draft\":{\"name\":小写连字符名、\"description\":...,"
        "\"instructions\":...}}；没有足够证据则返回 {\"draft\":null}。"
        f"\nObservation：{observation_json}\n关联原文：\n{source}"
    )


def _parse_observation(
    content: str,
    session_id: str,
    allowed_ids: set[str],
    token_limit: int,
) -> Observation | None:
    """解析并校验输入内容。"""
    payload = _json_object(content)
    entry = payload.get("observation")
    if entry is None:
        return None
    if not isinstance(entry, dict):
        raise ValueError("Observation 输出无效")
    if set(entry) != {"kind", "observation", "source_message_ids"}:
        raise ValueError("Observation 字段无效")
    kind = str(entry.get("kind", ""))
    text = _draft_text(entry.get("observation"), "observation", 2_000)
    if kind not in OBSERVATION_KINDS or count_content_tokens(text) > token_limit or _sentence_count(text) > 3:
        raise ValueError("Observation 不符合质量边界")
    raw_ids = entry.get("source_message_ids")
    if not isinstance(raw_ids, list) or not raw_ids or not all(isinstance(item, str) for item in raw_ids):
        raise ValueError("Observation 必须包含来源")
    ids = list(dict.fromkeys(raw_ids))
    if any(item not in allowed_ids for item in ids):
        raise ValueError("Observation 来源不属于当前会话范围")
    return Observation(new_id("obs"), utc_now_iso(), kind, text, session_id, ids)


def _parse_explicit_signal(content: str) -> bool:
    """解析并校验输入内容。"""
    payload = _json_object(content)
    value = payload.get("explicit_learning_signal")
    if not isinstance(value, bool):
        raise ValueError("学习信号输出无效")
    return value


def _observation_batches(observations: list[Observation], token_limit: int) -> list[list[Observation]]:
    """执行当前处理。"""
    batches: list[list[Observation]] = []
    current: list[Observation] = []
    tokens = 0
    for observation in sorted(observations, key=lambda item: (item.created_at, item.id)):
        size = count_content_tokens(observation.observation)
        if current and tokens + size > token_limit:
            batches.append(current)
            current = []
            tokens = 0
        current.append(observation)
        tokens += size
    if current:
        batches.append(current)
    return batches


def _messages_fit_token_limit(messages: list[MessageRecord], token_limit: int) -> bool:
    """执行当前处理。"""
    return sum(max(0, message.token_count) for message in messages) <= token_limit


def _message_batches(messages: list[MessageRecord], token_limit: int) -> list[list[MessageRecord]]:
    """执行当前处理。"""
    batches: list[list[MessageRecord]] = []
    current: list[MessageRecord] = []
    current_tokens = 0
    for message in messages:
        message_tokens = max(0, message.token_count)
        if message_tokens > token_limit:
            raise ValueError("单条消息超过主动审阅 token 上限")
        if current and current_tokens + message_tokens > token_limit:
            batches.append(current)
            current = []
            current_tokens = 0
        current.append(message)
        current_tokens += message_tokens
    if current:
        batches.append(current)
    return batches


def _completed_turns(messages: list[MessageRecord]) -> list[list[MessageRecord]]:
    """按已保存的成功 assistant 响应重建完整轮次。"""
    turns: list[list[MessageRecord]] = []
    pending_users: list[MessageRecord] = []
    for message in messages:
        if message.role == "user":
            pending_users.append(message)
            continue
        if message.role != "assistant" or not pending_users:
            continue
        if message.metadata.get("incomplete"):
            pending_users = []
            continue
        turns.append([*pending_users, message])
        pending_users = []
    return turns


def _json_object(content: str) -> dict[str, Any]:
    """解析并校验输入内容。"""
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.endswith("```"):
            text = text[:-3]
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("输出必须是 JSON object")
    return payload


def _draft_text(value: object, label: str, maximum: int) -> str:
    """校验并限制文本内容。"""
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > maximum:
        raise ValueError(f"{label} 无效")
    return value.strip()


def _sentence_count(text: str) -> int:
    """执行当前处理。"""
    return max(1, sum(text.count(mark) for mark in ".。!?！？"))

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_command_catalog_exposes_catalog_actions() -> None:
    build_command_catalog = importlib.import_module(
        "Turning-Good-Agent.web.backend.read_models"
    ).build_command_catalog

    runtime = SimpleNamespace(
        skills=SimpleNamespace(list_skills=lambda: []),
        mcp=SimpleNamespace(statuses={}),
        agent_loop=SimpleNamespace(tools=SimpleNamespace(tool_names=[])),
    )

    entries = build_command_catalog(runtime)["entries"]
    actions = {entry["catalog_action"] for entry in entries if entry["action"] == "execute_catalog"}

    assert actions == {
        "compact",
        "run_skill_evolution",
        "run_dream:session",
        "run_dream:global",
        "run_breakbeat:session",
        "run_breakbeat:global",
    }


def test_catalog_executor_emits_activity_without_chat_response() -> None:
    async def run() -> None:
        CatalogActionExecutor = importlib.import_module(
            "Turning-Good-Agent.gateway.catalog_actions"
        ).CatalogActionExecutor

        events: list[tuple[str, dict[str, object]]] = []

        class Hub:
            async def publish(self, session_id: str, request_id: str, event_type: str, payload=None):
                del session_id, request_id
                events.append((event_type, payload or {}))

        class Coordinator:
            hub = Hub()

            async def begin_catalog_action(self, session_id: str, request_id: str, *, exclusive: bool) -> None:
                del session_id, request_id, exclusive

            async def finish_catalog_action(self, session_id: str, request_id: str) -> None:
                del session_id, request_id

        class Proactive:
            async def run_catalog_action(self, action: str, scope: str, session_id: str, route) -> str:
                del scope, session_id, route
                return f"{action} done"

        executor = CatalogActionExecutor(
            runtime_provider=lambda: SimpleNamespace(settings=SimpleNamespace(runtime=SimpleNamespace(turn_timeout_seconds=1))),
            proactive_provider=lambda: Proactive(),
            web_coordinator=Coordinator(),
        )
        request_id = await executor.execute("session-1", "run_dream:session")
        await executor.wait_for_idle()

        assert request_id
        assert [event[0] for event in events] == [
            "task.queued",
            "tool.started",
            "tool.finished",
            "task.completed",
        ]
        assert not any(event_type.startswith("response.") for event_type, _ in events)

    asyncio.run(run())


def test_manual_compaction_persists_summary_tokens_and_trace_without_messages(tmp_path: Path) -> None:
    async def run() -> None:
        ChannelRoute = importlib.import_module("Turning-Good-Agent.bus.messages").ChannelRoute
        compact_session = importlib.import_module(
            "Turning-Good-Agent.gateway.catalog_actions"
        ).compact_session
        llm_types = importlib.import_module("Turning-Good-Agent.llm.types")
        LLMResponse, LLMUsage = llm_types.LLMResponse, llm_types.LLMUsage
        TokenMonitor = importlib.import_module(
            "Turning-Good-Agent.observability.token_monitor"
        ).TokenMonitor
        JsonlSessionStore = importlib.import_module(
            "Turning-Good-Agent.sessions.store"
        ).JsonlSessionStore

        store = JsonlSessionStore(tmp_path)
        route = ChannelRoute("local-user", "web", "browser", "session-1")
        await store.create_session(route.session_id, route)
        await store.save_message(route.session_id, "user", "第一条事实", 4)
        await store.save_message(route.session_id, "assistant", "第一条答复", 4)
        before_messages = await store.count_rows("messages")

        class Llm:
            async def complete(self, messages, tools):
                del messages, tools
                return LLMResponse("压缩摘要", usage=LLMUsage(8, 3, 11))

        settings = SimpleNamespace(
            memory=SimpleNamespace(compact_token_threshold=1, recent_window_token_limit=1),
        )
        runtime = SimpleNamespace(
            settings=settings,
            sessions=SimpleNamespace(store=store, all_messages=store.all_messages),
            agent_loop=SimpleNamespace(llm=Llm(), tools=SimpleNamespace(openai_tools=lambda: [])),
            token_monitor=TokenMonitor(),
        )

        result = await compact_session(runtime, route.session_id, "compact-1")

        session = await store.load_session(route.session_id)
        assert result.summary == "压缩摘要"
        assert session is not None and session.summary == "压缩摘要"
        assert await store.count_rows("messages") == before_messages
        assert await store.count_rows("tool_calls") == 0
        assert (await store.all_true_token_usage(route.session_id))[-1]["compacted"] == 1
        trace = (await store.all_turn_traces(route.session_id))[-1]
        assert trace["state"] == "COMPACT"
        assert trace["metadata"]["compacted_token_count"] == 8

    asyncio.run(run())


def test_catalog_action_rejects_duplicate_ability() -> None:
    async def run() -> None:
        CatalogActionExecutor = importlib.import_module(
            "Turning-Good-Agent.gateway.catalog_actions"
        ).CatalogActionExecutor

        class Hub:
            async def publish(self, *args, **kwargs):
                del args, kwargs

        class Coordinator:
            hub = Hub()

            async def begin_catalog_action(self, *args, **kwargs):
                del args, kwargs

            async def finish_catalog_action(self, *args, **kwargs):
                del args, kwargs

        gate = asyncio.Event()

        class Proactive:
            async def run_catalog_action(self, *args, **kwargs):
                del args, kwargs
                await gate.wait()
                return "done"

        executor = CatalogActionExecutor(
            runtime_provider=lambda: SimpleNamespace(settings=SimpleNamespace(runtime=SimpleNamespace(turn_timeout_seconds=1))),
            proactive_provider=lambda: Proactive(),
            web_coordinator=Coordinator(),
        )
        await executor.execute("session-1", "run_dream:global")
        with pytest.raises(RuntimeError, match="正在运行"):
            await executor.execute("session-1", "run_dream:global")
        gate.set()
        await executor.wait_for_idle()

    asyncio.run(run())

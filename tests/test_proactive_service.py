from __future__ import annotations

import asyncio
import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace


service_module = importlib.import_module("Turning-Good-Agent.proactive.service")
messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
turn_context_module = importlib.import_module("Turning-Good-Agent.runtime.turn_context")
memory_module = importlib.import_module("Turning-Good-Agent.memory.long_term")
manager_module = importlib.import_module("Turning-Good-Agent.proactive.manager")
settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
tools_module = importlib.import_module("Turning-Good-Agent.tools.registry")
agent_loop_module = importlib.import_module("Turning-Good-Agent.runtime.agent_loop")
llm_types_module = importlib.import_module("Turning-Good-Agent.llm.types")
channels_module = importlib.import_module("Turning-Good-Agent.channels.base")

ProactiveService = service_module.ProactiveService
InboundMessage = messages_module.InboundMessage
ProfileMemory = memory_module.ProfileMemory
ProactiveManager = manager_module.ProactiveManager
Settings = settings_module.Settings
ToolRegistry = tools_module.ToolRegistry
AgentLoopResult = agent_loop_module.AgentLoopResult
LLMUsage = llm_types_module.LLMUsage
SilentChannelAdapter = channels_module.SilentChannelAdapter


class EmptySessions:
    async def list_sessions(self):
        return []

    async def all_messages(self, session_id: str):
        del session_id
        return []


class FailingSessions(EmptySessions):
    async def list_sessions(self):
        raise RuntimeError("private infrastructure detail")


class EmptyLoop:
    def __init__(self) -> None:
        self.tools = ToolRegistry()


class PolicyCheckingLoop(EmptyLoop):
    def __init__(self) -> None:
        super().__init__()
        self.channel_adapter = None
        self.auto_approve_tools = None
        self.allowed_tool_names = None
        self.approval_requested = False

    async def run(self, messages, channel_adapter, auto_approve_tools, *, allowed_tool_names=None, **_kwargs):
        self.channel_adapter = channel_adapter
        self.auto_approve_tools = auto_approve_tools
        self.allowed_tool_names = allowed_tool_names
        if allowed_tool_names & {"configured_approval", "tool_level_approval"}:
            self.approval_requested = True
            raise AssertionError("background Cron must not request Tool approval")
        return AgentLoopResult(
            final_content="天气已查询。",
            messages=messages,
            usage=LLMUsage(),
            tool_calls=[],
        )


class PolicyTool:
    description = "测试 Tool"
    input_schema = {"type": "object", "properties": {}}
    parallel_safe = False

    def __init__(self, name: str, *, approval_required: bool = False) -> None:
        self.name = name
        self.approval_required = approval_required


class FakeMcp:
    def __init__(self) -> None:
        self.listeners = []

    def add_status_listener(self, listener) -> None:
        self.listeners.append(listener)

    def remove_status_listener(self, listener) -> None:
        self.listeners.remove(listener)


class RecordingNotificationPublisher:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def enqueue(self, **payload: object) -> None:
        self.events.append(payload)


def test_service_preserves_active_inbound_identity_for_manual_review(tmp_path) -> None:
    settings = Settings(data_dir=Path(tmp_path))
    inbound = InboundMessage(
        id="inbound-1",
        session_id="session-1",
        user_id="user-1",
        channel="cli",
        content="只审阅当前会话。",
        created_at="2026-08-02T01:02:03+00:00",
    )
    runtime = SimpleNamespace(
        settings=settings,
        agent_loop=EmptyLoop(),
        sessions=EmptySessions(),
        profile_memory=ProfileMemory(Path(tmp_path), settings.proactive),
        mcp=FakeMcp(),
        proactive=ProactiveManager(),
        is_globally_idle=lambda: True,
    )
    service = ProactiveService(
        runtime,
        notification_publisher=RecordingNotificationPublisher(),
    )
    route_token = turn_context_module.set_current_route(inbound.route)
    inbound_token = turn_context_module.set_current_inbound(inbound)
    try:
        record = service._current_message_record()

        assert record is not None
        assert (record.id, record.created_at, record.session_id, record.content) == (
            inbound.id,
            inbound.created_at,
            inbound.session_id,
            inbound.content,
        )
    finally:
        turn_context_module.reset_current_inbound(inbound_token)
        turn_context_module.reset_current_route(route_token)


def test_service_runs_cron_with_only_live_background_safe_tools(tmp_path) -> None:
    """Would fail if background Cron could receive approval or proactive Tools."""

    async def run() -> None:
        settings = Settings(data_dir=Path(tmp_path))
        settings.tool_permissions.approval_required_tools = ["configured_approval"]
        settings.tool_permissions.auto_approve_tools = True
        loop = PolicyCheckingLoop()
        for tool in (
            PolicyTool("weather"),
            PolicyTool("configured_approval"),
            PolicyTool("tool_level_approval", approval_required=True),
        ):
            loop.tools.register(tool)
        runtime = SimpleNamespace(
            settings=settings,
            agent_loop=loop,
            sessions=EmptySessions(),
            profile_memory=ProfileMemory(Path(tmp_path), settings.proactive),
            mcp=FakeMcp(),
            proactive=ProactiveManager(),
            is_globally_idle=lambda: True,
        )
        service = ProactiveService(runtime, notification_publisher=RecordingNotificationPublisher())
        service.install_tools()
        due_at = datetime.now(UTC) + timedelta(minutes=1)
        service.cron.create_job(
            "查询天气。",
            recurring=False,
            next_run_at=due_at.isoformat(),
        )

        assert await service.cron.run_due(due_at) == 1
        await service.cron.wait_for_jobs()

        assert loop.allowed_tool_names == frozenset({"weather"})
        assert isinstance(loop.channel_adapter, SilentChannelAdapter)
        assert loop.auto_approve_tools is False
        assert loop.approval_requested is False

    asyncio.run(run())


def test_proactive_service_registers_cli_tools_and_stops_its_tasks(tmp_path) -> None:
    async def run() -> None:
        settings = Settings(data_dir=Path(tmp_path))
        runtime = SimpleNamespace(
            settings=settings,
            agent_loop=EmptyLoop(),
            sessions=EmptySessions(),
            profile_memory=ProfileMemory(Path(tmp_path), settings.proactive),
            mcp=FakeMcp(),
            proactive=ProactiveManager(),
            is_globally_idle=lambda: True,
        )
        service = ProactiveService(runtime, notification_publisher=RecordingNotificationPublisher())

        service.install_tools()
        assert {
            "create_cron",
            "run_breakbeat",
            "complete_breakbeat",
            "run_dream",
            "read_profile_memory",
            "run_skill_evolution",
            "list_incidents",
        }.issubset(
            set(runtime.agent_loop.tools.tool_names)
        )
        await service.start()
        assert service.running is True
        assert not hasattr(service, "_next_breakbeat")
        assert not hasattr(service, "_next_dream")
        assert service.breakbeat.next_run_at is not None
        assert service.dream.next_run_at is not None
        await service.stop()
        assert service.running is False

    asyncio.run(run())


def test_service_uses_skill_manager_persisted_deadline(tmp_path) -> None:
    async def run() -> None:
        settings = Settings(data_dir=Path(tmp_path))
        runtime = SimpleNamespace(
            settings=settings,
            agent_loop=EmptyLoop(),
            sessions=EmptySessions(),
            profile_memory=ProfileMemory(Path(tmp_path), settings.proactive),
            mcp=FakeMcp(),
            proactive=ProactiveManager(),
            is_globally_idle=lambda: True,
        )
        service = ProactiveService(runtime, notification_publisher=RecordingNotificationPublisher())

        await service.start()

        assert not hasattr(service, "_next_skill_evolution")
        assert service.skill_evolution.next_run_at is not None
        await service.stop()

    asyncio.run(run())


def test_scheduler_can_be_woken_without_one_second_polling(tmp_path) -> None:
    async def run() -> None:
        settings = Settings(data_dir=Path(tmp_path))
        runtime = SimpleNamespace(
            settings=settings,
            agent_loop=EmptyLoop(),
            sessions=EmptySessions(),
            profile_memory=ProfileMemory(Path(tmp_path), settings.proactive),
            mcp=FakeMcp(),
            proactive=ProactiveManager(),
            is_globally_idle=lambda: True,
        )
        service = ProactiveService(runtime, notification_publisher=RecordingNotificationPublisher())
        calls = 0
        original = service.cron.run_due

        async def recording_run_due(now):
            nonlocal calls
            calls += 1
            return await original(now)

        service.cron.run_due = recording_run_due
        await service.start()
        for _ in range(10):
            if calls:
                break
            await asyncio.sleep(0)
        first_count = calls

        service._wake_scheduler()
        for _ in range(10):
            if calls > first_count:
                break
            await asyncio.sleep(0)

        assert first_count >= 1
        assert calls > first_count
        await service.stop()

    asyncio.run(run())


def test_service_publishes_automatic_breakbeat_through_gateway_without_a_delivery_worker(tmp_path) -> None:
    """Would fail if the service still owned DeliveryGate flushing or a target_channel callback."""

    async def run() -> None:
        settings = Settings(data_dir=Path(tmp_path))
        runtime = SimpleNamespace(
            settings=settings,
            agent_loop=EmptyLoop(),
            sessions=EmptySessions(),
            profile_memory=ProfileMemory(Path(tmp_path), settings.proactive),
            mcp=FakeMcp(),
            proactive=ProactiveManager(),
        )
        publisher = RecordingNotificationPublisher()
        service = ProactiveService(runtime, notification_publisher=publisher)

        async def run_breakbeat(*, manual: bool, scope: str):
            assert (manual, scope) == (False, "global")
            return SimpleNamespace(should_notify=True, summary="事项已处理", errors=[])

        service.breakbeat.run = run_breakbeat
        await service._run_breakbeat_tick()

        assert publisher.events == [
            {
                "event_type": "proactive.breakbeat.completed",
                "content": "事项已处理",
                "source_id": "breakbeat",
                "notification_scope": "all_subscribed",
                "principal_id": "local-user",
            }
        ]
        assert not hasattr(service, "_delivery_task")

    asyncio.run(run())


def test_service_publishes_one_partial_breakbeat_result_and_updates_incident_silently(tmp_path) -> None:
    async def run() -> None:
        settings = Settings(data_dir=Path(tmp_path))
        runtime = SimpleNamespace(
            settings=settings,
            agent_loop=EmptyLoop(),
            sessions=EmptySessions(),
            profile_memory=ProfileMemory(Path(tmp_path), settings.proactive),
            mcp=FakeMcp(),
            proactive=ProactiveManager(),
        )
        publisher = RecordingNotificationPublisher()
        service = ProactiveService(runtime, notification_publisher=publisher)

        async def run_breakbeat(*, manual: bool, scope: str):
            assert (manual, scope) == (False, "global")
            return SimpleNamespace(
                status="partial",
                created=1,
                successful_batches=1,
                failure_categories=("invalid_json",),
                summary="Breakbeat 部分完成：新增 1 项；1 个批次失败。",
            )

        service.breakbeat.run = run_breakbeat
        await service._run_breakbeat_tick()

        assert publisher.events == [
            {
                "event_type": "proactive.breakbeat.partial",
                "content": "Breakbeat 部分完成：新增 1 项；1 个批次失败。",
                "source_id": "breakbeat",
                "notification_scope": "all_subscribed",
                "principal_id": "local-user",
            }
        ]
        assert service.incidents.get_by_fingerprint("proactive:breakbeat").state == "open"

    asyncio.run(run())


def test_automatic_breakbeat_exception_advances_deadline_and_publishes_one_safe_failure(tmp_path) -> None:
    """Would fail if an unexpected review error retried immediately or produced no result notice."""

    async def run() -> None:
        settings = Settings(data_dir=Path(tmp_path))
        settings.proactive.breakbeat_refresh_minutes = 60
        runtime = SimpleNamespace(
            settings=settings,
            agent_loop=EmptyLoop(),
            sessions=FailingSessions(),
            profile_memory=ProfileMemory(Path(tmp_path), settings.proactive),
            mcp=FakeMcp(),
            proactive=ProactiveManager(),
        )
        publisher = RecordingNotificationPublisher()
        service = ProactiveService(runtime, notification_publisher=publisher)
        finished_at = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
        service.breakbeat._clock = lambda: finished_at

        await service._run_breakbeat_tick()

        assert service.breakbeat.next_run_at == finished_at + timedelta(hours=1)
        assert publisher.events == [
            {
                "event_type": "proactive.breakbeat.failed",
                "content": "Breakbeat 执行失败，请稍后重试。",
                "source_id": "breakbeat",
                "notification_scope": "all_subscribed",
                "principal_id": "local-user",
            }
        ]
        assert "private infrastructure detail" not in str(publisher.events)
        assert service.incidents.get_by_fingerprint("proactive:breakbeat").state == "open"

    asyncio.run(run())


def test_manual_breakbeat_and_dream_keep_foreground_results_when_notification_fails(tmp_path) -> None:
    class FailingPublisher(RecordingNotificationPublisher):
        async def enqueue(self, **payload: object) -> None:
            del payload
            raise OSError("notification unavailable")

    async def run() -> None:
        settings = Settings(data_dir=Path(tmp_path))
        runtime = SimpleNamespace(
            settings=settings,
            agent_loop=EmptyLoop(),
            sessions=EmptySessions(),
            profile_memory=ProfileMemory(Path(tmp_path), settings.proactive),
            mcp=FakeMcp(),
            proactive=ProactiveManager(),
        )
        service = ProactiveService(runtime, notification_publisher=FailingPublisher())
        service.install_tools()
        service.breakbeat.run = lambda **_kwargs: _async_result(
            SimpleNamespace(status="success", created=1, summary="新增事项")
        )
        service.dream.run = lambda **_kwargs: _async_result(
            SimpleNamespace(changed=True, summary="Dream 已更新长期记忆。", errors=0, updated_targets=("user",))
        )
        route_token = turn_context_module.set_current_route(
            messages_module.ChannelRoute("stan", "cli", "research", "cli-session")
        )
        try:
            breakbeat_result = await runtime.agent_loop.tools.get("run_breakbeat").run({"scope": "global"})
            dream_result = await runtime.agent_loop.tools.get("run_dream").run({"scope": "global"})
        finally:
            turn_context_module.reset_current_route(route_token)

        assert breakbeat_result.content == "新增事项"
        assert "Dream 审阅完成" in dream_result.content

    asyncio.run(run())


async def _async_result(value):
    return value


def test_manual_proactive_tools_publish_only_changed_results_to_the_origin_route(tmp_path) -> None:
    """Would fail if a manual result bypassed the origin-only Gateway notification path."""

    async def run() -> None:
        settings = Settings(data_dir=Path(tmp_path))
        settings.gateway.principal_id = "stan"
        runtime = SimpleNamespace(
            settings=settings,
            agent_loop=EmptyLoop(),
            sessions=EmptySessions(),
            profile_memory=ProfileMemory(Path(tmp_path), settings.proactive),
            mcp=FakeMcp(),
            proactive=ProactiveManager(),
        )
        publisher = RecordingNotificationPublisher()
        changed_domains: list[str] = []

        async def on_domain_change(domain: str) -> None:
            changed_domains.append(domain)

        service = ProactiveService(
            runtime,
            notification_publisher=publisher,
            on_domain_change=on_domain_change,
        )
        service.install_tools()

        async def run_breakbeat(*, manual: bool, scope: str, **_kwargs):
            assert (manual, scope) == (True, "global")
            return SimpleNamespace(should_notify=True, summary="新增事项")

        async def run_dream(*, manual: bool, scope: str, **_kwargs):
            assert (manual, scope) == (True, "global")
            return SimpleNamespace(
                changed=True,
                updated_targets=("user",),
                errors=0,
                summary="Dream 已更新长期记忆。",
            )

        async def run_skill_evolution(*, manual: bool, **_kwargs):
            assert manual is True
            return SimpleNamespace(created_count=1, summary="已生成一个 Draft")

        service.breakbeat.run = run_breakbeat
        service.dream.run = run_dream
        service.skill_evolution.run_evolution = run_skill_evolution
        route = messages_module.ChannelRoute("stan", "cli", "research", "cli-session")
        route_token = turn_context_module.set_current_route(route)
        try:
            await runtime.agent_loop.tools.get("run_breakbeat").run({"scope": "global"})
            await runtime.agent_loop.tools.get("run_dream").run({"scope": "global"})
            await runtime.agent_loop.tools.get("run_skill_evolution").run({})
        finally:
            turn_context_module.reset_current_route(route_token)

        assert publisher.events == [
            {
                "event_type": "proactive.breakbeat.completed",
                "content": "新增事项",
                "source_id": "breakbeat",
                "notification_scope": "origin",
                "origin_route": route,
                "principal_id": "stan",
            },
            {
                "event_type": "proactive.dream.completed",
                "content": "Dream 已更新长期记忆。",
                "source_id": "dream",
                "notification_scope": "origin",
                "origin_route": route,
                "principal_id": "stan",
            },
            {
                "event_type": "proactive.skill_evolution.completed",
                "content": "已生成一个 Draft",
                "source_id": "skill_evolution",
                "notification_scope": "origin",
                "origin_route": route,
                "principal_id": "stan",
            },
        ]
        assert changed_domains == ["breakbeat", "dream", "skill"]

    asyncio.run(run())


def test_manual_proactive_tools_skip_notifications_when_no_domain_result_changes(tmp_path) -> None:
    """Would fail if no-op manual reviews generated misleading proactive notices."""

    async def run() -> None:
        settings = Settings(data_dir=Path(tmp_path))
        runtime = SimpleNamespace(
            settings=settings,
            agent_loop=EmptyLoop(),
            sessions=EmptySessions(),
            profile_memory=ProfileMemory(Path(tmp_path), settings.proactive),
            mcp=FakeMcp(),
            proactive=ProactiveManager(),
        )
        publisher = RecordingNotificationPublisher()
        service = ProactiveService(runtime, notification_publisher=publisher)
        service.install_tools()

        async def run_breakbeat(**_kwargs):
            return SimpleNamespace(should_notify=False, summary="无新增事项")

        async def run_dream(**_kwargs):
            return SimpleNamespace(
                changed=False,
                updated_targets=(),
                errors=0,
                summary="Dream 审阅完成：无记忆变更。",
            )

        async def run_skill_evolution(**_kwargs):
            return SimpleNamespace(created_count=0, summary="未生成新的 Draft")

        service.breakbeat.run = run_breakbeat
        service.dream.run = run_dream
        service.skill_evolution.run_evolution = run_skill_evolution
        route_token = turn_context_module.set_current_route(
            messages_module.ChannelRoute("stan", "web", "browser", "web-session")
        )
        try:
            await runtime.agent_loop.tools.get("run_breakbeat").run({"scope": "global"})
            await runtime.agent_loop.tools.get("run_dream").run({"scope": "global"})
            await runtime.agent_loop.tools.get("run_skill_evolution").run({})
        finally:
            turn_context_module.reset_current_route(route_token)

        assert publisher.events == []

    asyncio.run(run())

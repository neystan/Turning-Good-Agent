from __future__ import annotations

import asyncio
import importlib
import shutil
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest


messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
registry_module = importlib.import_module("Turning-Good-Agent.channels.registry")
settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
principals_module = importlib.import_module("Turning-Good-Agent.gateway.principals")
memory_module = importlib.import_module("Turning-Good-Agent.memory.long_term")
manager_module = importlib.import_module("Turning-Good-Agent.proactive.manager")
notifications_module = importlib.import_module("Turning-Good-Agent.proactive.notifications")
service_module = importlib.import_module("Turning-Good-Agent.proactive.service")
sessions_module = importlib.import_module("Turning-Good-Agent.sessions.manager")
session_store_module = importlib.import_module("Turning-Good-Agent.sessions.store")
skills_module = importlib.import_module("Turning-Good-Agent.skills.manager")
tools_module = importlib.import_module("Turning-Good-Agent.tools.registry")
turn_context_module = importlib.import_module("Turning-Good-Agent.runtime.turn_context")

ChannelAccountRegistry = registry_module.ChannelAccountRegistry
ChannelConflictError = registry_module.ChannelConflictError
ChannelRoute = messages_module.ChannelRoute
GatewayNotificationPublisher = notifications_module.GatewayNotificationPublisher
GatewayPrincipalResolver = principals_module.GatewayPrincipalResolver
NotificationFanout = notifications_module.NotificationFanout
ProactiveManager = manager_module.ProactiveManager
ProactiveService = service_module.ProactiveService
ProfileMemory = memory_module.ProfileMemory
ProfileMemorySnapshot = memory_module.ProfileMemorySnapshot
SessionManager = sessions_module.SessionManager
Settings = settings_module.Settings
SkillManager = skills_module.SkillManager
ToolRegistry = tools_module.ToolRegistry


class EmptyLoop:
    def __init__(self) -> None:
        self.tools = ToolRegistry()


class FakeMcp:
    def __init__(self) -> None:
        self.listeners = []

    def add_status_listener(self, listener) -> None:
        self.listeners.append(listener)

    def remove_status_listener(self, listener) -> None:
        self.listeners.remove(listener)


class RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def enqueue(self, **payload: object) -> tuple[object, ...]:
        self.events.append(payload)
        return ()


class FakeRuntime:
    def __init__(self, data_dir: Path) -> None:
        self.settings = Settings(data_dir=data_dir)
        self.settings.gateway.principal_id = "owner"
        self.sessions = SessionManager(
            session_store_module.JsonlSessionStore(data_dir), self.settings
        )
        self.profile_memory = ProfileMemory(data_dir, self.settings.proactive)
        self.skills = SkillManager(data_dir / ".skills", self.settings.skills)
        self.agent_loop = EmptyLoop()
        self.mcp = FakeMcp()
        self.proactive = ProactiveManager()
        self._principal_resolver = GatewayPrincipalResolver(
            self.settings,
            owner_sessions=self.sessions,
            owner_profile_memory=self.profile_memory,
            tools=self.agent_loop.tools,
        )

    def resolve_principal_context(self, route: ChannelRoute):
        return self._principal_resolver.resolve(route)


def build_service(tmp_path: Path) -> tuple[ProactiveService, FakeRuntime, ChannelAccountRegistry]:
    runtime = FakeRuntime(tmp_path)
    registry = ChannelAccountRegistry(tmp_path, owner_principal_id="owner")
    service = ProactiveService(
        runtime,
        notification_publisher=RecordingPublisher(),
        principal_registry=registry,
    )
    return service, runtime, registry


def future_time(minutes: int) -> str:
    return (datetime.now(UTC) + timedelta(minutes=minutes)).isoformat()


def test_workspace_for_isolates_owner_and_guest_persistence(tmp_path: Path) -> None:
    service, runtime, _ = build_service(tmp_path)

    owner = service.workspace_for("owner")
    guest = service.workspace_for("guest")
    owner_job = owner.cron.create_job(
        "owner task", recurring=False, next_run_at=future_time(10)
    )
    guest_job = guest.cron.create_job(
        "guest task", recurring=False, next_run_at=future_time(20)
    )

    assert service.workspace_for("guest") is guest
    assert owner.store.root == tmp_path / "proactive"
    assert guest.store.root.parent.parent == tmp_path / "principals"
    assert owner.profile_memory is runtime.profile_memory
    assert guest.profile_memory is not runtime.profile_memory
    assert guest.sessions.principal_id == "guest"
    assert guest.draft_skills.directory == guest.store.root.parent / "skills"
    assert [job.id for job in owner.cron.list_jobs()] == [owner_job.id]
    assert [job.id for job in guest.cron.list_jobs()] == [guest_job.id]


def test_start_restores_registry_principals_with_one_scheduler_task(tmp_path: Path) -> None:
    async def run() -> None:
        service, _, registry = build_service(tmp_path)
        account = registry.create_weixin_invitation(principal_id="guest")

        await service.start()
        try:
            assert service.workspace_for(account.principal_id).principal_id == "guest"
            assert service._scheduler_task is not None
            assert sum(
                task.get_name() == "proactive-scheduler"
                for task in asyncio.all_tasks()
                if not task.done()
            ) == 1
        finally:
            await service.stop()

    asyncio.run(run())


def test_retired_principal_is_not_recreated_by_scheduler(tmp_path: Path) -> None:
    async def run() -> None:
        service, runtime, registry = build_service(tmp_path)
        account = registry.create_weixin_invitation(principal_id="guest")
        workspace = service.workspace_for(account.principal_id)
        root = workspace.store.root.parent

        assert service.is_principal_idle(account.principal_id)
        await service.retire_principal(account.principal_id)
        forgotten_root = runtime._principal_resolver.forget_non_owner(account.principal_id)
        assert forgotten_root == root
        shutil.rmtree(root)

        service._refresh_known_workspaces()

        assert account.principal_id not in service._workspaces
        assert not root.exists()

    asyncio.run(run())


def test_retirement_waits_for_in_flight_scheduler_snapshot(tmp_path: Path) -> None:
    async def run() -> None:
        service, runtime, registry = build_service(tmp_path)
        account = registry.create_weixin_invitation(principal_id="guest")
        workspace = service.workspace_for(account.principal_id)
        root = workspace.store.root.parent
        entered = asyncio.Event()
        release = asyncio.Event()

        async def suspended_run_due(_now: datetime) -> int:
            entered.set()
            await release.wait()
            root.mkdir(parents=True, exist_ok=True)
            (root / "scheduler-write").write_text("done", encoding="utf-8")
            return 0

        workspace.cron.run_due = suspended_run_due
        due_task = asyncio.create_task(
            service._run_workspace_due(workspace, datetime.now(UTC))
        )
        await entered.wait()
        retirement = asyncio.create_task(service.retire_principal(account.principal_id))
        await asyncio.sleep(0)

        assert not retirement.done()

        release.set()
        await due_task
        await retirement
        forgotten_root = runtime._principal_resolver.forget_non_owner(account.principal_id)
        assert forgotten_root == root
        shutil.rmtree(root)
        await asyncio.sleep(0)

        assert account.principal_id not in service._workspaces
        assert not root.exists()

    asyncio.run(run())


def test_busy_principal_retirement_does_not_cancel_observation(tmp_path: Path) -> None:
    async def run() -> None:
        service, _, _ = build_service(tmp_path)
        workspace = service.workspace_for("guest")
        release = asyncio.Event()

        async def observe(*_args: object, **_kwargs: object) -> None:
            await release.wait()

        workspace.skill_evolution.maybe_observe = observe
        service._running = True
        service._accepting_work = True
        await service.handle(
            "conversation.completed",
            {
                "principal_id": "guest",
                "session_id": "guest-session",
                "completed_assistant_message_id": "assistant-1",
            },
        )
        await asyncio.sleep(0)

        assert not service.is_principal_idle("guest")
        with pytest.raises(ChannelConflictError) as exc_info:
            await service.retire_principal("guest")
        assert exc_info.value.code == "binding_busy"
        assert str(exc_info.value) == "独立主体仍有后台任务"
        assert "guest" in service._workspaces
        assert any(not task.cancelled() for task in service._background_tasks)

        release.set()
        await asyncio.gather(*tuple(service._background_tasks))

    asyncio.run(run())


def test_conversation_completed_observes_only_payload_principal(tmp_path: Path) -> None:
    async def run() -> None:
        service, _, _ = build_service(tmp_path)
        owner = service.workspace_for("owner")
        guest = service.workspace_for("guest")
        observed: list[tuple[str, str]] = []

        async def owner_observe(session_id: str, **_kwargs: object) -> None:
            observed.append(("owner", session_id))

        async def guest_observe(session_id: str, **_kwargs: object) -> None:
            observed.append(("guest", session_id))

        owner.skill_evolution.maybe_observe = owner_observe
        guest.skill_evolution.maybe_observe = guest_observe
        service._running = True
        service._accepting_work = True

        await service.handle(
            "conversation.completed",
            {
                "principal_id": "guest",
                "session_id": "guest-session",
                "completed_assistant_message_id": "assistant-1",
            },
        )
        await asyncio.gather(*tuple(service._background_tasks))

        assert observed == [("guest", "guest-session")]

    asyncio.run(run())


def test_interactive_tools_resolve_current_route_workspace(tmp_path: Path) -> None:
    async def run() -> None:
        service, runtime, _ = build_service(tmp_path)
        service.install_tools()
        guest = service.workspace_for("guest")
        guest.profile_memory.write(ProfileMemorySnapshot(user="guest profile"))
        runtime.profile_memory.write(ProfileMemorySnapshot(user="owner profile"))
        route = ChannelRoute("guest", "weixin", "guest-chat", "guest-session")
        route_token = turn_context_module.set_current_route(route)
        try:
            profile = await runtime.agent_loop.tools.get("read_profile_memory").run({})
            await runtime.agent_loop.tools.get("create_cron").run(
                {
                    "prompt": "guest task",
                    "recurring": False,
                    "delay_seconds": 60,
                }
            )
        finally:
            turn_context_module.reset_current_route(route_token)

        assert "guest profile" in profile.content
        assert "owner profile" not in profile.content
        assert [job.prompt for job in guest.cron.list_jobs()] == ["guest task"]
        assert service.cron.list_jobs() == []

    asyncio.run(run())


def test_catalog_action_rejects_route_session_mismatch(tmp_path: Path) -> None:
    async def run() -> None:
        service, _, _ = build_service(tmp_path)
        service._running = True
        service._accepting_work = True
        route = ChannelRoute("guest", "weixin", "guest-chat", "other-session")

        with pytest.raises(PermissionError, match="session"):
            await service.run_catalog_action(
                "run_skill_evolution", "global", "guest-session", route
            )

    asyncio.run(run())


def test_revoked_binding_keeps_workspace_cron_but_loses_subscription(tmp_path: Path) -> None:
    service, _, registry = build_service(tmp_path)
    pending = registry.create_weixin_invitation(principal_id="guest")
    awaiting = registry.update(replace(pending, status="awaiting_first_dm"))
    active = registry.update(
        replace(
            awaiting,
            status="active",
            enabled=True,
            subscribed=True,
            private={"conversation_id": "guest-chat"},
        )
    )
    workspace = service.workspace_for("guest")
    job = workspace.cron.create_job(
        "guest reminder", recurring=False, next_run_at=future_time(10)
    )

    registry.revoke("weixin", active.id)

    assert [item.id for item in workspace.cron.list_jobs()] == [job.id]
    assert registry.subscribed_recipients("guest") == ()


def test_all_subscribed_notification_never_crosses_principal() -> None:
    async def run() -> None:
        sent = []
        fanout = NotificationFanout(
            subscriptions=lambda _principal: (
                notifications_module.NotificationSubscription(
                    messages_module.Recipient("guest", "weixin", "guest-chat"),
                    enabled=True,
                ),
                notifications_module.NotificationSubscription(
                    messages_module.Recipient("owner", "web", "proactive"),
                    enabled=True,
                ),
            ),
            classify_recipient=lambda _recipient: True,
        )
        event = notifications_module.ProactiveResultEvent(
            id="event-guest",
            event_type="proactive.dream.completed",
            content="guest result",
            source_id="dream",
            notification_scope="all_subscribed",
            principal_id="guest",
        )

        async def publish(message) -> None:
            sent.append(message)

        delivered = await fanout.dispatch(event, publish_outbound=publish)

        assert {message.recipient.principal_id for message in delivered} == {"guest"}
        assert sent == list(delivered)

    asyncio.run(run())


def test_automatic_result_explicitly_carries_workspace_principal(tmp_path: Path) -> None:
    async def run() -> None:
        service, _, _ = build_service(tmp_path)
        guest = service.workspace_for("guest")

        async def run_breakbeat(*, manual: bool, scope: str):
            assert (manual, scope) == (False, "global")
            return type(
                "Outcome",
                (),
                {
                    "status": "success",
                    "created": 1,
                    "should_notify": True,
                    "summary": "guest result",
                },
            )()

        guest.breakbeat.run = run_breakbeat
        await service._run_breakbeat_tick(guest)

        assert service.notifications.events == [
            {
                "event_type": "proactive.breakbeat.completed",
                "content": "guest result",
                "source_id": "breakbeat",
                "notification_scope": "all_subscribed",
                "principal_id": "guest",
            }
        ]

    asyncio.run(run())


def test_publisher_without_origin_requires_explicit_principal() -> None:
    async def run() -> None:
        fanout = NotificationFanout(
            subscriptions=lambda _principal: (),
            classify_recipient=lambda _recipient: True,
        )
        publisher = GatewayNotificationPublisher(
            fanout=fanout,
            publish_outbound=lambda _message: asyncio.sleep(0),
            active_cli_route=lambda _principal: None,
            principal_id="owner",
        )

        with pytest.raises(ValueError, match="principal_id"):
            await publisher.enqueue(
                event_type="proactive.cron.completed",
                content="done",
                source_id="cron-1",
                notification_scope="all_subscribed",
            )

    asyncio.run(run())

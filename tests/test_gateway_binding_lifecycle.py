from __future__ import annotations

import asyncio
import importlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest


host_module = importlib.import_module("Turning-Good-Agent.gateway.host")
im_module = importlib.import_module("Turning-Good-Agent.channels.im")
messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
registry_module = importlib.import_module("Turning-Good-Agent.channels.registry")
weixin_ilink_module = importlib.import_module("Turning-Good-Agent.channels.weixin_ilink")
runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
control_module = importlib.import_module("Turning-Good-Agent.web.backend.channel_control")

ChannelConflictError = registry_module.ChannelConflictError
ChannelRoute = messages_module.ChannelRoute
GatewayHost = host_module.GatewayHost


def make_host(tmp_path: Path) -> GatewayHost:
    settings = settings_module.Settings(data_dir=tmp_path / "state")
    settings.gateway.principal_id = "owner"
    settings.proactive.enabled = False
    settings.local_config_path = tmp_path / "settings.local.json"
    runtime = runtime_module.AgentRuntime.create_default(settings, object())
    return GatewayHost(settings, runtime=runtime)


def active_weixin(host: GatewayHost, principal_id: str | None):
    pending = host.channel_registry.create_weixin_invitation(principal_id=principal_id)
    awaiting = host.channel_registry.update(replace(pending, status="awaiting_first_dm"))
    return host.channel_registry.update(
        replace(
            awaiting,
            status="active",
            enabled=True,
            subscribed=True,
            private={
                **awaiting.private,
                "credential_state": "active",
                "conversation_id": awaiting.id,
                "from_user_id": f"user-{awaiting.id}",
                "context_token": f"context-{awaiting.id}",
            },
        )
    )


async def create_owner_session(host: GatewayHost, route: ChannelRoute) -> str:
    await host.current_runtime.sessions.load_or_create(route)
    return route.session_id


async def session_exists(host: GatewayHost, session_id: str) -> bool:
    return await host.current_runtime.sessions.store.load_session(session_id) is not None


def test_owner_weixin_delete_is_exact_and_preserves_other_owner_state(tmp_path: Path) -> None:
    async def run() -> None:
        host = make_host(tmp_path)
        binding = active_weixin(host, "owner")
        exact_id = await create_owner_session(
            host,
            ChannelRoute("owner", "weixin", binding.id, "weixin-exact"),
        )
        child_id = await create_owner_session(
            host,
            ChannelRoute("owner", "weixin", f"{binding.id}:child", "weixin-child"),
        )
        web_id = await create_owner_session(
            host,
            ChannelRoute("owner", "web", binding.id, "owner-web"),
        )
        owner_marker = host.settings.data_dir / "owner-marker"
        owner_marker.write_text("keep", encoding="utf-8")

        result = await host.delete_channel_binding("weixin", binding.id)

        assert result.account_id == binding.id
        assert result.platform == "weixin"
        assert result.principal_kind == "owner"
        assert result.deleted_session_ids == [exact_id]
        assert host.channel_registry.get("weixin", binding.id) is None
        assert not await session_exists(host, exact_id)
        assert await session_exists(host, child_id)
        assert await session_exists(host, web_id)
        assert owner_marker.read_text(encoding="utf-8") == "keep"

    asyncio.run(run())


def test_owner_feishu_delete_uses_only_account_colon_prefix(tmp_path: Path) -> None:
    async def run() -> None:
        host = make_host(tmp_path)
        first = host.channel_registry.register_feishu_bot(
            app_id="app-first",
            app_secret="secret",
            domain="open.feishu.cn",
        )
        second = host.channel_registry.register_feishu_bot(
            app_id="app-second",
            app_secret="secret",
            domain="open.feishu.cn",
        )
        first_chat = await create_owner_session(
            host,
            ChannelRoute("owner", "feishu", f"{first.id}:chat-a", "first-chat"),
        )
        first_exact = await create_owner_session(
            host,
            ChannelRoute("owner", "feishu", first.id, "first-no-colon"),
        )
        second_chat = await create_owner_session(
            host,
            ChannelRoute("owner", "feishu", f"{second.id}:chat-b", "second-chat"),
        )

        result = await host.delete_channel_binding("feishu", first.id)

        assert result.deleted_session_ids == [first_chat]
        assert not await session_exists(host, first_chat)
        assert await session_exists(host, first_exact)
        assert await session_exists(host, second_chat)
        assert host.channel_registry.get("feishu", first.id) is None
        assert host.channel_registry.get("feishu", second.id) is not None

    asyncio.run(run())


def test_independent_delete_retires_and_removes_only_its_root(tmp_path: Path) -> None:
    async def run() -> None:
        host = make_host(tmp_path)
        binding = active_weixin(host, None)
        route = ChannelRoute(binding.principal_id, "weixin", binding.id, "guest-session")
        context = host.principal_resolver.resolve(route)
        await context.sessions.load_or_create(route)
        workspace = host.proactive_service.workspace_for(binding.principal_id)
        root = context.data_root
        (root / "private-marker").write_text("remove", encoding="utf-8")
        owner_marker = host.settings.data_dir / "owner-marker"
        owner_marker.write_text("keep", encoding="utf-8")
        assert workspace.principal_id == binding.principal_id

        result = await host.delete_channel_binding("weixin", binding.id)

        assert result.principal_kind == "independent"
        assert result.deleted_session_ids == [route.session_id]
        assert host.channel_registry.get("weixin", binding.id) is None
        assert binding.principal_id not in host.proactive_service._workspaces
        assert binding.principal_id not in host.principal_resolver._bundles
        assert not root.exists()
        assert owner_marker.read_text(encoding="utf-8") == "keep"

    asyncio.run(run())


def test_terminal_delivery_busy_delete_has_no_mutation(tmp_path: Path) -> None:
    async def run() -> None:
        host = make_host(tmp_path)
        binding = active_weixin(host, "owner")
        route = ChannelRoute("owner", "weixin", binding.id, "busy-session")
        await create_owner_session(host, route)
        host.im_coordinator._controls[route.session_id] = im_module.ImTurnControl(
            "request-busy", route
        )
        disabled: list[str] = []

        async def disable(binding_id: str) -> None:
            disabled.append(binding_id)

        host.weixin_transport.disable_account = disable

        with pytest.raises(ChannelConflictError) as exc_info:
            await host.delete_channel_binding("weixin", binding.id)

        assert exc_info.value.code == "binding_busy"
        assert str(exc_info.value) == "该 Binding 正在处理消息或等待回复投递"
        assert host.channel_registry.get("weixin", binding.id) == binding
        assert await session_exists(host, route.session_id)
        assert disabled == []

    asyncio.run(run())


def test_independent_integrity_conflict_is_preflight_without_mutation(tmp_path: Path) -> None:
    async def run() -> None:
        host = make_host(tmp_path)
        binding = active_weixin(host, None)
        route = ChannelRoute(binding.principal_id, "weixin", binding.id, "guest-session")
        context = host.principal_resolver.resolve(route)
        await context.sessions.load_or_create(route)
        other = host.channel_registry.create_weixin_invitation(principal_id=None)
        other_path = host.channel_registry.root / "weixin" / f"{other.id}.json"
        payload = json.loads(other_path.read_text(encoding="utf-8"))
        payload["principal_id"] = binding.principal_id
        other_path.write_text(
            json.dumps(payload, ensure_ascii=False),
            encoding="utf-8",
        )

        with pytest.raises(ChannelConflictError) as exc_info:
            await host.delete_channel_binding("weixin", binding.id)

        assert exc_info.value.code == "independent_principal_integrity_error"
        assert host.channel_registry.get("weixin", binding.id) is not None
        assert await context.sessions.list_sessions()
        assert context.data_root.exists()

    asyncio.run(run())


@pytest.mark.skip(reason="候选重扫已收缩为 Transport 内直接切换")
def test_rebind_commit_callback_runs_only_inside_idle_reservation(tmp_path: Path) -> None:
    async def run() -> None:
        host = make_host(tmp_path)
        binding = active_weixin(host, "owner")
        scope = host._scope_for_account(binding)
        called: list[str] = []

        async def try_commit(binding_id: str) -> bool:
            assert any(item == scope for item in host.turn_coordinator._reserved_scopes.values())
            called.append(binding_id)
            return True

        host.weixin_transport.try_commit_rebind = try_commit
        assert await host.request_weixin_rebind_commit(binding.id) is True
        assert called == [binding.id]
        assert host.turn_coordinator._reserved_scopes == {}

        route = ChannelRoute("owner", "weixin", binding.id, "busy-rebind")
        host.im_coordinator._controls[route.session_id] = im_module.ImTurnControl("busy", route)
        assert await host.request_weixin_rebind_commit(binding.id) is False
        assert called == [binding.id]

    asyncio.run(run())


@pytest.mark.skip(reason="候选重扫不再经 Host 生命周期预约")
def test_candidate_loop_commits_through_real_host_reservation(tmp_path: Path) -> None:
    class CandidateClient:
        async def begin_login(self, _binding):
            return weixin_ilink_module.IlinkLoginResult(
                "pending_qr",
                {"qrcode": "candidate-challenge"},
                qr_content="candidate-qr",
            )

        async def continue_login(self, _binding):
            return weixin_ilink_module.IlinkLoginResult(
                "awaiting_first_dm",
                {
                    "bot_token": "candidate-token",
                    "ilink_bot_id": "candidate-bot",
                },
            )

        async def poll(self, _binding):
            raise AssertionError("poll is not expected")

        async def send_text(self, _binding, _content):
            return True

    async def run() -> None:
        host = make_host(tmp_path)
        binding = active_weixin(host, "owner")
        host.weixin_transport._client = CandidateClient()
        host.weixin_transport._qr_presenter = None
        host.weixin_transport.poll_interval_seconds = 0.01

        await host.weixin_transport.start_rebind(binding.id)
        for _ in range(100):
            current = host.channel_registry.get("weixin", binding.id)
            if current is not None and current.status == "awaiting_first_dm":
                break
            await asyncio.sleep(0.01)

        current = host.channel_registry.get("weixin", binding.id)
        assert current is not None
        assert current.status == "awaiting_first_dm"
        assert current.private.get("bot_token") == "candidate-token"
        assert current.private.get("from_user_id") is None
        assert host.weixin_transport.get_rebind_state(binding.id) is None
        assert host.turn_coordinator._reserved_scopes == {}
        await host.weixin_transport.close()

    asyncio.run(run())


@pytest.mark.skip(reason="候选重扫不再等待空闲重试")
def test_busy_candidate_retry_does_not_reserve_or_drop_old_user_message(
    tmp_path: Path,
) -> None:
    class BusyCandidateClient:
        def __init__(self) -> None:
            self.events: list[dict[str, object]] = []

        async def begin_login(self, _binding):
            return weixin_ilink_module.IlinkLoginResult(
                "pending_qr",
                {"qrcode": "candidate-challenge"},
                qr_content="candidate-qr",
            )

        async def continue_login(self, _binding):
            return weixin_ilink_module.IlinkLoginResult(
                "awaiting_first_dm",
                {
                    "bot_token": "candidate-token",
                    "ilink_bot_id": "candidate-bot",
                },
            )

        async def poll(self, _binding):
            event = self.events.pop(0) if self.events else None
            return weixin_ilink_module.IlinkPollResult(
                (event,) if event is not None else (),
                {"cursor": "old-cursor-after"},
            )

        async def send_text(self, _binding, _content):
            return True

    async def run() -> None:
        host = make_host(tmp_path)
        binding = active_weixin(host, "owner")
        client = BusyCandidateClient()
        transport = host.weixin_transport
        transport._client = client
        transport._qr_presenter = None
        transport.poll_interval_seconds = 0.01
        retry_seen = asyncio.Event()
        request_commit = host.request_weixin_rebind_commit

        async def observe_busy_retry(binding_id: str) -> bool:
            result = await request_commit(binding_id)
            if not result:
                retry_seen.set()
            return result

        transport._request_rebind_commit = observe_busy_retry
        busy_route = ChannelRoute(
            binding.principal_id,
            "weixin",
            binding.id,
            "busy-session",
        )
        route_key = host.turn_coordinator._route_key(busy_route)
        host.turn_coordinator._active_routes[route_key] = "busy-request"
        host.im_coordinator._controls[busy_route.session_id] = im_module.ImTurnControl(
            "busy-request",
            busy_route,
        )

        await transport.start_rebind(binding.id)
        await retry_seen.wait()
        transport.poll_interval_seconds = 60
        await asyncio.sleep(0.02)
        assert host.turn_coordinator._reserved_scopes == {}

        client.events.append(
            {
                "message_id": "old-during-busy",
                "from_user_id": binding.private["from_user_id"],
                "message_type": "text",
                "item_list": [{"type": "text", "text": "old message survives"}],
                "context_token": "old-context-next",
            }
        )
        assert await transport.poll_once(binding.id) == 1
        pending = host.turn_coordinator._pending_routes.get(route_key)
        assert pending is not None
        assert [message.content for message, _dispatch in pending] == [
            "old message survives"
        ]
        current = host.channel_registry.get("weixin", binding.id)
        assert current is not None
        assert f"weixin:{binding.id}:old-during-busy" in current.private["inbound_ids"]
        assert host.turn_coordinator._reserved_scopes == {}
        await transport.close()

    asyncio.run(run())


@pytest.mark.skip(reason="删除仅保留路由空闲门，不再冻结 Transport")
def test_scheduler_cannot_start_across_transport_quiesce_and_delete_commit(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        host = make_host(tmp_path)
        binding = active_weixin(host, None)
        route = ChannelRoute(binding.principal_id, "weixin", binding.id, "guest-session")
        context = host.principal_resolver.resolve(route)
        await context.sessions.load_or_create(route)
        workspace = host.proactive_service.workspace_for(binding.principal_id)
        root = context.data_root
        quiesce_entered = asyncio.Event()
        release_quiesce = asyncio.Event()
        scheduler_entered = asyncio.Event()

        async def suspended_disable(_account) -> None:
            quiesce_entered.set()
            await release_quiesce.wait()

        async def observable_run_due(_now: datetime) -> int:
            scheduler_entered.set()
            return 0

        host._disable_transport = suspended_disable
        workspace.cron.run_due = observable_run_due
        deletion = asyncio.create_task(
            host.delete_channel_binding("weixin", binding.id)
        )
        await quiesce_entered.wait()
        scheduling = asyncio.create_task(
            host.proactive_service._run_workspace_due(workspace, datetime.now(UTC))
        )
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(scheduler_entered.wait(), timeout=0.05)
            assert host.channel_registry.get("weixin", binding.id) is not None
            assert await context.sessions.list_sessions()
            assert root.exists()
        finally:
            release_quiesce.set()

        await deletion
        await scheduling

        assert not scheduler_entered.is_set()
        assert host.channel_registry.get("weixin", binding.id) is None
        assert not root.exists()

    asyncio.run(run())


@pytest.mark.skip(reason="删除不再使用 Transport quiesce 协议")
def test_transport_quiesce_failure_releases_retirement_without_durable_mutation(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        host = make_host(tmp_path)
        binding = active_weixin(host, None)
        route = ChannelRoute(binding.principal_id, "weixin", binding.id, "guest-session")
        context = host.principal_resolver.resolve(route)
        await context.sessions.load_or_create(route)
        workspace = host.proactive_service.workspace_for(binding.principal_id)
        root = context.data_root

        async def failing_disable(_account) -> None:
            raise RuntimeError("transport quiesce failed")

        host._disable_transport = failing_disable

        with pytest.raises(RuntimeError, match="transport quiesce failed"):
            await host.delete_channel_binding("weixin", binding.id)

        assert host.channel_registry.get("weixin", binding.id) is not None
        assert await context.sessions.list_sessions()
        assert root.exists()
        assert binding.principal_id in host.proactive_service._workspaces
        assert binding.principal_id not in host.proactive_service._retired_principal_ids

        entered = asyncio.Event()

        async def observable_run_due(_now: datetime) -> int:
            entered.set()
            return 0

        workspace.cron.run_due = observable_run_due
        await host.proactive_service._run_workspace_due(workspace, datetime.now(UTC))
        assert entered.is_set()

    asyncio.run(run())


@pytest.mark.skip(reason="独立主体删除使用直接退休检查")
def test_busy_principal_prepare_rejects_before_transport_or_durable_mutation(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        host = make_host(tmp_path)
        binding = active_weixin(host, None)
        route = ChannelRoute(binding.principal_id, "weixin", binding.id, "guest-session")
        context = host.principal_resolver.resolve(route)
        await context.sessions.load_or_create(route)
        host.proactive_service.workspace_for(binding.principal_id)
        release_work = asyncio.Event()

        async def active_work() -> None:
            await release_work.wait()

        work = asyncio.create_task(active_work())
        host.proactive_service._breakbeat_tasks[binding.principal_id] = work
        disabled: list[str] = []

        async def record_disable(account) -> None:
            disabled.append(account.id)

        host._disable_transport = record_disable
        try:
            with pytest.raises(ChannelConflictError) as exc_info:
                await host.delete_channel_binding("weixin", binding.id)

            assert exc_info.value.code == "binding_busy"
            assert disabled == []
            assert host.channel_registry.get("weixin", binding.id) is not None
            assert await context.sessions.list_sessions()
            assert context.data_root.exists()
            assert binding.principal_id not in host.proactive_service._retired_principal_ids
        finally:
            release_work.set()
            await work
            host.proactive_service._breakbeat_tasks.pop(binding.principal_id, None)

    asyncio.run(run())


@pytest.mark.skip(reason="运行时重载不再与 Binding 删除共享生命周期锁")
def test_delete_blocks_inflight_reload_activation_until_registry_unlink(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        settings = settings_module.Settings(data_dir=tmp_path / "state")
        settings.gateway.principal_id = "owner"
        settings.proactive.enabled = True
        settings.local_config_path = tmp_path / "settings.local.json"
        initial = runtime_module.AgentRuntime.create_default(settings, object())
        factory_calls = 0

        async def runtime_factory():
            nonlocal factory_calls
            factory_calls += 1
            return runtime_module.AgentRuntime.create_default(settings, object())

        host = GatewayHost(
            settings,
            runtime=initial,
            runtime_factory=runtime_factory,
        )
        binding = active_weixin(host, None)
        route = ChannelRoute(binding.principal_id, "weixin", binding.id, "guest-session")
        context = host.principal_resolver.resolve(route)
        await context.sessions.load_or_create(route)
        host.proactive_service.workspace_for(binding.principal_id)
        initial_service = host.proactive_service
        prepare_entered = asyncio.Event()
        release_prepare = asyncio.Event()
        prepare_calls = 0
        activate_calls = 0
        original_prepare = host.runtime_supervisor._on_prepare
        original_activate = host.runtime_supervisor._on_activate

        async def paused_prepare(replacement) -> None:
            nonlocal prepare_calls
            prepare_calls += 1
            assert original_prepare is not None
            await original_prepare(replacement)
            if prepare_calls == 1:
                prepare_entered.set()
                await release_prepare.wait()

        async def observed_activate(replacement) -> None:
            nonlocal activate_calls
            activate_calls += 1
            assert original_activate is not None
            await original_activate(replacement)

        host.runtime_supervisor._on_prepare = paused_prepare
        host.runtime_supervisor._on_activate = observed_activate
        transport_paused = asyncio.Event()
        release_transport = asyncio.Event()

        async def paused_disable(_account) -> None:
            transport_paused.set()
            await release_transport.wait()

        host._disable_transport = paused_disable
        reload_task = asyncio.create_task(host.request_runtime_reload("revision-next"))
        await prepare_entered.wait()
        deletion = asyncio.create_task(
            host.delete_channel_binding("weixin", binding.id)
        )
        for _ in range(100):
            if host.runtime_supervisor.lifecycle_busy:
                break
            await asyncio.sleep(0)
        assert host.runtime_supervisor.lifecycle_busy
        assert not transport_paused.is_set()
        release_prepare.set()
        await transport_paused.wait()
        assert not host._is_globally_idle()

        first_status = await reload_task
        assert first_status.state == "pending"
        assert activate_calls == 0
        assert host.proactive_service is initial_service
        assert host.channel_registry.get("weixin", binding.id) is not None

        release_transport.set()
        await deletion

        assert factory_calls == 2
        assert activate_calls == 1
        assert host.runtime_supervisor.status().state == "active"
        assert host.channel_registry.get("weixin", binding.id) is None
        assert binding.principal_id not in host.proactive_service._workspaces
        assert not context.data_root.exists()

    asyncio.run(run())


@pytest.mark.skip(reason="已删除的全局 Binding 生命周期协议")
def test_delete_and_enable_cannot_retain_a_new_weixin_client(tmp_path: Path) -> None:
    class PausedCloseClient:
        def __init__(self) -> None:
            self.close_entered = asyncio.Event()
            self.release_close = asyncio.Event()

        async def close(self) -> None:
            self.close_entered.set()
            await self.release_close.wait()

    async def run() -> None:
        settings = settings_module.Settings(data_dir=tmp_path / "state")
        settings.gateway.principal_id = "owner"
        settings.proactive.enabled = False
        settings.local_config_path = tmp_path / "settings.local.json"
        runtime = runtime_module.AgentRuntime.create_default(settings, object())
        clients: list[PausedCloseClient] = []

        def client_factory(_account):
            client = PausedCloseClient()
            clients.append(client)
            return client

        host = GatewayHost(settings, runtime=runtime, weixin_client=client_factory)
        binding = active_weixin(host, None)
        await host.weixin_transport._prepare_binding(binding)
        assert len(clients) == 1

        deletion = asyncio.create_task(
            host.delete_channel_binding("weixin", binding.id)
        )
        await clients[0].close_entered.wait()
        assert host.channel_registry.get("weixin", binding.id) is None

        enable = asyncio.create_task(host.weixin_transport.enable_account(binding.id))
        await asyncio.sleep(0)
        assert not enable.done()

        clients[0].release_close.set()
        await deletion
        with pytest.raises(ValueError, match="微信 Binding 不存在"):
            await enable

        assert len(clients) == 1
        assert host.weixin_transport._clients == {}

    asyncio.run(run())


@pytest.mark.parametrize("platform", ["weixin", "feishu"])
@pytest.mark.skip(reason="已删除的全局 Binding 生命周期协议")
def test_control_enable_waits_for_permanent_delete_lifecycle_guard(
    tmp_path: Path,
    platform: str,
) -> None:
    async def run() -> None:
        host = make_host(tmp_path)
        if platform == "weixin":
            binding = active_weixin(host, None)
        else:
            binding = host.channel_registry.register_feishu_bot(
                app_id="app-delete-race",
                app_secret="secret",
                domain="open.feishu.cn",
            )
        service = control_module.ChannelControlService(
            host.channel_registry,
            weixin_transport=host.weixin_transport,
            feishu_transport=host.feishu_transport,
            delete_binding=host.delete_channel_binding,
            binding_lifecycle_guard=host.channel_binding_lifecycle,
            owner_principal_id="owner",
        )
        stop_entered = asyncio.Event()
        release_stop = asyncio.Event()
        order: list[str] = []
        enable_calls: list[str] = []

        async def paused_stop(_account) -> None:
            order.append("transport-stop-entered")
            stop_entered.set()
            await release_stop.wait()
            order.append("transport-stopped")

        async def record_enable(account_id: str) -> None:
            enable_calls.append(account_id)

        host._disable_transport = paused_stop
        transport = (
            host.weixin_transport if platform == "weixin" else host.feishu_transport
        )
        transport.enable_account = record_enable
        registry_delete = host.channel_registry.delete

        def ordered_delete(item_platform, account_id):
            assert order == ["transport-stop-entered", "transport-stopped"]
            order.append("registry-unlinked")
            return registry_delete(item_platform, account_id)

        host.channel_registry.delete = ordered_delete
        deletion = asyncio.create_task(
            host.delete_channel_binding(platform, binding.id)
        )
        await stop_entered.wait()
        enabling = asyncio.create_task(service.enable(platform, binding.id))
        await asyncio.sleep(0)
        assert not enabling.done()
        assert enable_calls == []

        release_stop.set()
        await deletion
        with pytest.raises(KeyError, match="账号不存在"):
            await enabling

        assert order == [
            "transport-stop-entered",
            "transport-stopped",
            "registry-unlinked",
        ]
        assert enable_calls == []
        assert host.channel_registry.get(platform, binding.id) is None

    asyncio.run(run())

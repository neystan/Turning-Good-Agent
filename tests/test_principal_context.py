from __future__ import annotations

import asyncio
import hashlib
import importlib
from pathlib import Path

import pytest

messages_module = importlib.import_module("Turning-Good-Agent.bus.messages")
settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
manager_module = importlib.import_module("Turning-Good-Agent.sessions.manager")
store_module = importlib.import_module("Turning-Good-Agent.sessions.store")
principals_module = importlib.import_module("Turning-Good-Agent.gateway.principals")
host_module = importlib.import_module("Turning-Good-Agent.gateway.host")
context_module = importlib.import_module("Turning-Good-Agent.runtime.principal_context")
runtime_module = importlib.import_module("Turning-Good-Agent.runtime.runtime")
turn_context_module = importlib.import_module("Turning-Good-Agent.runtime.turn_context")
memory_module = importlib.import_module("Turning-Good-Agent.memory.long_term")
llm_types_module = importlib.import_module("Turning-Good-Agent.llm.types")

ChannelRoute = messages_module.ChannelRoute
InboundMessage = messages_module.InboundMessage
Settings = settings_module.Settings
GatewaySettings = settings_module.GatewaySettings
SessionManager = manager_module.SessionManager
JsonlSessionStore = store_module.JsonlSessionStore
GatewayPrincipalResolver = principals_module.GatewayPrincipalResolver
GatewayHost = host_module.GatewayHost
PrincipalSessionReader = context_module.PrincipalSessionReader
AgentRuntime = runtime_module.AgentRuntime
ProfileMemorySnapshot = memory_module.ProfileMemorySnapshot
LLMResponse = llm_types_module.LLMResponse
LLMUsage = llm_types_module.LLMUsage


def make_resolver(tmp_path: Path) -> GatewayPrincipalResolver:
    settings = Settings(
        data_dir=tmp_path,
        gateway=GatewaySettings(principal_id="owner"),
    )
    return GatewayPrincipalResolver(settings)


def route(principal_id: str, conversation_id: str = "conversation") -> object:
    return ChannelRoute(
        principal_id,
        "weixin",
        conversation_id,
        f"session-{principal_id}-{conversation_id}",
    )


def test_owner_context_reuses_existing_data_root(tmp_path: Path) -> None:
    context = make_resolver(tmp_path).resolve(route("owner"))

    assert context.data_root == tmp_path
    assert context.profile_memory.root == tmp_path / "memory"


def test_non_owner_context_uses_opaque_hashed_root(tmp_path: Path) -> None:
    principal_id = "guest-principal"
    context = make_resolver(tmp_path).resolve(route(principal_id))
    digest = hashlib.sha256(
        b"tga-principal-v1\0" + principal_id.encode("utf-8")
    ).hexdigest()

    assert context.data_root == tmp_path / "principals" / digest
    assert context.profile_memory.root == context.data_root / "memory"
    assert principal_id not in context.data_root.name


def test_non_owner_session_reader_rejects_foreign_session(tmp_path: Path) -> None:
    async def run() -> None:
        settings = Settings(
            data_dir=tmp_path,
            gateway=GatewaySettings(principal_id="owner"),
        )
        owner_manager = SessionManager(JsonlSessionStore(tmp_path), settings)
        owner_route = route("owner", "owner-conversation")
        await owner_manager.load_or_create(owner_route)
        context = GatewayPrincipalResolver(
            settings,
            owner_sessions=owner_manager,
        ).resolve(route("guest", "guest-conversation"))

        with pytest.raises(PermissionError):
            await context.sessions.all_messages(owner_route.session_id)

    asyncio.run(run())


def test_session_reader_filters_by_principal(tmp_path: Path) -> None:
    async def run() -> None:
        context = make_resolver(tmp_path).resolve(route("guest"))
        await context.sessions.load_or_create(route("guest", "guest-conversation"))
        await context.sessions.load_or_create(route("guest", "second-conversation"))

        sessions = await context.sessions.list_sessions()

        assert {session.principal_id for session in sessions} == {"guest"}
        assert {session.conversation_id for session in sessions} == {
            "guest-conversation",
            "second-conversation",
        }

    asyncio.run(run())


def test_principal_session_reader_rejects_mismatched_route(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, gateway=GatewaySettings(principal_id="owner"))
    manager = SessionManager(JsonlSessionStore(tmp_path), settings)
    reader = PrincipalSessionReader(manager, "guest")

    with pytest.raises(PermissionError):
        reader._validate_route(route("owner"))


def test_non_owner_reader_cannot_access_the_backing_manager_or_store(tmp_path: Path) -> None:
    reader = make_resolver(tmp_path).resolve(route("guest")).sessions

    with pytest.raises(AttributeError):
        getattr(reader, "manager")
    with pytest.raises(AttributeError):
        getattr(reader, "store")


def test_non_owner_reader_rejects_global_approve_command(tmp_path: Path) -> None:
    async def run() -> None:
        resolver = make_resolver(tmp_path)
        reader = resolver.resolve(route("guest")).sessions
        message = InboundMessage.new("/approve on", route("guest"))

        with pytest.raises(PermissionError, match="全局工具自动审批"):
            await reader.handle_inbound_command(message.session_id, message)

        assert resolver.settings.tool_permissions.auto_approve_tools is False

    asyncio.run(run())


def test_principal_storage_bundle_is_reused_when_route_allowlist_changes(tmp_path: Path) -> None:
    class RoutePolicy:
        def allowed_tool_names(self, route: ChannelRoute, tools: object, settings: object) -> frozenset[str]:
            del tools, settings
            return frozenset({route.channel})

    resolver = GatewayPrincipalResolver(
        Settings(data_dir=tmp_path, gateway=GatewaySettings(principal_id="owner")),
        policy=RoutePolicy(),
    )
    first = resolver.resolve(route("guest", "weixin-conversation"))
    second = resolver.resolve(
        ChannelRoute("guest", "telegram", "telegram-conversation", "guest-telegram")
    )

    assert first.allowed_tool_names == frozenset({"weixin"})
    assert second.allowed_tool_names == frozenset({"telegram"})
    assert first.data_root == second.data_root
    assert first.profile_memory is second.profile_memory
    assert first.sessions is second.sessions
    assert first.sessions.locks is second.sessions.locks


def test_forget_non_owner_removes_only_target_bundle_and_preserves_owner(tmp_path: Path) -> None:
    resolver = make_resolver(tmp_path)
    owner = resolver.resolve(route("owner"))
    guest = resolver.resolve(route("guest"))
    other = resolver.resolve(route("other"))

    root = resolver.forget_non_owner("guest")

    assert root == guest.data_root
    assert "guest" not in resolver._bundles
    assert resolver.resolve(route("owner")).sessions is owner.sessions
    assert resolver.resolve(route("other")).sessions is other.sessions


def test_forget_non_owner_rejects_owner(tmp_path: Path) -> None:
    resolver = make_resolver(tmp_path)
    owner = resolver.resolve(route("owner"))

    with pytest.raises(ValueError, match="Owner"):
        resolver.forget_non_owner("owner")

    assert resolver.resolve(route("owner")).sessions is owner.sessions


def test_host_installs_resolver_for_supplied_and_replacement_runtimes(tmp_path: Path) -> None:
    settings = Settings(data_dir=tmp_path, gateway=GatewaySettings(principal_id="owner"))
    settings.proactive.enabled = False
    initial = AgentRuntime.create_default(settings, object())
    initial._principal_resolver = None
    host = GatewayHost(settings, runtime=initial)
    replacement = AgentRuntime.create_default(settings, object())
    replacement._principal_resolver = None

    assert host.current_runtime.resolve_principal_context(route("guest")).data_root != tmp_path

    async def run() -> None:
        await host._prepare_runtime(replacement)

    asyncio.run(run())

    assert replacement.resolve_principal_context(route("guest")).data_root != tmp_path


def test_runtime_turn_uses_the_resolved_profile_and_session_root(tmp_path: Path) -> None:
    class RecordingLlm:
        def __init__(self) -> None:
            self.requests: list[list[dict[str, object]]] = []

        async def complete(
            self, messages: list[dict[str, object]], tools: list[dict[str, object]]
        ) -> object:
            del tools
            self.requests.append(messages)
            return LLMResponse(
                "done", usage=LLMUsage(input_tokens=1, output_tokens=1, total_tokens=2)
            )

    async def run() -> None:
        settings = Settings(
            data_dir=tmp_path,
            gateway=GatewaySettings(principal_id="owner"),
        )
        llm = RecordingLlm()
        runtime = AgentRuntime.create_default(settings, llm)
        owner_route = route("owner", "owner-conversation")
        guest_route = route("guest", "guest-conversation")
        owner_context = runtime.resolve_principal_context(owner_route)
        guest_context = runtime.resolve_principal_context(guest_route)
        owner_context.profile_memory.write(ProfileMemorySnapshot(user="owner-only"))
        guest_context.profile_memory.write(ProfileMemorySnapshot(user="guest-only"))

        await runtime.run_turn(
            InboundMessage("owner-event", route=owner_route, content="hello owner")
        )
        await runtime.run_turn(
            InboundMessage("guest-event", route=guest_route, content="hello guest")
        )

        owner_prompt = "\n".join(str(item["content"]) for item in llm.requests[0])
        guest_prompt = "\n".join(str(item["content"]) for item in llm.requests[1])
        assert "owner-only" in owner_prompt
        assert "guest-only" not in owner_prompt
        assert "guest-only" in guest_prompt
        assert "owner-only" not in guest_prompt
        assert len(await owner_context.sessions.list_sessions()) == 1
        assert len(await guest_context.sessions.list_sessions()) == 1
        assert turn_context_module.current_principal_context() is None

    asyncio.run(run())


def test_runtime_resolver_failure_resets_all_turn_contextvars() -> None:
    class FailingResolver:
        def resolve(self, route: object) -> object:
            del route
            raise RuntimeError("resolver failed")

    async def run() -> None:
        runtime = AgentRuntime.__new__(AgentRuntime)
        runtime._active_turns = 0
        runtime._activity_lock = asyncio.Lock()
        runtime._principal_resolver = FailingResolver()
        message = InboundMessage("event", route=route("guest"), content="hello")

        with pytest.raises(RuntimeError, match="resolver failed"):
            await runtime.run_turn(message)

        assert runtime.is_globally_idle() is True
        assert turn_context_module.current_route() is None
        assert turn_context_module.current_inbound() is None
        assert turn_context_module.current_principal_context() is None

    asyncio.run(run())

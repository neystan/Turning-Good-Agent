from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ...channels.base import ChannelCapabilities
from ...gateway.auth import is_authorized_bearer
from ...llm.factory import build_llm
from ...sessions.types import MessageRecord, Session, ToolCallRecord
from .config_control import ApprovalToolChanges, ConfigApplyRequest, ConfigValidationError, WebConfigControlService
from .http_errors import config_validation_response
from .read_models import (
    build_command_catalog,
    build_context_read_model,
    build_mcp_server_detail,
    build_mcp_server_list,
    build_tool_catalog,
    page_tool_calls,
)
from .proactive_control import (
    ProactiveConflictError,
    ProactiveNotFoundError,
)
from .proactive_events import snapshot_payload

if TYPE_CHECKING:
    from ...gateway.host import GatewayHost


WEB_CHANNEL_CAPABILITIES = ChannelCapabilities(supports_guidance=True)


def _require_boolean(action: dict[str, Any], name: str) -> bool:
    """只接受 JSON 原生 boolean，避免字符串或数字发生真值转换。"""
    value = action.get(name)
    if not isinstance(value, bool):
        raise ValueError(f"{name} 必须是布尔值")
    return value


def create_app(gateway: "GatewayHost") -> FastAPI:
    """将 Web API 挂载到唯一 Gateway；Web 不拥有 Runtime 或主动生命周期。"""
    settings = gateway.settings
    coordinator = gateway.web_coordinator
    supervisor = gateway.runtime_supervisor
    config_control = gateway.config_control
    proactive_control = gateway.proactive_control
    proactive_events = gateway.proactive_events
    proactive_owner_state = gateway.proactive_owner_state

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        """Web 仅委托 Gateway 的统一生命周期。"""
        await gateway.start()
        try:
            yield
        finally:
            await gateway.close()

    app = FastAPI(title="Turning Good Agent", lifespan=lifespan)
    app.state.gateway = gateway
    app.state.coordinator = coordinator
    app.state.runtime_supervisor = supervisor
    app.state.config_control = config_control
    app.state.proactive_control = proactive_control
    app.state.proactive_events = proactive_events
    app.state.proactive_owner_state = proactive_owner_state

    def proactive_error(exc: Exception) -> HTTPException:
        if isinstance(exc, ProactiveNotFoundError):
            return HTTPException(404, str(exc))
        if isinstance(exc, ProactiveConflictError):
            return HTTPException(409, str(exc))
        return HTTPException(422, str(exc))

    async def web_session_or_404(session_id: str) -> Session:
        """仅允许 Web 控制面读取或修改其自身 Channel 的聊天会话。"""
        session = await supervisor.current_runtime.sessions.store.load_session(session_id)
        if session is None or (session.principal_id, session.channel) != (
            gateway.settings.gateway.principal_id,
            "web",
        ):
            raise HTTPException(404, "会话不存在")
        return session

    def proactive_snapshot(domain: str) -> dict[str, object]:
        try:
            return snapshot_payload(
                proactive_control.snapshot_domain(domain),
                proactive_owner_state,
            )
        except ValueError as exc:
            raise proactive_error(exc) from exc

    @app.get("/api/proactive")
    async def get_proactive_snapshot() -> dict[str, object]:
        return {
            "snapshots": proactive_events.initial_snapshots(),
            "owner": proactive_owner_state.to_dict(),
            "proactive_revision": proactive_control.proactive_revision,
            "mutations_allowed": True,
        }

    @app.get("/api/proactive/cron")
    async def get_proactive_cron() -> dict[str, object]:
        return proactive_snapshot("cron")

    @app.get("/api/proactive/breakbeat")
    async def get_proactive_breakbeat() -> dict[str, object]:
        return proactive_snapshot("breakbeat")

    @app.get("/api/proactive/memory")
    async def get_proactive_memory() -> dict[str, object]:
        return proactive_snapshot("dream")

    @app.get("/api/proactive/skills")
    async def get_proactive_skills() -> dict[str, object]:
        return proactive_snapshot("skill")

    @app.get("/api/proactive/incidents")
    async def get_proactive_incidents() -> dict[str, object]:
        return proactive_snapshot("incident")

    async def proactive_action(domain: str, action: Any, *args: str) -> dict[str, object]:
        try:
            await action(*args)
            return await proactive_events.publish_snapshot(domain, changed=False)  # type: ignore[arg-type]
        except (ProactiveNotFoundError, ProactiveConflictError, ValueError) as exc:
            raise proactive_error(exc) from exc

    @app.delete("/api/proactive/cron/{job_id}")
    async def delete_proactive_cron(job_id: str) -> dict[str, object]:
        return await proactive_action("cron", proactive_control.delete_cron, job_id)

    @app.post("/api/proactive/breakbeat/{item_id}/complete")
    async def complete_proactive_breakbeat(item_id: str) -> dict[str, object]:
        return await proactive_action("breakbeat", proactive_control.complete_breakbeat, item_id)

    @app.delete("/api/proactive/breakbeat/{item_id}")
    async def delete_proactive_breakbeat(item_id: str) -> dict[str, object]:
        return await proactive_action("breakbeat", proactive_control.delete_breakbeat, item_id)

    @app.delete("/api/proactive/skills/drafts/{name}")
    async def delete_proactive_draft(name: str) -> dict[str, object]:
        return await proactive_action("skill", proactive_control.delete_draft, name)

    @app.post("/api/proactive/incidents/{fingerprint}/resolve")
    async def resolve_proactive_incident(fingerprint: str) -> dict[str, object]:
        return await proactive_action("incident", proactive_control.resolve_incident, fingerprint)

    @app.delete("/api/proactive/incidents/{fingerprint}")
    async def delete_proactive_incident(fingerprint: str) -> dict[str, object]:
        return await proactive_action("incident", proactive_control.delete_incident, fingerprint)

    def config_view() -> dict[str, object]:
        desired = config_control.read_desired()
        status = supervisor.status()
        return {
            "desired_revision": desired.revision,
            "active_revision": status.active_revision,
            "state": status.state,
            "last_apply_error": status.last_apply_error,
            "desired": desired.desired,
            "active": WebConfigControlService.redact_settings(supervisor.current_runtime.settings),
        }

    @app.get("/api/control/config")
    async def get_control_config() -> dict[str, object]:
        return config_view()

    @app.get("/api/settings/ui")
    async def get_ui_settings() -> dict[str, bool]:
        """返回当前 Runtime 立即生效的全局审批开关。"""
        return {"auto_approve_tools": supervisor.current_runtime.settings.tool_permissions.auto_approve_tools}

    @app.patch("/api/settings/ui")
    async def patch_ui_settings(payload: dict[str, Any]) -> dict[str, bool]:
        """立即更新全局自动审批，独立于空闲期的配置应用。"""
        enabled = payload.get("auto_approve_tools")
        if not isinstance(enabled, bool):
            raise HTTPException(422, "auto_approve_tools 必须是 boolean")
        active_settings = supervisor.current_runtime.settings
        active_settings.update_auto_approve_tools(enabled)
        return {"auto_approve_tools": enabled}

    @app.post("/api/control/config/apply")
    async def apply_control_config(payload: dict[str, Any]) -> dict[str, object]:
        try:
            raw_approval = payload.get("approval_required_tools", {})
            if not isinstance(raw_approval, dict):
                raise ConfigValidationError({"approval_required_tools": "必须是 object"})
            request = ConfigApplyRequest(
                changes=payload.get("changes", {}),
                approval_required_tools=ApprovalToolChanges(
                    add=list(raw_approval.get("add", [])), remove=list(raw_approval.get("remove", []))
                ),
            )
            desired = config_control.apply(request)
            await gateway.request_runtime_reload(desired.revision)
            return config_view()
        except ConfigValidationError as exc:
            return config_validation_response(exc.field_errors)

    @app.post("/api/control/config/test-llm")
    async def test_control_llm(payload: dict[str, Any]) -> dict[str, object]:
        try:
            candidate = config_control.candidate_llm(payload.get("changes", payload))
            started = time.perf_counter()
            await build_llm(candidate).complete([{"role": "user", "content": "ping"}], [])
            return {"ok": True, "latency_ms": round((time.perf_counter() - started) * 1000)}
        except ConfigValidationError as exc:
            return config_validation_response(exc.field_errors)
        except Exception as exc:
            raise HTTPException(502, f"LLM 连通性测试失败：{str(exc)[:200]}") from exc

    @app.get("/api/control/commands")
    async def get_command_catalog() -> dict[str, object]:
        return build_command_catalog(supervisor.current_runtime)

    @app.get("/api/control/tools")
    async def get_tool_catalog() -> dict[str, object]:
        return build_tool_catalog(supervisor.current_runtime, supervisor.status().active_revision)

    @app.get("/api/control/sessions/{session_id}/context")
    async def get_control_context(session_id: str) -> dict[str, object]:
        await web_session_or_404(session_id)
        model = await build_context_read_model(supervisor.current_runtime, session_id, supervisor.status().active_revision)
        if model is None:
            raise HTTPException(404, "会话不存在")
        return model

    @app.get("/api/control/sessions/{session_id}/tool-calls")
    async def get_control_tool_calls(session_id: str, limit: int = 50, cursor: str | None = None) -> dict[str, object]:
        await web_session_or_404(session_id)
        try:
            model = await page_tool_calls(supervisor.current_runtime, session_id, limit, cursor)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        if model is None:
            raise HTTPException(404, "会话不存在")
        return model

    @app.get("/api/control/mcp/servers")
    async def get_control_mcp_servers() -> dict[str, object]:
        return build_mcp_server_list(supervisor.current_runtime)

    @app.get("/api/control/mcp/servers/{name}")
    async def get_control_mcp_server(name: str) -> dict[str, object]:
        model = build_mcp_server_detail(supervisor.current_runtime, name)
        if model is None:
            raise HTTPException(404, "MCP Server 不存在")
        return model

    @app.get("/api/sessions")
    async def list_sessions(archived: bool = False) -> list[dict[str, Any]]:
        """返回一个会话分组的轻量列表。"""
        sessions = await supervisor.current_runtime.sessions.list_sessions(
            archived,
            channel="web",
            principal_id=gateway.settings.gateway.principal_id,
        )
        return [_session_dict(item) for item in sessions]

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        """返回单个会话元数据。"""
        return _session_dict(await web_session_or_404(session_id))

    @app.get("/api/sessions/{session_id}/messages")
    async def get_messages(session_id: str) -> list[dict[str, Any]]:
        """返回已持久化的原始对话消息。"""
        await web_session_or_404(session_id)
        return [_message_dict(item) for item in await supervisor.current_runtime.sessions.all_messages(session_id)]

    @app.get("/api/sessions/{session_id}/observability")
    async def get_observability(session_id: str) -> dict[str, Any]:
        """聚合既有 JSON/JSONL 的会话观测数据。"""
        session = await web_session_or_404(session_id)
        return {
            "session": _session_dict(session),
            "traces": await supervisor.current_runtime.sessions.store.all_turn_traces(session_id),
            "token_usage": await supervisor.current_runtime.sessions.store.all_true_token_usage(session_id),
            "tool_calls": [
                _tool_call_dict(item) for item in await supervisor.current_runtime.sessions.store.all_tool_calls(session_id)
            ],
        }

    @app.get("/api/sessions/{session_id}/context-window")
    async def get_context_window(session_id: str) -> dict[str, int]:
        """返回 Composer 所需的轻量上下文窗口读数。"""
        await web_session_or_404(session_id)
        traces = await supervisor.current_runtime.sessions.store.all_turn_traces(session_id)
        return _context_window_dict(traces, supervisor.current_runtime.settings.runtime.max_context_tokens)

    @app.patch("/api/sessions/{session_id}")
    async def patch_session(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """更新标题、置顶或归档状态。"""
        await web_session_or_404(session_id)
        if bool(payload.get("archived")) and coordinator.is_active(session_id):
            raise HTTPException(409, "运行中、等待审批或停止中的会话不能归档")
        values: dict[str, Any] = {}
        if "title" in payload:
            title = str(payload["title"]).strip()
            if not title:
                raise HTTPException(422, "标题不能为空")
            values["title"] = title[:120]
        for key in ("pinned", "archived"):
            if key in payload:
                values[key] = bool(payload[key])
        updated = await supervisor.current_runtime.sessions.update_session_metadata(session_id, **values)
        assert updated is not None
        await coordinator.hub.publish(session_id, "", "session.updated", _session_dict(updated))
        return _session_dict(updated)

    @app.delete("/api/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: str) -> None:
        """删除一个非活动会话的完整目录。"""
        if coordinator.is_active(session_id):
            raise HTTPException(409, "运行中、等待审批或停止中的会话不能删除")
        await web_session_or_404(session_id)
        await supervisor.current_runtime.sessions.store.clear_session(session_id)

    @app.websocket("/ws/proactive")
    async def proactive_websocket(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = await proactive_events.subscribe()
        forwarder: asyncio.Task[None] | None = None
        disconnect_watcher: asyncio.Task[None] | None = None

        async def forward_events() -> None:
            while True:
                await websocket.send_json(await queue.get())

        async def wait_for_disconnect() -> None:
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    return

        try:
            for payload in proactive_events.initial_snapshots():
                await websocket.send_json(payload)
            forwarder = asyncio.create_task(forward_events())
            disconnect_watcher = asyncio.create_task(wait_for_disconnect())
            done, _ = await asyncio.wait(
                {forwarder, disconnect_watcher},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                task.result()
        except WebSocketDisconnect:
            return
        except RuntimeError as exc:
            if "after sending 'websocket.close'" not in str(exc):
                raise
            return
        finally:
            for task in (forwarder, disconnect_watcher):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(
                *(task for task in (forwarder, disconnect_watcher) if task is not None),
                return_exceptions=True,
            )
            await proactive_events.unsubscribe(queue)

    @app.websocket("/ws/cli")
    async def cli_websocket(websocket: WebSocket) -> None:
        """将认证后的本地 CLI 连接绑定为 Gateway 的即时 Channel。"""
        auth_token = getattr(settings.gateway, "auth_token", None)
        if not isinstance(auth_token, str) or not is_authorized_bearer(
            websocket.headers.get("authorization"), auth_token
        ):
            await websocket.close(code=1008)
            return
        await websocket.accept()
        connection_id: str | None = None
        route = None
        registered = False

        async def send_action_error(message: str, request_id: str | None = None) -> None:
            payload: dict[str, str] = {"type": "error", "message": message}
            if request_id:
                payload["request_id"] = request_id
            await websocket.send_json(payload)

        def require_text(action: dict[str, Any], name: str) -> str:
            value = action.get(name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} 必须是非空字符串")
            return value.strip()

        def validate_bound_session(action: dict[str, Any]) -> None:
            assert route is not None
            supplied = action.get("session_id")
            if supplied is not None and str(supplied) != route.session_id:
                raise ValueError("CLI 会话不匹配")

        try:
            handshake = await websocket.receive_json()
            if handshake.get("type") != "cli.connect":
                raise ValueError("首个 CLI 动作必须是 cli.connect")
            connection_id = require_text(handshake, "connection_id")
            conversation_id = require_text(handshake, "conversation_id")

            async def sender(payload: dict[str, object]) -> bool:
                await websocket.send_json(payload)
                return True

            route = gateway.cli_transport.register(
                connection_id,
                settings.gateway.principal_id,
                conversation_id,
                sender,
            )
            registered = True
            await websocket.send_json(
                {
                    "type": "cli.ready",
                    "connection_id": connection_id,
                    "conversation_id": route.conversation_id,
                    "session_id": route.session_id,
                }
            )

            while True:
                action = await websocket.receive_json()
                action_type = action.get("type")
                request_id = str(action.get("request_id", "")) or None
                try:
                    if action_type == "message.send":
                        validate_bound_session(action)
                        accepted_request_id = await gateway.cli_coordinator.send(
                            route,
                            require_text(action, "request_id"),
                            require_text(action, "content"),
                        )
                        await websocket.send_json(
                            {
                                "type": "message.accepted",
                                "session_id": route.session_id,
                                "request_id": accepted_request_id,
                            }
                        )
                    elif action_type == "guidance.send":
                        validate_bound_session(action)
                        if not gateway.cli_transport.capabilities.supports_guidance:
                            raise ValueError("CLI Channel 不支持运行中引导")
                    elif action_type == "task.stop":
                        validate_bound_session(action)
                        await gateway.cli_coordinator.stop(route)
                        await websocket.send_json({"type": "task.stopping", "session_id": route.session_id})
                    elif action_type == "approval.resolve":
                        validate_bound_session(action)
                        approval_id = require_text(action, "approval_id")
                        approved = _require_boolean(action, "approved")
                        if not await gateway.cli_coordinator.resolve_approval(route, approval_id, approved):
                            raise RuntimeError("审批已失效")
                        await websocket.send_json(
                            {
                                "type": "approval.resolved",
                                "session_id": route.session_id,
                                "approval_id": approval_id,
                                "approved": approved,
                            }
                        )
                    else:
                        raise ValueError("不支持的 CLI WebSocket 动作")
                except (RuntimeError, ValueError) as exc:
                    await send_action_error(str(exc), request_id)
        except (RuntimeError, ValueError) as exc:
            await send_action_error(str(exc))
            await websocket.close(code=1008)
        except WebSocketDisconnect:
            pass
        finally:
            if route is not None:
                await gateway.cli_coordinator.disconnect(route)
            if registered and connection_id is not None:
                gateway.cli_transport.deregister(connection_id)

    @app.websocket("/ws/web")
    async def websocket_endpoint(websocket: WebSocket) -> None:
        """处理实时事件订阅、消息、引导、停止与审批动作。"""
        await websocket.accept()
        subscribed_session: str | None = None
        subscribed_queue: asyncio.Queue[Any] | None = None
        forwarder: asyncio.Task[None] | None = None

        async def forward_events(queue: asyncio.Queue[Any]) -> None:
            """将 EventHub 事件持续转发到当前 WebSocket。"""
            while True:
                event = await queue.get()
                await websocket.send_json(event.as_dict())

        async def send_action_error(message: str, client_action_id: str | None) -> None:
            """返回可关联的动作错误，保持 WebSocket 连接可用。"""
            payload: dict[str, str] = {"type": "error", "message": message}
            if client_action_id:
                payload["client_action_id"] = client_action_id
            await websocket.send_json(payload)

        def client_action_id(action: dict[str, Any]) -> str | None:
            """读取前端生成的可选动作关联标识。"""
            value = action.get("client_action_id")
            return str(value) if value else None

        try:
            while True:
                action = await websocket.receive_json()
                action_type = action.get("type")
                if action_type == "session.subscribe":
                    session_id = str(action["session_id"])
                    try:
                        await web_session_or_404(session_id)
                    except HTTPException as exc:
                        await send_action_error(str(exc.detail), client_action_id(action))
                        continue
                    if forwarder is not None:
                        forwarder.cancel()
                        await asyncio.gather(forwarder, return_exceptions=True)
                    if subscribed_session is not None:
                        coordinator.hub.unsubscribe(subscribed_session, subscribed_queue)
                    after = action.get("after_event_id")
                    replay, subscribed_queue = coordinator.hub.subscribe(
                        session_id, int(after) if after is not None else None
                    )
                    subscribed_session = session_id
                    for event in replay:
                        await websocket.send_json(event.as_dict())
                    forwarder = asyncio.create_task(forward_events(subscribed_queue))
                elif action_type == "message.send":
                    action_id = client_action_id(action)
                    try:
                        requested_id = action.get("session_id")
                        session_id = str(requested_id) if requested_id else None
                        if requested_id:
                            session = await supervisor.current_runtime.sessions.store.load_session(session_id)
                            if session is not None and session.archived:
                                raise ValueError("请先恢复已归档会话")
                        session_id, request_id = await coordinator.send(
                            session_id,
                            str(action.get("content", "")),
                            client_action_id=action_id,
                        )
                    except (ValueError, RuntimeError) as exc:
                        await send_action_error(str(exc), action_id)
                    else:
                        await websocket.send_json(
                            {
                                "type": "message.accepted",
                                "session_id": session_id,
                                "request_id": request_id,
                                "client_action_id": action_id,
                            }
                        )
                elif action_type == "catalog.execute":
                    action_id = client_action_id(action)
                    try:
                        session_id = str(action.get("session_id", ""))
                        if not session_id:
                            raise ValueError("Catalog 动作需要当前会话")
                        session = await web_session_or_404(session_id)
                        if session.archived:
                            raise ValueError("请先恢复已归档会话")
                        catalog_action = str(action.get("catalog_action", ""))
                        request_id = await gateway.catalog_actions.execute(session_id, catalog_action)
                    except (ValueError, RuntimeError) as exc:
                        await send_action_error(str(exc), action_id)
                    else:
                        await websocket.send_json(
                            {
                                "type": "catalog.accepted",
                                "session_id": session_id,
                                "request_id": request_id,
                                "catalog_action": catalog_action,
                                "client_action_id": action_id,
                            }
                        )
                elif action_type == "guidance.send":
                    try:
                        if not WEB_CHANNEL_CAPABILITIES.supports_guidance:
                            raise RuntimeError("Web Channel 不支持运行中引导")
                        await coordinator.send_guidance(str(action.get("session_id", "")), str(action.get("content", "")))
                    except (ValueError, RuntimeError) as exc:
                        await send_action_error(str(exc), client_action_id(action))
                elif action_type == "task.stop":
                    try:
                        await coordinator.stop(str(action.get("session_id", "")))
                    except (ValueError, RuntimeError) as exc:
                        await send_action_error(str(exc), client_action_id(action))
                elif action_type == "approval.resolve":
                    action_id = client_action_id(action)
                    try:
                        approved = _require_boolean(action, "approved")
                        resolved = await coordinator.resolve_approval(
                            str(action.get("session_id", "")), str(action.get("approval_id", "")), approved
                        )
                        if not resolved:
                            raise RuntimeError("审批已失效")
                    except (ValueError, RuntimeError) as exc:
                        await send_action_error(str(exc), action_id)
                else:
                    await send_action_error("不支持的 WebSocket 动作", client_action_id(action))
        except WebSocketDisconnect:
            pass
        finally:
            if forwarder is not None:
                forwarder.cancel()
                await asyncio.gather(forwarder, return_exceptions=True)
            if subscribed_session is not None:
                coordinator.hub.unsubscribe(subscribed_session, subscribed_queue)

    _mount_static(app)
    return app


def _mount_static(app: FastAPI) -> None:
    """在前端构建产物存在时提供本机单页应用。"""
    static_dir = Path(__file__).resolve().parents[3] / "web" / "static"
    index = static_dir / "index.html"
    if not index.exists():
        return
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.mount("/assets", StaticFiles(directory=static_dir / "assets"), name="assets")

    @app.get("/")
    @app.get("/sessions/{session_id}")
    async def index_page(session_id: str | None = None) -> FileResponse:
        """返回前端入口，由浏览器处理会话路由。"""
        del session_id
        return FileResponse(index)


def _session_dict(session: Session) -> dict[str, Any]:
    """转换不含内部上下文的会话列表对象。"""
    return {
        "id": session.id,
        "channel": session.channel,
        "title": session.title,
        "pinned": session.pinned,
        "archived": session.archived,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
    }


def _message_dict(message: MessageRecord) -> dict[str, Any]:
    """转换可安全返回浏览器的消息对象。"""
    return {
        "id": message.id,
        "role": message.role,
        "content": message.content,
        "created_at": message.created_at,
        "metadata": message.metadata,
    }


def _tool_call_dict(record: ToolCallRecord) -> dict[str, Any]:
    """转换既有工具调用记录。"""
    return {
        "turn_id": record.turn_id,
        "tool_call_id": record.tool_call_id,
        "tool_name": record.tool_name,
        "args": record.args,
        "content": record.content,
        "error": record.error,
        "duration_ms": record.duration_ms,
        "created_at": record.created_at,
    }


def _context_window_dict(traces: list[dict[str, Any]], max_context_tokens: int) -> dict[str, int]:
    """从最近一次 SAVE trace 提取已保存的上下文 token。"""
    current_context_tokens = 0
    for trace in reversed(traces):
        if trace.get("state") != "SAVE":
            continue
        metadata = trace.get("metadata")
        value = metadata.get("current_context_tokens") if isinstance(metadata, dict) else None
        if isinstance(value, int) and not isinstance(value, bool):
            current_context_tokens = max(0, value)
        break
    return {
        "current_context_tokens": current_context_tokens,
        "max_context_tokens": max(0, max_context_tokens),
    }

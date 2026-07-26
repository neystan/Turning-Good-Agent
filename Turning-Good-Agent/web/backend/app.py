from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from ...bus.queue import AsyncMessageBus
from ...config.settings import Settings
from ...runtime.runtime import AgentRuntime
from ...sessions.types import MessageRecord, Session, ToolCallRecord
from .coordinator import WebSessionCoordinator


def create_app(settings: Settings, runtime: AgentRuntime) -> FastAPI:
    """创建复用既有 Runtime 的本机 Web Host。"""
    coordinator = WebSessionCoordinator(
        runtime,
        AsyncMessageBus(),
        settings.web.max_concurrent_sessions,
        settings.web.event_buffer_size,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        """统一管理 Runtime 和 Web 调度器生命周期。"""
        await runtime.start()
        await coordinator.start()
        try:
            yield
        finally:
            await coordinator.close()
            await runtime.close()

    app = FastAPI(title="Turning Good Agent", lifespan=lifespan)
    app.state.coordinator = coordinator
    app.state.runtime = runtime

    @app.get("/api/sessions")
    async def list_sessions(archived: bool = False) -> list[dict[str, Any]]:
        """返回一个会话分组的轻量列表。"""
        return [_session_dict(item) for item in await runtime.sessions.store.list_sessions(archived)]

    @app.get("/api/sessions/{session_id}")
    async def get_session(session_id: str) -> dict[str, Any]:
        """返回单个会话元数据。"""
        session = await runtime.sessions.store.load_session(session_id)
        if session is None:
            raise HTTPException(404, "会话不存在")
        return _session_dict(session)

    @app.get("/api/sessions/{session_id}/messages")
    async def get_messages(session_id: str) -> list[dict[str, Any]]:
        """返回已持久化的原始对话消息。"""
        if await runtime.sessions.store.load_session(session_id) is None:
            raise HTTPException(404, "会话不存在")
        return [_message_dict(item) for item in await runtime.sessions.all_messages(session_id)]

    @app.get("/api/sessions/{session_id}/observability")
    async def get_observability(session_id: str) -> dict[str, Any]:
        """聚合既有 JSON/JSONL 的会话观测数据。"""
        session = await runtime.sessions.store.load_session(session_id)
        if session is None:
            raise HTTPException(404, "会话不存在")
        return {
            "session": _session_dict(session),
            "traces": await runtime.sessions.store.all_turn_traces(session_id),
            "token_usage": await runtime.sessions.store.all_true_token_usage(session_id),
            "tool_calls": [_tool_call_dict(item) for item in await runtime.sessions.store.all_tool_calls(session_id)],
        }

    @app.patch("/api/sessions/{session_id}")
    async def patch_session(session_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """更新标题、置顶或归档状态。"""
        session = await runtime.sessions.store.load_session(session_id)
        if session is None:
            raise HTTPException(404, "会话不存在")
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
        updated = await runtime.sessions.update_session_metadata(session_id, **values)
        assert updated is not None
        await coordinator.hub.publish(session_id, "", "session.updated", _session_dict(updated))
        return _session_dict(updated)

    @app.delete("/api/sessions/{session_id}", status_code=204)
    async def delete_session(session_id: str) -> None:
        """删除一个非活动会话的完整目录。"""
        if coordinator.is_active(session_id):
            raise HTTPException(409, "运行中、等待审批或停止中的会话不能删除")
        if await runtime.sessions.store.load_session(session_id) is None:
            raise HTTPException(404, "会话不存在")
        await runtime.sessions.store.clear_session(session_id)

    @app.get("/api/settings/ui")
    async def get_ui_settings() -> dict[str, bool]:
        """返回浏览器可见的非敏感审批设置。"""
        return {"auto_approve_tools": settings.tool_permissions.auto_approve_tools}

    @app.patch("/api/settings/ui")
    async def patch_ui_settings(payload: dict[str, Any]) -> dict[str, bool]:
        """更新全局工具自动审批策略。"""
        if "auto_approve_tools" in payload:
            settings.update_auto_approve_tools(bool(payload["auto_approve_tools"]))
        return {"auto_approve_tools": settings.tool_permissions.auto_approve_tools}

    @app.websocket("/ws")
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
                        session_id = str(requested_id or f"web-{uuid4()}")
                        if requested_id:
                            session = await runtime.sessions.store.load_session(session_id)
                            if session is not None and session.archived:
                                raise ValueError("请先恢复已归档会话")
                        request_id = await coordinator.send(session_id, str(action.get("content", "")), settings.user_id)
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
                elif action_type == "guidance.send":
                    try:
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
                        resolved = await coordinator.resolve_approval(
                            str(action.get("session_id", "")), str(action.get("approval_id", "")), bool(action.get("approved"))
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

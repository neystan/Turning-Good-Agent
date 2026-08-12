from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..bus.messages import ChannelRoute, InboundMessage
from ..memory.long_term import ProfileMemory
from ..sessions.manager import SessionManager
from ..sessions.types import MessageRecord, Session


class RuntimePrincipalResolver(Protocol):
    """定义 Runtime 获取主体 Context 的最小接口。"""

    def resolve(self, route: ChannelRoute) -> "PrincipalRuntimeContext":
        """解析可信路由。"""


@dataclass(frozen=True, slots=True)
class PrincipalRuntimeContext:
    """绑定一轮 Runtime 所需的主体数据根、会话读取器和工具边界。"""

    principal_id: str
    data_root: Path
    profile_memory: ProfileMemory
    sessions: "PrincipalSessionReader"
    allowed_tool_names: frozenset[str] | None


class PrincipalSessionReader:
    """将 SessionManager 限制在一个可信主体的会话命名空间。"""

    def __init__(
        self,
        manager: SessionManager,
        principal_id: str,
        *,
        allow_global_approval: bool = False,
    ) -> None:
        if not isinstance(principal_id, str) or not principal_id:
            raise ValueError("principal id 无效")
        self._manager = manager
        self.principal_id = principal_id
        self._allow_global_approval = allow_global_approval

    @property
    def locks(self):
        """返回已绑定主体根的会话锁。"""
        return self._manager.locks

    async def list_sessions(self) -> list[Session]:
        """只读取当前主体的会话列表。"""
        return await self._manager.list_sessions(principal_id=self.principal_id)

    async def all_messages(self, session_id: str) -> list[MessageRecord]:
        """读取消息前确认 Session 的主体归属。"""
        session = await self._manager.store.load_session(session_id)
        if session is None or session.principal_id != self.principal_id:
            raise PermissionError("不能读取其它主体的 session")
        return await self._manager.all_messages(session_id)

    async def load_or_create(self, route: ChannelRoute) -> Session:
        """只允许为绑定主体加载或创建会话。"""
        self._validate_route(route)
        return await self._manager.load_or_create(route)

    async def handle_inbound_command(self, session_id: str, message: InboundMessage) -> str | None:
        """在委托 slash command 前校验主体路由。"""
        self._validate_route(message.route)
        if not self._allow_global_approval and message.content.strip().startswith("/approve"):
            raise PermissionError("非 Owner 不能修改全局工具自动审批")
        session = await self._manager.store.load_session(session_id)
        if session is not None and session.principal_id != self.principal_id:
            raise PermissionError("不能操作其它主体的 session")
        return await self._manager.handle_inbound_command(session_id, message)

    async def handle_command(self, session: Session, message: InboundMessage) -> str | None:
        """校验会话与入站主体后委托命令处理。"""
        if session.principal_id != self.principal_id:
            raise PermissionError("不能操作其它主体的 session")
        self._validate_route(message.route)
        return await self._manager.handle_command(session, message)

    async def recent_messages(self, session_id: str, limit: int) -> list[MessageRecord]:
        """读取当前主体会话的最近消息。"""
        await self._require_session(session_id)
        return await self._manager.recent_messages(session_id, limit)

    async def update_session_metadata(self, session_id: str, **values: object) -> Session | None:
        """更新当前主体会话元数据。"""
        await self._require_session(session_id)
        return await self._manager.update_session_metadata(session_id, **values)

    async def cleanup_expired_sessions(self, retention_days: int) -> int:
        """只清理当前主体根下的过期会话。"""
        return await self._manager.cleanup_expired_sessions(retention_days)

    async def update_summary(self, session_id: str, summary: str) -> None:
        """更新当前主体会话的摘要。"""
        await self._require_session(session_id)
        await self._manager.store.update_summary(session_id, summary)

    async def update_uncompacted_history(self, session_id: str, history: list[MessageRecord]) -> None:
        """更新当前主体会话的未压缩历史。"""
        await self._require_session(session_id)
        await self._manager.store.update_uncompacted_history(session_id, history)

    async def save_user_message(
        self,
        session_id: str,
        content: str,
        token_count: int,
        *,
        message_id: str | None = None,
        created_at: str | None = None,
    ) -> MessageRecord:
        """写入当前主体会话的用户消息。"""
        await self._require_session(session_id)
        return await self._manager.save_user_message(
            session_id,
            content,
            token_count,
            message_id=message_id,
            created_at=created_at,
        )

    async def save_assistant_message(
        self,
        session_id: str,
        content: str,
        token_count: int,
        metadata: dict[str, object] | None = None,
    ) -> MessageRecord:
        """写入当前主体会话的 assistant 消息。"""
        await self._require_session(session_id)
        return await self._manager.save_assistant_message(
            session_id, content, token_count, metadata
        )

    async def save_tool_calls(
        self, turn_id: str, session_id: str, tool_calls: list[dict[str, object]]
    ) -> None:
        """写入当前主体会话的工具调用记录。"""
        await self._require_session(session_id)
        await self._manager.store.save_tool_calls(turn_id, session_id, tool_calls)

    async def save_true_token_usage(
        self, turn_id: str, session_id: str, usage: dict[str, Any]
    ) -> None:
        """写入当前主体会话的真实 token 用量。"""
        await self._require_session(session_id)
        await self._manager.store.save_true_token_usage(turn_id, session_id, usage)

    # 读取当前主体会话的有界 Multi-Agent Run 摘要。
    async def read_multi_agent_runs(
        self,
        session_id: str,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        await self._require_session(session_id)
        return await self._manager.store.read_multi_agent_runs(session_id, limit)

    async def save_turn_traces(self, traces: list[object]) -> None:
        """写入仅属于当前主体会话的 trace。"""
        for trace in traces:
            await self._require_session(trace.session_id)
        await self._manager.store.save_turn_traces(traces)

    async def last_total_tokens(self, session_id: str) -> int:
        """读取当前主体会话的累计 token。"""
        await self._require_session(session_id)
        return await self._manager.store.last_total_tokens(session_id)

    async def _require_session(self, session_id: str) -> Session:
        session = await self._manager.store.load_session(session_id)
        if session is None or session.principal_id != self.principal_id:
            raise PermissionError("不能访问其它主体的 session")
        return session

    def _validate_route(self, route: ChannelRoute) -> None:
        if route.principal_id != self.principal_id:
            raise PermissionError("route 不属于当前主体")

from ..bus.messages import InboundMessage
from ..context.session_context import build_session_context, count_message_tokens
from .locks import SessionLocks
from .store import JsonlSessionStore
from .types import MessageRecord, Session


COMMANDS: dict[str, str] = {
    "/history": "查看当前会话的完整历史消息",
    "/context": "查看当前会注入模型的会话上下文",
    "/tools": "查看当前会话的工具调用记录",
    "/approve": "查看或设置当前会话工具自动审批",
    "/clear": "清空当前会话的消息和摘要",
    "/new": "开始一个新会话，CLI 会切换到新的 session",
    "/exit": "退出当前 CLI 会话",
}


class SessionManager:
    """封装会话加载、保存和快捷命令。"""

    def __init__(self, store: JsonlSessionStore) -> None:
        self.store = store
        self.locks = SessionLocks()

    async def load_or_create(self, session_id: str, user_id: str, channel: str) -> Session:
        """加载已有会话，不存在时创建。"""
        session = await self.store.load_session(session_id)
        if session is not None:
            return session
        return await self.store.create_session(session_id, user_id, channel)

    async def handle_inbound_command(self, session_id: str, msg: InboundMessage) -> str | None:
        """按 session_id 处理 slash command，避免空命令创建会话。"""
        command = msg.content.strip()
        if not command.startswith("/"):
            return None
        if command == "/history":
            records = await self.all_messages(session_id)
            if not records:
                return "暂无历史消息。"
            return "\n".join(f"{item.role}: {item.content}" for item in records)
        if command == "/context":
            return await self.context_view(session_id)
        if command == "/tools":
            return await self.tools_view(session_id)
        if command == "/approve":
            return await self.auto_approve_status(session_id)
        if command == "/approve on":
            return await self.enable_auto_approve(session_id, msg.user_id, msg.channel)
        if command == "/approve off":
            return await self.disable_auto_approve(session_id)
        if command.startswith("/approve"):
            return "用法：/approve [on|off]"
        if command == "/clear":
            await self.store.clear_session(session_id)
            return "当前会话已清空。"
        if command == "/new":
            return "已开始新会话。"
        if command == "/exit":
            return "再见。"
        return f"未知命令：{command}\n可用命令：\n{self.command_help()}"

    async def handle_command(self, session: Session, msg: InboundMessage) -> str | None:
        """处理 slash command，普通消息返回 None。"""
        return await self.handle_inbound_command(session.id, msg)

    async def cleanup_expired_sessions(self, retention_days: int) -> int:
        """清理超过保留期的会话。"""
        return await self.store.cleanup_expired_sessions(retention_days)

    def command_help(self) -> str:
        """返回可用命令说明。"""
        return "\n".join(f"{name}：{description}" for name, description in COMMANDS.items())

    async def context_view(self, session_id: str) -> str:
        """返回当前模型上下文视图。"""
        session = await self.store.load_session(session_id)
        if session is None:
            return "当前会话暂无上下文。"
        context = build_session_context(session, await self.all_messages(session_id))
        lines = [
            f"会话摘要：{context.summary or '无'}",
            f"完整历史消息数：{len(context.full_history)}",
            f"未压缩历史消息数：{len(context.uncompacted_history)}",
            f"未压缩历史token数：{count_message_tokens(context.uncompacted_history)}",
        ]
        if context.uncompacted_history:
            lines.append("未压缩历史：")
            lines.extend(f"{item.role}: {item.content}" for item in context.uncompacted_history)
        return "\n".join(lines)

    async def tools_view(self, session_id: str) -> str:
        """返回当前会话的工具调用视图。"""
        records = await self.store.all_tool_calls(session_id)
        if not records:
            return "暂无工具调用记录。"
        lines: list[str] = []
        for item in records:
            lines.append(
                f"{item.created_at} turn={item.turn_id} tool={item.tool_name} tool_call_id={item.tool_call_id}"
            )
            lines.append(f"args: {item.args}")
            lines.append(f"result: {item.content}")
            lines.append(f"error: {item.error or '无'}")
            lines.append(f"duration_ms: {item.duration_ms:.3f}")
        return "\n".join(lines)

    async def auto_approve_status(self, session_id: str) -> str:
        """返回当前会话的工具自动审批状态。"""
        session = await self.store.load_session(session_id)
        enabled = session is not None and session.auto_approve_tools
        return f"当前会话工具自动审批：{'已开启' if enabled else '已关闭'}。"

    async def enable_auto_approve(self, session_id: str, user_id: str, channel: str) -> str:
        """按需创建会话并开启工具自动审批。"""
        await self.load_or_create(session_id, user_id, channel)
        await self.store.update_auto_approve_tools(session_id, True)
        return "当前会话已开启工具自动审批。\n审批类工具将不再逐次确认；安全检查仍然生效。"

    async def disable_auto_approve(self, session_id: str) -> str:
        """关闭已存在会话的工具自动审批。"""
        session = await self.store.load_session(session_id)
        if session is not None:
            await self.store.update_auto_approve_tools(session_id, False)
            return "当前会话已关闭工具自动审批。\n审批类工具将恢复逐次确认。"
        return "当前会话已关闭工具自动审批。"

    async def save_user_message(
        self,
        session_id: str,
        content: str,
        token_count: int = 0,
    ) -> MessageRecord:
        """保存用户消息。"""
        return await self.store.save_message(session_id, "user", content, token_count)

    async def save_assistant_message(
        self,
        session_id: str,
        content: str,
        token_count: int = 0,
    ) -> MessageRecord:
        """保存 assistant 回复。"""
        return await self.store.save_message(session_id, "assistant", content, token_count)

    async def recent_messages(self, session_id: str, limit: int) -> list[MessageRecord]:
        """读取最近消息。"""
        return await self.store.recent_messages(session_id, limit)

    async def all_messages(self, session_id: str) -> list[MessageRecord]:
        """读取当前会话全部消息。"""
        return await self.store.all_messages(session_id)

from ..bus.messages import InboundMessage
from .locks import SessionLocks
from .store import JsonlSessionStore
from .types import MessageRecord, Session


COMMANDS: dict[str, str] = {
    "/history": "查看当前会话的完整历史消息",
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

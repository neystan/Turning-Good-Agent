from dataclasses import dataclass

from ..sessions.types import MessageRecord, Session


@dataclass(slots=True)
class SessionContext:
    """保存会话历史的两种视图。"""

    session: Session
    full_history: list[MessageRecord]
    uncompacted_history: list[MessageRecord]
    summary: str


def build_session_context(session: Session, full_history: list[MessageRecord]) -> SessionContext:
    """根据 session 快照构建会话上下文。"""
    if session.uncompacted_history or session.summary:
        uncompacted_history = session.uncompacted_history
    else:
        uncompacted_history = full_history
    return SessionContext(
        session=session,
        full_history=full_history,
        uncompacted_history=uncompacted_history,
        summary=session.summary,
    )


def count_message_tokens(messages: list[MessageRecord]) -> int:
    """统计消息记录里的 tokenizer token 权重。"""
    return sum(item.token_count for item in messages)

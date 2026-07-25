from __future__ import annotations

import asyncio
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Callable

from ...bus.messages import utc_now_iso


@dataclass(slots=True)
class SessionEvent:
    """表示发送给一个 Web 会话的实时事件。"""

    event_id: int
    session_id: str
    request_id: str
    type: str
    payload: dict[str, Any]
    created_at: str

    def as_dict(self) -> dict[str, Any]:
        """转换为 WebSocket 可发送对象。"""
        return {
            "event_id": self.event_id,
            "session_id": self.session_id,
            "request_id": self.request_id,
            "type": self.type,
            "payload": self.payload,
            "created_at": self.created_at,
        }


class SessionEventHub:
    """保存每会话有界事件窗口并向订阅者广播。"""

    def __init__(self, buffer_size: int, snapshot_factory: Callable[[str], dict[str, Any]]) -> None:
        """初始化事件序号、缓冲和快照生成器。"""
        self.buffer_size = buffer_size
        self.snapshot_factory = snapshot_factory
        self._events: dict[str, deque[SessionEvent]] = defaultdict(lambda: deque(maxlen=buffer_size))
        self._subscribers: dict[str, set[asyncio.Queue[SessionEvent]]] = defaultdict(set)
        self._event_ids: dict[str, int] = defaultdict(int)

    async def publish(
        self,
        session_id: str,
        request_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> SessionEvent:
        """写入内存窗口并广播一个实时事件。"""
        self._event_ids[session_id] += 1
        event = SessionEvent(
            self._event_ids[session_id], session_id, request_id, event_type, payload or {}, utc_now_iso()
        )
        self._events[session_id].append(event)
        for queue in tuple(self._subscribers[session_id]):
            queue.put_nowait(event)
        return event

    def subscribe(self, session_id: str, after_event_id: int | None) -> tuple[list[SessionEvent], asyncio.Queue[SessionEvent]]:
        """订阅会话，并返回可回放事件或快照。"""
        queue: asyncio.Queue[SessionEvent] = asyncio.Queue()
        events = self._events[session_id]
        if after_event_id is None:
            replay = [self._snapshot_event(session_id)]
        elif events and after_event_id >= events[0].event_id - 1:
            replay = [item for item in events if item.event_id > after_event_id]
        else:
            replay = [self._snapshot_event(session_id)]
        self._subscribers[session_id].add(queue)
        return replay, queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue[SessionEvent] | None) -> None:
        """移除一个 WebSocket 订阅队列。"""
        if queue is not None:
            self._subscribers[session_id].discard(queue)

    def _snapshot_event(self, session_id: str) -> SessionEvent:
        """构造不落盘的会话运行快照。"""
        return SessionEvent(
            self._event_ids[session_id], session_id, "", "session.snapshot", self.snapshot_factory(session_id), utc_now_iso()
        )

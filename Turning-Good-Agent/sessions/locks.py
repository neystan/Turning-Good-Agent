import asyncio


class SessionLocks:
    """管理 session 级异步锁。"""

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}

    def lock_for(self, session_id: str) -> asyncio.Lock:
        """返回指定会话的串行执行锁。"""
        if session_id not in self._locks:
            self._locks[session_id] = asyncio.Lock()
        return self._locks[session_id]

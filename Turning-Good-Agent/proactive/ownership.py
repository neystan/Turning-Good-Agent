from __future__ import annotations

import asyncio
from contextlib import contextmanager
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
import threading
from uuid import uuid4


_PROCESS_LOCKS: dict[str, threading.RLock] = {}
_PROCESS_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True, slots=True)
class OwnershipState:
    mode: str
    owner_id: str | None
    owner_kind: str | None
    owner_pid: int | None

    @property
    def writable(self) -> bool:
        return self.mode == "owner"

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "writable": self.writable,
            "owner_id": self.owner_id,
            "owner_kind": self.owner_kind,
            "owner_pid": self.owner_pid,
        }


class ProactiveOwnershipLease:
    """使用 data_dir 内原子租约保证跨 Host 的主动调度器唯一。"""

    def __init__(
        self,
        data_dir: Path,
        *,
        owner_kind: str,
        owner_id: str | None = None,
        heartbeat_seconds: float = 1.0,
    ) -> None:
        self._path = data_dir / "proactive" / ".owner.json"
        self._lock_path = data_dir / "proactive" / ".owner.lock"
        self._owner_kind = owner_kind
        self._owner_id = owner_id or str(uuid4())
        self._heartbeat_seconds = heartbeat_seconds
        self._acquired = False
        self._heartbeat_task: asyncio.Task[None] | None = None

    @property
    def owner_id(self) -> str:
        return self._owner_id

    async def acquire_owner(self) -> OwnershipState:
        """尝试取得唯一租约；读取不确定时保守地保持只读。"""
        if self._acquired:
            return self._state_for_self()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            with self._exclusive_mutation():
                if self._acquired:
                    return self._state_for_self()
                try:
                    descriptor = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                except FileExistsError:
                    current = self._read_record()
                    if current is None or not self._stale(current):
                        return self._state_for_record(current)
                    try:
                        self._path.unlink()
                    except FileNotFoundError:
                        continue
                    continue
                try:
                    with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                        json.dump(self._record(), handle, ensure_ascii=False)
                        handle.flush()
                        os.fsync(handle.fileno())
                except BaseException:
                    self._path.unlink(missing_ok=True)
                    raise
                self._acquired = True
                self._heartbeat_task = asyncio.create_task(self._heartbeat(), name="proactive-owner-heartbeat")
                return self._state_for_self()

    async def try_takeover(self) -> OwnershipState:
        """在另一 Host 已退出后重新尝试取得租约。"""
        return await self.acquire_owner()

    async def release_owner(self) -> None:
        """仅释放自身租约；不因读异常误删未知或新所有者的记录。"""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
            self._heartbeat_task = None
        if not self._acquired:
            return
        with self._exclusive_mutation():
            current = self._read_record()
            if current is not None and current.get("owner_id") == self._owner_id:
                self._path.unlink(missing_ok=True)
        self._acquired = False

    def state(self) -> OwnershipState:
        """读取当前所有权；自身持有时不依赖一次瞬时磁盘读取。"""
        if self._acquired:
            return self._state_for_self()
        return self._state_for_record(self._read_record())

    async def _heartbeat(self) -> None:
        while self._acquired:
            await asyncio.sleep(self._heartbeat_seconds)
            with self._exclusive_mutation():
                if not self._acquired:
                    return
                current = self._read_record()
                if current is None or current.get("owner_id") != self._owner_id:
                    self._acquired = False
                    return
                self._write_record(self._record())

    def _record(self) -> dict[str, object]:
        return {
            "owner_id": self._owner_id,
            "owner_kind": self._owner_kind,
            "owner_pid": os.getpid(),
            "heartbeat_at": datetime.now(UTC).isoformat(),
        }

    def _read_record(self) -> dict[str, object] | None:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (json.JSONDecodeError, OSError):
            return None
        return payload if isinstance(payload, dict) else None

    def _write_record(self, payload: dict[str, object]) -> None:
        temporary = self._path.with_name(f".{self._path.name}.{uuid4().hex}.tmp")
        try:
            with temporary.open("x", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self._path)
        except OSError:
            temporary.unlink(missing_ok=True)

    def _stale(self, record: dict[str, object]) -> bool:
        owner_id = record.get("owner_id")
        pid = record.get("owner_pid")
        if not isinstance(owner_id, str) or not isinstance(pid, int):
            return True
        if os.name == "nt":
            return not _windows_pid_alive(pid)
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        except PermissionError:
            return False
        return False

    @contextmanager
    def _exclusive_mutation(self):
        key = str(self._lock_path.resolve())
        with _PROCESS_LOCKS_GUARD:
            process_lock = _PROCESS_LOCKS.setdefault(key, threading.RLock())
        with process_lock:
            self._lock_path.parent.mkdir(parents=True, exist_ok=True)
            with self._lock_path.open("a+b") as handle:
                handle.seek(0, os.SEEK_END)
                if handle.tell() == 0:
                    handle.write(b"0")
                    handle.flush()
                    os.fsync(handle.fileno())
                handle.seek(0)
                _lock_file(handle)
                try:
                    yield
                finally:
                    _unlock_file(handle)

    def _state_for_self(self) -> OwnershipState:
        return OwnershipState("owner", self._owner_id, self._owner_kind, os.getpid())

    @staticmethod
    def _state_for_record(record: dict[str, object] | None) -> OwnershipState:
        if record is None:
            return OwnershipState("readonly", None, None, None)
        return OwnershipState(
            "readonly",
            record.get("owner_id") if isinstance(record.get("owner_id"), str) else None,
            record.get("owner_kind") if isinstance(record.get("owner_kind"), str) else None,
            record.get("owner_pid") if isinstance(record.get("owner_pid"), int) else None,
        )


def _windows_pid_alive(pid: int) -> bool:
    """在 Windows 上查询 PID，避免 os.kill(pid, 0) 的非 POSIX 语义。"""
    import ctypes

    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
            return False
        return exit_code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _lock_file(handle) -> None:
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return
    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

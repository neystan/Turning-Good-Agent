from __future__ import annotations

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


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
    """用一个本机原子租约保证主动调度器在 data_dir 中唯一。"""

    def __init__(
        self,
        data_dir: Path,
        *,
        owner_kind: str,
        owner_id: str | None = None,
        heartbeat_seconds: float = 1.0,
    ) -> None:
        self._path = data_dir / "proactive" / ".owner.json"
        self._owner_kind = owner_kind
        self._owner_id = owner_id or str(uuid4())
        self._heartbeat_seconds = heartbeat_seconds
        self._acquired = False
        self._heartbeat_task: asyncio.Task[None] | None = None

    @property
    def owner_id(self) -> str:
        return self._owner_id

    async def acquire_owner(self) -> OwnershipState:
        """尝试取得唯一租约；活跃所有者存在时返回只读状态。"""
        if self._acquired:
            return self._state_for_self()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = self._record()
        try:
            descriptor = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            current = self._read_record()
            if self._stale(current):
                self._remove_stale_record()
                return await self.acquire_owner()
            return self._state_for_record(current)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            self._path.unlink(missing_ok=True)
            raise
        self._acquired = True
        self._heartbeat_task = asyncio.create_task(self._heartbeat(), name="proactive-owner-heartbeat")
        return self._state_for_self()

    async def try_takeover(self) -> OwnershipState:
        """在另一个 Host 退出后重新尝试取得租约。"""
        return await self.acquire_owner()

    async def release_owner(self) -> None:
        """仅删除自身创建的租约，避免误删新所有者。"""
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
            self._heartbeat_task = None
        if not self._acquired:
            return
        current = self._read_record()
        if current.get("owner_id") == self._owner_id:
            self._path.unlink(missing_ok=True)
        self._acquired = False

    def state(self) -> OwnershipState:
        """读取当前所有权；本进程租约优先于磁盘快照。"""
        if self._acquired:
            return self._state_for_self()
        current = self._read_record()
        return self._state_for_record(current)

    async def _heartbeat(self) -> None:
        while self._acquired:
            await asyncio.sleep(self._heartbeat_seconds)
            current = self._read_record()
            if current.get("owner_id") != self._owner_id:
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

    def _read_record(self) -> dict[str, object]:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_record(self, payload: dict[str, object]) -> None:
        try:
            self._path.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        except OSError:
            self._acquired = False

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

    def _remove_stale_record(self) -> None:
        try:
            self._path.unlink()
        except FileNotFoundError:
            return

    def _state_for_self(self) -> OwnershipState:
        return OwnershipState("owner", self._owner_id, self._owner_kind, os.getpid())

    @staticmethod
    def _state_for_record(record: dict[str, object]) -> OwnershipState:
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

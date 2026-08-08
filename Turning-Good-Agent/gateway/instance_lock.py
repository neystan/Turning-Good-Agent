from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import BinaryIO


_REGISTRY_LOCK = threading.Lock()
_HELD_PATHS: set[Path] = set()


class GatewayInstanceLock:
    """为一个 data_dir 保留唯一 Gateway 进程锁。"""

    def __init__(self, data_dir: Path) -> None:
        self._path = (data_dir / "gateway.lock").resolve()
        self._handle: BinaryIO | None = None

    def acquire(self) -> "GatewayInstanceLock":
        """非阻塞获取锁；已有 Gateway 时明确失败。"""
        if self._handle is not None:
            return self
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with _REGISTRY_LOCK:
            if self._path in _HELD_PATHS:
                raise RuntimeError("Gateway is already running for this data directory.")
            handle = self._path.open("a+b")
            try:
                _acquire_file_lock(handle)
            except OSError as error:
                handle.close()
                if _is_lock_contention(error):
                    raise RuntimeError("Gateway is already running for this data directory.") from error
                raise
            self._handle = handle
            _HELD_PATHS.add(self._path)
        return self

    def close(self) -> None:
        """释放锁；关闭后允许另一个 Gateway 获取。"""
        handle = self._handle
        if handle is None:
            return
        self._handle = None
        try:
            _release_file_lock(handle)
        finally:
            handle.close()
            with _REGISTRY_LOCK:
                _HELD_PATHS.discard(self._path)

    def __enter__(self) -> "GatewayInstanceLock":
        return self.acquire()

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        del exc_type, exc_value, traceback
        self.close()


def _acquire_file_lock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        if not handle.read(1):
            handle.seek(0)
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _release_file_lock(handle: BinaryIO) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _is_lock_contention(error: OSError) -> bool:
    return getattr(error, "winerror", None) in {32, 33} or error.errno in {11, 13}

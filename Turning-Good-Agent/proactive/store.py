from __future__ import annotations

import errno
import json
import os
from pathlib import Path


class ProactiveStore:
    """管理 Phase 7 独立于会话事实的 JSON 持久化。"""

    def __init__(self, data_dir: Path) -> None:
        """初始化对象状态。"""
        self.root = data_dir / "proactive"

    def write_json(self, name: str, payload: object) -> None:
        """将一个小型可变状态文件安全写入主动目录。"""
        path = self._path(name)
        serialized = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        temporary = path.with_name(f".{path.name}.tmp")
        self._write_text(temporary, serialized)
        try:
            os.replace(temporary, path)
        except OSError as exc:
            if not _replace_blocked(exc):
                raise
            self._write_text(path, serialized)
            temporary.unlink(missing_ok=True)

    def read_json(self, name: str, default: object) -> object:
        """读取 JSON 状态文件，文件不存在时返回调用方默认值。"""
        path = self._path(name, create_root=False)
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))

    def _path(self, name: str, *, create_root: bool = True) -> Path:
        """解析主动数据文件路径。"""
        if Path(name).name != name:
            raise ValueError("主动状态文件名不能包含目录")
        if create_root:
            self.root.mkdir(parents=True, exist_ok=True)
        return self.root / name

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        """安全写入文本文件。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())


def _replace_blocked(error: OSError) -> bool:
    """仅对 Windows/Docker bind mount 的可预期替换阻塞采用直接写回退。"""
    return isinstance(error, PermissionError) or error.errno in {errno.EACCES, errno.EBUSY, errno.EPERM}

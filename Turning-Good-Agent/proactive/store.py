from __future__ import annotations

import errno
import json
import os
from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any

from .types import CronJob


class ProactiveStore:
    """管理 Phase 7 独立于会话事实的 JSON/JSONL 持久化。"""

    def __init__(self, data_dir: Path) -> None:
        """初始化对象状态。"""
        self.root = data_dir / "proactive"

    def save_cron_jobs(self, jobs: Iterable[CronJob]) -> None:
        """原子保存全部 Cron Job 当前状态。"""
        self.write_json("cron_jobs.json", [job.to_dict() for job in jobs])

    def load_cron_jobs(self) -> list[CronJob]:
        """加载当前 Cron Job；缺失状态文件按空列表处理。"""
        path = self._path("cron_jobs.json", create_root=False)
        payload = self.read_json("cron_jobs.json", [])
        if not isinstance(payload, list):
            raise ValueError(f"{path} 必须是数组")
        if any(not isinstance(item, dict) for item in payload):
            raise ValueError(f"{path} 中的每条 Cron Job 必须是 object")
        if any(isinstance(item, dict) and _is_legacy_cron(item) for item in payload):
            self.raise_legacy_data("cron_jobs.json")
        try:
            return [CronJob.from_dict(item) for item in payload]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{path} 包含无效的 Cron Job：{exc}") from exc

    def raise_legacy_data(self, name: str) -> None:
        """对尚未发布的旧 Phase 7 数据给出只读清理提示。"""
        path = self._path(name, create_root=False)
        raise ValueError(
            f"检测到旧版 Phase 7 主动数据：{path}。"
            f"请先备份，再清理 {self.root} 后重启；"
            "memory/USER.md 与 memory/SOUL.md 不受影响。"
        )

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

    def append_jsonl(self, name: str, payload: dict[str, Any]) -> None:
        """追加一条主动审计记录并强制落盘。"""
        path = self._path(name)
        with path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def append_jsonl_many(self, name: str, payloads: Iterable[dict[str, Any]]) -> None:
        """在一次原子重写中追加多条 JSONL 记录。"""
        additions = [dict(payload) for payload in payloads]
        if additions:
            self.replace_jsonl(name, [*self.read_jsonl(name), *additions])

    def read_jsonl(self, name: str) -> list[dict[str, Any]]:
        """读取可恢复的 JSONL 审计，跳过空行。"""
        path = self._path(name, create_root=False)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def replace_jsonl(self, name: str, payloads: Iterable[dict[str, Any]]) -> None:
        """安全重写需执行数据保留清理的 JSONL 审计文件。"""
        path = self._path(name)
        serialized = "".join(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n" for payload in payloads)
        temporary = path.with_name(f".{path.name}.tmp")
        self._write_text(temporary, serialized)
        try:
            os.replace(temporary, path)
        except OSError as exc:
            if not _replace_blocked(exc):
                raise
            self._write_text(path, serialized)
            temporary.unlink(missing_ok=True)

    def filter_jsonl(self, name: str, keep: Callable[[dict[str, Any]], bool]) -> int:
        """原子删除不满足条件的 JSONL 记录并返回删除数量。"""
        records = self.read_jsonl(name)
        retained = [record for record in records if keep(record)]
        removed = len(records) - len(retained)
        if removed:
            self.replace_jsonl(name, retained)
        return removed

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


def _is_legacy_cron(payload: dict[str, Any]) -> bool:
    """识别 Phase 7 已废弃的 Cron 生命周期与一次性表达。"""
    if set(payload).intersection({"status", "last_triggered_at", "last_completed_at", "last_error"}):
        return True
    recurring = payload.get("recurring")
    if not isinstance(recurring, bool):
        return True
    return recurring is False and payload.get("cron") is not None

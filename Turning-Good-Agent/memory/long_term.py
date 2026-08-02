from __future__ import annotations

import errno
import os
from dataclasses import dataclass
from pathlib import Path

from ..config.settings import ProactiveSettings
from ..sessions.token_counter import count_content_tokens


class ProfileMemoryLimitError(ValueError):
    """表示用户或 Agent 长期记忆超过固定注入预算。"""


@dataclass(frozen=True, slots=True)
class ProfileMemorySnapshot:
    """一次读取到的 USER/SOUL 文件内容。"""

    soul: str = ""
    user: str = ""

    def system_contents(self) -> list[str]:
        """以固定 SOUL、USER 顺序返回可注入的 system 内容。"""
        contents: list[str] = []
        if self.soul:
            contents.append(f"Agent 长期记忆（SOUL）：\n{self.soul}")
        if self.user:
            contents.append(f"用户长期记忆（USER）：\n{self.user}")
        return contents


class ProfileMemory:
    """管理部署级 USER.md 与 SOUL.md 的读取、预算和安全写入。"""

    def __init__(self, data_dir: Path | None = None, settings: ProactiveSettings | None = None) -> None:
        """初始化对象状态。"""
        self.root = (data_dir or Path(".sessions")) / "memory"
        self.settings = settings or ProactiveSettings()

    def read(self) -> ProfileMemorySnapshot:
        """读取当前文件，并拒绝任何超出完整注入预算的版本。"""
        snapshot = ProfileMemorySnapshot(
            soul=self._read_file("SOUL.md"),
            user=self._read_file("USER.md"),
        )
        self._validate(snapshot)
        return snapshot

    def write(self, snapshot: ProfileMemorySnapshot) -> None:
        """先验证完整候选，再原子更新两个画像文件。"""
        self._validate(snapshot)
        previous = {
            "SOUL.md": self._read_raw_file("SOUL.md"),
            "USER.md": self._read_raw_file("USER.md"),
        }
        try:
            self._write_file("SOUL.md", snapshot.soul)
            self._write_file("USER.md", snapshot.user)
        except Exception:
            for name, content in previous.items():
                if content is None:
                    (self.root / name).unlink(missing_ok=True)
                else:
                    self._write_rendered_file(name, content)
            raise

    def _validate(self, snapshot: ProfileMemorySnapshot) -> None:
        """校验画像内容和 token 限额。"""
        soul_tokens = count_content_tokens(snapshot.soul)
        user_tokens = count_content_tokens(snapshot.user)
        if soul_tokens > self.settings.soul_profile_token_limit:
            raise ProfileMemoryLimitError(
                f"SOUL.md 超过 {self.settings.soul_profile_token_limit} tokens 限制"
            )
        if user_tokens > self.settings.user_profile_token_limit:
            raise ProfileMemoryLimitError(
                f"USER.md 超过 {self.settings.user_profile_token_limit} tokens 限制"
            )
        if soul_tokens + user_tokens > self.settings.profile_total_token_limit:
            raise ProfileMemoryLimitError(
                f"长期记忆超过 {self.settings.profile_total_token_limit} tokens 限制"
            )

    def _read_file(self, name: str) -> str:
        """读取画像文件内容。"""
        path = self.root / name
        return path.read_text(encoding="utf-8").strip() if path.exists() else ""

    def _read_raw_file(self, name: str) -> str | None:
        """读取画像文件原始文本与存在状态。"""
        path = self.root / name
        return path.read_text(encoding="utf-8") if path.exists() else None

    def _write_file(self, name: str, content: str) -> None:
        """写入画像文件内容。"""
        rendered = content.strip() + ("\n" if content.strip() else "")
        self._write_rendered_file(name, rendered)

    def _write_rendered_file(self, name: str, rendered: str) -> None:
        """写入已经完成渲染的画像文本。"""
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / name
        temporary = path.with_name(f".{path.name}.tmp")
        self._write_text(temporary, rendered)
        try:
            os.replace(temporary, path)
        except OSError as exc:
            if not _replace_blocked(exc):
                temporary.unlink(missing_ok=True)
                raise
            self._write_text(path, rendered)
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _write_text(path: Path, content: str) -> None:
        """安全写入文本文件。"""
        with path.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())


def profile_memory_system_contents(profile_memory: str | ProfileMemorySnapshot) -> list[str]:
    """兼容旧字符串输入并返回实际 system 注入内容。"""
    if isinstance(profile_memory, ProfileMemorySnapshot):
        return profile_memory.system_contents()
    return [f"长期偏好：{profile_memory}"] if profile_memory else []


def _replace_blocked(error: OSError) -> bool:
    """判断是否需要直接写入回退。"""
    return isinstance(error, PermissionError) or error.errno in {errno.EACCES, errno.EBUSY, errno.EPERM}

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

from .types import SkillManifest
from .validator import SkillValidator


class SkillInstaller:
    """从受限 Git 源安装一个外部 Skill。"""

    def __init__(self, directory: Path, validator: SkillValidator) -> None:
        """保存正式目录和 Skill 校验器。"""
        self.directory = directory
        self.validator = validator

    async def install(self, source: str, ref: str | None, skill_path: str | None) -> SkillManifest:
        """克隆、校验并原子发布一个外部 Skill。"""
        self._validate_source(source, ref)
        staging_root = self.directory / ".staging" / str(uuid4())
        repository = staging_root / "repository"
        try:
            await self.clone(source, repository, ref)
            candidate = self._select_skill_directory(repository, skill_path)
            self._reject_symlinks(candidate)
            manifest = self.validator.validate(candidate)
            target = self.directory / manifest.name
            if target.exists():
                raise RuntimeError(f"Skill 已存在：{manifest.name}")
            staged_skill = staging_root / manifest.name
            shutil.copytree(candidate, staged_skill, ignore=shutil.ignore_patterns(".git"))
            shutil.move(str(staged_skill), str(target))
            return manifest
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)

    async def clone(self, source: str, destination: Path, ref: str | None) -> None:
        """以非 shell 方式浅克隆公开 HTTPS Git 仓库。"""
        command = ["git", "clone", "--depth", "1"]
        if ref:
            command.extend(["--branch", ref])
        command.extend([source, str(destination)])
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await process.communicate()
        if process.returncode != 0:
            detail = stderr.decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"Git 克隆失败：{detail or process.returncode}")

    @staticmethod
    def _validate_source(source: str, ref: str | None) -> None:
        """限制安装源为无凭据的 HTTPS Git URL。"""
        parsed = urlparse(source)
        if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
            raise RuntimeError("Skill 安装仅支持 HTTPS Git URL")
        if ref is not None and (not ref.strip() or ref.startswith("-")):
            raise RuntimeError("Skill Git ref 无效")

    def _select_skill_directory(self, repository: Path, skill_path: str | None) -> Path:
        """选择用户指定或仓库内唯一的 Skill 目录。"""
        if skill_path:
            candidate = (repository / skill_path).resolve()
            if not candidate.is_dir() or not candidate.is_relative_to(repository.resolve()):
                raise RuntimeError("Skill 路径必须位于已克隆仓库内")
            return candidate
        candidates = [path.parent for path in repository.rglob("SKILL.md") if ".git" not in path.parts]
        if len(candidates) != 1:
            raise RuntimeError("仓库包含多个或没有 Skill，请指定 skill_path")
        return candidates[0]

    @staticmethod
    def _reject_symlinks(directory: Path) -> None:
        """拒绝 Skill 目录中的符号链接，避免安装时越界复制。"""
        if any(path.is_symlink() for path in directory.rglob("*") if ".git" not in path.parts):
            raise RuntimeError("Skill 目录不能包含符号链接")

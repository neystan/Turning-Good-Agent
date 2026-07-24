from __future__ import annotations

import shutil
from pathlib import Path

from ..config.settings import SkillsSettings
from ..sessions.token_counter import count_content_tokens
from .creator import SkillCreator
from .installer import SkillInstaller
from .types import LoadedSkill, SkillCatalogEntry, SkillManifest, SkillScanError
from .validator import SkillValidator


class SkillManager:
    """管理唯一 skills 目录的扫描、加载和发布。"""

    def __init__(self, directory: Path, settings: SkillsSettings) -> None:
        """保存唯一目录、配置和内存 Catalog。"""
        self.directory = directory
        self.settings = settings
        self.validator = SkillValidator()
        self.creator = SkillCreator(self.validator)
        self.installer = SkillInstaller(directory, self.validator)
        self._manifests: dict[str, SkillManifest] = {}
        self.errors: list[SkillScanError] = []

    @property
    def catalog_token_count(self) -> int:
        """返回全量 Skill Catalog 的 token 数。"""
        return sum(count_content_tokens(f"{item.name}：{item.description}") for item in self.list_skills())

    def scan(self) -> None:
        """扫描正式目录并原子替换有效 Catalog。"""
        self.directory.mkdir(parents=True, exist_ok=True)
        candidates: dict[str, list[SkillManifest]] = {}
        errors: list[SkillScanError] = []
        for child in sorted(self.directory.iterdir(), key=lambda path: path.name):
            if not child.is_dir() or child.name == ".drafts":
                continue
            try:
                manifest = self.validator.validate(child)
            except (OSError, ValueError) as exc:
                errors.append(SkillScanError(child.name, str(exc)))
                continue
            candidates.setdefault(manifest.name, []).append(manifest)
        manifests: dict[str, SkillManifest] = {}
        for name, matched in candidates.items():
            if len(matched) == 1:
                manifests[name] = matched[0]
                continue
            errors.extend(SkillScanError(item.path.parent.name, f"重复的 Skill name：{name}") for item in matched)
        self._manifests = manifests
        self.errors = errors

    def list_skills(self) -> list[SkillCatalogEntry]:
        """返回按名称稳定排序的有效 Catalog。"""
        return [
            SkillCatalogEntry(item.name, item.description, item.extra_metadata)
            for item in sorted(self._manifests.values(), key=lambda manifest: manifest.name)
        ]

    def load(self, name: str) -> LoadedSkill:
        """读取一个完整 Skill 并构造当前轮 system 附件。"""
        manifest = self._manifests.get(name)
        if manifest is None:
            raise RuntimeError(f"Skill 不存在或无效：{name}")
        token_count = count_content_tokens(manifest.body)
        if token_count > self.settings.max_skill_tokens:
            raise RuntimeError(f"Skill {name} 超过 {self.settings.max_skill_tokens} tokens 限制")
        return LoadedSkill(name, manifest.body, token_count)

    async def create_draft(self, name: str, description: str, instructions: str) -> None:
        """创建候选 Skill 草稿，拒绝覆盖正式或草稿目录。"""
        if name in self._manifests or (self.directory / name).exists():
            raise RuntimeError(f"Skill 已存在：{name}")
        self.creator.create(self.directory / ".drafts", name, description, instructions)

    async def publish_draft(self, name: str) -> None:
        """再次校验并将草稿移动为正式 Skill。"""
        draft = self.directory / ".drafts" / name
        target = self.directory / name
        if target.exists() or name in self._manifests:
            raise RuntimeError(f"Skill 已存在：{name}")
        try:
            self.validator.validate(draft)
        except (OSError, ValueError) as exc:
            raise RuntimeError(f"Skill 草稿无效：{exc}") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(draft), str(target))
        self.scan()
        if name not in self._manifests:
            raise RuntimeError(f"Skill 发布后校验失败：{name}")

    async def install(self, source: str, ref: str | None, skill_path: str | None) -> SkillManifest:
        """安装外部 Skill 并刷新内存 Catalog。"""
        manifest = await self.installer.install(source, ref, skill_path)
        self.scan()
        if manifest.name not in self._manifests:
            raise RuntimeError(f"Skill 安装后校验失败：{manifest.name}")
        return manifest

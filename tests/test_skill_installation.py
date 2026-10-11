import asyncio
from importlib import import_module
from pathlib import Path

import pytest


SkillManager = import_module("Turning-Good-Agent.skills.manager").SkillManager
SkillsSettings = import_module("Turning-Good-Agent.config.settings").SkillsSettings
InstallSkillTool = import_module("Turning-Good-Agent.skills.install_skill_tool").InstallSkillTool


def write_skill(root: Path, name: str) -> None:
    """创建一个可安装的外部 Skill 夹具。"""
    target = root / name
    target.mkdir(parents=True)
    (target / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: 外部 Skill\n---\n\n执行外部工作流。\n",
        encoding="utf-8",
    )


def test_install_tool_requires_approval_and_manager_refreshes_catalog(tmp_path, monkeypatch):
    """验证安装 Tool 审批属性及安装后的 Catalog 刷新。"""
    source = tmp_path / "source"
    write_skill(source, "external-review")
    manager = SkillManager(tmp_path / ".skills", SkillsSettings())
    manager.scan()

    async def fake_clone(source_url, destination, ref):
        """用本地目录代替 Git 克隆。"""
        del source_url, ref
        destination.mkdir(parents=True)
        import shutil

        shutil.copytree(source / "external-review", destination / "external-review")

    monkeypatch.setattr(manager.installer, "clone", fake_clone)
    result = asyncio.run(InstallSkillTool(manager).run({"source": "https://example.com/repo.git"}))

    assert InstallSkillTool(manager).approval_required is True
    assert result.content == "Skill 已安装：external-review"
    assert [item.name for item in manager.list_skills()] == ["external-review"]


def test_install_rejects_non_https_source_and_conflicts(tmp_path, monkeypatch):
    """验证安装不会接受本地路径或覆盖已有 Skill。"""
    manager = SkillManager(tmp_path / ".skills", SkillsSettings())
    manager.scan()

    with pytest.raises(RuntimeError, match="仅支持 HTTPS Git URL"):
        asyncio.run(manager.install("file:///tmp/skill", None, None))

    write_skill(tmp_path / ".skills", "external-review")
    manager.scan()

    async def fake_clone(source_url, destination, ref):
        """提供与现有目录同名的候选 Skill。"""
        del source_url, ref
        write_skill(destination, "external-review")

    monkeypatch.setattr(manager.installer, "clone", fake_clone)
    with pytest.raises(RuntimeError, match="Skill 已存在"):
        asyncio.run(manager.install("https://example.com/repo.git", None, None))

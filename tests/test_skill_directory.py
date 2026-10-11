import asyncio
from importlib import import_module


AgentRuntime = import_module("Turning-Good-Agent.runtime.runtime").AgentRuntime
Settings = import_module("Turning-Good-Agent.config.settings").Settings


def test_default_skill_directory_is_hidden_project_directory(tmp_path, monkeypatch):
    """验证 Runtime 只从项目根目录 .skills 扫描 Catalog。"""
    monkeypatch.chdir(tmp_path)
    skill = tmp_path / ".skills" / "sample"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: sample\ndescription: 用于测试隐藏目录。\n---\n\n执行检查。\n",
        encoding="utf-8",
    )
    runtime = AgentRuntime.create_default(Settings(data_dir=tmp_path), object())
    asyncio.run(runtime.start())

    assert runtime.settings.skills.directory == ".skills"
    assert [item.name for item in runtime.skills.list_skills()] == ["sample"]

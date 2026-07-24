from pathlib import Path

from .validator import SkillValidator


class SkillCreator:
    """创建待发布的本地 Skill 草稿。"""

    def __init__(self, validator: SkillValidator) -> None:
        """保存草稿校验器。"""
        self.validator = validator

    def create(self, drafts_dir: Path, name: str, description: str, instructions: str) -> None:
        """写入并校验一个新的草稿目录。"""
        target = drafts_dir / name
        if target.exists():
            raise RuntimeError(f"Skill 草稿已存在：{name}")
        target.mkdir(parents=True)
        skill_file = target / "SKILL.md"
        skill_file.write_text(self._render(name, description, instructions), encoding="utf-8")
        try:
            self.validator.validate(target)
        except Exception:
            skill_file.unlink(missing_ok=True)
            target.rmdir()
            raise

    @staticmethod
    def _render(name: str, description: str, instructions: str) -> str:
        """渲染兼容格式的 SKILL.md。"""
        return f"---\nname: {name}\ndescription: {description}\n---\n\n{instructions.strip()}\n"

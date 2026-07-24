from typing import Any

from ..tools.base import ToolResult
from .manager import SkillManager


class CreateSkillDraftTool:
    """创建需要用户审批的 Skill 草稿。"""

    name = "create_skill_draft"
    description = "根据用户明确要求创建一个本地 Skill 草稿。"
    parallel_safe = False
    approval_required = True
    input_schema = {
        "type": "object",
        "properties": {
            "name": {"type": "string", "description": "小写连字符 Skill 名称"},
            "description": {"type": "string", "description": "Skill 简短描述"},
            "instructions": {"type": "string", "description": "SKILL.md 正文"},
        },
        "required": ["name", "description", "instructions"],
    }

    def __init__(self, manager: SkillManager) -> None:
        """保存唯一 Skill 管理器。"""
        self.manager = manager

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """写入已校验的草稿文件。"""
        await self.manager.create_draft(str(args["name"]), str(args["description"]), str(args["instructions"]))
        return ToolResult(f"Skill 草稿已创建：{args['name']}")


class PublishSkillDraftTool:
    """发布需要用户审批的 Skill 草稿。"""

    name = "publish_skill_draft"
    description = "根据用户明确要求发布一个已校验的 Skill 草稿。"
    parallel_safe = False
    approval_required = True
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Skill 草稿名称"}},
        "required": ["name"],
    }

    def __init__(self, manager: SkillManager) -> None:
        """保存唯一 Skill 管理器。"""
        self.manager = manager

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """校验并发布草稿到正式目录。"""
        await self.manager.publish_draft(str(args["name"]))
        return ToolResult(f"Skill 已发布：{args['name']}")

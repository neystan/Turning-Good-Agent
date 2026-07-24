from typing import Any

from ..tools.base import ToolResult
from .manager import SkillManager


class InstallSkillTool:
    """安装需要用户审批的外部 Skill。"""

    name = "install_skill"
    description = "仅在用户明确要求时，从 HTTPS Git 仓库安装并校验一个 Skill。"
    parallel_safe = False
    approval_required = True
    input_schema = {
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "HTTPS Git 仓库 URL"},
            "ref": {"type": "string", "description": "可选 Git 分支或标签"},
            "skill_path": {"type": "string", "description": "仓库内可选 Skill 目录"},
        },
        "required": ["source"],
    }

    def __init__(self, manager: SkillManager) -> None:
        """保存唯一 Skill 管理器。"""
        self.manager = manager

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """安装 Skill 并刷新当前 Catalog。"""
        manifest = await self.manager.install(
            str(args["source"]),
            str(args["ref"]) if args.get("ref") else None,
            str(args["skill_path"]) if args.get("skill_path") else None,
        )
        return ToolResult(f"Skill 已安装：{manifest.name}")

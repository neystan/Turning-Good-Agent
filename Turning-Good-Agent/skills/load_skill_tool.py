from typing import Any

from ..context.system_prompt import build_loaded_skill_prompt
from ..tools.base import ToolResult
from ..tools.context_attachment import ContextAttachment
from ..sessions.token_counter import count_content_tokens
from .manager import SkillManager


class LoadSkillTool:
    """按需加载一个完整 Skill 到当前轮上下文。"""

    name = "load_skill"
    description = "加载一个已发现 Skill 的完整工作流指导，仅当前轮可见。"
    parallel_safe = False
    approval_required = False
    input_schema = {
        "type": "object",
        "properties": {"name": {"type": "string", "description": "Skill 名称"}},
        "required": ["name"],
    }

    def __init__(self, manager: SkillManager) -> None:
        """保存唯一 Skill 管理器。"""
        self.manager = manager

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """加载正文并返回受限当前轮附件。"""
        loaded = self.manager.load(str(args["name"]))
        content = build_loaded_skill_prompt(loaded.name, loaded.body)
        attachment = ContextAttachment(
            source=f"skill:{loaded.name}",
            messages=[{"role": "system", "content": content}],
            token_count=count_content_tokens(content),
            kind="skill",
            verified=True,
        )
        return ToolResult(
            f"已加载 Skill：{loaded.name}",
            metadata={"loaded_skill_name": loaded.name, "loaded_skill_token_count": loaded.token_count},
            context_attachment=attachment,
        )

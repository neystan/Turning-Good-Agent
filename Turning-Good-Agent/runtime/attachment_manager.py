import json
from typing import Any

from ..config.settings import RuntimeSettings, SkillsSettings
from ..sessions.token_counter import count_content_tokens
from ..tools.context_attachment import ContextAttachment, validate_context_attachment


class AttachmentManager:
    """管理当前 AgentLoop 的附件预算和角色边界。"""

    def __init__(
        self,
        runtime: RuntimeSettings,
        skills: SkillsSettings,
        mcp_token_limit: int,
        openai_tools: list[dict[str, object]],
    ) -> None:
        """保存单轮预算并固定本轮 Tool schema。"""
        self.runtime = runtime
        self.skills = skills
        self.mcp_token_limit = mcp_token_limit
        self.tool_schema_tokens = count_content_tokens(json.dumps(openai_tools, ensure_ascii=False, sort_keys=True))
        self.mcp_tokens = 0
        self.skill_names: list[str] = []
        self.skill_tokens = 0

    def check(
        self,
        attachment: ContextAttachment | object | None,
        record: dict[str, Any],
        working: list[dict[str, Any]],
    ) -> str | None:
        """校验待追加附件，失败时不改变单轮状态。"""
        return self._validate(attachment, record, working)

    def commit(
        self,
        attachment: ContextAttachment | object | None,
        record: dict[str, Any],
        working: list[dict[str, Any]],
    ) -> None:
        """提交已校验附件并更新本轮预算与观测。"""
        if attachment is None:
            return
        assert isinstance(attachment, ContextAttachment)
        if attachment.kind == "mcp":
            self.mcp_tokens += attachment.token_count
            working.extend(attachment.messages)
            return
        name = str(record["metadata"]["loaded_skill_name"])
        body_tokens = int(record["metadata"]["loaded_skill_token_count"])
        self.skill_names.append(name)
        self.skill_tokens += body_tokens
        working.extend(attachment.messages)

    def _validate(
        self,
        attachment: ContextAttachment | object | None,
        record: dict[str, Any],
        working: list[dict[str, Any]],
    ) -> str | None:
        """依次校验通用格式、类型预算与总上下文。"""
        if attachment is None:
            return None
        kind = getattr(attachment, "kind", "mcp")
        token_limit = self.mcp_token_limit if kind == "mcp" else self.runtime.max_context_tokens
        used_tokens = self.mcp_tokens if kind == "mcp" else 0
        error = validate_context_attachment(attachment, used_tokens, token_limit)
        if error is not None:
            return error
        assert isinstance(attachment, ContextAttachment)
        if attachment.kind == "skill":
            error = self._validate_skill(record, attachment)
            if error is not None:
                return error
        return self._validate_context_budget(attachment, working)

    def _validate_skill(self, record: dict[str, Any], attachment: ContextAttachment) -> str | None:
        """校验 Skill 数量、正文 token 和可信来源。"""
        metadata = record.get("metadata")
        if not isinstance(metadata, dict):
            return "Skill 附件缺少加载元数据"
        name = metadata.get("loaded_skill_name")
        body_tokens = metadata.get("loaded_skill_token_count")
        if not isinstance(name, str) or attachment.source != f"skill:{name}":
            return "Skill 附件来源无效"
        if not isinstance(body_tokens, int) or body_tokens < 0:
            return "Skill 附件 token 元数据无效"
        if len(self.skill_names) >= self.skills.max_loaded_skills_per_turn:
            return f"本轮最多加载 {self.skills.max_loaded_skills_per_turn} 个 Skill"
        if body_tokens > self.skills.max_skill_tokens:
            return f"单个 Skill 超过 {self.skills.max_skill_tokens} tokens 限制"
        if self.skill_tokens + body_tokens > self.skills.max_loaded_skill_tokens_per_turn:
            return f"本轮已加载 Skill 总量超过 {self.skills.max_loaded_skill_tokens_per_turn} tokens 限制"
        return None

    def _validate_context_budget(self, attachment: ContextAttachment, working: list[dict[str, Any]]) -> str | None:
        """确保下一次模型请求仍在总上下文限制内。"""
        message_tokens = sum(count_content_tokens(str(message.get("content", ""))) for message in working)
        if message_tokens + self.tool_schema_tokens + attachment.token_count > self.runtime.max_context_tokens:
            return f"追加附件后上下文超过 {self.runtime.max_context_tokens} tokens 限制"
        return None

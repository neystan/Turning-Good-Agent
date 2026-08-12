from typing import Any

from .system_prompt import SkillCatalogItem, build_system_prompt, render_skill_catalog
from ..memory.long_term import ProfileMemorySnapshot, profile_memory_system_contents
from ..sessions.types import MessageRecord
from ..sessions.token_counter import count_content_tokens


WORKER_DEPENDENCY_MAX_CHARS = 12_000
WORKER_DEPENDENCY_MAX_TOKENS = 6_000


class ContextBuilder:
    """组装模型调用所需的消息列表。"""

    def build(
        self,
        summary: str,
        history: list[MessageRecord],
        user_content: str,
        profile_memory: str | ProfileMemorySnapshot,
        skills: list[SkillCatalogItem] | None = None,
    ) -> list[dict[str, Any]]:
        """构建 system、摘要、历史和当前用户消息。"""
        messages: list[dict[str, Any]] = [{"role": "system", "content": build_system_prompt(skills or [])}]
        messages.extend({"role": "system", "content": content} for content in profile_memory_system_contents(profile_memory))
        if summary:
            messages.append({"role": "system", "content": f"会话摘要：{summary}"})
        for item in history:
            messages.append({"role": item.role, "content": item.content})
        messages.append({"role": "user", "content": user_content})
        return messages

    # 组装不携带父历史的固定 Worker 消息。
    def build_worker(
        self,
        system_prompt: str,
        profile_memory: ProfileMemorySnapshot,
        skills: list[SkillCatalogItem],
        role: str,
        brief: str,
        dependency_content: str | None = None,
    ) -> list[dict[str, Any]]:
        if dependency_content is not None:
            if (
                not isinstance(dependency_content, str)
                or len(dependency_content) > WORKER_DEPENDENCY_MAX_CHARS
                or count_content_tokens(dependency_content) > WORKER_DEPENDENCY_MAX_TOKENS
            ):
                raise ValueError("Worker 依赖 content 超出上限")
        messages: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
        messages.extend(
            {"role": "system", "content": content}
            for content in profile_memory.system_contents()
        )
        catalog = render_skill_catalog(skills)
        if catalog:
            messages.append({"role": "system", "content": "安全只读 Skill 元数据：" + catalog})
        task_content = f"Worker role：{role}\nWorker brief：{brief}"
        if dependency_content:
            task_content += f"\n\n紧邻前序有界 content：\n{dependency_content}"
        messages.append({"role": "user", "content": task_content})
        return messages

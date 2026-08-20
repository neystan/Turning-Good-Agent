import base64
import json
from pathlib import Path
from typing import Any

from .system_prompt import SkillCatalogItem, build_system_prompt, render_skill_catalog
from ..memory.long_term import ProfileMemorySnapshot, profile_memory_system_contents
from ..sessions.types import MessageRecord
from ..sessions.token_counter import count_content_tokens


WORKER_DEPENDENCY_MAX_CHARS = 12_000
WORKER_DEPENDENCY_MAX_TOKENS = 6_000


def _attachment_context(attachments: list[Any]) -> str:
    descriptors: list[dict[str, Any]] = []
    has_document = False
    for attachment in attachments:
        metadata = getattr(attachment, "metadata", None)
        attachment_id = getattr(metadata, "attachment_id", None)
        source = getattr(metadata, "source", None)
        if not isinstance(attachment_id, str) or source not in {"document", "image"}:
            continue
        has_document = has_document or source == "document"
        descriptors.append(
            {
                "attachment_id": attachment_id,
                "source": source,
                "filename": str(getattr(metadata, "filename", "")),
                "mime_type": str(getattr(metadata, "mime_type", "")),
                "size_bytes": int(getattr(metadata, "size_bytes", 0)),
            }
        )
    if not descriptors:
        return ""
    instruction = (
        "文档内容未自动注入；需要文档内容时调用 read_attachment，并原样传入对应 attachment_id。"
        if has_document
        else "图片内容已作为当前请求的多模态输入提供。"
    )
    return "当前消息附件 metadata（不含本地路径）：\n" + json.dumps(descriptors, ensure_ascii=False) + "\n" + instruction


class ContextBuilder:
    """组装模型调用所需的消息列表。"""

    def build(
        self,
        summary: str,
        history: list[MessageRecord],
        user_content: str,
        profile_memory: str | ProfileMemorySnapshot,
        skills: list[SkillCatalogItem] | None = None,
        attachments: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """构建 system、摘要、历史和当前用户消息。"""
        messages: list[dict[str, Any]] = [{"role": "system", "content": build_system_prompt(skills or [])}]
        messages.extend({"role": "system", "content": content} for content in profile_memory_system_contents(profile_memory))
        if summary:
            messages.append({"role": "system", "content": f"会话摘要：{summary}"})
        for item in history:
            messages.append({"role": item.role, "content": item.content})
        current_attachments = attachments or []
        attachment_context = _attachment_context(current_attachments)
        current_user_content = user_content
        if attachment_context:
            current_user_content = f"{user_content}\n\n{attachment_context}" if user_content else attachment_context
        image_parts: list[dict[str, Any]] = []
        for attachment in current_attachments:
            metadata = getattr(attachment, "metadata", None)
            path = getattr(attachment, "path", None)
            if getattr(metadata, "source", None) != "image" or not isinstance(path, Path):
                continue
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            image_parts.append({"type": "image_url", "image_url": {"url": f"data:{metadata.mime_type};base64,{encoded}"}})
        if image_parts:
            parts: list[dict[str, Any]] = []
            if current_user_content:
                parts.append({"type": "text", "text": current_user_content})
            parts.extend(image_parts)
            messages.append({"role": "user", "content": parts})
        else:
            messages.append({"role": "user", "content": current_user_content})
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

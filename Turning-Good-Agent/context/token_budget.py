from __future__ import annotations

import json

from .session_context import count_message_tokens
from .system_prompt import SkillCatalogItem, build_system_prompt, render_skill_catalog
from ..sessions.token_counter import count_content_tokens
from ..sessions.types import MessageRecord


def build_context_token_breakdown(
    *,
    summary: str,
    history: list[MessageRecord],
    current_input: str,
    output: str,
    profile_memory: str,
    openai_tools: list[dict[str, object]],
    include_current_turn: bool,
    skills: list[SkillCatalogItem] | None = None,
) -> dict[str, int]:
    """按实际模型输入构建统一 token 分解。"""
    skill_catalog_tokens = count_content_tokens(render_skill_catalog(skills or []))
    system_tokens = count_content_tokens(build_system_prompt([]))
    profile_memory_tokens = count_content_tokens(f"长期偏好：{profile_memory}") if profile_memory else 0
    summary_tokens = count_content_tokens(f"会话摘要：{summary}") if summary else 0
    history_tokens = count_message_tokens(history)
    current_input_tokens = count_content_tokens(current_input)
    output_tokens = count_content_tokens(output)
    tool_schema_tokens = (
        count_content_tokens(json.dumps(openai_tools, ensure_ascii=False, sort_keys=True)) if openai_tools else 0
    )
    current_context_tokens = (
        system_tokens
        + skill_catalog_tokens
        + profile_memory_tokens
        + summary_tokens
        + history_tokens
        + tool_schema_tokens
    )
    if include_current_turn:
        current_context_tokens += current_input_tokens + output_tokens
    return {
        "system_tokens": system_tokens,
        "skill_catalog_tokens": skill_catalog_tokens,
        "profile_memory_tokens": profile_memory_tokens,
        "summary_tokens": summary_tokens,
        "history_tokens": history_tokens,
        "current_input_tokens": current_input_tokens,
        "output_tokens": output_tokens,
        "tool_schema_tokens": tool_schema_tokens,
        "current_context_tokens": current_context_tokens,
    }


def build_save_context_token_breakdown(
    *,
    summary: str,
    uncompacted_history: list[MessageRecord],
    current_input: str,
    output: str,
    profile_memory: str,
    openai_tools: list[dict[str, object]],
    tool_count: int,
    skills: list[SkillCatalogItem] | None = None,
) -> dict[str, int]:
    """按最终未压缩历史生成 SAVE 上下文观测。"""
    include_current_turn = _has_current_turn(uncompacted_history, current_input, output)
    history = uncompacted_history[:-2] if include_current_turn else uncompacted_history
    breakdown = build_context_token_breakdown(
        summary=summary,
        history=history,
        current_input=current_input,
        output=output,
        profile_memory=profile_memory,
        openai_tools=openai_tools,
        include_current_turn=include_current_turn,
        skills=skills,
    )
    breakdown["tool_count"] = tool_count
    return breakdown


def _has_current_turn(history: list[MessageRecord], current_input: str, output: str) -> bool:
    """判断最终未压缩历史是否保留本轮完整对话。"""
    if len(history) < 2:
        return False
    user, assistant = history[-2:]
    return (
        user.role == "user"
        and user.content == current_input
        and assistant.role == "assistant"
        and assistant.content == output
    )

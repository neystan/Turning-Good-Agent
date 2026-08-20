from __future__ import annotations

from ..config.settings import Settings
from .client import LLMProvider
from .openai_compatible import OpenAICompatibleLLM


OPENAI_COMPATIBLE_PROVIDER = "openai-compatible"


def build_llm(settings: Settings) -> LLMProvider:
    """根据统一配置创建当前支持的 LLM Provider。"""
    if settings.llm.provider != OPENAI_COMPATIBLE_PROVIDER:
        raise ValueError(f"不支持的 LLM Provider：{settings.llm.provider}")
    if not settings.llm.api_key:
        raise ValueError("使用 openai-compatible 时必须设置 api_key")
    if not settings.llm.model:
        raise ValueError("使用 openai-compatible 时必须设置 model")
    return OpenAICompatibleLLM(
        api_key=settings.llm.api_key,
        base_url=settings.llm.base_url,
        model=settings.llm.model,
        timeout_seconds=settings.llm.timeout_seconds,
        max_retries=settings.llm.max_retries,
        retry_delay_seconds=settings.llm.retry_delay_seconds,
        supports_vision=settings.llm.supports_vision,
    )

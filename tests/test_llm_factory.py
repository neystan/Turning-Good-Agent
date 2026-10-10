from __future__ import annotations

import importlib
import pytest


factory = importlib.import_module("Turning-Good-Agent.llm.factory")
settings_module = importlib.import_module("Turning-Good-Agent.config.settings")
provider_module = importlib.import_module("Turning-Good-Agent.llm.openai_compatible")


def test_build_llm_creates_openai_compatible_provider() -> None:
    settings = settings_module.Settings()
    settings.llm.api_key = "test-key"
    settings.llm.model = "test-model"

    provider = factory.build_llm(settings)

    assert isinstance(provider, provider_module.OpenAICompatibleLLM)


def test_settings_reject_non_boolean_vision_capability() -> None:
    settings = settings_module.Settings()
    settings.llm.supports_vision = "true"  # type: ignore[assignment]
    validate = importlib.import_module("Turning-Good-Agent.config.validate").validate_settings

    with pytest.raises(ValueError, match="llm.supports_vision"):
        validate(settings)

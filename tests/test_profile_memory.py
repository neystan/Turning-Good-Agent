from __future__ import annotations

import errno
import importlib

import pytest


builder_module = importlib.import_module("Turning-Good-Agent.context.builder")
memory_module = importlib.import_module("Turning-Good-Agent.memory.long_term")
settings_module = importlib.import_module("Turning-Good-Agent.config.settings")

ContextBuilder = builder_module.ContextBuilder
ProfileMemory = memory_module.ProfileMemory
ProfileMemoryLimitError = memory_module.ProfileMemoryLimitError
ProfileMemorySnapshot = memory_module.ProfileMemorySnapshot


def test_context_builder_injects_soul_then_user_before_session_summary() -> None:
    messages = ContextBuilder().build(
        summary="已有会话摘要",
        history=[],
        user_content="继续任务",
        profile_memory=ProfileMemorySnapshot(soul="Agent 已学会先验证。", user="用户偏好中文。"),
    )

    assert messages == [
        {"role": "system", "content": messages[0]["content"]},
        {"role": "system", "content": "Agent 长期记忆（SOUL）：\nAgent 已学会先验证。"},
        {"role": "system", "content": "用户长期记忆（USER）：\n用户偏好中文。"},
        {"role": "system", "content": "会话摘要：已有会话摘要"},
        {"role": "user", "content": "继续任务"},
    ]


def test_profile_memory_rejects_write_that_exceeds_fixed_soul_budget(tmp_path) -> None:
    settings = settings_module.Settings()
    memory = ProfileMemory(tmp_path, settings.proactive)

    with pytest.raises(ProfileMemoryLimitError) as raised:
        memory.write(ProfileMemorySnapshot(soul="Agent " * 5_000, user=""))

    assert str(raised.value) == "SOUL.md 超过 4000 tokens 限制"
    assert not (tmp_path / "memory" / "SOUL.md").exists()


def test_profile_memory_restores_both_files_when_second_replace_fails(tmp_path, monkeypatch) -> None:
    settings = settings_module.Settings()
    memory = ProfileMemory(tmp_path, settings.proactive)
    original = ProfileMemorySnapshot(soul="old soul", user="old user")
    memory.write(original)
    real_replace = memory_module.os.replace
    failed = False

    def fail_first_user_replace(source, target) -> None:
        nonlocal failed
        if not failed and target.name == "USER.md":
            failed = True
            raise OSError(errno.EIO, "simulated user replace failure")
        real_replace(source, target)

    monkeypatch.setattr(memory_module.os, "replace", fail_first_user_replace)

    with pytest.raises(OSError, match="simulated user replace failure"):
        memory.write(ProfileMemorySnapshot(soul="new soul", user="new user"))

    assert memory.read() == original

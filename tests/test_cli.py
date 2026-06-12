import sys
from importlib import import_module


def test_configure_readline_for_unicode_input_binds_expected_options(monkeypatch):
    """验证 CLI 会配置中文输入删除相关的 readline 选项。"""
    cli = import_module("Turning-Good-Agent.cli")
    calls: list[str] = []

    class FakeReadline:
        """记录 readline 绑定命令。"""

        def parse_and_bind(self, command: str) -> None:
            """保存每条绑定命令。"""
            calls.append(command)

    monkeypatch.setitem(sys.modules, "readline", FakeReadline())

    cli.configure_readline_for_unicode_input()

    assert calls == [
        "set bind-tty-special-chars off",
        "set input-meta on",
        "set output-meta on",
        "set convert-meta off",
    ]


def test_resolve_cli_session_id_is_ephemeral_without_explicit_session():
    """验证默认 CLI 会话不会跨进程复用固定 default。"""
    cli = import_module("Turning-Good-Agent.cli")

    first = cli.resolve_cli_session_id(None)
    second = cli.resolve_cli_session_id(None)

    assert first != second
    assert first.startswith("cli-")
    assert second.startswith("cli-")


def test_resolve_cli_session_id_preserves_explicit_session():
    """验证显式 session ID 可以继续恢复旧会话。"""
    cli = import_module("Turning-Good-Agent.cli")

    assert cli.resolve_cli_session_id("demo") == "demo"

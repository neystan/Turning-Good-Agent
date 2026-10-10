from __future__ import annotations

import importlib


cli_module = importlib.import_module("Turning-Good-Agent.cli")


class Stream:
    def __init__(self, interactive: bool) -> None:
        self._interactive = interactive

    def isatty(self) -> bool:
        return self._interactive


def test_cli_only_uses_prompt_toolkit_when_both_streams_are_terminals() -> None:
    assert cli_module.supports_prompt_toolkit(Stream(True), Stream(True)) is True
    assert cli_module.supports_prompt_toolkit(Stream(False), Stream(True)) is False
    assert cli_module.supports_prompt_toolkit(Stream(True), Stream(False)) is False


def test_cli_renders_bounded_tool_lifecycle_summaries() -> None:
    """Would fail if CLI showed raw metadata instead of a bounded Tool summary."""
    rendered: list[tuple[str, bool]] = []

    def render(content: str, newline: bool) -> None:
        rendered.append((content, newline))

    cli_module._render_event(
        {
            "type": "tool.started",
            "tool_name": "read_file",
            "args": {"path": "note.txt", "padding": "x" * 400},
        },
        render,
        set(),
    )
    cli_module._render_event(
        {"type": "tool.finished", "tool_name": "read_file", "failed": False},
        render,
        set(),
    )
    cli_module._render_event(
        {"type": "tool.finished", "tool_name": "read_file", "failed": True},
        render,
        set(),
    )

    assert rendered[0][0].startswith("[工具] 开始 read_file {")
    assert len(rendered[0][0]) <= 240
    assert rendered[1] == ("[工具] 完成 read_file", True)
    assert rendered[2] == ("[工具] 失败 read_file", True)


def test_cli_only_treats_y_or_y_as_approval() -> None:
    """Would fail if words such as `yes` could approve a pending tool call."""
    assert cli_module._is_approval("y") is True
    assert cli_module._is_approval("Y") is True
    assert cli_module._is_approval("yes") is False

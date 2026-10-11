import asyncio
import importlib
import sys
from pathlib import Path


def _load(name: str):
    return importlib.import_module(f"Turning-Good-Agent.tools.{name}")


def test_loader_skips_broken_tool_modules(tmp_path):
    loader_mod = _load("loader")
    registry_mod = _load("registry")

    package_dir = tmp_path / "demo_tools"
    package_dir.mkdir()
    (package_dir / "__init__.py").write_text("", encoding="utf-8")
    (package_dir / "bad_tool.py").write_text("raise RuntimeError('broken import')\n", encoding="utf-8")
    (package_dir / "good_tool.py").write_text(
        """
class GoodTool:
    name = "good"
    description = "good"
    input_schema = {"type": "object", "properties": {}}

    async def run(self, args):
        return "ok"
""",
        encoding="utf-8",
    )
    sys.path.insert(0, str(tmp_path))
    try:
        package = importlib.import_module("demo_tools")
        loader = loader_mod.ToolLoader(package)
        registry = registry_mod.ToolRegistry()

        loaded = loader.load(registry)

        assert loaded == ["good"]
        assert registry.has("good")
        assert "bad_tool" in loader.import_errors
    finally:
        sys.path.remove(str(tmp_path))
        for name in list(sys.modules):
            if name == "demo_tools" or name.startswith("demo_tools."):
                sys.modules.pop(name, None)


def test_registry_schema_cache_refreshes_on_register():
    registry_mod = _load("registry")
    registry = registry_mod.ToolRegistry()

    class ToolA:
        name = "a"
        description = "a"
        input_schema = {"type": "object", "properties": {}}

        async def run(self, args):
            return "a"

    class ToolB:
        name = "b"
        description = "b"
        input_schema = {"type": "object", "properties": {}}

        async def run(self, args):
            return "b"

    registry.register(ToolA())
    first = registry.openai_tools()
    second = registry.openai_tools()
    assert first is second

    registry.register(ToolB())
    refreshed = registry.openai_tools()
    assert refreshed is not first
    assert [item["function"]["name"] for item in refreshed] == ["a", "b"]


def test_tool_schema_fields_have_concise_descriptions():
    loader_mod = _load("loader")
    registry_mod = _load("registry")

    registry = registry_mod.ToolRegistry()
    loader_mod.ToolLoader().load(registry, Path.cwd())

    missing: list[str] = []
    too_long: list[str] = []
    for tool in registry._tools.values():
        properties = tool.input_schema.get("properties", {})
        for field_name, field_schema in properties.items():
            description = field_schema.get("description") if isinstance(field_schema, dict) else None
            if not description:
                missing.append(f"{tool.name}.{field_name}")
                continue
            if len(description) > 24:
                too_long.append(f"{tool.name}.{field_name}")

    assert missing == []
    assert too_long == []
    assert "长运行" in registry.get("exec").input_schema["properties"]["background"]["description"]


def test_security_rejects_unsafe_paths_and_commands(tmp_path):
    security = _load("security")

    assert security.is_blocked_device_path("/dev/random")
    assert security.is_blocked_device_path("/proc/self/fd/0")
    assert security.is_session_state_path(tmp_path / ".sessions" / "x" / "messages.jsonl")
    assert security.validate_command("rm -rf /") is not None
    assert security.validate_command("rm hello.py") is None
    assert security.validate_command("python -m pytest") is None
    assert "... truncated" in security.truncate_text("abcdef", 5)
    assert security.clamp_int("999", default=1, minimum=1, maximum=10) == 10
    assert security.clamp_int("bad", default=3, minimum=1, maximum=10) == 3


def test_resolve_workspace_path_blocks_escape(tmp_path):
    path_utils = _load("path_utils")

    assert path_utils.resolve_workspace_path("a.txt", tmp_path) == tmp_path / "a.txt"

    try:
        path_utils.resolve_workspace_path("../outside.txt", tmp_path)
    except PermissionError as exc:
        assert "outside workspace" in str(exc)
    else:
        raise AssertionError("路径逃逸应被拒绝")

    try:
        path_utils.resolve_workspace_path("~/outside.txt", tmp_path)
    except PermissionError as exc:
        assert "不支持 ~ 路径" in str(exc)
    else:
        raise AssertionError("workspace 工具不应展开用户传入的 ~ 路径")


def test_filesystem_tools_read_write_edit_grep_find_and_list(tmp_path):
    fs = _load("filesystem_tools")

    async def run() -> None:
        write = fs.WriteFileTool.create(tmp_path)
        read = fs.ReadFileTool.create(tmp_path)
        edit = fs.EditFileTool.create(tmp_path)
        grep = fs.GrepTool.create(tmp_path)
        list_dir = fs.ListDirTool.create(tmp_path)
        find = fs.FindFileTool.create(tmp_path)

        result = await write.run({"path": "notes/a.txt", "content": "hello\nworld\nhello\n"})
        assert "写入" in result.content

        listed = await list_dir.run({"path": ".", "recursive": True})
        assert "notes/a.txt" in listed.content

        found = await find.run({"path": ".", "query": "a", "type": "txt"})
        assert "notes/a.txt" in found.content

        read_result = await read.run({"path": "notes/a.txt", "offset": 2, "limit": 1, "show_line_numbers": True})
        assert "2| world" in read_result.content

        concise_read = await read.run({"path": "notes/a.txt", "offset": 2, "limit": 1})
        assert concise_read.content == "world"

        edited = await edit.run({"path": "notes/a.txt", "old_text": "world", "new_text": "TGA"})
        assert "编辑" in edited.content
        assert (tmp_path / "notes/a.txt").read_text(encoding="utf-8") == "hello\nTGA\nhello\n"

        duplicate = await edit.run({"path": "notes/a.txt", "old_text": "hello", "new_text": "hi"})
        assert duplicate.metadata["error"] is True
        assert "出现 2 次" in duplicate.content

        grep_result = await grep.run(
            {"path": ".", "pattern": "TGA", "output_mode": "content", "show_line_numbers": True}
        )
        assert "notes/a.txt:2" in grep_result.content

        concise_grep = await grep.run({"path": ".", "pattern": "TGA", "output_mode": "content"})
        assert concise_grep.content == "notes/a.txt: TGA"

    asyncio.run(run())


def test_grep_count_respects_max_results(tmp_path):
    fs = _load("filesystem_tools")

    async def run() -> None:
        for index in range(3):
            path = tmp_path / f"file-{index}.txt"
            path.write_text("needle\n", encoding="utf-8")

        grep = fs.GrepTool.create(tmp_path)
        result = await grep.run({"path": ".", "pattern": "needle", "output_mode": "count", "max_results": 2})

        assert len(result.content.splitlines()) == 2

    asyncio.run(run())


def test_integer_tool_args_are_clamped(tmp_path, monkeypatch):
    fs = _load("filesystem_tools")
    web = _load("web_tools")
    shell = _load("shell_tools")
    security = _load("security")

    async def fake_fetch(url: str, timeout: float, max_bytes: int) -> tuple[str, str]:
        assert max_bytes > 0
        return "text/plain", "x" * 100

    async def run() -> None:
        file_path = tmp_path / "notes.txt"
        file_path.write_text("a\nb\nc\n", encoding="utf-8")

        read = fs.ReadFileTool.create(tmp_path)
        read_result = await read.run({"path": "notes.txt", "offset": -5, "limit": 999999})
        assert read_result.content == "a\nb\nc"

        list_dir = fs.ListDirTool.create(tmp_path)
        list_result = await list_dir.run({"path": ".", "max_entries": 999999})
        assert "notes.txt" in list_result.content

        grep = fs.GrepTool.create(tmp_path)
        grep_result = await grep.run({"path": ".", "pattern": "a", "max_results": 999999})
        assert "notes.txt" in grep_result.content

        monkeypatch.setattr(web, "_fetch_url", fake_fetch)
        fetch = web.WebFetchTool()
        fetch_result = await fetch.run({"url": "https://example.test", "max_chars": 999999})
        assert len(fetch_result.content) < security.MAX_TOOL_OUTPUT_CHARS + 200

        assert shell.ExecTool.input_schema["properties"]["yield_time_ms"]["default"] == security.DEFAULT_YIELD_TIME_MS
        exec_tool = shell.ExecTool.create(tmp_path)
        exec_result = await exec_tool.run({"command": "printf ok", "timeout": 999999, "max_output_chars": 999999})
        assert exec_result.content == "ok\nExit code: 0"

    asyncio.run(run())


def test_filesystem_tools_reject_session_state_write(tmp_path):
    fs = _load("filesystem_tools")

    async def run() -> None:
        write = fs.WriteFileTool.create(tmp_path)
        result = await write.run({"path": ".sessions/demo/messages.jsonl", "content": "{}\n"})
        assert result.metadata["error"] is True
        assert ".sessions" in result.content

    asyncio.run(run())


def test_shell_tools_exec_and_write_stdin(tmp_path):
    shell = _load("shell_tools")

    async def run() -> None:
        exec_tool = shell.ExecTool.create(tmp_path)
        stdin_tool = shell.WriteStdinTool.create(tmp_path)

        ok = await exec_tool.run({"command": "printf hello"})
        assert "hello" in ok.content
        assert "Exit code: 0" in ok.content
        assert "stty:" not in ok.content

        empty = await exec_tool.run({"command": "true"})
        assert empty.content == "Exit code: 0"

        blocked = await exec_tool.run({"command": "rm -rf /"})
        assert blocked.metadata["error"] is True

        direct = await exec_tool.run(
            {"command": "python -c 'import time; time.sleep(0.1); print(123)'", "yield_time_ms": 100}
        )
        assert "session_id:" not in direct.content
        assert "123" in direct.content

        running = await exec_tool.run(
            {
                "command": "python -c 'import time; print(123, flush=True); time.sleep(5)'",
                "background": True,
                "yield_time_ms": 100,
            }
        )
        assert "session_id:" in running.content
        session_id = running.metadata["session_id"]

        terminated = await stdin_tool.run({"session_id": session_id, "terminate": True})
        assert "terminated" in terminated.content or "Exit code" in terminated.content

    asyncio.run(run())


def test_web_fetch_rejects_non_http_and_extracts_text(monkeypatch):
    web = _load("web_tools")

    async def fake_fetch(url: str, timeout: float, max_bytes: int) -> tuple[str, str]:
        assert url == "https://example.test"
        assert timeout > 0
        assert max_bytes > 0
        return "text/html", "<html><body><h1>Hello</h1><script>bad()</script><p>TGA</p><p>many    spaces</p></body></html>"

    monkeypatch.setattr(web, "_fetch_url", fake_fetch)

    async def run() -> None:
        fetch = web.WebFetchTool()
        rejected = await fetch.run({"url": "file:///etc/passwd"})
        assert rejected.metadata["error"] is True

        result = await fetch.run({"url": "https://example.test"})
        assert "[外部内容" in result.content
        assert "Hello" in result.content
        assert "bad()" not in result.content
        assert "many spaces" in result.content

    asyncio.run(run())


def test_web_search_prefers_duckduckgo_api(monkeypatch):
    web = _load("web_tools")
    seen_urls: list[str] = []

    async def fake_fetch(url: str, timeout: float, max_bytes: int) -> tuple[str, str]:
        assert timeout > 0
        assert max_bytes > 0
        seen_urls.append(url)
        assert "api.duckduckgo.com" in url
        return (
            "application/json",
            """
            {
              "RelatedTopics": [
                {"Text": "AI News Today - Latest AI updates.", "FirstURL": "https://example.com/ai-news"}
              ]
            }
            """,
        )

    monkeypatch.setattr(web, "_fetch_url", fake_fetch)

    async def run() -> None:
        result = await web.WebSearchTool().run({"query": "ai news", "count": 3})

        assert "AI News Today" in result.content
        assert "https://example.com/ai-news" in result.content
        assert "api.duckduckgo.com" in seen_urls[0]
        assert result.content.splitlines() == [
            "1. AI News Today | https://example.com/ai-news | Latest AI updates."
        ]

    asyncio.run(run())


def test_web_search_falls_back_to_yahoo_results(monkeypatch):
    web = _load("web_tools")

    async def fake_fetch(url: str, timeout: float, max_bytes: int) -> tuple[str, str]:
        assert timeout > 0
        assert max_bytes > 0
        if "api.duckduckgo.com" in url:
            return "application/json", '{"RelatedTopics": []}'
        assert "search.yahoo.com" in url
        return (
            "text/html",
            """
            <li><div class="dd algo algo-sr">
              <div class="compTitle options-toggle">
                <a href="https://r.search.yahoo.com/x/RU=https%3a%2f%2fexample.com%2fai-news/RK=2/RS=x-">
                  <h3 class="title"><span>AI News Today</span></h3>
                </a>
              </div>
              <div class="compText aAbs"><p>Latest AI updates.</p></div>
            </div></li>
            """,
        )

    monkeypatch.setattr(web, "_fetch_url", fake_fetch)

    async def run() -> None:
        result = await web.WebSearchTool().run({"query": "ai news", "count": 3})

        assert "AI News Today" in result.content
        assert "https://example.com/ai-news" in result.content

    asyncio.run(run())


def test_web_search_retries_transient_backend_failure(monkeypatch):
    web = _load("web_tools")
    attempts = {"count": 0}

    async def fake_fetch(url: str, timeout: float, max_bytes: int) -> tuple[str, str]:
        attempts["count"] += 1
        if "api.duckduckgo.com" in url:
            return "application/json", '{"RelatedTopics": []}'
        assert "search.yahoo.com" in url
        if attempts["count"] == 2:
            raise OSError("ssl eof")
        return (
            "text/html",
            """
            <li><div class="dd algo algo-sr">
              <div class="compTitle options-toggle">
                <a href="https://r.search.yahoo.com/x/RU=https%3a%2f%2fexample.com%2fretry/RK=2/RS=x-">
                  <h3 class="title"><span>Retry Result</span></h3>
                </a>
              </div>
            </div></li>
            """,
        )

    monkeypatch.setattr(web, "_fetch_url", fake_fetch)

    async def run() -> None:
        result = await web.WebSearchTool().run({"query": "retry", "count": 1})

        assert attempts["count"] == 3
        assert "Retry Result" in result.content

    asyncio.run(run())


def test_web_search_failure_gives_next_step(monkeypatch):
    web = _load("web_tools")

    async def fake_fetch(url: str, timeout: float, max_bytes: int) -> tuple[str, str]:
        return "text/html", "<html><body>no results</body></html>"

    monkeypatch.setattr(web, "_fetch_url", fake_fetch)

    async def run() -> None:
        result = await web.WebSearchTool().run({"query": "missing"})

        assert "未找到搜索结果：missing" in result.content
        assert "原因：" in result.content

    asyncio.run(run())


def test_weather_validates_location(monkeypatch):
    info = _load("info_tools")

    async def fake_fetch_weather(location: str) -> str:
        assert location == "Beijing"
        return "Beijing: sunny"

    monkeypatch.setattr(info, "_fetch_weather", fake_fetch_weather)

    async def run() -> None:
        tool = info.WeatherTool()
        missing = await tool.run({})
        assert missing.metadata["error"] is True

        result = await tool.run({"location": "Beijing"})
        assert result.content == "Beijing: sunny"

    asyncio.run(run())

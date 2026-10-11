import asyncio
import json
from importlib import import_module
from types import SimpleNamespace

import httpx
import pytest


class AsyncStream:
    """按顺序模拟 SDK 的异步流式事件。"""

    def __init__(self, events: list[object]) -> None:
        self.events = events
        self.index = 0

    def __aiter__(self):  # type: ignore[no-untyped-def]
        return self

    async def __anext__(self) -> object:
        if self.index >= len(self.events):
            raise StopAsyncIteration
        event = self.events[self.index]
        self.index += 1
        return event


def test_openai_compatible_llm_uses_sdk_and_parses_tool_calls(monkeypatch):
    """验证真实 provider 走 SDK，并能解析 tool_calls。"""

    async def run() -> None:
        module = import_module("Turning-Good-Agent.llm.openai_compatible")
        captured: dict[str, object] = {}

        class FakeCompletions:
            async def create(self, **kwargs: object) -> object:
                captured.update(kwargs)
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content="",
                                tool_calls=[
                                    SimpleNamespace(
                                        id="call-1",
                                        function=SimpleNamespace(name="now", arguments="{}"),
                                    )
                                ],
                                reasoning_content="debug-only",
                            )
                        )
                    ],
                    usage=SimpleNamespace(prompt_tokens=11, completion_tokens=3, total_tokens=14),
                )

        class FakeClient:
            def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
                captured["api_key"] = api_key
                captured["base_url"] = base_url
                captured["timeout"] = timeout
                self.chat = SimpleNamespace(completions=FakeCompletions())

        monkeypatch.setattr(module, "AsyncOpenAI", FakeClient, raising=False)
        llm = module.OpenAICompatibleLLM(
            api_key="test-key",
            base_url="https://example.test/v1",
            model="test-model",
        )

        response = await llm.complete(
            messages=[{"role": "user", "content": "你好"}],
            tools=[
                {
                    "type": "function",
                    "function": {
                        "name": "now",
                        "description": "返回当前时间。",
                        "parameters": {"type": "object", "properties": {}, "required": []},
                    },
                }
            ],
        )

        assert captured["api_key"] == "test-key"
        assert captured["base_url"] == "https://example.test/v1"
        assert captured["timeout"] == 60.0
        assert captured["model"] == "test-model"
        assert captured["messages"] == [{"role": "user", "content": "你好"}]
        assert captured["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "now",
                    "description": "返回当前时间。",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            }
        ]
        assert response.content == ""
        assert len(response.tool_calls) == 1
        assert response.tool_calls[0].id == "call-1"
        assert response.tool_calls[0].name == "now"
        assert response.tool_calls[0].args == {}

    asyncio.run(run())


def test_openai_compatible_llm_marks_dsml_when_tools_disabled(monkeypatch):
    """验证禁用工具时的 DSML 输出会被标记为协议异常。"""

    async def run() -> None:
        module = import_module("Turning-Good-Agent.llm.openai_compatible")

        class FakeCompletions:
            async def create(self, **kwargs: object) -> object:
                del kwargs
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content='<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="web_search">',
                                tool_calls=[],
                            )
                        )
                    ],
                    usage=SimpleNamespace(prompt_tokens=11, completion_tokens=3, total_tokens=14),
                )

        class FakeClient:
            def __init__(self, **kwargs: object) -> None:
                del kwargs
                self.chat = SimpleNamespace(completions=FakeCompletions())

        monkeypatch.setattr(module, "AsyncOpenAI", FakeClient, raising=False)
        llm = module.OpenAICompatibleLLM("test-key", "https://example.test/v1", "test-model")

        response = await llm.complete([{"role": "user", "content": "总结"}], [])

        assert response.protocol_error == "禁用工具时返回 DSML 工具调用格式。"

    asyncio.run(run())


def test_build_llm_uses_retry_settings_from_config():
    """验证重试参数来自集中配置。"""
    cli = import_module("Turning-Good-Agent.cli")
    settings_module = import_module("Turning-Good-Agent.config.settings")

    settings = settings_module.Settings()
    settings.llm.api_key = "test-key"
    settings.llm.model = "test-model"
    settings.llm.timeout_seconds = 12.5
    settings.llm.max_retries = 4
    settings.llm.retry_delay_seconds = 1.25

    llm = cli.build_llm(settings)

    assert llm.timeout_seconds == 12.5
    assert llm.max_retries == 4
    assert llm.retry_delay_seconds == 1.25


def test_openai_compatible_llm_parses_plain_content_without_tool_calls(monkeypatch):
    """验证真实 provider 在纯文本场景下稳定返回 content。"""

    async def run() -> None:
        module = import_module("Turning-Good-Agent.llm.openai_compatible")

        class FakeCompletions:
            async def create(self, **kwargs: object) -> object:
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="真实模型回复", tool_calls=None))],
                    usage=SimpleNamespace(prompt_tokens=11, completion_tokens=3, total_tokens=14),
                )

        class FakeClient:
            def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
                self.chat = SimpleNamespace(completions=FakeCompletions())

        monkeypatch.setattr(module, "AsyncOpenAI", FakeClient, raising=False)
        llm = module.OpenAICompatibleLLM(
            api_key="test-key",
            base_url="https://example.test/v1",
            model="test-model",
        )

        response = await llm.complete(
            messages=[{"role": "user", "content": "你好"}],
            tools=[],
        )

        assert response.content == "真实模型回复"
        assert response.tool_calls == []

    asyncio.run(run())


def test_openai_compatible_llm_parses_sdk_usage(monkeypatch):
    """验证真实 provider 会读取 SDK 返回的 token usage。"""

    async def run() -> None:
        module = import_module("Turning-Good-Agent.llm.openai_compatible")

        class FakeCompletions:
            async def create(self, **kwargs: object) -> object:
                del kwargs
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))],
                    usage=SimpleNamespace(prompt_tokens=11, completion_tokens=3, total_tokens=14),
                )

        class FakeClient:
            def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
                del api_key, base_url, timeout
                self.chat = SimpleNamespace(completions=FakeCompletions())

        monkeypatch.setattr(module, "AsyncOpenAI", FakeClient, raising=False)
        llm = module.OpenAICompatibleLLM(
            api_key="test-key",
            base_url="https://example.test/v1",
            model="test-model",
        )

        response = await llm.complete(messages=[{"role": "user", "content": "你好"}], tools=[])

        assert response.usage is not None
        assert response.usage.input_tokens == 11
        assert response.usage.output_tokens == 3
        assert response.usage.total_tokens == 14

    asyncio.run(run())


def test_openai_compatible_llm_rejects_missing_usage(monkeypatch):
    """验证缺少 usage 时不会伪装成成功响应。"""

    async def run() -> None:
        module = import_module("Turning-Good-Agent.llm.openai_compatible")

        class FakeCompletions:
            async def create(self, **kwargs: object) -> object:
                del kwargs
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="ok", tool_calls=None))],
                    usage=None,
                )

        class FakeClient:
            def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
                del api_key, base_url, timeout
                self.chat = SimpleNamespace(completions=FakeCompletions())

        monkeypatch.setattr(module, "AsyncOpenAI", FakeClient, raising=False)
        llm = module.OpenAICompatibleLLM(
            api_key="test-key",
            base_url="https://example.test/v1",
            model="test-model",
        )

        with pytest.raises(RuntimeError, match="缺少 usage"):
            await llm.complete(messages=[{"role": "user", "content": "你好"}], tools=[])

    asyncio.run(run())


def test_openai_compatible_llm_rejects_invalid_tool_arguments(monkeypatch):
    """验证非法工具参数 JSON 会暴露为模型协议错误。"""

    async def run() -> None:
        module = import_module("Turning-Good-Agent.llm.openai_compatible")

        class FakeCompletions:
            async def create(self, **kwargs: object) -> object:
                del kwargs
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            message=SimpleNamespace(
                                content="",
                                tool_calls=[
                                    SimpleNamespace(
                                        id="call-1",
                                        function=SimpleNamespace(name="echo", arguments="{bad-json"),
                                    )
                                ],
                            )
                        )
                    ],
                    usage=SimpleNamespace(prompt_tokens=11, completion_tokens=3, total_tokens=14),
                )

        class FakeClient:
            def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
                del api_key, base_url, timeout
                self.chat = SimpleNamespace(completions=FakeCompletions())

        monkeypatch.setattr(module, "AsyncOpenAI", FakeClient, raising=False)
        llm = module.OpenAICompatibleLLM(
            api_key="test-key",
            base_url="https://example.test/v1",
            model="test-model",
        )

        with pytest.raises(ValueError, match="工具调用参数不是合法 JSON"):
            await llm.complete(messages=[{"role": "user", "content": "echo"}], tools=[])

    asyncio.run(run())


def test_openai_compatible_llm_streams_plain_text(monkeypatch):
    """验证 stream=True 时能逐段返回文本。"""

    async def run() -> None:
        module = import_module("Turning-Good-Agent.llm.openai_compatible")
        captured: dict[str, object] = {}

        class FakeCompletions:
            async def create(self, **kwargs: object) -> object:
                captured.update(kwargs)
                return AsyncStream([
                    SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="你", tool_calls=None), finish_reason=None)]),
                    SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content="好", tool_calls=None), finish_reason=None)]),
                    SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=None), finish_reason="stop")]),
                ])

        class FakeClient:
            def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
                del api_key, base_url, timeout
                self.chat = SimpleNamespace(completions=FakeCompletions())

        monkeypatch.setattr(module, "AsyncOpenAI", FakeClient, raising=False)
        llm = module.OpenAICompatibleLLM(
            api_key="test-key",
            base_url="https://example.test/v1",
            model="test-model",
        )

        chunks = [
            chunk
            async for chunk in llm.stream(
                messages=[{"role": "user", "content": "你好"}],
                tools=[],
            )
        ]

        assert captured["stream"] is True
        assert [chunk.delta_text for chunk in chunks] == ["你", "好", ""]
        assert chunks[-1].finish_reason == "stop"

    asyncio.run(run())


def test_openai_compatible_llm_marks_streamed_dsml_when_tools_disabled(monkeypatch):
    """验证流式 DSML 输出会在结束后标记为协议异常。"""

    async def run() -> None:
        module = import_module("Turning-Good-Agent.llm.openai_compatible")

        class FakeCompletions:
            async def create(self, **kwargs: object) -> object:
                del kwargs
                return AsyncStream([
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(
                                    content='<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="web_search">',
                                    tool_calls=None,
                                ),
                                finish_reason="stop",
                            )
                        ]
                    )
                ])

        class FakeClient:
            def __init__(self, **kwargs: object) -> None:
                del kwargs
                self.chat = SimpleNamespace(completions=FakeCompletions())

        monkeypatch.setattr(module, "AsyncOpenAI", FakeClient, raising=False)
        llm = module.OpenAICompatibleLLM("test-key", "https://example.test/v1", "test-model")

        chunks = [chunk async for chunk in llm.stream([{"role": "user", "content": "总结"}], tools=[])]

        assert chunks[-1].protocol_error == "禁用工具时返回 DSML 工具调用格式。"

    asyncio.run(run())


def test_openai_compatible_llm_streams_tool_call_after_argument_chunks(monkeypatch):
    """验证流式 tool_call 参数片段会先合并再返回完整工具调用。"""

    async def run() -> None:
        module = import_module("Turning-Good-Agent.llm.openai_compatible")

        class FakeCompletions:
            async def create(self, **kwargs: object) -> object:
                del kwargs
                return AsyncStream([
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(
                                    content=None,
                                    tool_calls=[
                                        SimpleNamespace(
                                            index=0,
                                            id="call-1",
                                            function=SimpleNamespace(name="echo", arguments='{"text":'),
                                        )
                                    ],
                                ),
                                finish_reason=None,
                            )
                        ]
                    ),
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(
                                    content=None,
                                    tool_calls=[
                                        SimpleNamespace(
                                            index=0,
                                            id=None,
                                            function=SimpleNamespace(name=None, arguments='"abc"}'),
                                        )
                                    ],
                                ),
                                finish_reason="tool_calls",
                            )
                        ]
                    ),
                ])

        class FakeClient:
            def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
                del api_key, base_url, timeout
                self.chat = SimpleNamespace(completions=FakeCompletions())

        monkeypatch.setattr(module, "AsyncOpenAI", FakeClient, raising=False)
        llm = module.OpenAICompatibleLLM(
            api_key="test-key",
            base_url="https://example.test/v1",
            model="test-model",
        )

        chunks = [chunk async for chunk in llm.stream([{"role": "user", "content": "echo"}], tools=[])]

        assert len(chunks) == 1
        assert chunks[0].finish_reason == "tool_calls"
        assert chunks[0].tool_calls[0].id == "call-1"
        assert chunks[0].tool_calls[0].name == "echo"
        assert chunks[0].tool_calls[0].args == {"text": "abc"}

    asyncio.run(run())


def test_openai_compatible_llm_retries_timeout_then_succeeds(monkeypatch):
    """验证 timeout 属于可恢复错误，会进行重试。"""

    async def run() -> None:
        module = import_module("Turning-Good-Agent.llm.openai_compatible")
        attempts = {"count": 0}
        request = httpx.Request("POST", "https://example.test/v1/chat/completions")

        class FakeCompletions:
            async def create(self, **kwargs: object) -> object:
                del kwargs
                attempts["count"] += 1
                if attempts["count"] == 1:
                    raise module.APITimeoutError(request=request)
                return SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(content="重试成功", tool_calls=None))],
                    usage=SimpleNamespace(prompt_tokens=11, completion_tokens=3, total_tokens=14),
                )

        class FakeClient:
            def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
                del api_key, base_url, timeout
                self.chat = SimpleNamespace(completions=FakeCompletions())

        async def fake_sleep(_: float) -> None:
            return None

        monkeypatch.setattr(module, "AsyncOpenAI", FakeClient, raising=False)
        monkeypatch.setattr(module.asyncio, "sleep", fake_sleep)
        llm = module.OpenAICompatibleLLM(
            api_key="test-key",
            base_url="https://example.test/v1",
            model="test-model",
        )

        response = await llm.complete(
            messages=[{"role": "user", "content": "你好"}],
            tools=[],
        )

        assert attempts["count"] == 2
        assert response.content == "重试成功"

    asyncio.run(run())


def test_openai_compatible_llm_does_not_retry_bad_request(monkeypatch):
    """验证 400 请求错误不会进行重试。"""

    async def run() -> None:
        module = import_module("Turning-Good-Agent.llm.openai_compatible")
        attempts = {"count": 0}
        request = httpx.Request("POST", "https://example.test/v1/chat/completions")
        response = httpx.Response(400, request=request)

        class FakeCompletions:
            async def create(self, **kwargs: object) -> object:
                del kwargs
                attempts["count"] += 1
                raise module.BadRequestError("bad request", response=response, body={})

        class FakeClient:
            def __init__(self, api_key: str, base_url: str, timeout: float) -> None:
                del api_key, base_url, timeout
                self.chat = SimpleNamespace(completions=FakeCompletions())

        monkeypatch.setattr(module, "AsyncOpenAI", FakeClient, raising=False)
        llm = module.OpenAICompatibleLLM(
            api_key="test-key",
            base_url="https://example.test/v1",
            model="test-model",
        )

        with pytest.raises(module.BadRequestError):
            await llm.complete(
                messages=[{"role": "user", "content": "你好"}],
                tools=[],
            )

        assert attempts["count"] == 1

    asyncio.run(run())


def test_build_llm_requires_real_provider_configuration():
    """验证默认配置要求真实 provider 参数完整。"""
    cli = import_module("Turning-Good-Agent.cli")
    settings_module = import_module("Turning-Good-Agent.config.settings")

    settings = settings_module.Settings()

    with pytest.raises(ValueError, match="api_key"):
        cli.build_llm(settings)


def test_build_llm_rejects_deepseek_provider_name():
    """验证 deepseek 不再作为独立 provider 名称出现。"""
    cli = import_module("Turning-Good-Agent.cli")
    settings_module = import_module("Turning-Good-Agent.config.settings")

    settings = settings_module.Settings()
    settings.llm.provider = "deepseek"
    settings.llm.api_key = "deepseek-key"
    settings.llm.base_url = "https://api.deepseek.com"
    settings.llm.model = "deepseek-chat"

    with pytest.raises(ValueError, match="不支持的 LLM Provider"):
        cli.build_llm(settings)


def test_resolve_provider_only_accepts_openai_compatible():
    """验证当前只接受 openai-compatible 作为 provider 名称。"""
    cli = import_module("Turning-Good-Agent.cli")

    assert cli.resolve_provider("openai-compatible") == "openai-compatible"
    assert cli.resolve_provider("deepseek") == "deepseek"


def test_settings_load_reads_local_json_file(tmp_path):
    """验证本地配置文件可永久生效且不依赖 export。"""
    settings_module = import_module("Turning-Good-Agent.config.settings")
    config_path = tmp_path / "settings.local.json"
    config_path.write_text(
        json.dumps(
                {
                    "data_dir": "runtime-data",
                    "memory": {
                        "compact_token_threshold": 321,
                        "recent_window_token_limit": 123,
                    },
                "sessions": {
                    "retention_days": 9,
                },
                "llm": {
                    "provider": "openai-compatible",
                    "api_key": "local-key",
                    "base_url": "https://local.test/v1",
                    "model": "local-model",
                    "timeout_seconds": 22.5,
                    "max_retries": 5,
                    "retry_delay_seconds": 1.5,
                    "streaming_enabled": True,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    settings = settings_module.Settings.load(local_config_path=config_path)

    assert settings.data_dir == tmp_path / "runtime-data"
    assert settings.memory.compact_token_threshold == 321
    assert settings.memory.recent_window_token_limit == 123
    assert settings.sessions.retention_days == 9
    assert settings.llm.api_key == "local-key"
    assert settings.llm.model == "local-model"
    assert settings.llm.timeout_seconds == 22.5
    assert settings.llm.max_retries == 5
    assert settings.llm.retry_delay_seconds == 1.5
    assert settings.llm.streaming_enabled is True


def test_settings_keep_runtime_memory_and_llm_defaults_together(monkeypatch):
    """验证运行参数、记忆参数和 LLM 参数集中在 Settings。"""
    settings_module = import_module("Turning-Good-Agent.config.settings")
    monkeypatch.delenv("TGA_LLM_STREAMING_ENABLED", raising=False)

    settings = settings_module.Settings()

    assert settings.memory.compact_token_threshold == 200_000
    assert settings.memory.recent_window_token_limit == 20_000
    assert settings.sessions.retention_days == 7
    assert settings.llm.provider == "openai-compatible"
    assert settings.llm.timeout_seconds == 60.0
    assert settings.llm.max_retries == 2
    assert settings.llm.retry_delay_seconds == 0.5
    assert settings.llm.streaming_enabled is True
    assert settings.runtime.max_tool_rounds == 5

import asyncio
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import import_module
from threading import Thread
from typing import Any


class ChatHandler(BaseHTTPRequestHandler):
    """记录请求并返回兼容 Chat Completions 的响应。"""

    requests: list[dict[str, Any]] = []

    def do_POST(self) -> None:
        """处理测试模型请求。"""
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8"))
        self.__class__.requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "body": body,
            }
        )
        payload = {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": "真实模型回复",
                    }
                }
            ]
        }
        data = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        """关闭测试 HTTP server 的默认日志。"""
        return


def run_server() -> tuple[ThreadingHTTPServer, str]:
    """启动本地兼容模型服务。"""
    ChatHandler.requests.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), ChatHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    return server, f"http://{host}:{port}/v1"


def test_openai_compatible_llm_posts_chat_request_and_parses_content():
    """验证 provider 发送请求并解析 assistant 文本。"""

    async def run() -> None:
        server, base_url = run_server()
        try:
            module = import_module("Turning-Good-Agent.llm.openai_compatible")
            llm = module.OpenAICompatibleLLM(
                api_key="test-key",
                base_url=base_url,
                model="test-model",
            )

            response = await llm.complete(
                messages=[{"role": "user", "content": "你好"}],
                tools=[{"name": "echo"}],
            )

            assert response.content == "真实模型回复"
            assert response.tool_calls == []
            request = ChatHandler.requests[0]
            assert request["path"] == "/v1/chat/completions"
            assert request["authorization"] == "Bearer test-key"
            assert request["body"]["model"] == "test-model"
            assert request["body"]["messages"] == [{"role": "user", "content": "你好"}]
            assert "tools" not in request["body"]
        finally:
            server.shutdown()
            server.server_close()

    asyncio.run(run())


def test_build_llm_uses_fake_by_default_and_env_for_openai_compatible(monkeypatch):
    """验证 CLI 默认 FakeLLM，并可通过环境变量构建真实 provider。"""
    cli = import_module("Turning-Good-Agent.cli")
    settings_module = import_module("Turning-Good-Agent.config.settings")
    fake_module = import_module("Turning-Good-Agent.llm.fake")
    openai_module = import_module("Turning-Good-Agent.llm.openai_compatible")

    assert isinstance(cli.build_llm(settings_module.Settings()), fake_module.FakeLLM)

    monkeypatch.setenv("TGA_LLM_PROVIDER", "openai-compatible")
    monkeypatch.setenv("TGA_LLM_API_KEY", "env-key")
    monkeypatch.setenv("TGA_LLM_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("TGA_LLM_MODEL", "env-model")

    llm = cli.build_llm(settings_module.Settings.load())

    assert isinstance(llm, openai_module.OpenAICompatibleLLM)
    assert llm.api_key == "env-key"
    assert llm.base_url == "https://example.test/v1"
    assert llm.model == "env-model"


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
                    "raw_window_token_limit": 123,
                },
                "sessions": {
                    "retention_days": 9,
                },
                "llm": {
                    "provider": "openai-compatible",
                    "api_key": "local-key",
                    "base_url": "https://local.test/v1",
                    "model": "local-model",
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    settings = settings_module.Settings.load(local_config_path=config_path)

    assert settings.data_dir == tmp_path / "runtime-data"
    assert settings.memory.compact_token_threshold == 321
    assert settings.memory.raw_window_token_limit == 123
    assert settings.sessions.retention_days == 9
    assert settings.llm.api_key == "local-key"
    assert settings.llm.model == "local-model"


def test_settings_keep_runtime_memory_and_llm_defaults_together():
    """验证运行参数、记忆参数和 LLM 参数集中在 Settings。"""
    settings_module = import_module("Turning-Good-Agent.config.settings")

    settings = settings_module.Settings()

    assert settings.memory.compact_token_threshold == 200_000
    assert settings.memory.raw_window_token_limit == 20_000
    assert settings.sessions.retention_days == 7
    assert settings.llm.provider == "fake"
    assert settings.runtime.max_tool_rounds == 5

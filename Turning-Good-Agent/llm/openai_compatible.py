import asyncio
import json
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .types import LLMResponse


class OpenAICompatibleLLM:
    """调用 OpenAI-compatible Chat Completions 接口。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
    ) -> None:
        """保存模型连接配置。"""
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        """调用模型并返回纯文本回复。"""
        del tools
        payload = {
            "model": self.model,
            "messages": messages,
        }
        data = await asyncio.to_thread(self._post_chat, payload)
        content = data["choices"][0]["message"].get("content") or ""
        return LLMResponse(content=content)

    def _post_chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        """发送同步 HTTP 请求，供异步入口放到线程中执行。"""
        body = json.dumps(payload).encode("utf-8")
        request = Request(
            url=f"{self.base_url}/chat/completions",
            data=body,
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"模型请求失败：HTTP {exc.code} {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"模型连接失败：{exc.reason}") from exc

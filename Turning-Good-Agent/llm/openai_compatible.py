import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from openai import APIConnectionError, APITimeoutError, BadRequestError, InternalServerError, OpenAI, RateLimitError

from .types import LLMChunk, LLMResponse, ToolCall


class OpenAICompatibleLLM:
    """调用 OpenAI-compatible Chat Completions 接口。"""

    def __init__(
        self,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float = 60.0,
        max_retries: int = 2,
        retry_delay_seconds: float = 0.5,
    ) -> None:
        """保存模型连接配置并初始化 SDK client。"""
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_retries = max_retries
        self.retry_delay_seconds = retry_delay_seconds
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout_seconds,
        )

    async def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> LLMResponse:
        """调用模型并归一化文本与工具调用结果。"""
        response = await self._create_completion(messages, tools)
        choices = getattr(response, "choices", None) or []
        if not choices:
            raise RuntimeError("模型响应缺少 choices。")
        message = getattr(choices[0], "message", None)
        if message is None:
            raise RuntimeError("模型响应缺少 message。")
        content = getattr(message, "content", None) or ""
        return LLMResponse(
            content=content,
            tool_calls=self._parse_tool_calls(getattr(message, "tool_calls", None)),
        )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[LLMChunk]:
        """流式调用模型并产出文本增量。"""
        stream = await self._create_completion(messages, tools, stream=True)
        tool_call_parts: dict[int, dict[str, str]] = {}
        iterator = iter(stream)
        while True:
            event = await asyncio.to_thread(self._next_stream_event, iterator)
            if event is None:
                break
            choices = getattr(event, "choices", None) or []
            for choice in choices:
                delta = getattr(choice, "delta", None)
                finish_reason = getattr(choice, "finish_reason", None)
                delta_text = getattr(delta, "content", None) or "" if delta is not None else ""
                if delta is not None:
                    self._merge_tool_call_deltas(tool_call_parts, getattr(delta, "tool_calls", None))
                tool_calls = self._build_stream_tool_calls(tool_call_parts) if finish_reason == "tool_calls" else []
                if delta_text or finish_reason or tool_calls:
                    yield LLMChunk(
                        delta_text=delta_text,
                        tool_calls=tool_calls,
                        finish_reason=finish_reason,
                    )

    async def _create_completion(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        stream: bool = False,
    ) -> Any:
        """调用 SDK，并只对可恢复错误做轻量重试。"""
        attempt = 0
        while True:
            try:
                return await asyncio.to_thread(
                    self.client.chat.completions.create,
                    model=self.model,
                    messages=messages,
                    tools=tools or None,
                    stream=stream,
                )
            except (APITimeoutError, APIConnectionError, RateLimitError, InternalServerError):
                attempt += 1
                if attempt > self.max_retries:
                    raise
                await asyncio.sleep(self.retry_delay_seconds * attempt)
            except BadRequestError:
                raise

    def _parse_tool_calls(self, tool_calls: Any) -> list[ToolCall]:
        """解析 SDK 返回的 tool_calls。"""
        if not tool_calls:
            return []
        normalized: list[ToolCall] = []
        for item in tool_calls:
            function = getattr(item, "function", None)
            if function is None:
                continue
            normalized.append(
                ToolCall(
                    id=str(getattr(item, "id", "")),
                    name=str(getattr(function, "name", "")),
                    args=self._parse_arguments(getattr(function, "arguments", "{}")),
                )
            )
        return normalized

    def _parse_arguments(self, raw_arguments: Any) -> dict[str, Any]:
        """解析工具参数 JSON。"""
        if isinstance(raw_arguments, dict):
            return raw_arguments
        if not isinstance(raw_arguments, str) or not raw_arguments.strip():
            return {}
        try:
            parsed = json.loads(raw_arguments)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    def _merge_tool_call_deltas(self, parts: dict[int, dict[str, str]], tool_calls: Any) -> None:
        """合并流式 tool call 参数片段。"""
        if not tool_calls:
            return
        for position, item in enumerate(tool_calls):
            index = int(getattr(item, "index", position) or position)
            current = parts.setdefault(index, {"id": "", "name": "", "arguments": ""})
            item_id = getattr(item, "id", None)
            if item_id:
                current["id"] = str(item_id)
            function = getattr(item, "function", None)
            if function is None:
                continue
            name = getattr(function, "name", None)
            if name:
                current["name"] = str(name)
            arguments = getattr(function, "arguments", None)
            if arguments:
                current["arguments"] += str(arguments)

    def _build_stream_tool_calls(self, parts: dict[int, dict[str, str]]) -> list[ToolCall]:
        """把已合并的流式 tool call 转成内部结构。"""
        calls: list[ToolCall] = []
        for index in sorted(parts):
            item = parts[index]
            calls.append(
                ToolCall(
                    id=item["id"],
                    name=item["name"],
                    args=self._parse_arguments(item["arguments"]),
                )
            )
        return calls

    @staticmethod
    def _next_stream_event(iterator: Any) -> Any | None:
        """在线程中读取下一个同步 stream 事件。"""
        try:
            return next(iterator)
        except StopIteration:
            return None

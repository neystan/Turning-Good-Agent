import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any

from openai import (
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

from .types import LLMChunk, LLMResponse, LLMUsage, ToolCall


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
        self.client = AsyncOpenAI(
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
            usage=self._require_usage(getattr(response, "usage", None)),
        )

    async def stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
    ) -> AsyncIterator[LLMChunk]:
        """流式调用模型并产出文本增量。"""
        stream = await self._create_completion(messages, tools, stream=True)
        tool_call_parts: dict[int, dict[str, str]] = {}
        async for event in stream:
            usage = self._parse_usage(getattr(event, "usage", None))
            if usage is not None:
                yield LLMChunk(usage=usage)
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
                payload = {
                    "model": self.model,
                    "messages": messages,
                    "tools": tools or None,
                    "stream": stream,
                }
                if stream:
                    payload["stream_options"] = {"include_usage": True}
                return await self.client.chat.completions.create(**payload)
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
                raise ValueError("模型工具调用缺少 function。")
            call_id = str(getattr(item, "id", "") or "")
            name = str(getattr(function, "name", "") or "")
            if not call_id:
                raise ValueError("模型工具调用缺少 id。")
            if not name:
                raise ValueError("模型工具调用缺少 function.name。")
            normalized.append(
                ToolCall(
                    id=call_id,
                    name=name,
                    args=self._parse_arguments(getattr(function, "arguments", "{}"), name),
                )
            )
        return normalized

    def _parse_arguments(self, raw_arguments: Any, tool_name: str) -> dict[str, Any]:
        """解析工具参数 JSON。"""
        if isinstance(raw_arguments, dict):
            return raw_arguments
        if not isinstance(raw_arguments, str) or not raw_arguments.strip():
            return {}
        try:
            parsed = json.loads(raw_arguments)
        except json.JSONDecodeError as exc:
            raise ValueError(f"模型工具调用参数不是合法 JSON：{tool_name}") from exc
        if not isinstance(parsed, dict):
            raise ValueError(f"模型工具调用参数必须是 JSON object：{tool_name}")
        return parsed

    def _require_usage(self, usage: Any) -> LLMUsage:
        """解析并要求模型返回真实 usage。"""
        parsed = self._parse_usage(usage)
        if parsed is None or parsed.total_tokens <= 0:
            raise RuntimeError("模型响应缺少 usage，无法记录 true_token_usage。")
        return parsed

    def _parse_usage(self, usage: Any) -> LLMUsage | None:
        """解析 SDK 返回的 token usage。"""
        if usage is None:
            return None
        input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0)
        output_tokens = int(getattr(usage, "completion_tokens", 0) or 0)
        total_tokens = int(getattr(usage, "total_tokens", 0) or 0)
        if total_tokens <= 0:
            total_tokens = input_tokens + output_tokens
        return LLMUsage(input_tokens=input_tokens, output_tokens=output_tokens, total_tokens=total_tokens)

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
            if not item["id"]:
                raise ValueError("模型流式工具调用缺少 id。")
            if not item["name"]:
                raise ValueError("模型流式工具调用缺少 function.name。")
            calls.append(
                ToolCall(
                    id=item["id"],
                    name=item["name"],
                    args=self._parse_arguments(item["arguments"], item["name"]),
                )
            )
        return calls

from collections.abc import Mapping
from typing import Any

from ..llm.types import ToolCall
from ..sessions.token_counter import TOKEN_ENCODING, count_content_tokens
from .base import AgentHook


_HEAD_TOOLS = {"list_dir", "find_file", "grep", "web_search"}
_TAIL_TOOLS = {"exec", "write_stdin"}
_HEAD_TAIL_TOOLS = {"read_file", "web_fetch"}
_RETRY_HINT = "请使用更精确的条件、范围或过滤再次查询。"


class ToolResultTruncationHook(AgentHook):
    """按工具类型限制模型可见结果的 token 数。"""

    def __init__(self, max_tokens: int) -> None:
        """保存模型侧工具结果 token 上限。"""
        self.max_tokens = max(1, int(max_tokens))

    async def after_tool_call(
        self,
        call: ToolCall,
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        """截断过长工具结果并返回同结构记录。"""
        updated = dict(record)
        content = str(updated.get("content", ""))
        if call.name == "web_fetch":
            source_url = str(call.args.get("url", ""))
            if source_url and source_url not in content:
                content = f"来源 URL：{source_url}\n\n{content}"
        if count_content_tokens(content) <= self.max_tokens:
            updated["content"] = content
            return updated
        if call.name in _HEAD_TOOLS:
            truncated = self._truncate_head_lines(content)
        elif call.name in _TAIL_TOOLS:
            truncated = self._truncate_tokens(content, "tail")
        elif call.name in _HEAD_TAIL_TOOLS:
            truncated = self._truncate_tokens(content, "head_tail")
        else:
            truncated = self._truncate_tokens(content, "head_tail")
        updated["content"] = truncated
        return updated

    def _truncate_head_lines(self, content: str) -> str:
        """保留列表前部并说明省略条数。"""
        lines = content.splitlines()
        selected: list[str] = []
        for line in lines:
            candidate = selected + [line]
            omitted = len(lines) - len(candidate)
            notice = f"[已省略 {omitted} 条结果；{_RETRY_HINT}]"
            rendered = "\n".join(candidate + ["", notice])
            if count_content_tokens(rendered) > self.max_tokens:
                break
            selected = candidate
        omitted = len(lines) - len(selected)
        notice = f"[已省略 {omitted} 条结果；{_RETRY_HINT}]"
        rendered = "\n".join(selected + ["", notice])
        return self._fit_token_limit(rendered)

    def _truncate_tokens(self, content: str, strategy: str) -> str:
        """按头部、尾部或头尾策略截断 token。"""
        notice = f"[已省略部分工具结果；{_RETRY_HINT}]"
        notice_tokens = TOKEN_ENCODING.encode("\n\n" + notice)
        budget = max(0, self.max_tokens - len(notice_tokens))
        tokens = TOKEN_ENCODING.encode(content)
        if strategy == "tail":
            kept = tokens[-budget:] if budget else []
        else:
            head_size = budget // 2
            tail_size = budget - head_size
            kept = tokens[:head_size] + tokens[-tail_size:] if budget else []
        return self._fit_token_limit(TOKEN_ENCODING.decode(kept) + "\n\n" + notice)

    def _fit_token_limit(self, content: str) -> str:
        """保证最终文本不超过配置 token 上限。"""
        tokens = TOKEN_ENCODING.encode(content)
        return TOKEN_ENCODING.decode(tokens[: self.max_tokens])

from dataclasses import dataclass

from ..sessions.types import MessageRecord
from ..llm.client import LLMProvider
from ..llm.types import LLMUsage


@dataclass(slots=True)
class CompactionPlan:
    """保存一次短期记忆压缩计划。"""

    should_compact: bool
    compact_source: list[MessageRecord]
    recent_window: list[MessageRecord]
    compacted_message_count: int
    compacted_token_count: int
    raw_window_message_count: int
    raw_window_token_count: int


class ShortTermMemory:
    """管理短期历史和摘要压缩。"""

    def __init__(self, compact_token_threshold: int, recent_window_token_limit: int) -> None:
        self.recent_window_token_limit = recent_window_token_limit
        self.compact_token_threshold = compact_token_threshold

    def should_compact(self, messages: list[MessageRecord]) -> bool:
        """判断未压缩历史 token 是否超过阈值。"""
        return self.count_tokens(messages) > self.compact_token_threshold

    def plan_compaction(self, messages: list[MessageRecord], force: bool = False) -> CompactionPlan:
        """生成保留完整对话的压缩计划。"""
        if not force and not self.should_compact(messages):
            return CompactionPlan(
                should_compact=False,
                compact_source=[],
                recent_window=list(messages),
                compacted_message_count=0,
                compacted_token_count=0,
                raw_window_message_count=len(messages),
                raw_window_token_count=self.count_tokens(messages),
            )
        recent_window = self.recent_window(messages)
        recent_ids = {item.id for item in recent_window}
        compact_source = [item for item in messages if item.id not in recent_ids]
        return CompactionPlan(
            should_compact=True,
            compact_source=compact_source,
            recent_window=recent_window,
            compacted_message_count=len(compact_source),
            compacted_token_count=self.count_tokens(compact_source),
            raw_window_message_count=len(recent_window),
            raw_window_token_count=self.count_tokens(recent_window),
        )

    def recent_window(self, messages: list[MessageRecord]) -> list[MessageRecord]:
        """返回不超过 token 上限的最近完整对话。"""
        selected: list[MessageRecord] = []
        total = 0
        for turn in reversed(self.complete_turns(messages)):
            turn_tokens = self.count_tokens(turn)
            if turn_tokens > self.recent_window_token_limit:
                break
            if total + turn_tokens > self.recent_window_token_limit:
                break
            selected = turn + selected
            total += turn_tokens
        return selected

    async def compact(self, existing_summary: str, messages: list[MessageRecord], llm: LLMProvider) -> tuple[str, LLMUsage]:
        """调用 LLM 生成新的短期摘要。"""
        if not messages:
            return existing_summary, LLMUsage()
        response = await llm.complete(self.summary_messages(existing_summary, messages), tools=[])
        if response.usage is None or response.usage.total_tokens <= 0:
            raise RuntimeError("摘要 LLM 响应缺少 usage，无法保存压缩结果。")
        summary = response.content.strip()
        if not summary:
            raise RuntimeError("摘要 LLM 响应为空，无法保存压缩结果。")
        return summary, response.usage

    def summary_messages(self, existing_summary: str, messages: list[MessageRecord]) -> list[dict[str, str]]:
        """构建短期摘要专用模型消息。"""
        source = "\n".join(f"{item.role}: {item.content}" for item in messages)
        user_content = (
            "请基于已有摘要和待压缩会话片段，生成新的会话摘要。\n"
            "要求：只保留对后续对话有用的事实、偏好、约定、未完成事项和关键上下文；"
            "去掉寒暄、重复内容和无意义细节；直接输出摘要正文。\n\n"
            f"已有摘要：\n{existing_summary or '无'}\n\n"
            f"待压缩会话片段：\n{source}"
        )
        return [
            {"role": "system", "content": "你是 Turning Good Agent 的短期记忆摘要器。"},
            {"role": "user", "content": user_content},
        ]

    def count_tokens(self, messages: list[MessageRecord]) -> int:
        """统计消息持久化的 tokenizer token 权重。"""
        return sum(item.token_count for item in messages)

    def complete_turns(self, messages: list[MessageRecord]) -> list[list[MessageRecord]]:
        """按 user/assistant 组合提取完整对话。"""
        turns: list[list[MessageRecord]] = []
        index = 0
        while index < len(messages) - 1:
            current = messages[index]
            following = messages[index + 1]
            if current.role == "user" and following.role == "assistant":
                turns.append([current, following])
                index += 2
                continue
            index += 1
        return turns

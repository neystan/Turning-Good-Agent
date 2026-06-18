from ..sessions.types import MessageRecord


class ShortTermMemory:
    """管理短期历史和摘要压缩。"""

    def __init__(self, compact_token_threshold: int, raw_window_token_limit: int) -> None:
        self.raw_window_token_limit = raw_window_token_limit
        self.compact_token_threshold = compact_token_threshold

    def should_compact(self, messages: list[MessageRecord]) -> bool:
        """判断未压缩历史 token 是否超过阈值。"""
        return self.count_tokens(messages) > self.compact_token_threshold

    def recent_window(self, messages: list[MessageRecord]) -> list[MessageRecord]:
        """返回不超过 token 上限的最近完整对话。"""
        selected: list[MessageRecord] = []
        total = 0
        for turn in reversed(self.complete_turns(messages)):
            turn_tokens = self.count_tokens(turn)
            if turn_tokens > self.raw_window_token_limit:
                break
            if total + turn_tokens > self.raw_window_token_limit:
                break
            selected = turn + selected
            total += turn_tokens
        return selected

    def compact(self, existing_summary: str, messages: list[MessageRecord]) -> str:
        """生成抽取式短期摘要。"""
        if not messages:
            return existing_summary
        parts = [existing_summary] if existing_summary else []
        parts.extend(f"{item.role}: {item.content}" for item in messages)
        return "\n".join(parts)

    def count_tokens(self, messages: list[MessageRecord]) -> int:
        """统计消息持久化的真实 token 权重。"""
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

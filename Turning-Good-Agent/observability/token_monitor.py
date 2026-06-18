from ..llm.types import LLMUsage


class TokenMonitor:
    """记录单轮和会话累计 token 使用量。"""

    def record_llm_usage(
        self,
        usage: LLMUsage,
        previous_total_tokens: int,
        compacted: bool = False,
        compacted_message_count: int = 0,
        compacted_token_count: int = 0,
        raw_window_message_count: int = 0,
        raw_window_token_count: int = 0,
        tool_call_count: int = 0,
        tool_names: list[str] | None = None,
    ) -> dict[str, int | list[str]]:
        """使用模型真实 usage 生成完整 token 记录。"""
        if usage.total_tokens <= 0:
            raise ValueError("LLM usage 缺少有效 total_tokens。")
        return {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "turn_total_tokens": usage.total_tokens,
            "total_tokens": previous_total_tokens + usage.total_tokens,
            "compacted": int(compacted),
            "compacted_message_count": compacted_message_count,
            "compacted_token_count": compacted_token_count,
            "raw_window_message_count": raw_window_message_count,
            "raw_window_token_count": raw_window_token_count,
            "tool_call_count": tool_call_count,
            "tool_names": tool_names or [],
        }

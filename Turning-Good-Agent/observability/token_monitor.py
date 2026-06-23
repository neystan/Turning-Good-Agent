from ..llm.types import LLMUsage


class TokenMonitor:
    """记录单轮和会话累计 token 使用量。"""

    def record_llm_usage(
        self,
        usage: LLMUsage,
        previous_total_tokens: int,
        compacted: bool = False,
    ) -> dict[str, int]:
        """使用模型真实 usage 生成完整 token 记录。"""
        if usage.total_tokens <= 0:
            raise ValueError("LLM usage 缺少有效 total_tokens。")
        return {
            "input_tokens": usage.input_tokens,
            "output_tokens": usage.output_tokens,
            "turn_total_tokens": usage.total_tokens,
            "total_tokens": previous_total_tokens + usage.total_tokens,
            "compacted": int(compacted),
        }

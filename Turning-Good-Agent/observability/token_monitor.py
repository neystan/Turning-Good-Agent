from ..context.budget import estimate_tokens


class TokenMonitor:
    """记录单轮和会话累计 token 使用量。"""

    def record(
        self,
        input_text: str,
        output_text: str,
        compacted: bool,
        previous_total_tokens: int,
    ) -> dict[str, int]:
        """返回本轮 token 和会话累计 token。"""
        input_tokens = estimate_tokens(input_text)
        output_tokens = estimate_tokens(output_text)
        turn_total = input_tokens + output_tokens
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "turn_total_tokens": turn_total,
            "total_tokens": previous_total_tokens + turn_total,
            "compacted": int(compacted),
        }

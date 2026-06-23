import re


TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+|[^\s]")


def count_content_tokens(content: str) -> int:
    """按消息自身内容估算 token 权重。"""
    if not content:
        return 0
    return max(1, len(TOKEN_PATTERN.findall(content)))

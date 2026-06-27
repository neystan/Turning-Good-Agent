import tiktoken


TOKEN_ENCODING = tiktoken.get_encoding("o200k_base")


def count_content_tokens(content: str) -> int:
    """使用 tokenizer 计算文本 token 权重。"""
    if not content:
        return 0
    return max(1, len(TOKEN_ENCODING.encode(content)))

import re


TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]|[A-Za-z0-9_]+|[^\sA-Za-z0-9_\u4e00-\u9fff]")


def estimate_tokens(text: str) -> int:
    """估算文本 token 数，优先使用 tokenizer，缺失时用分词规则兜底。"""
    if not text:
        return 1
    try:
        import tiktoken

        encoding = tiktoken.get_encoding("cl100k_base")
        return max(1, len(encoding.encode(text)))
    except Exception:
        return max(1, len(TOKEN_PATTERN.findall(text)))

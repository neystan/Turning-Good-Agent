import re
from pathlib import Path
from urllib.parse import urlparse


MAX_READ_CHARS = 128_000
MAX_LIST_ENTRIES = 500
MAX_GREP_FILE_BYTES = 2_000_000
MAX_TOOL_OUTPUT_CHARS = 20_000
MAX_WEB_RESPONSE_BYTES = 2_000_000
DEFAULT_EXEC_TIMEOUT_SECONDS = 60
MAX_EXEC_TIMEOUT_SECONDS = 600
MAX_EXEC_SESSIONS = 8
EXEC_IDLE_TIMEOUT_SECONDS = 1800

IGNORE_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
}

_BLOCKED_DEVICE_PATHS = {
    "/dev/zero",
    "/dev/random",
    "/dev/urandom",
    "/dev/full",
    "/dev/stdin",
    "/dev/stdout",
    "/dev/stderr",
    "/dev/tty",
    "/dev/console",
    "/dev/fd/0",
    "/dev/fd/1",
    "/dev/fd/2",
}

_DENY_COMMAND_PATTERNS = [
    r"\brm\s+-[rf]{1,2}\b",
    r"\bmkfs\b",
    r"\bdiskpart\b",
    r"\bdd\s+if=",
    r">\s*/dev/sd",
    r"\bshutdown\b",
    r"\breboot\b",
    r"\bpoweroff\b",
    r":\(\)\s*\{.*\};\s*:",
    r">>?\s*\S*\.sessions/",
    r"\btee\b[^|;&<>]*\.sessions/",
    r"\b(?:cp|mv)\b(?:\s+[^\s|;&<>]+)+\s+\S*\.sessions/",
    r"\bsed\s+-i[^|;&<>]*\.sessions/",
]


def truncate_text(text: str, limit: int = MAX_TOOL_OUTPUT_CHARS) -> str:
    """按字符上限截断工具输出。"""
    if len(text) <= limit:
        return text
    if limit <= 20:
        return text[: max(0, limit - 14)] + "... truncated"
    half = limit // 2
    omitted = len(text) - limit
    return text[:half] + f"\n\n... truncated {omitted} chars ...\n\n" + text[-half:]


def is_blocked_device_path(path: str | Path) -> bool:
    """判断路径是否是危险设备文件。"""
    raw = str(path)
    try:
        resolved = str(Path(raw).resolve())
    except (OSError, ValueError):
        resolved = raw
    if raw in _BLOCKED_DEVICE_PATHS or resolved in _BLOCKED_DEVICE_PATHS:
        return True
    if re.match(r"/proc/(?:self|\d+)/fd/[012]$", raw):
        return True
    if re.match(r"/proc/(?:self|\d+)/fd/[012]$", resolved):
        return True
    return resolved.startswith("/dev/")


def is_session_state_path(path: str | Path) -> bool:
    """判断路径是否位于会话状态目录。"""
    return ".sessions" in Path(path).parts


def validate_read_path(path: Path) -> str | None:
    """校验可读路径。"""
    if is_blocked_device_path(path):
        return f"拒绝读取危险设备路径：{path}"
    return None


def validate_write_path(path: Path) -> str | None:
    """校验可写路径。"""
    if is_blocked_device_path(path):
        return f"拒绝写入危险设备路径：{path}"
    if is_session_state_path(path):
        return f"拒绝直接写入 .sessions 状态文件：{path}"
    return None


def validate_command(command: str) -> str | None:
    """校验 shell 命令是否命中危险模式。"""
    lowered = command.strip().lower()
    for pattern in _DENY_COMMAND_PATTERNS:
        if re.search(pattern, lowered):
            return "命令被安全策略拒绝"
    return None


def validate_http_url(url: str) -> str | None:
    """校验 URL 只允许 http/https。"""
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return f"只允许 http/https URL，实际是：{parsed.scheme or 'none'}"
    if not parsed.netloc:
        return "URL 缺少域名"
    return None


def is_binary_bytes(raw: bytes) -> bool:
    """用简单启发式判断二进制内容。"""
    if b"\x00" in raw:
        return True
    sample = raw[:4096]
    if not sample:
        return False
    non_text = sum(byte < 9 or 13 < byte < 32 for byte in sample)
    return non_text / len(sample) > 0.2

import fnmatch
import os
import re
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from . import security
from .base import ToolResult
from .path_utils import resolve_workspace_path, workspace_from_context


_TYPE_GLOB_MAP = {
    "py": ("*.py", "*.pyi"),
    "python": ("*.py", "*.pyi"),
    "js": ("*.js", "*.jsx", "*.mjs", "*.cjs"),
    "ts": ("*.ts", "*.tsx", "*.mts", "*.cts"),
    "json": ("*.json",),
    "md": ("*.md", "*.mdx"),
    "markdown": ("*.md", "*.mdx"),
    "txt": ("*.txt",),
    "yaml": ("*.yaml", "*.yml"),
    "yml": ("*.yaml", "*.yml"),
    "toml": ("*.toml",),
    "html": ("*.html", "*.htm"),
    "css": ("*.css", "*.scss", "*.sass"),
}


def _error(message: str) -> ToolResult:
    """创建错误工具结果。"""
    return ToolResult(message, {"error": True})


def _match_glob(rel_path: str, name: str, pattern: str | None) -> bool:
    """按 glob 匹配文件路径。"""
    if not pattern:
        return True
    normalized = pattern.strip().replace("\\", "/")
    if "/" in normalized or normalized.startswith("**"):
        return PurePosixPath(rel_path).match(normalized)
    return fnmatch.fnmatch(name, normalized)


def _matches_type(name: str, file_type: str | None) -> bool:
    """按文件类型简写匹配文件名。"""
    if not file_type:
        return True
    lowered = file_type.lower().strip()
    patterns = _TYPE_GLOB_MAP.get(lowered, (f"*.{lowered}",))
    return any(fnmatch.fnmatch(name.lower(), pattern.lower()) for pattern in patterns)


def _iter_paths(root: Path, include_dirs: bool = False) -> Iterable[Path]:
    """遍历路径并跳过常见噪声目录。"""
    if root.is_file():
        yield root
        return
    if include_dirs:
        yield root
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in security.IGNORE_DIRS)
        current = Path(dirpath)
        if include_dirs and current != root:
            yield current
        for filename in sorted(filenames):
            yield current / filename


class _FsTool:
    """文件系统工具公共基类。"""

    source = "builtin"
    discoverable = True

    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()

    @classmethod
    def create(cls, context: Any | None = None):
        """按加载上下文创建工具。"""
        return cls(workspace_from_context(context))

    def _resolve(self, path: str) -> Path:
        """解析 workspace 内路径。"""
        return resolve_workspace_path(path, self.workspace)

    def _display(self, path: Path) -> str:
        """返回 workspace 相对展示路径。"""
        try:
            return path.relative_to(self.workspace).as_posix()
        except ValueError:
            return path.as_posix()


class ListDirTool(_FsTool):
    """列出目录内容。"""

    name = "list_dir"
    description = "列出目录内容，支持 recursive 和 max_entries。"
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "recursive": {"type": "boolean"},
            "max_entries": {"type": "integer", "minimum": 1, "maximum": security.MAX_LIST_ENTRIES},
        },
        "required": ["path"],
    }

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """执行目录列表。"""
        try:
            root = self._resolve(args["path"])
            if not root.exists():
                return _error(f"目录不存在：{args['path']}")
            if not root.is_dir():
                return _error(f"不是目录：{args['path']}")
            recursive = bool(args.get("recursive", False))
            max_entries = int(args.get("max_entries") or 200)
            items: list[str] = []
            iterator = root.rglob("*") if recursive else root.iterdir()
            for item in sorted(iterator):
                if any(part in security.IGNORE_DIRS for part in item.parts):
                    continue
                suffix = "/" if item.is_dir() else ""
                if len(items) < max_entries:
                    items.append(self._display(item) + suffix)
            if not items:
                return ToolResult("(目录为空)")
            return ToolResult("\n".join(items))
        except Exception as exc:
            return _error(f"列目录失败：{exc}")


class FindFileTool(_FsTool):
    """按名称、glob 或类型查找文件。"""

    name = "find_file"
    description = "按路径片段、glob 或文件类型查找文件。"
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "query": {"type": "string"},
            "glob": {"type": "string"},
            "type": {"type": "string"},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
    }

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """执行文件查找。"""
        try:
            target = self._resolve(args.get("path") or ".")
            if not target.exists():
                return _error(f"路径不存在：{args.get('path') or '.'}")
            root = target if target.is_dir() else target.parent
            query = str(args.get("query") or "").lower()
            max_results = int(args.get("max_results") or 200)
            matches: list[str] = []
            for item in _iter_paths(target):
                if not item.is_file():
                    continue
                rel = item.relative_to(root).as_posix()
                display = self._display(item)
                if query and query not in display.lower():
                    continue
                if not _match_glob(rel, item.name, args.get("glob")):
                    continue
                if not _matches_type(item.name, args.get("type")):
                    continue
                matches.append(display)
                if len(matches) >= max_results:
                    break
            return ToolResult("\n".join(matches) if matches else "未找到文件")
        except Exception as exc:
            return _error(f"查找文件失败：{exc}")


class ReadFileTool(_FsTool):
    """读取 UTF-8 文本文件。"""

    name = "read_file"
    description = "读取 UTF-8 文本文件，支持 offset/limit 行分页。"
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "offset": {"type": "integer", "minimum": 1},
            "limit": {"type": "integer", "minimum": 1, "maximum": 2000},
        },
        "required": ["path"],
    }

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """执行文件读取。"""
        try:
            path = self._resolve(args["path"])
            error = security.validate_read_path(path)
            if error:
                return _error(error)
            if not path.exists():
                return _error(f"文件不存在：{args['path']}")
            if not path.is_file():
                return _error(f"不是文件：{args['path']}")
            raw = path.read_bytes()
            if security.is_binary_bytes(raw):
                return _error(f"拒绝读取二进制文件：{args['path']}")
            text = raw.decode("utf-8").replace("\r\n", "\n")
            lines = text.splitlines()
            offset = max(1, int(args.get("offset") or 1))
            limit = int(args.get("limit") or 200)
            start = offset - 1
            if start >= len(lines) and lines:
                return _error(f"offset 超出文件行数：{len(lines)}")
            selected = lines[start : start + limit]
            numbered = [f"{start + index + 1}| {line}" for index, line in enumerate(selected)]
            content = "\n".join(numbered) if numbered else "(空文件)"
            return ToolResult(security.truncate_text(content, security.MAX_READ_CHARS))
        except UnicodeDecodeError:
            return _error(f"文件不是 UTF-8 文本：{args.get('path')}")
        except Exception as exc:
            return _error(f"读取文件失败：{exc}")


class WriteFileTool(_FsTool):
    """创建或覆盖写入文件。"""

    name = "write_file"
    description = "创建新文件或整体覆盖写入文件。"
    input_schema = {
        "type": "object",
        "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
        "required": ["path", "content"],
    }

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """执行整文件写入。"""
        try:
            path = self._resolve(args["path"])
            error = security.validate_write_path(path)
            if error:
                return _error(error)
            content = str(args.get("content", ""))
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return ToolResult(f"已写入 {len(content)} 个字符到 {self._display(path)}")
        except Exception as exc:
            return _error(f"写入文件失败：{exc}")


class EditFileTool(_FsTool):
    """精确替换文件文本。"""

    name = "edit_file"
    description = "用 old_text/new_text 对已有文件做精确文本替换。"
    input_schema = {
        "type": "object",
        "properties": {
            "path": {"type": "string"},
            "old_text": {"type": "string"},
            "new_text": {"type": "string"},
            "replace_all": {"type": "boolean"},
        },
        "required": ["path", "old_text", "new_text"],
    }

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """执行精确文本替换。"""
        try:
            path = self._resolve(args["path"])
            error = security.validate_write_path(path)
            if error:
                return _error(error)
            if not path.exists() or not path.is_file():
                return _error(f"文件不存在：{args['path']}")
            raw = path.read_bytes()
            if security.is_binary_bytes(raw):
                return _error(f"拒绝编辑二进制文件：{args['path']}")
            content = raw.decode("utf-8").replace("\r\n", "\n")
            old_text = str(args["old_text"]).replace("\r\n", "\n")
            new_text = str(args["new_text"]).replace("\r\n", "\n")
            count = content.count(old_text)
            if count == 0:
                return _error("old_text 未找到")
            replace_all = bool(args.get("replace_all", False))
            if count > 1 and not replace_all:
                return _error(f"old_text 出现 {count} 次，请提供更精确上下文或 replace_all=true")
            updated = content.replace(old_text, new_text if replace_all else new_text, -1 if replace_all else 1)
            path.write_text(updated, encoding="utf-8")
            return ToolResult(f"已编辑 {self._display(path)}，替换 {count if replace_all else 1} 处")
        except UnicodeDecodeError:
            return _error(f"文件不是 UTF-8 文本：{args.get('path')}")
        except Exception as exc:
            return _error(f"编辑文件失败：{exc}")


class GrepTool(_FsTool):
    """搜索文件内容。"""

    name = "grep"
    description = "在文件内容中搜索文本或正则，支持 content/files_with_matches/count 输出。"
    input_schema = {
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "minLength": 1},
            "path": {"type": "string"},
            "glob": {"type": "string"},
            "type": {"type": "string"},
            "case_insensitive": {"type": "boolean"},
            "fixed_strings": {"type": "boolean"},
            "output_mode": {"type": "string", "enum": ["content", "files_with_matches", "count"]},
            "max_results": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
        "required": ["pattern"],
    }

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """执行内容搜索。"""
        try:
            target = self._resolve(args.get("path") or ".")
            if not target.exists():
                return _error(f"路径不存在：{args.get('path') or '.'}")
            flags = re.IGNORECASE if args.get("case_insensitive") else 0
            pattern = re.escape(args["pattern"]) if args.get("fixed_strings") else args["pattern"]
            regex = re.compile(pattern, flags)
            root = target if target.is_dir() else target.parent
            mode = args.get("output_mode") or "files_with_matches"
            max_results = int(args.get("max_results") or 200)
            results: list[str] = []
            counts: dict[str, int] = {}
            for file_path in _iter_paths(target):
                if not file_path.is_file():
                    continue
                rel = file_path.relative_to(root).as_posix()
                if not _match_glob(rel, file_path.name, args.get("glob")):
                    continue
                if not _matches_type(file_path.name, args.get("type")):
                    continue
                raw = file_path.read_bytes()
                if len(raw) > security.MAX_GREP_FILE_BYTES or security.is_binary_bytes(raw):
                    continue
                try:
                    lines = raw.decode("utf-8").splitlines()
                except UnicodeDecodeError:
                    continue
                display = self._display(file_path)
                match_count = 0
                for line_no, line in enumerate(lines, start=1):
                    if not regex.search(line):
                        continue
                    match_count += 1
                    if mode == "content":
                        results.append(f"{display}:{line_no}\n> {line_no}| {line}")
                    if len(results) >= max_results and mode == "content":
                        break
                if match_count and mode == "files_with_matches":
                    results.append(display)
                if match_count and mode == "count":
                    counts[display] = match_count
                if len(results) >= max_results:
                    break
            if mode == "count":
                results = [f"{name}: {count}" for name, count in counts.items()]
            return ToolResult(security.truncate_text("\n\n".join(results) if results else "未找到匹配"))
        except re.error as exc:
            return _error(f"正则错误：{exc}")
        except Exception as exc:
            return _error(f"搜索失败：{exc}")

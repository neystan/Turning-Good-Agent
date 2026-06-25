from pathlib import Path
from typing import Any


def workspace_from_context(context: Any | None = None) -> Path:
    """从加载上下文解析工具工作目录。"""
    if isinstance(context, (str, Path)):
        return Path(context).expanduser().resolve()
    workspace = getattr(context, "workspace", None)
    if workspace:
        return Path(workspace).expanduser().resolve()
    return Path.cwd().resolve()


def is_under(path: Path, root: Path) -> bool:
    """判断路径是否位于根目录内。"""
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def resolve_workspace_path(path: str, workspace: Path) -> Path:
    """解析 workspace 内路径并阻止目录逃逸。"""
    raw = Path(path).expanduser()
    candidate = raw if raw.is_absolute() else workspace / raw
    resolved = candidate.resolve()
    root = workspace.resolve()
    if resolved != root and root not in resolved.parents:
        raise PermissionError(f"path is outside workspace: {path}")
    return resolved

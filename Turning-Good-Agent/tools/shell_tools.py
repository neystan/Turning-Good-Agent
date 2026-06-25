import asyncio
from pathlib import Path
from typing import Any

from . import security
from .base import ToolResult
from .exec_sessions import DEFAULT_EXEC_SESSION_MANAGER, format_poll
from .path_utils import resolve_workspace_path, workspace_from_context


def _error(message: str) -> ToolResult:
    """创建错误工具结果。"""
    return ToolResult(message, {"error": True})


class ExecTool:
    """执行受限 shell 命令。"""

    name = "exec"
    source = "builtin"
    discoverable = True
    description = "执行受限 shell 命令，支持超时、输出截断和长运行 session。"
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string"},
            "working_dir": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1, "maximum": security.MAX_EXEC_TIMEOUT_SECONDS},
            "yield_time_ms": {"type": "integer", "minimum": 0, "maximum": 30_000},
            "max_output_chars": {"type": "integer", "minimum": 1000, "maximum": 50_000},
        },
        "required": ["command"],
    }

    def __init__(self, workspace: Path | None = None) -> None:
        self.workspace = (workspace or Path.cwd()).resolve()

    @classmethod
    def create(cls, context: Any | None = None):
        """按加载上下文创建工具。"""
        return cls(workspace_from_context(context))

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """执行 shell 命令。"""
        command = str(args["command"])
        error = security.validate_command(command)
        if error:
            return _error(error)
        try:
            cwd = resolve_workspace_path(args.get("working_dir") or ".", self.workspace)
            timeout = min(int(args.get("timeout") or security.DEFAULT_EXEC_TIMEOUT_SECONDS), security.MAX_EXEC_TIMEOUT_SECONDS)
            max_output = int(args.get("max_output_chars") or security.MAX_TOOL_OUTPUT_CHARS)
            if "yield_time_ms" in args and args.get("yield_time_ms") is not None:
                session_id, poll = await DEFAULT_EXEC_SESSION_MANAGER.start(
                    command,
                    str(cwd),
                    timeout,
                    int(args.get("yield_time_ms") or 0),
                    max_output,
                )
                return ToolResult(format_poll(session_id, poll), {"session_id": session_id, "running": not poll.done})
            process = await asyncio.create_subprocess_exec(
                "/bin/bash",
                "-lc",
                command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(cwd),
            )
            try:
                stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=timeout)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
                return _error(f"命令超时：{timeout} 秒")
            output = []
            if stdout:
                output.append(stdout.decode("utf-8", errors="replace"))
            if stderr:
                output.append("STDERR:\n" + stderr.decode("utf-8", errors="replace"))
            output.append(f"Exit code: {process.returncode}")
            return ToolResult(security.truncate_text("\n".join(output), max_output))
        except Exception as exc:
            return _error(f"执行命令失败：{exc}")


class WriteStdinTool:
    """与长运行命令会话交互。"""

    name = "write_stdin"
    source = "builtin"
    discoverable = True
    description = "向 exec 返回的 session_id 写入 stdin、轮询输出或终止进程。"
    input_schema = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string"},
            "chars": {"type": "string"},
            "terminate": {"type": "boolean"},
            "yield_time_ms": {"type": "integer", "minimum": 0, "maximum": 30_000},
            "max_output_chars": {"type": "integer", "minimum": 1000, "maximum": 50_000},
        },
        "required": ["session_id"],
    }

    @classmethod
    def create(cls, context: Any | None = None):
        """创建工具实例。"""
        return cls()

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """写入或轮询命令会话。"""
        try:
            poll = await DEFAULT_EXEC_SESSION_MANAGER.write(
                str(args["session_id"]),
                str(args.get("chars") or ""),
                bool(args.get("terminate", False)),
                int(args.get("yield_time_ms") or 0),
                int(args.get("max_output_chars") or security.MAX_TOOL_OUTPUT_CHARS),
            )
            return ToolResult(format_poll(str(args["session_id"]), poll), {"running": not poll.done})
        except KeyError:
            return _error(f"命令会话不存在：{args['session_id']}")
        except Exception as exc:
            return _error(f"写入命令会话失败：{exc}")

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


def _decode_output(raw: bytes) -> str:
    """解码并去掉输出边缘空白。"""
    return raw.decode("utf-8", errors="replace").strip()


def _format_exec_output(stdout: bytes, stderr: bytes, exit_code: int | None) -> str:
    """格式化普通命令输出。"""
    output: list[str] = []
    stdout_text = _decode_output(stdout) if stdout else ""
    stderr_text = _decode_output(stderr) if stderr else ""
    if stdout_text:
        output.append(stdout_text)
    if stderr_text:
        output.append("STDERR:\n" + stderr_text)
    output.append(f"Exit code: {exit_code}")
    return "\n".join(output)


class ExecTool:
    """执行受限 shell 命令。"""

    name = "exec"
    source = "builtin"
    discoverable = True
    description = "执行 shell 命令。"
    input_schema = {
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "shell 命令"},
            "working_dir": {"type": "string", "description": "工作目录"},
            "timeout": {
                "type": "integer",
                "description": "超时秒数",
                "minimum": 1,
                "maximum": security.MAX_EXEC_TIMEOUT_SECONDS,
            },
            "yield_time_ms": {
                "type": "integer",
                "description": "等待毫秒数",
                "minimum": 0,
                "maximum": security.MAX_YIELD_TIME_MS,
                "default": security.DEFAULT_YIELD_TIME_MS,
            },
            "background": {"type": "boolean", "description": "创建长运行会话"},
            "max_output_chars": {"type": "integer", "description": "最大输出字符", "minimum": 1000, "maximum": 50_000},
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
            timeout = security.clamp_int(
                args.get("timeout"),
                security.DEFAULT_EXEC_TIMEOUT_SECONDS,
                1,
                security.MAX_EXEC_TIMEOUT_SECONDS,
            )
            max_output = security.clamp_int(args.get("max_output_chars"), security.MAX_TOOL_OUTPUT_CHARS, 1000, 50_000)
            if bool(args.get("background", False)):
                yield_time_ms = security.clamp_int(
                    args.get("yield_time_ms"),
                    security.DEFAULT_YIELD_TIME_MS,
                    0,
                    security.MAX_YIELD_TIME_MS,
                )
                session_id, poll = await DEFAULT_EXEC_SESSION_MANAGER.start(
                    command,
                    str(cwd),
                    timeout,
                    yield_time_ms,
                    max_output,
                )
                return ToolResult(format_poll(session_id, poll), {"session_id": session_id, "running": not poll.done})
            process = await asyncio.create_subprocess_exec(
                "/bin/bash",
                "-c",
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
            return ToolResult(security.truncate_text(_format_exec_output(stdout, stderr, process.returncode), max_output))
        except Exception as exc:
            return _error(f"执行命令失败：{exc}")


class WriteStdinTool:
    """与长运行命令会话交互。"""

    name = "write_stdin"
    source = "builtin"
    discoverable = True
    description = "操作命令会话。"
    input_schema = {
        "type": "object",
        "properties": {
            "session_id": {"type": "string", "description": "命令会话 ID"},
            "chars": {"type": "string", "description": "写入内容"},
            "terminate": {"type": "boolean", "description": "终止会话"},
            "yield_time_ms": {
                "type": "integer",
                "description": "等待毫秒数",
                "minimum": 0,
                "maximum": security.MAX_YIELD_TIME_MS,
                "default": security.DEFAULT_YIELD_TIME_MS,
            },
            "max_output_chars": {"type": "integer", "description": "最大输出字符", "minimum": 1000, "maximum": 50_000},
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
                security.clamp_int(args.get("yield_time_ms"), security.DEFAULT_YIELD_TIME_MS, 0, security.MAX_YIELD_TIME_MS),
                security.clamp_int(args.get("max_output_chars"), security.MAX_TOOL_OUTPUT_CHARS, 1000, 50_000),
            )
            return ToolResult(format_poll(str(args["session_id"]), poll), {"running": not poll.done})
        except KeyError:
            return _error(f"命令会话不存在：{args['session_id']}")
        except Exception as exc:
            return _error(f"写入命令会话失败：{exc}")

import asyncio
import time
import uuid
from contextlib import suppress
from dataclasses import dataclass

from . import security


@dataclass(slots=True)
class SessionPoll:
    """保存命令会话轮询结果。"""

    output: str
    done: bool
    exit_code: int | None
    elapsed_s: float
    timed_out: bool = False
    terminated: bool = False


class _ExecSession:
    """封装一个长运行子进程。"""

    def __init__(self, session_id: str, process: asyncio.subprocess.Process, command: str, timeout: int | None) -> None:
        self.session_id = session_id
        self.process = process
        self.command = command
        self.started_at = time.monotonic()
        self.last_access = time.monotonic()
        self.deadline = self.started_at + timeout if timeout else float("inf")
        self.timed_out = False
        self._chunks: list[str] = []
        self._lock = asyncio.Lock()
        self._stdout_task = asyncio.create_task(self._read_stream(process.stdout, ""))
        self._stderr_task = asyncio.create_task(self._read_stream(process.stderr, "STDERR:\n"))

    async def _read_stream(self, stream: asyncio.StreamReader | None, prefix: str) -> None:
        """持续读取输出流。"""
        if stream is None:
            return
        first = True
        while True:
            chunk = await stream.read(4096)
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            if prefix and first:
                text = prefix + text
                first = False
            async with self._lock:
                self._chunks.append(text)

    async def write(self, chars: str) -> str | None:
        """向子进程 stdin 写入文本。"""
        if self.process.returncode is not None:
            return "session has exited"
        if self.process.stdin is None:
            return "stdin unavailable"
        try:
            self.process.stdin.write(chars.encode("utf-8"))
            await self.process.stdin.drain()
        except (BrokenPipeError, ConnectionResetError):
            return "stdin closed"
        return None

    async def kill(self) -> None:
        """终止子进程。"""
        if self.process.returncode is not None:
            return
        self.process.kill()
        with suppress(asyncio.TimeoutError):
            await asyncio.wait_for(self.process.wait(), timeout=5)

    async def poll(self, yield_time_ms: int, max_output_chars: int, *, terminated: bool = False) -> SessionPoll:
        """等待片刻并返回新增输出。"""
        self.last_access = time.monotonic()
        if yield_time_ms > 0 and self.process.returncode is None:
            await asyncio.sleep(yield_time_ms / 1000)
        if self.process.returncode is None and time.monotonic() >= self.deadline:
            self.timed_out = True
            await self.kill()
        if self.process.returncode is not None:
            with suppress(asyncio.TimeoutError):
                await asyncio.wait_for(asyncio.gather(self._stdout_task, self._stderr_task), timeout=2)
        async with self._lock:
            output = "".join(self._chunks)
            self._chunks.clear()
        return SessionPoll(
            output=security.truncate_text(output, max_output_chars),
            done=self.process.returncode is not None,
            exit_code=self.process.returncode,
            elapsed_s=max(0.0, time.monotonic() - self.started_at),
            timed_out=self.timed_out,
            terminated=terminated,
        )


class ExecSessionManager:
    """管理所有活跃命令会话。"""

    def __init__(self, max_sessions: int = security.MAX_EXEC_SESSIONS) -> None:
        self.max_sessions = max_sessions
        self._sessions: dict[str, _ExecSession] = {}
        self._lock = asyncio.Lock()

    async def start(
        self,
        command: str,
        cwd: str,
        timeout: int | None,
        yield_time_ms: int,
        max_output_chars: int,
    ) -> tuple[str, SessionPoll]:
        """启动长运行命令并保存 session。"""
        async with self._lock:
            if len(self._sessions) >= self.max_sessions:
                raise RuntimeError("活跃命令会话数量已达上限")
            process = await asyncio.create_subprocess_exec(
                "/bin/bash",
                "-c",
                command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
            session_id = uuid.uuid4().hex[:12]
            session = _ExecSession(session_id, process, command, timeout)
            self._sessions[session_id] = session
        poll = await session.poll(yield_time_ms, max_output_chars)
        if poll.done:
            async with self._lock:
                self._sessions.pop(session_id, None)
        return session_id, poll

    async def write(
        self,
        session_id: str,
        chars: str,
        terminate: bool,
        yield_time_ms: int,
        max_output_chars: int,
    ) -> SessionPoll:
        """写入或轮询已有命令会话。"""
        async with self._lock:
            session = self._sessions.get(session_id)
        if session is None:
            raise KeyError(session_id)
        if chars:
            error = await session.write(chars)
            if error:
                raise RuntimeError(error)
        if terminate:
            await session.kill()
        poll = await session.poll(yield_time_ms, max_output_chars, terminated=terminate)
        if poll.done:
            async with self._lock:
                self._sessions.pop(session_id, None)
        return poll


DEFAULT_EXEC_SESSION_MANAGER = ExecSessionManager()


def format_poll(session_id: str, poll: SessionPoll) -> str:
    """格式化命令会话轮询结果。"""
    parts = [poll.output] if poll.output else []
    if poll.timed_out:
        parts.append("Error: Command timed out")
    if poll.terminated and not poll.timed_out:
        parts.append("session terminated")
    if poll.done:
        parts.append(f"Exit code: {poll.exit_code}")
    else:
        parts.append(f"Process running. session_id: {session_id}")
    parts.append(f"Elapsed: {poll.elapsed_s:.1f}s")
    return "\n".join(parts)

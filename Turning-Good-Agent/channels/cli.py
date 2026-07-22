import asyncio
import json
from collections import OrderedDict
from contextlib import suppress

from ..llm.types import ToolCall


class CliChannelAdapter:
    """管理 CLI 输出、工具状态和审批。"""

    def __init__(self) -> None:
        """初始化终端行和运行中工具状态。"""
        self._has_delta = False
        self._line_open = False
        self._active_tools: OrderedDict[str, str] = OrderedDict()
        self._tool_animation: asyncio.Task[None] | None = None
        self._rendered_tool_line_count = 0
        self._render_lock = asyncio.Lock()

    async def on_delta(self, text: str) -> None:
        """立即输出一段模型文本。"""
        if not text:
            return
        await self._stop_tool_animation()
        print(text, end="", flush=True)
        self._has_delta = True
        self._line_open = True

    async def on_status(self, text: str) -> None:
        """在独立行输出普通系统状态。"""
        await self._stop_tool_animation()
        if self._line_open:
            print()
            self._line_open = False
        print(f"[系统] {text}", flush=True)

    async def on_tool_started(self, tool_call_id: str, tool_name: str) -> None:
        """登记工具并重绘运行中状态区。"""
        async with self._render_lock:
            if self._line_open:
                print()
                self._line_open = False
            self._active_tools[tool_call_id] = tool_name
            self._render_active_tools()
            if self._tool_animation is None:
                self._tool_animation = asyncio.create_task(self._animate_tools())

    async def on_tool_finished(self, tool_call_id: str, tool_name: str, failed: bool) -> None:
        """移除工具并输出固定完成或失败行。"""
        task: asyncio.Task[None] | None = None
        async with self._render_lock:
            if tool_call_id not in self._active_tools:
                return
            self._clear_rendered_tools()
            self._active_tools.pop(tool_call_id)
            status = "工具失败" if failed else "工具完成"
            print(f"[系统] {status}：{tool_name}", flush=True)
            self._render_active_tools()
            if not self._active_tools:
                task = self._tool_animation
                self._tool_animation = None
        await self._cancel_task(task)

    async def on_completed(self, content: str) -> None:
        """结束成功输出并按需打印完整回复。"""
        await self._stop_tool_animation()
        if self._has_delta:
            if self._line_open:
                print()
            self._reset_turn()
            return
        print(content)
        self._reset_turn()

    async def on_error(self, content: str) -> None:
        """结束错误输出并打印错误内容。"""
        await self._stop_tool_animation()
        if self._line_open:
            print()
        print(content)
        self._reset_turn()

    async def request_tool_approval(self, call: ToolCall) -> str | None:
        """在 CLI 中同步请求用户确认工具调用。"""
        args = json.dumps(call.args, ensure_ascii=False)
        answer = input(f"\n[审批] 允许执行 {call.name} {args}？[y/N] ").strip().lower()
        return None if answer in {"y", "yes", "允许"} else "用户拒绝执行工具"

    async def _animate_tools(self) -> None:
        """周期性重绘全部运行中工具。"""
        dots = 1
        try:
            while True:
                await asyncio.sleep(0.3)
                async with self._render_lock:
                    if not self._active_tools:
                        return
                    self._render_active_tools(dots)
                    dots = dots % 3 + 1
        except asyncio.CancelledError:
            raise

    async def _stop_tool_animation(self) -> None:
        """清理运行中状态区并停止动画任务。"""
        task: asyncio.Task[None] | None = None
        async with self._render_lock:
            self._clear_rendered_tools()
            self._active_tools.clear()
            task = self._tool_animation
            self._tool_animation = None
        await self._cancel_task(task)

    async def _cancel_task(self, task: asyncio.Task[None] | None) -> None:
        """取消指定后台任务并等待其退出。"""
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

    def _render_active_tools(self, dots: int = 1) -> None:
        """清除旧状态区后绘制全部运行中工具。"""
        self._clear_rendered_tools()
        for tool_name in self._active_tools.values():
            print(f"\r\x1b[2K[系统] 正在调用工具：{tool_name}{'.' * dots}", flush=True)
        self._rendered_tool_line_count = len(self._active_tools)

    def _clear_rendered_tools(self) -> None:
        """清除终端中已绘制的运行中状态区。"""
        line_count = self._rendered_tool_line_count
        if not line_count:
            return
        print(f"\x1b[{line_count}A", end="")
        for _ in range(line_count):
            print("\r\x1b[2K\x1b[1B", end="")
        print(f"\x1b[{line_count}A", end="", flush=True)
        self._rendered_tool_line_count = 0

    def _reset_turn(self) -> None:
        """重置本轮文本输出状态。"""
        self._has_delta = False
        self._line_open = False

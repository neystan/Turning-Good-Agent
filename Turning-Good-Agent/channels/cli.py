class CliChannelOutput:
    """管理 CLI 单轮响应的终端输出。"""

    def __init__(self) -> None:
        """初始化当前终端行状态。"""
        self._has_delta = False
        self._line_open = False

    async def on_delta(self, text: str) -> None:
        """立即输出一段模型文本。"""
        if not text:
            return
        print(text, end="", flush=True)
        self._has_delta = True
        self._line_open = True

    async def on_status(self, text: str) -> None:
        """在独立行输出系统状态。"""
        if self._line_open:
            print()
            self._line_open = False
        print(f"[系统] {text}", flush=True)

    async def on_completed(self, content: str) -> None:
        """结束成功输出并按需打印完整回复。"""
        if self._has_delta:
            if self._line_open:
                print()
        else:
            print(content)
        self._reset()

    async def on_error(self, content: str) -> None:
        """结束错误输出并打印错误内容。"""
        if self._line_open:
            print()
        print(content)
        self._reset()

    def _reset(self) -> None:
        """重置本轮输出状态。"""
        self._has_delta = False
        self._line_open = False

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from .base import ToolResult

if TYPE_CHECKING:
    from ..config.settings import Settings


class EchoTool:
    """回显输入文本。"""

    name = "echo"
    source = "builtin"
    discoverable = True
    parallel_safe = True
    description = "回显输入文本。"
    input_schema = {
        "type": "object",
        "properties": {"text": {"type": "string", "description": "回显文本"}},
        "required": ["text"],
    }

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """返回 text 参数。"""
        return ToolResult(str(args.get("text", "")))


class NowTool:
    """返回主动能力全局时区中的当前时间。"""

    name = "now"
    worker_read_only = True
    source = "builtin"
    discoverable = True
    parallel_safe = True
    description = "返回主动能力配置时区中的当前时间和 IANA 时区名。"
    input_schema = {"type": "object", "properties": {}}

    def __init__(
        self,
        timezone_name: str = "Asia/Shanghai",
        *,
        clock: Callable[[ZoneInfo], datetime] = datetime.now,
    ) -> None:
        """绑定全局主动时区；clock 仅用于确定性测试。"""
        self.timezone_name = timezone_name
        self._timezone = ZoneInfo(timezone_name)
        self._clock = clock

    @classmethod
    def create(cls, settings: "Settings") -> "NowTool":
        """从集中配置创建工具。"""
        return cls(settings.proactive.timezone)

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """返回 ISO 格式时间。"""
        del args
        moment = self._clock(self._timezone)
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=self._timezone)
        else:
            moment = moment.astimezone(self._timezone)
        iso_time = moment.isoformat(timespec="seconds")
        return ToolResult(
            f"{iso_time}（{self.timezone_name}）",
            metadata={"timezone": self.timezone_name, "iso_time": iso_time},
        )

import asyncio
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen

from . import security
from .base import ToolResult


def _error(message: str) -> ToolResult:
    """创建错误工具结果。"""
    return ToolResult(message, {"error": True})


async def _fetch_weather(location: str) -> str:
    """从 wttr.in 获取简短天气。"""

    def _load() -> str:
        url = f"https://wttr.in/{quote(location)}?format=3"
        request = Request(url, headers={"User-Agent": "Turning-Good-Agent/0.1"})
        with urlopen(request, timeout=15) as response:  # nosec: 固定天气接口
            raw = response.read(security.MAX_TOOL_OUTPUT_CHARS)
        return raw.decode("utf-8", errors="replace").strip()

    return await asyncio.to_thread(_load)


class WeatherTool:
    """查询城市天气。"""

    name = "weather"
    source = "builtin"
    discoverable = True
    description = "查询天气。"
    input_schema = {
        "type": "object",
        "properties": {"location": {"type": "string", "description": "城市或地区", "minLength": 1}},
        "required": ["location"],
    }

    async def run(self, args: dict[str, Any]) -> ToolResult:
        """执行天气查询。"""
        location = str(args.get("location") or "").strip()
        if not location:
            return _error("location 不能为空")
        try:
            return ToolResult(security.truncate_text(await _fetch_weather(location)))
        except Exception as exc:
            return _error(f"查询天气失败：{exc}")

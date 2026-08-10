from __future__ import annotations

from typing import Protocol

from ..bus.messages import ChannelRoute
from ..config.settings import Settings
from ..tools.registry import ToolRegistry


IM_CHANNELS = frozenset({"feishu", "weixin"})


def im_command_rejection(route: ChannelRoute, content: str) -> str | None:
    """返回 IM 不支持的遗留 slash 命令提示，其他输入返回 None。"""
    if route.channel not in IM_CHANNELS:
        return None
    command = content.strip()
    if command == "/new":
        return "当前 IM 渠道不支持 /new。如需清空当前话题，请发送 /clear。"
    if command == "/exit":
        return "当前 IM 渠道不支持 /exit。"
    if command.startswith("/approve"):
        return "当前 IM 渠道不支持工具审批。请在 Web 或 CLI 中操作。"
    return None


class ChannelToolPolicy(Protocol):
    """定义不同 Channel 可见且可执行的工具边界。"""

    def allowed_tool_names(
        self,
        route: ChannelRoute,
        tools: ToolRegistry,
        settings: Settings,
    ) -> frozenset[str] | None:
        """返回当前路由允许的工具名；None 保留全量工具。"""


class DefaultChannelToolPolicy:
    """为即时消息 Channel 移除所有需要审批的工具。"""

    _IM_CHANNELS = IM_CHANNELS

    def allowed_tool_names(
        self,
        route: ChannelRoute,
        tools: ToolRegistry,
        settings: Settings,
    ) -> frozenset[str] | None:
        """根据可信路由、实时注册表和设置计算本轮工具集合。"""
        if route.channel not in self._IM_CHANNELS:
            return None
        configured_approval = set(settings.tool_permissions.approval_required_tools)
        return frozenset(
            name
            for name in tools.tool_names
            if name not in configured_approval and not bool(getattr(tools.get(name), "approval_required", False))
        )

from __future__ import annotations

from typing import Protocol

from ..bus.messages import ChannelRoute
from ..config.settings import Settings
from ..tools.registry import ToolRegistry


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

    _IM_CHANNELS = frozenset({"feishu", "weixin"})

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

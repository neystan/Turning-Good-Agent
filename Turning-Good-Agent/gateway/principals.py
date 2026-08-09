from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ..bus.messages import ChannelRoute
from ..channels.policy import ChannelToolPolicy, DefaultChannelToolPolicy
from ..config.settings import GatewaySettings, Settings
from ..memory.long_term import ProfileMemory
from ..runtime.principal_context import PrincipalRuntimeContext, PrincipalSessionReader
from ..sessions.manager import SessionManager
from ..sessions.store import JsonlSessionStore
from ..tools.registry import ToolRegistry


@dataclass(frozen=True, slots=True)
class _PrincipalStorageBundle:
    data_root: Path
    profile_memory: ProfileMemory
    sessions: PrincipalSessionReader


class GatewayPrincipalResolver:
    """为一个 Gateway 解析 Owner 与非 Owner 的 Runtime 数据根。"""

    def __init__(
        self,
        settings: Settings | Path,
        *,
        owner_sessions: SessionManager | None = None,
        owner_profile_memory: ProfileMemory | None = None,
        tools: ToolRegistry | None = None,
        policy: ChannelToolPolicy | None = None,
        owner_principal_id: str | None = None,
    ) -> None:
        if isinstance(settings, Path):
            settings = Settings(
                data_dir=settings,
                gateway=GatewaySettings(principal_id=owner_principal_id or "local-user"),
            )
        self.settings = settings
        self.owner_principal_id = owner_principal_id or settings.gateway.principal_id
        if not self.owner_principal_id:
            raise ValueError("owner principal id 无效")
        self._owner_sessions = owner_sessions or SessionManager(
            JsonlSessionStore(settings.data_dir), settings
        )
        self._owner_profile_memory = owner_profile_memory or ProfileMemory(
            settings.data_dir, settings.proactive
        )
        self._tools = tools if tools is not None else ToolRegistry()
        self._policy: ChannelToolPolicy = policy or DefaultChannelToolPolicy()
        self._bundles: dict[str, _PrincipalStorageBundle] = {}

    def set_channel_tool_policy(self, policy: ChannelToolPolicy) -> None:
        """更新后续主体 Context 使用的工具策略。"""
        self._policy = policy

    def forget_non_owner(self, principal_id: str) -> Path:
        """忘记独立主体缓存并返回其 opaque 数据根。"""
        if not isinstance(principal_id, str) or not principal_id:
            raise ValueError("principal_id 不能为空")
        if principal_id == self.owner_principal_id:
            raise ValueError("Owner 主体数据根不可移除")
        bundle = self._bundles.pop(principal_id, None)
        return bundle.data_root if bundle is not None else self._non_owner_root(principal_id)

    def resolve(self, route: ChannelRoute) -> PrincipalRuntimeContext:
        """根据可信路由返回主体专属 Context。"""
        if not isinstance(route, ChannelRoute) or not route.principal_id:
            raise ValueError("route principal id 无效")
        bundle = self._bundles.get(route.principal_id)
        if bundle is None:
            if route.principal_id == self.owner_principal_id:
                data_root = self.settings.data_dir
                sessions = self._owner_sessions
                profile_memory = self._owner_profile_memory
            else:
                data_root = self._non_owner_root(route.principal_id)
                sessions = SessionManager(JsonlSessionStore(data_root), self.settings)
                profile_memory = ProfileMemory(data_root, self.settings.proactive)
            bundle = _PrincipalStorageBundle(
                data_root=data_root,
                profile_memory=profile_memory,
                sessions=PrincipalSessionReader(
                    sessions,
                    route.principal_id,
                    allow_global_approval=route.principal_id == self.owner_principal_id,
                ),
            )
            self._bundles[route.principal_id] = bundle
        context = PrincipalRuntimeContext(
            principal_id=route.principal_id,
            data_root=bundle.data_root,
            profile_memory=bundle.profile_memory,
            sessions=bundle.sessions,
            allowed_tool_names=self._allowed_tool_names(route),
        )
        return context

    def _allowed_tool_names(self, route: ChannelRoute) -> frozenset[str] | None:
        return self._policy.allowed_tool_names(route, self._tools, self.settings)

    def _non_owner_root(self, principal_id: str) -> Path:
        digest = hashlib.sha256(
            b"tga-principal-v1\0" + principal_id.encode("utf-8")
        ).hexdigest()
        return self.settings.data_dir / "principals" / digest

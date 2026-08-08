from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from ..bus.messages import ChannelRoute
from ..memory.long_term import ProfileMemory
from ..runtime.principal_context import PrincipalSessionReader
from ..skills.manager import SkillManager
from .breakbeat import BreakbeatManager
from .cron import CronManager
from .dream import DreamManager
from .incidents import IncidentMonitor
from .skill_evolution import SkillEvolutionManager
from .store import ProactiveStore


@dataclass(slots=True)
class ProactiveWorkspace:
    """保存一个主体独立的主动状态与领域 manager。"""

    principal_id: str
    store: ProactiveStore
    profile_memory: ProfileMemory
    draft_skills: SkillManager
    sessions: PrincipalSessionReader
    cron: CronManager
    breakbeat: BreakbeatManager
    dream: DreamManager
    skill_evolution: SkillEvolutionManager
    incidents: IncidentMonitor


class WorkspaceProvider(Protocol):
    """让交互式 Tool 按当前可信路由动态选择 workspace。"""

    def workspace_for(self, principal_id: str) -> ProactiveWorkspace:
        """返回主体 workspace。"""

    def workspace_for_route(self, route: ChannelRoute) -> ProactiveWorkspace:
        """返回可信路由所属 workspace。"""

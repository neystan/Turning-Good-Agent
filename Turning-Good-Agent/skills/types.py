from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

@dataclass(frozen=True, slots=True)
class SkillManifest:
    """保存已校验 Skill 的完整内容。"""

    name: str
    description: str
    body: str
    path: Path
    extra_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SkillCatalogEntry:
    """保存注入根提示词的最小 Skill 元数据。"""

    name: str
    description: str
    extra_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class LoadedSkill:
    """保存当前轮已加载的完整 Skill 附件。"""

    name: str
    body: str
    token_count: int


@dataclass(frozen=True, slots=True)
class SkillScanError:
    """保存单个目录的扫描错误。"""

    directory: str
    message: str

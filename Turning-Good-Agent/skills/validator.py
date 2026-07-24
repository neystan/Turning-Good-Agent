from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from .types import SkillManifest


_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class SkillValidator:
    """解析并校验外部 SKILL.md。"""

    def validate(self, directory: Path) -> SkillManifest:
        """校验目录内 SKILL.md 并返回标准 Manifest。"""
        skill_file = directory / "SKILL.md"
        if not skill_file.is_file():
            raise ValueError("缺少 SKILL.md")
        metadata, body = self._parse(skill_file.read_text(encoding="utf-8"))
        name = metadata.pop("name", None)
        description = metadata.pop("description", None)
        if not isinstance(name, str) or not name.strip():
            raise ValueError("frontmatter 缺少 name")
        if not _NAME_PATTERN.fullmatch(name):
            raise ValueError("name 仅支持小写字母、数字和连字符")
        if directory.name != name:
            raise ValueError("目录名必须与 name 一致")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("frontmatter 缺少 description")
        if not body.strip():
            raise ValueError("SKILL.md 正文不能为空")
        return SkillManifest(name, description.strip(), body.strip(), skill_file, metadata)

    def _parse(self, content: str) -> tuple[dict[str, Any], str]:
        """解析受限 YAML frontmatter 与正文。"""
        if not content.startswith("---\n"):
            raise ValueError("SKILL.md 缺少 frontmatter")
        end = content.find("\n---", 4)
        if end < 0:
            raise ValueError("SKILL.md frontmatter 未结束")
        raw_metadata = content[4:end]
        body = content[end + 4 :].lstrip("\r\n")
        return self._parse_mapping(raw_metadata.splitlines()), body

    def _parse_mapping(self, lines: list[str]) -> dict[str, Any]:
        """解析 Skill 格式需要的简单 YAML 映射。"""
        parsed: dict[str, Any] = {}
        current_mapping: dict[str, Any] | None = None
        for raw_line in lines:
            if not raw_line.strip() or raw_line.lstrip().startswith("#"):
                continue
            if raw_line.startswith((" ", "\t")):
                if current_mapping is None:
                    raise ValueError("frontmatter 缩进无效")
                key, value = self._split_pair(raw_line.strip())
                current_mapping[key] = self._parse_value(value)
                continue
            key, value = self._split_pair(raw_line)
            if value:
                parsed[key] = self._parse_value(value)
                current_mapping = None
            else:
                current_mapping = {}
                parsed[key] = current_mapping
        return parsed

    @staticmethod
    def _split_pair(line: str) -> tuple[str, str]:
        """拆分一个 YAML 键值对。"""
        if ":" not in line:
            raise ValueError("frontmatter 格式无效")
        key, value = line.split(":", 1)
        if not key.strip():
            raise ValueError("frontmatter 键不能为空")
        return key.strip(), value.strip()

    @staticmethod
    def _parse_value(value: str) -> Any:
        """解析常见标量和 JSON 风格复合值。"""
        if not value:
            return ""
        if value[0:1] in {'"', "'"}:
            quote = value[0]
            if len(value) < 2 or value[-1] != quote:
                raise ValueError("frontmatter 引号未闭合")
            return value[1:-1]
        if value.startswith(("[", "{")):
            try:
                return json.loads(value)
            except json.JSONDecodeError as exc:
                raise ValueError("frontmatter JSON 值无效") from exc
        return value

from __future__ import annotations

import importlib
import inspect
import pkgutil
from typing import Any

from .base import BaseTool
from .registry import ToolRegistry


SKIP_MODULES = {"base", "registry", "executor", "loader", "schema", "__init__"}


class ToolLoader:
    """扫描并加载内置工具。"""

    def __init__(self, package: Any | None = None) -> None:
        if package is None:
            package = importlib.import_module(__package__)
        self.package = package

    def discover(self) -> list[type]:
        """发现当前 tools 包中的工具类。"""
        discovered: list[type] = []
        seen: set[int] = set()
        for _, module_name, _ in pkgutil.iter_modules(self.package.__path__):
            if module_name.startswith("_") or module_name in SKIP_MODULES:
                continue
            module = importlib.import_module(f".{module_name}", self.package.__name__)
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if not self._is_tool_class(attr, seen):
                    continue
                seen.add(id(attr))
                discovered.append(attr)
        return sorted(discovered, key=lambda item: item.__name__)

    def load(self, registry: ToolRegistry, context: Any | None = None) -> list[str]:
        """实例化可用工具并注册到 registry。"""
        loaded: list[str] = []
        for tool_cls in self.discover():
            if not self._enabled(tool_cls, context):
                continue
            tool = self._create(tool_cls, context)
            registry.register(tool)
            loaded.append(tool.name)
        return sorted(loaded)

    @staticmethod
    def _is_tool_class(value: Any, seen: set[int]) -> bool:
        """判断对象是否是可发现工具类。"""
        return (
            inspect.isclass(value)
            and id(value) not in seen
            and not inspect.isabstract(value)
            and getattr(value, "discoverable", True)
            and hasattr(value, "name")
            and hasattr(value, "description")
            and hasattr(value, "input_schema")
            and hasattr(value, "run")
        )

    @staticmethod
    def _enabled(tool_cls: type, context: Any | None) -> bool:
        """按工具类 enabled 钩子判断是否启用。"""
        enabled = getattr(tool_cls, "enabled", None)
        if callable(enabled):
            return bool(enabled(context))
        return True

    @staticmethod
    def _create(tool_cls: type, context: Any | None) -> BaseTool:
        """创建工具实例。"""
        create = getattr(tool_cls, "create", None)
        if callable(create):
            return create(context)
        return tool_cls()

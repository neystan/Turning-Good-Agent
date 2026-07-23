from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class McpCapability:
    """描述内存中的一项 MCP 能力。"""

    server_name: str
    kind: str
    name: str
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class McpCatalog:
    """保存单个 Server 发现到的能力目录。"""

    tools: list[McpCapability] = field(default_factory=list)
    resources: list[McpCapability] = field(default_factory=list)
    resource_templates: list[McpCapability] = field(default_factory=list)
    prompts: list[McpCapability] = field(default_factory=list)


@dataclass(slots=True)
class McpServerStatus:
    """记录单个 MCP Server 的连接状态。"""

    name: str
    connected: bool = False
    error: str | None = None

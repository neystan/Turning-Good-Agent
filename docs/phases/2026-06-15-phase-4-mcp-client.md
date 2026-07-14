# Turning-Good-Agent Phase 4 MCP Client MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 MCP client 最小闭环，让 Turning-Good-Agent 可以连接 MCP server、发现工具并调用工具。

**Architecture:** MCP client 独立放在 `mcp/`，不把协议细节塞进 `AgentLoop`。Runtime 只消费统一后的 tool schema，MCP tool 通过 adapter 注册进 `ToolRegistry`。本阶段依赖 Phase 2 中收口后的工具边界和 Phase 3 建立的 Runtime 扩展边界。

**Tech Stack:** Python 3.11+、asyncio、JSON-RPC、stdio transport，后续扩展 SSE 和 streamable HTTP。

---

## Scope

本阶段实现：

- `stdio` transport
- MCP initialize
- `tools/list`
- `tools/call`
- MCP tool adapter 到 `BaseTool`
- 配置文件中声明 MCP server

本阶段不实现：

- 完整三传输
- 复杂认证
- resource 缓存
- prompt template 自动注入

## Target File Map

Create: `Turning-Good-Agent/mcp/types.py`

定义 MCP server 配置、tool 定义和调用结果。

Create: `Turning-Good-Agent/mcp/stdio.py`

管理 stdio 子进程、发送 JSON-RPC 请求、读取响应。

Create: `Turning-Good-Agent/mcp/client.py`

实现 initialize、list_tools、call_tool。

Create: `Turning-Good-Agent/mcp/adapter.py`

把 MCP tool 转成 `BaseTool` 可注册对象。

Modify: `Turning-Good-Agent/config/settings.py`

增加 MCP server 配置列表。

Modify: `Turning-Good-Agent/runtime/runtime.py`

创建默认 runtime 时加载 MCP tools。

## Task 1: MCP Config

- [ ] **Step 1: 增加 settings 配置**

建议结构：

```json
{
  "mcp": {
    "servers": [
      {
        "name": "demo",
        "transport": "stdio",
        "command": "python",
        "args": ["server.py"]
      }
    ]
  }
}
```

- [ ] **Step 2: `settings.py` 增加 dataclass**

建议：

```python
@dataclass(slots=True)
class McpServerSettings:
    name: str
    transport: str
    command: str
    args: list[str] = field(default_factory=list)
```

## Task 2: JSON-RPC Stdio Transport

- [ ] **Step 1: 创建 stdio client**

要求：

- 启动子进程
- 写入 JSON-RPC request
- 读取 JSON-RPC response
- 每个 request 有递增 id

- [ ] **Step 2: 错误处理**

要求：

- 进程启动失败返回明确错误
- JSON parse 失败返回明确错误
- timeout 返回明确错误

## Task 3: MCP Client

- [ ] **Step 1: 实现 initialize**

发送：

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-03-26",
    "capabilities": {},
    "clientInfo": {"name": "Turning-Good-Agent", "version": "0.1.0"}
  }
}
```

- [ ] **Step 2: 实现 tools/list**

返回工具名称、描述和 input schema。

- [ ] **Step 3: 实现 tools/call**

输入 tool name 和 arguments，返回文本结果。

## Task 4: ToolRegistry Adapter

- [ ] **Step 1: 把 MCP tool 包装成 `BaseTool`**

包装后的 tool：

- `name` 包含 server 前缀，避免冲突
- `description` 来自 MCP tool
- `input_schema` 来自 MCP tool
- `run()` 调用 MCP `tools/call`

- [ ] **Step 2: 注册到 Runtime**

`AgentRuntime.create_default()` 中加载配置里的 MCP server，并注册工具。

## Completion Criteria

- 可以连接一个 stdio MCP server。
- 可以发现 MCP tools。
- 可以通过真实 LLM 路径调用 MCP tool。
- MCP 失败不影响内置工具注册。

# Turning-Good-Agent Phase 2 真实 LLM Tool Calling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 OpenAI-compatible 真实模型可以调用 `ToolRegistry` 中注册的工具，并把 tool call 与 tool result 写入会话文件。

**Architecture:** 保持 `AgentLoop` 为唯一工具调用循环。`ToolRegistry.schemas()` 继续作为内部工具 schema 源，新增 OpenAI-compatible schema 转换层；`OpenAICompatibleLLM` 负责解析 assistant tool calls 并返回统一 `LLMResponse`。

**Tech Stack:** Python 3.11+、OpenAI-compatible Chat Completions、asyncio、JSON/JSONL。

---

## Scope

本阶段实现：

- OpenAI-compatible tools schema 转换
- 真实模型返回 tool_calls 的解析
- tool call 消息和 tool result 消息进入 AgentLoop working messages
- tool call 记录落盘到 session trace 或 messages
- 用真实模型测试 `echo` / `now`

本阶段不实现：

- MCP tools
- skills tools
- 多模型 provider
- parallel tool calls 的复杂调度

## Target File Map

Modify: `Turning-Good-Agent/llm/openai_compatible.py`

解析 OpenAI-compatible 响应中的 `tool_calls`，返回 `LLMResponse(tool_calls=...)`。

Modify: `Turning-Good-Agent/tools/registry.py`

保留内部 schema，同时提供或配合生成 OpenAI-compatible tool schema。

Modify: `Turning-Good-Agent/runtime/agent_loop.py`

把 assistant tool call 和 tool result 追加到 working messages，确保真实模型可以继续下一轮推理。

Modify: `Turning-Good-Agent/runtime/runtime.py`

把本轮 tool calls 写入可观测记录，保证用户可以从文件看到调用过程。

Modify: `docs/TURNING_GOOD_AGENT_SPEC.md`

更新 Phase 2 完成状态和真实 LLM tool calling 边界。

## Task 1: Tool Schema Adapter

- [ ] **Step 1: 定义 OpenAI-compatible schema 输出规则**

内部 schema：

```python
{
    "name": "now",
    "description": "返回当前时间。",
    "input_schema": {"type": "object", "properties": {}, "required": []},
}
```

转换后：

```python
{
    "type": "function",
    "function": {
        "name": "now",
        "description": "返回当前时间。",
        "parameters": {"type": "object", "properties": {}, "required": []},
    },
}
```

- [ ] **Step 2: 在 `tools/registry.py` 增加清晰方法**

建议方法：

```python
def openai_tools(self) -> list[dict[str, object]]:
    """返回 OpenAI-compatible tool schema。"""
```

- [ ] **Step 3: 本地验证**

运行：

```bash
python -m Turning-Good-Agent chat
```

然后输入：

```text
time
```

预期 FakeLLM 仍可通过工具路径返回时间；真实模型 schema 转换在后续手动验证中确认。

## Task 2: Parse Real Tool Calls

- [ ] **Step 1: 修改 `OpenAICompatibleLLM.complete()`**

要求：

- 发送 `tools` 参数给 Chat Completions
- 从响应中读取 `message.tool_calls`
- 把每个 tool call 转成统一 `ToolCall`
- `arguments` 必须 JSON parse，失败时返回空 dict 并保留错误 metadata

- [ ] **Step 2: 统一返回**

当模型返回工具调用时：

```python
return LLMResponse(content=message.content or "", tool_calls=tool_calls)
```

当没有工具调用时：

```python
return LLMResponse(content=message.content or "")
```

## Task 3: AgentLoop Message Protocol

- [ ] **Step 1: 保存 assistant tool call 消息**

在执行工具前，把 assistant tool call message 追加到 `working`。

- [ ] **Step 2: 保存 tool result 消息**

工具执行后追加：

```python
{
    "role": "tool",
    "tool_call_id": call.id,
    "name": call.name,
    "content": record["content"],
}
```

- [ ] **Step 3: 达到 max_tool_rounds 后返回明确文本**

继续沿用：

```text
工具调用轮数已达到上限。
```

## Task 4: Observability

- [ ] **Step 1: 在 runtime 保存 tool calls**

本阶段先把 `ctx.tool_calls` 写入 trace metadata 或 token usage metadata 中的最小字段。

建议字段：

```text
tool_call_count
tool_names
```

- [ ] **Step 2: 不扩大 session metadata**

不要把 tool call 统计写入 `session.json.metadata`。

## Task 5: Manual Verification

- [ ] **Step 1: 配置真实模型**

编辑 `settings.local.json`：

```json
{
  "llm": {
    "provider": "openai-compatible",
    "api_key": "你的 API Key",
    "base_url": "https://api.openai.com/v1",
    "model": "你的模型名"
  }
}
```

- [ ] **Step 2: 运行 CLI**

```bash
cd /download/Turning-Good-Agent
python -m Turning-Good-Agent chat
```

- [ ] **Step 3: 手动输入**

```text
现在几点？
```

预期：

- 模型调用 `now`
- CLI 返回包含当前时间的回答
- session 目录中能看到 tool 调用记录

## Completion Criteria

- 真实 LLM 可以调用 `now`。
- 真实 LLM 可以调用 `echo`。
- tool call 和 tool result 不只存在内存中，也能从文件检查。
- FakeLLM 行为不被破坏。

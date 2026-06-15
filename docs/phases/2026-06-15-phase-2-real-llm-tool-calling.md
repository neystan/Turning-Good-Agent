# Turning-Good-Agent Phase 2 真实 LLM SDK 化与 Tool Calling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使用 OpenAI Python SDK 接入真实 LLM，并让 OpenAI-compatible 真实模型可以调用 `ToolRegistry` 中注册的工具，把 tool call 与 tool result 写入会话文件。

**Architecture:** 保持 `AgentLoop` 为唯一工具调用循环。`OpenAICompatibleLLM` 使用 `client.chat.completions.create(...)` 作为真实模型调用主路径，并把 SDK 响应归一化为内部 `LLMResponse`。`ToolRegistry.schemas()` 继续作为内部工具 schema 源，新增 OpenAI-compatible schema 转换层。

**Tech Stack:** Python 3.11+、OpenAI Python SDK、OpenAI-compatible Chat Completions、asyncio、JSON/JSONL。

---

## Scope

本阶段实现：

- OpenAI Python SDK 依赖接入
- `OpenAICompatibleLLM` 改为 SDK 调用
- `openai_compatible` 接入族统一接入
- 真实模型空 `content`、`tool_calls`、兼容扩展字段的响应归一化
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
- 多厂商专用 SDK 适配

## Target File Map

Modify: `pyproject.toml`

增加 `openai` 运行依赖。

Modify: `Turning-Good-Agent/config/settings.py`

保持集中配置，必要时补充真实 LLM timeout 等 Provider 参数。不要把 API key 写入文档或代码。

Modify: `Turning-Good-Agent/llm/openai_compatible.py`

使用 OpenAI Python SDK 调用 Chat Completions，解析 `content`、`tool_calls` 和兼容扩展字段，返回统一 `LLMResponse`。

Modify: `Turning-Good-Agent/cli.py`

继续支持 `openai-compatible`，兼容 OpenAI Chat Completions 协议的厂商统一走这一接入族。

Modify: `Turning-Good-Agent/tools/registry.py`

保留内部 schema，同时提供或配合生成 OpenAI-compatible tool schema。

Modify: `Turning-Good-Agent/runtime/agent_loop.py`

把 assistant tool call 和 tool result 追加到 working messages，确保真实模型可以继续下一轮推理。

Modify: `Turning-Good-Agent/runtime/runtime.py`

把本轮 tool calls 写入可观测记录，保证用户可以从文件看到调用过程。

Modify: `README.md`

更新真实 LLM 配置说明，说明推荐 provider 名称和 DeepSeek 等兼容服务的写法。

Modify: `docs/TURNING_GOOD_AGENT_SPEC.md`

更新 Phase 2 完成状态和真实 LLM tool calling 边界。

## Task 1: SDK Provider Baseline

- [ ] **Step 1: 增加 OpenAI SDK 依赖**

在 `pyproject.toml` 增加：

```toml
dependencies = ["openai>=1.0.0"]
```

- [ ] **Step 2: 改造 `OpenAICompatibleLLM`**

要求：

- 使用 `OpenAI(api_key=..., base_url=...)`
- 使用 `client.chat.completions.create(...)`
- 保留 `model`、`messages`、`tools` 参数入口
- SDK 调用放入线程执行，避免阻塞当前 async runtime

建议结构：

```python
client = OpenAI(api_key=self.api_key, base_url=self.base_url)
response = await asyncio.to_thread(
    client.chat.completions.create,
    model=self.model,
    messages=messages,
    tools=tools or None,
)
```

- [ ] **Step 3: 本地验证**

运行：

```bash
python -m Turning-Good-Agent chat
```

预期真实 LLM 能稳定返回纯文本，并通过 SDK 路径统一解析响应。

## Task 2: Response Normalization

- [ ] **Step 1: 统一解析 SDK message**

要求：

- 读取 `choices[0].message`
- `message.content` 为空时返回空字符串，但不能丢失 `tool_calls`
- 如果 provider 暴露 `reasoning_content`，只用于调试或后续 trace，不直接作为最终用户回复
- 如果响应没有 `choices` 或没有 `message`，抛出清晰异常

- [ ] **Step 2: 修复“看起来无回复”**

当前真实对话偶发无回复的主要风险是只读取 `message.content`。Phase 2 必须保证：

- 有 `tool_calls` 时进入工具循环
- 无 `content` 且无 `tool_calls` 时返回明确错误文本或抛出可诊断异常
- CLI 不应静默打印空行

## Task 3: Tool Schema Adapter

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

预期真实模型 schema 转换可用，并能在后续手动验证中确认工具路径。

## Task 4: Parse Real Tool Calls

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

## Task 5: AgentLoop Message Protocol

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

## Task 6: Observability

- [ ] **Step 1: 在 runtime 保存 tool calls**

本阶段先把 `ctx.tool_calls` 写入 trace metadata 或 token usage metadata 中的最小字段。

建议字段：

```text
tool_call_count
tool_names
```

- [ ] **Step 2: 不扩大 session metadata**

不要把 tool call 统计写入 `session.json.metadata`。

## Task 7: Manual Verification

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

DeepSeek 等 OpenAI-compatible 服务仍然统一配置为：

```json
{
  "llm": {
    "provider": "openai-compatible",
    "api_key": "你的 API Key",
    "base_url": "https://api.deepseek.com",
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

- `OpenAICompatibleLLM` 使用 OpenAI Python SDK。
- 真实 LLM 纯文本对话稳定返回，不静默空回复。
- 真实 LLM 可以调用 `now`。
- 真实 LLM 可以调用 `echo`。
- tool call 和 tool result 至少进入 `AgentLoop` working messages。

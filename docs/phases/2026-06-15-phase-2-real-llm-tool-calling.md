# Turning-Good-Agent Phase 2 真实 LLM SDK 化、Tool Calling 与 CLI 流式输出 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 使用 OpenAI Python SDK 接入真实 LLM，并让 OpenAI-compatible 真实模型可以调用 `ToolRegistry` 中注册的工具；tool call 与 tool result 进入本轮 `AgentLoop` working messages，工具统计写入 `turn_traces.jsonl` 的 RUN metadata；同时为 CLI 纯文本流式输出增加可配置开关。

**Architecture:** 保持 `AgentLoop` 为唯一工具调用循环。`OpenAICompatibleLLM` 使用 `AsyncOpenAI().chat.completions.create(...)` 作为真实模型调用主路径，并把 SDK 响应归一化为内部 `LLMResponse`。`ToolRegistry.schemas()` 继续作为内部工具 schema 源，新增 OpenAI-compatible schema 转换层。流式输出作为同一 LLM 接入族下的可选能力，通过 `settings.llm.streaming_enabled` 显式开启，默认开启。

**Tech Stack:** Python 3.11+、OpenAI Python SDK、OpenAI-compatible Chat Completions、asyncio、JSON/JSONL。

---

## Current Completion Status

代码核对结论：Phase 2 主路径已经完成。

已完成：

- `OpenAICompatibleLLM` 使用 OpenAI Python SDK 的 `AsyncOpenAI`。
- 非流式调用通过 `await client.chat.completions.create(...)` 执行。
- 流式调用通过 SDK `stream=True` 和 `async for` 消费 chunk。
- `openai-compatible` 是当前唯一 LLM 接入族；DeepSeek、Qwen 等兼容服务统一走这一 Provider 名称。
- 非流式和流式都要求 provider 返回真实 `usage`；缺失时本轮失败，不写 token 账本。
- tool call 解析采用严格模式，缺少 `id`、`function.name` 或参数不是合法 JSON object 时直接报错。
- `BaseTool` 保持轻量接口，参数归一化和 JSON Schema 校验由 `tools/base.py` 的函数与 `ToolRegistry.prepare_call()` 承担。
- `ToolLoader` 自动加载内置工具，当前内置工具为 `echo` 和 `now`。
- 工具 schema 输出稳定排序，并通过 `openai_tools()` 转成 OpenAI-compatible schema。
- `AgentLoop` 会把 assistant tool call message 和 tool result message 追加到本轮 working messages。
- CLI 文本流式输出通过 `settings.llm.streaming_enabled` 控制，默认值为 `true`。
- 最终只把完整 user/assistant 消息写入 `messages.jsonl`，不保存每个流式 chunk。
- RUN 状态 trace metadata 记录 `tool_call_count` 和 `tool_names`。

已明确的 Phase 2 边界：

- tool call 和 tool result 不作为独立会话消息写入 `messages.jsonl`。
- 当前没有独立的 tool call 明细落盘文件；只有 RUN trace 的最小统计。
- Web、微信、飞书 channel 的流式展示不属于 Phase 2。
- MCP tools、skills tools、Python entry_points 插件不属于 Phase 2。
- parallel tool calls 的复杂调度不属于 Phase 2。
- 真实 API 的手工验证依赖本地 `settings.local.json`，仓库自动测试只覆盖代码路径和 fake/mock LLM 行为。

## Scope

本阶段实现：

- OpenAI Python SDK 依赖接入
- `OpenAICompatibleLLM` 改为 SDK 调用
- `openai_compatible` 接入族统一接入
- 真实模型空 `content`、`tool_calls`、兼容扩展字段的响应归一化
- OpenAI-compatible tools schema 转换
- tools 参数归一化和 JSON Schema 校验
- 内置工具自动加载
- 工具 schema 稳定排序
- 真实模型返回 tool_calls 的解析
- tool call 消息和 tool result 消息进入 AgentLoop working messages
- tool call 最小统计落盘到 RUN state trace metadata
- CLI 纯文本流式输出开关
- 非流式和流式共享最终消息落盘规则
- 用真实模型测试 `echo` / `now`

本阶段不实现：

- MCP tools
- skills tools
- 多模型 provider
- parallel tool calls 的复杂调度
- 多厂商专用 SDK 适配
- 流式 tool calling
- Web、微信、飞书 channel 的流式展示
- entry_points 第三方工具插件
- 完整 Schema 类体系

## Target File Map

Modify: `pyproject.toml`

增加 `openai` 运行依赖。

Modify: `Turning-Good-Agent/config/settings.py`

保持集中配置，补充真实 LLM timeout、retry 和 streaming 开关等 Provider 参数。不要把 API key 写入文档或代码。

Modify: `Turning-Good-Agent/llm/types.py`

定义 `LLMChunk`，用于表达流式文本增量和完成状态。

Modify: `Turning-Good-Agent/llm/openai_compatible.py`

使用 OpenAI Python SDK 调用 Chat Completions，解析 `content`、`tool_calls` 和兼容扩展字段，返回统一 `LLMResponse`。新增 `stream()` 路径，使用 `stream=True` 解析文本增量。

Modify: `Turning-Good-Agent/cli.py`

继续支持 `openai-compatible`，兼容 OpenAI Chat Completions 协议的厂商统一走这一接入族。

Modify: `Turning-Good-Agent/tools/registry.py`

保留内部 schema，同时提供或配合生成 OpenAI-compatible tool schema。新增 `prepare_call()`，集中处理工具查找、参数归一化、参数校验和错误文本。schema 输出保持稳定排序。

Modify: `Turning-Good-Agent/tools/base.py`

扩展 `BaseTool` 最小接口，增加参数归一化和参数校验能力。保留当前接口风格，不引入复杂继承层。

Add: `Turning-Good-Agent/tools/loader.py`

自动扫描并加载内置工具。第一版只处理当前包内工具类，不支持 entry_points 插件。

Modify: `Turning-Good-Agent/runtime/agent_loop.py`

把 assistant tool call 和 tool result 追加到 working messages，确保真实模型可以继续下一轮推理。流式模式第一版只处理纯文本，不处理流式 tool call delta。

Modify: `Turning-Good-Agent/bus/messages.py`

为后续 channel 统一输出补充流式响应事件类型，例如 `response.started`、`response.delta`、`response.completed` 和 `response.error`。

Modify: `Turning-Good-Agent/runtime/state.py`

把本轮 tool calls 归一化为 RUN 状态 trace metadata，保证用户可以从 `turn_traces.jsonl` 看到最小调用统计。

Modify: `README.md`

更新真实 LLM 配置说明，说明推荐 provider 名称和 DeepSeek 等兼容服务的写法。

Modify: `docs/TURNING_GOOD_AGENT_SPEC.md`

更新 Phase 2 完成状态和真实 LLM tool calling 边界。

## Task 1: SDK Provider Baseline

- [x] **Step 1: 增加 OpenAI SDK 依赖**

在 `pyproject.toml` 增加：

```toml
dependencies = ["openai>=1.0.0"]
```

- [x] **Step 2: 改造 `OpenAICompatibleLLM`**

要求：

- 使用 `AsyncOpenAI(api_key=..., base_url=...)`
- 使用 `client.chat.completions.create(...)`
- 保留 `model`、`messages`、`tools` 参数入口
- 全路径保持 async，不再在 Runtime 内包同步 SDK 调用

建议结构：

```python
client = AsyncOpenAI(api_key=self.api_key, base_url=self.base_url)
response = await client.chat.completions.create(
    model=self.model,
    messages=messages,
    tools=tools or None,
)
```

- [x] **Step 3: 本地验证**

运行：

```bash
python -m Turning-Good-Agent chat
```

预期真实 LLM 能稳定返回纯文本，并通过 SDK 路径统一解析响应。

## Task 2: Response Normalization

- [x] **Step 1: 统一解析 SDK message**

要求：

- 读取 `choices[0].message`
- `message.content` 为空时返回空字符串，但不能丢失 `tool_calls`
- 如果 provider 暴露 `reasoning_content`，只用于调试或后续 trace，不直接作为最终用户回复
- 如果响应没有 `choices` 或没有 `message`，抛出清晰异常

- [x] **Step 2: 修复“看起来无回复”**

当前真实对话偶发无回复的主要风险是只读取 `message.content`。Phase 2 必须保证：

- 有 `tool_calls` 时进入工具循环
- 无 `content` 且无 `tool_calls` 时返回明确错误文本或抛出可诊断异常
- CLI 不应静默打印空行

## Task 3: Tool Foundation

- [x] **Step 1: 扩展 `BaseTool` 参数边界**

目标：

- 继续保留 `name`、`description`、`input_schema`、`run(args)` 这条简单接口。
- 增加默认 `cast_args(args)` 和 `validate_args(args)`。
- `cast_args()` 只做安全转换：
  - `"3"` -> `3`
  - `"true"` / `"false"` -> `bool`
  - 其他无法确定的值保持原样。
- `validate_args()` 使用 JSON Schema object 做最小校验：
  - 参数必须是 object。
  - required 字段必须存在。
  - 基础类型必须匹配。
  - enum、minimum、maximum、minLength、maxLength、minItems、maxItems 尽量支持。

当前不引入 nanobot 的完整 `Schema` 类体系。

- [x] **Step 2: 增加 `ToolRegistry.prepare_call()`**

建议接口：

```python
def prepare_call(
    self,
    name: str,
    args: dict[str, Any],
) -> tuple[BaseTool | None, dict[str, Any], str | None]:
    """查找工具、归一化参数并返回错误文本。"""
```

行为：

- 工具不存在时返回可用工具列表。
- 参数不是 object 时返回明确错误。
- 参数校验失败时返回错误列表。
- 成功时返回工具实例和归一化后的参数。

- [x] **Step 3: 收敛 `ToolExecutor`**

`ToolExecutor` 不直接 `registry.get(...).run(...)`，而是：

1. 调用 `registry.prepare_call()`。
2. 有错误时返回 tool result record。
3. 无错误时执行工具。
4. 记录 `duration_ms` 和 `error`。

- [x] **Step 4: 增加工具稳定排序**

`ToolRegistry` 输出 schema 时使用稳定顺序：

1. 内置工具在前。
2. MCP tools 在后，默认按 `mcp_` 前缀识别。
3. 同组内按工具名排序。

第一版不做 schema 缓存。

## Task 4: Tool Loader

- [x] **Step 1: 新增 `tools/loader.py`**

目标：

- 自动扫描 `Turning-Good-Agent.tools` 包。
- 跳过基础模块：`base`、`registry`、`executor`、`loader`、`schema`、`__init__`。
- 找到可实例化工具类。
- 调用 `enabled(settings/context)` 判断是否启用。
- 调用 `create(settings/context)` 创建工具实例。
- 注册进 `ToolRegistry`。

- [x] **Step 2: 给工具类增加最小 metadata**

建议字段：

```python
source = "builtin"
discoverable = True
```

后续 MCP adapter 可以使用：

```python
source = "mcp"
```

- [x] **Step 3: Runtime 默认使用 loader**

`AgentRuntime.create_default()` 不再手动注册每个内置工具，而是通过 `ToolLoader` 加载。

- [x] **Step 4: 明确不做插件系统**

当前不支持：

- Python entry_points
- 第三方工具包自动安装
- 动态远程工具加载

这些能力等工具边界稳定后再进入后续阶段。

## Task 5: Tool Schema Adapter

- [x] **Step 1: 定义 OpenAI-compatible schema 输出规则**

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

- [x] **Step 2: 在 `tools/registry.py` 增加清晰方法**

建议方法：

```python
def openai_tools(self) -> list[dict[str, object]]:
    """返回 OpenAI-compatible tool schema。"""
```

- [x] **Step 3: 本地验证**

运行：

```bash
python -m Turning-Good-Agent chat
```

然后输入：

```text
time
```

预期真实模型 schema 转换可用，并能在后续手动验证中确认工具路径。

## Task 6: Parse Real Tool Calls

- [x] **Step 1: 修改 `OpenAICompatibleLLM.complete()`**

要求：

- 发送 `tools` 参数给 Chat Completions
- 从响应中读取 `message.tool_calls`
- 把每个 tool call 转成统一 `ToolCall`
- `arguments` 必须 JSON parse 成 object，失败时直接返回明确错误

- [x] **Step 2: 统一返回**

当模型返回工具调用时：

```python
return LLMResponse(content=message.content or "", tool_calls=tool_calls)
```

当没有工具调用时：

```python
return LLMResponse(content=message.content or "")
```

## Task 7: AgentLoop Message Protocol

- [x] **Step 1: 保存 assistant tool call 消息**

在执行工具前，把 assistant tool call message 追加到 `working`。

- [x] **Step 2: 保存 tool result 消息**

工具执行后追加：

```python
{
    "role": "tool",
    "tool_call_id": call.id,
    "name": call.name,
    "content": record["content"],
}
```

- [x] **Step 3: 达到 max_tool_rounds 后返回明确文本**

继续沿用：

```text
工具调用轮数已达到上限。
```

## Task 8: Observability

- [x] **Step 1: 在 runtime 保存 tool calls**

本阶段先把 `ctx.tool_calls` 写入 RUN trace metadata 中的最小字段。

建议字段：

```text
tool_call_count
tool_names
```

- [x] **Step 2: 不写入 session.json**

不要把 tool call 统计写入 `session.json`。

## Task 9: Streaming Switch

- [x] **Step 1: 增加集中配置**

在 `LLMSettings` 中增加：

```python
streaming_enabled: bool = True
```

配置文件示例：

```json
{
  "llm": {
    "provider": "openai-compatible",
    "api_key": "你的 API Key",
    "base_url": "https://api.openai.com/v1",
    "model": "你的模型名",
    "streaming_enabled": true
  }
}
```

默认值设为 `true`，用户显式关闭后回退到完整回复模式。

- [x] **Step 2: 定义 `LLMChunk`**

最小字段：

```python
@dataclass
class LLMChunk:
    delta_text: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
```

第一版不把 tool call delta 逐段暴露给 channel；LLM 层内部会把 tool call 参数片段合并成完整 `ToolCall`。

- [x] **Step 3: 增加 `OpenAICompatibleLLM.stream()`**

使用 OpenAI SDK：

```python
client.chat.completions.create(
    model=self.model,
    messages=messages,
    tools=tools or None,
    stream=True,
)
```

解析 `choices[0].delta.content`，逐段产出 `LLMChunk`；如果存在 `delta.tool_calls`，在 LLM 层内部合并参数片段。

当前补充约束：

- 流式实现使用异步 `async for` 消费 SDK stream。
- 如果本轮流式响应最终没有拿到有效 `usage`，则整轮按失败处理，不保存成功 assistant message。
- tool call 参数片段只在 `finish_reason == "tool_calls"` 时组装成完整 `ToolCall`，并在组装时执行严格校验。

- [x] **Step 4: AgentLoop 支持 CLI 文本流式**

当 `streaming_enabled = true` 时：

- `AgentLoop` 逐段消费 `LLMChunk`
- 累积完整 assistant 文本
- 通过 Runtime 传入的 delta 回调输出文本片段
- 最终仍只把完整 assistant message 写入 `messages.jsonl`
- 如果模型返回完整 tool call，则继续交给现有工具循环执行

- [x] **Step 5: OutboundMessage 增加流式事件语义**

建议事件：

```text
response.started
response.delta
response.completed
response.error
```

当前 CLI 第一版先通过 Runtime delta 回调即时打印；`OutboundMessage` 已具备事件类型，Web、微信、飞书 channel 在后续阶段接入。

- [x] **Step 6: 明确流式 tool calling 边界**

第一版不向 channel 暴露 tool call 参数增量；只在 LLM 层内部拼接参数片段，形成完整 `ToolCall` 后复用现有工具循环。

## Task 10: Manual Verification

- [x] **Step 1: 配置真实模型**

编辑 `settings.local.json`：

```json
{
  "llm": {
    "provider": "openai-compatible",
    "api_key": "你的 API Key",
    "base_url": "https://api.openai.com/v1",
    "model": "你的模型名",
    "streaming_enabled": true
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
    "model": "你的模型名",
    "streaming_enabled": true
  }
}
```

- [x] **Step 2: 运行 CLI**

```bash
cd /download/Turning-Good-Agent
python -m Turning-Good-Agent chat
```

- [x] **Step 3: 手动输入**

```text
现在几点？
```

预期：

- 模型调用 `now`
- CLI 返回包含当前时间的回答
- session 目录中能看到 tool 调用记录

- [x] **Step 4: 手动验证流式开关**

把 `settings.local.json` 改为：

```json
{
  "llm": {
    "streaming_enabled": true
  }
}
```

输入普通纯文本问题。预期 CLI 边接收边打印，最终 `messages.jsonl` 仍只保存完整 assistant 回复。

## Completion Criteria

- `OpenAICompatibleLLM` 使用 OpenAI Python SDK。
- `OpenAICompatibleLLM` 已切换到异步 `AsyncOpenAI` 路径。
- 真实 LLM 纯文本对话稳定返回，不静默空回复。
- 非流式和流式都要求 provider 返回真实 `usage`；缺失时本轮失败，不写 token 账本。
- 内置工具通过 `ToolLoader` 自动加载。
- 工具 schema 输出稳定排序。
- tool call 参数在执行前完成归一化和严格校验；非法 JSON 或缺少关键信息会直接报错。
- 真实 LLM 可以调用 `now`。
- 真实 LLM 可以调用 `echo`。
- tool call 和 tool result 至少进入 `AgentLoop` working messages。
- `settings.llm.streaming_enabled = false` 时保持原有非流式行为。
- `settings.llm.streaming_enabled = true` 时 CLI 普通文本回复支持流式输出。
- 流式模式下最终仍只保存完整 assistant message，不把每个 chunk 写入 `messages.jsonl`。

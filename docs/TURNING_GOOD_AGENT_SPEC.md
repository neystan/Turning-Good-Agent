# Turning-Good-Agent 持续更新 Spec

> Last updated: 2026-06-15  
> 状态：MVP 已可运行，真实 LLM SDK 化与基础 tool calling 已接入，下一步继续补 CLI 流式输出、observability 与 MCP。

## 1. 产品目标

Turning-Good-Agent 的目标是做一个轻量化通用 Agent。它不是只面向单个任务的脚本，而是一个可扩展的 runtime-first agent 框架。

最终形态应支持：

- 多 channel：CLI、Web、微信、飞书
- 统一 runtime：用状态机控制单轮会话生命周期
- AgentLoop：负责 LLM 与 tools 的循环调用
- Memory：短期压缩、长期偏好、事件记忆
- Context：统一注入 system prompt、summary、memory、tool schema、skills、MCP 等内容
- Tools：可扩展工具抽象
- Skills：本地技能扫描、加载、注入和创建
- MCP：支持工具、资源、提示词发现和 `call_tool`
- Hooks：顺序 hook 与事件 hook
- Observability：Web 面板查看会话、trace、token、压缩、延迟
- Proactive：主动追问、主动总结、主动提醒、dream、breakbeat、cron
- Multi-Agent：用户手动开启 planner + workers 协作模式

## 2. 设计原则

1. 先可运行，再扩展  
   每个阶段都必须能被用户实际运行和检查，不做纯概念堆叠。

2. Runtime-first  
   Channel 不直接管理 AgentLoop。所有 channel 都把输入转换成 `InboundMessage`，交给 runtime。

3. 文件可观察  
   会话、消息、trace、token 使用、压缩统计都优先落到可读文件，便于调试。

4. Token 节省优先  
   通过更精简 prompt、更强上下文预算、更激进压缩和更明确工具 schema 降低每轮消耗。

5. 抽象只放在稳定边界  
   `LLMProvider`、`BaseTool`、`SessionStore` 这类边界保留抽象；不为了测试制造额外层。

6. 配置集中  
   所有核心参数集中到 `config/settings.py`，本地私有配置放 `settings.local.json`。

## 3. 当前 MVP 范围

当前已经完成：

- CLI 对话入口
- 7 状态 Runtime
- JSON/JSONL 文件存储
- 每个 session 独立目录
- `/history`、`/context`、`/new`、`/clear`、`/exit`
- 会话 7 天保留期清理
- OpenAI-compatible SDK 对话
- OpenAI-compatible 基础 tool calling
- `echo`、`now` 内置工具
- AgentLoop 工具循环骨架
- token 驱动短期压缩
- COMPACT 独立状态
- trace 和 token usage 文件记录
- 本地 `settings.local.json` 配置

当前未完成：

- MCP client
- skills 扫描与加载
- Web channel 与 Web observability
- 微信、飞书 channel
- 长期记忆 RAG
- dream、breakbeat、cron
- Multi-Agent planner / worker

## 4. Runtime 状态机

当前状态机：

```text
COMMAND -> SESSION -> BUILD -> RUN -> COMPACT -> SAVE -> RESPOND
```

### COMMAND

职责：

- 处理 slash command
- 命中 shortcut 后直接进入 `RESPOND`

命令命中后不执行 `SESSION/BUILD/RUN/COMPACT/SAVE`，避免无意义创建 session、调用 LLM 或写入 token 记录。

当前命令语义：

- `/history`：查看完整 `messages.jsonl` 历史。
- `/context`：查看当前会注入 LLM 的上下文视图，也就是 `summary + uncompacted_history` 和关键统计。
- `/clear`：按 `InboundMessage.session_id` 删除当前 session 目录。
- `/new`：让 channel 切换到新的 session id。
- `/exit`：退出当前 CLI 会话。

### SESSION

职责：

- 清理过期 session
- 加载或创建当前 `Session`
- 写入 `ctx.session`

`session_id` 由 channel 在构造 `InboundMessage` 前生成或解析；`SESSION` 状态负责把这个标识解析为真正的 `session.json` 会话对象。

### BUILD

职责：

- 读取完整历史 `full_history`
- 从 `session.uncompacted_history` 读取当前未压缩历史窗口
- 注入完整 `summary + uncompacted_history`
- 构建模型输入上下文
- 当完整上下文超过 `max_context_tokens` 时，拒绝本轮并提示上下文过大

`BUILD` 不负责创建 session，只消费 `SESSION` 阶段得到的 `ctx.session`。

### RUN

职责：

- 执行 LLM 对话
- 执行 tool calling loop
- 得到最终 assistant 回复

### COMPACT

职责：

- 基于 `RUN` 后的本轮 user/assistant 结果判断是否需要压缩
- 基于 `uncompacted_history + 本轮 user + 本轮 assistant` 执行增量压缩
- 更新内存中的 `summary`
- 更新内存中的 `session.uncompacted_history`
- 生成本轮 token usage
- 在 trace metadata 中记录压缩观测字段

COMPACT 的事件结果固定为 `ok`。是否真的发生压缩只通过 metadata 表示。

### SAVE

职责：

- 保存当前 user message
- 保存当前 assistant message
- 保存 `summary` 和 `uncompacted_history`
- 写入 token usage
- 触发主动事件

### RESPOND

职责：

- 构造 `OutboundMessage`
- 返回给 channel

## 5. MessageBus 与 Channel

当前已有：

- `InboundMessage`
- `OutboundMessage`
- `AsyncMessageBus` 骨架
- CLI channel

最终 channel 设计：

```text
CLI / Web / WeChat / Feishu
-> Channel Adapter
-> InboundMessage
-> MessageBus
-> AgentRuntime
-> OutboundMessage
-> Channel Adapter
```

约束：

- Channel 只负责协议适配和用户交互。
- Runtime 不依赖具体 channel。
- 后续办公软件 channel 要把用户、群聊、thread、附件等信息放入 `metadata`。

## 6. Session 与存储

默认目录：

```text
.sessions/
  <北京时间>_<session_id>/
    session.json
    messages.jsonl
    turn_traces.jsonl
    token_usage.jsonl
```

规则：

- 每个 session 一个独立目录。
- 目录名包含北京时间，也就是东八区时间，方便人工区分。
- `/new` 只切换到新 session，不创建空目录。
- `/clear` 删除当前 session 整个目录。
- `retention_days` 默认 7 天，超期 session 在后续请求前清理。

`session.json` 保存内部状态：

- `id`
- `created_at`
- `updated_at`
- `summary`
- `uncompacted_history`

`messages.jsonl` 保存原文消息：

- `role`
- `content`
- `token_count`
- `created_at`

`token_count` 记录当前消息自身内容的 token 权重，用于短期压缩窗口计算，不等同于本轮 LLM `input_tokens/output_tokens`。

`turn_traces.jsonl` 保存状态流转：

- `turn_id`
- `state`
- `duration_ms`
- `event`
- `error`
- `metadata`

Runtime 在单轮结束后通过 `save_turn_traces()` 批量写入本轮 trace，文件格式仍保持一行一个状态。

`token_usage.jsonl` 保存 LLM token 账本：

- `input_tokens`
- `output_tokens`
- `turn_total_tokens`
- `total_tokens`
- `compacted`

token usage 账本必须使用 LLM SDK 返回的真实 `usage`。如果 provider 没有返回 usage，本轮会进入错误响应，不写入 `token_usage.jsonl`。
`input_tokens` 是本轮模型请求的完整输入 token，包含 system prompt、summary、uncompacted history、tool schema，以及 tool calling 循环中的后续请求；它不是单条用户消息 token，也不保证每轮单调递增。
Slash command 快捷路径不调用 LLM，因此不写入 `messages.jsonl` 和 `token_usage.jsonl`。
流式模式下，即使 CLI 已经输出过部分 delta，只要最终没有拿到有效 `usage`，本轮仍按失败处理，不保存成功 assistant message。

压缩明细只写入 `turn_traces.jsonl` 的 `COMPACT` 状态 metadata：

- `compacted`
- `compacted_message_count`
- `compacted_token_count`
- `raw_window_message_count`
- `raw_window_token_count`

工具调用统计只写入 `turn_traces.jsonl` 的 `RUN` 状态 metadata：

- `tool_call_count`
- `tool_names`

## 7. Memory

### 7.1 短期记忆

当前策略是 token 驱动压缩：

```text
compact_token_threshold = 200000
recent_window_token_limit = 20000
max_context_tokens = 300000
```

语义：

- 当未压缩原文历史 token 超过阈值，触发压缩。
- 压缩时从 `virtual_uncompacted_history` 尾部向前选择完整 user + assistant 对话对。
- `recent_window` 累计 token 必须小于等于 `recent_window_token_limit`，默认 `20000`。
- 更早的 `compact_source` 进入新增 summary，并 append 到旧 `summary`。
- 压缩在 `COMPACT` 状态执行，影响下一轮上下文。

`summary` 只表示会话历史摘要，来源是用户输入和 assistant 最终回答，不包含 system prompt、长期记忆、tool schema、skills 或 MCP schema。

如果之前已经压缩过，下一轮直接从 `session.uncompacted_history` 继续增量压缩，不重复压缩旧消息。

### 7.2 长期记忆

长期记忆最终分三类：

- Agent memory：agent 自身能力、已学到的工作方式
- User profile：用户偏好、习惯、常用约束
- Event memory：用户问过什么、做过什么、有哪些未完成事项

当前只保留骨架。后续要明确：

- 哪些长期记忆直接注入 system context
- 哪些长期记忆通过 RAG 检索
- 哪些事件只用于 proactive 能力

## 8. Context

上下文来源：

- `system_prompt`：来自 `context/system_prompt.py`，定义 agent 的基本行为。
- `long_memory`：来自长期记忆模块，当前主要是用户偏好。
- `summary`：来自 `session.json`，表示已压缩会话历史。
- `uncompacted_history`：来自 `session.json`，表示下一轮会和 summary 一起注入模型的未压缩原文窗口。
- `tool_schema`：来自 `ToolRegistry.schemas()`，描述当前可用工具。
- `skills`：后续来自 skills loader，按需注入。
- `mcp`：后续来自 MCP client，包含 tools/resources/prompts 的可见描述。
- `full_history`：来自 `messages.jsonl` 全量消息，用于 `/history`、观测和计算窗口。
- `recent_window`：只在 `COMPACT` 触发压缩时临时计算，从 `virtual_uncompacted_history` 尾部保留最近完整 user + assistant 对话原文。
- `current_input`：来自当前 `InboundMessage.content`。

目标上下文构建顺序：

1. system prompt
2. 长期记忆 / 用户偏好
3. skills / MCP / tool schema
4. 会话 summary
5. uncompacted_history
6. 当前用户消息

预算规则：

- 最终发给模型的上下文受 `max_context_tokens = 300000` 约束。
- `BUILD` 不裁剪 `uncompacted_history`，未压缩历史在压缩前必须完整注入。
- 如果 `system prompt + long memory + tool schema + skills + MCP + summary + uncompacted_history + current input` 超过 `max_context_tokens`，当前策略是拒绝本轮并提示上下文过大。
- `recent_window` 只在 `COMPACT` 需要压缩时临时计算。
- `recent_window` 受 `recent_window_token_limit = 20000` 约束，并在压缩后写入 `session.uncompacted_history`。
- system prompt、long memory、tool schema、skills、MCP、summary、uncompacted history、current input 都应计入上下文预算。
- tool result 当前只参与本轮 `AgentLoop` working messages，不会作为独立消息注入下一轮 `BUILD`。

后续要加入：

- MCP tools/resources/prompts
- skills 内容
- long-term memory retrieval result
- multi-agent plan/task state
- active reminders

目标是让 context 注入可解释、可裁剪、可观测。

## 9. Tools

当前抽象：

- `BaseTool`
- `ToolResult`
- `ToolRegistry`
- `ToolExecutor`
- 后续增加 `ToolLoader`

当前内置工具：

- `echo`
- `now`

当前已经完成：

- `OpenAICompatibleLLM` 已使用 OpenAI Python SDK
- `openai_compatible` 接入族统一接入
- 真实模型返回空 `content` 但包含 `tool_calls` 时，会继续进入工具调用循环
- OpenAI-compatible tools schema 转换
- `AgentLoop` 已补齐 assistant tool call message 和 tool result message
- Phase 2 后半段已开始接入可配置 CLI 文本流式输出

Tools 改造约束：

- `BaseTool` 保持轻量，但需要从“只有 name/description/input_schema/run”扩展为可校验工具接口。
- 参数 schema 统一使用 JSON Schema object；对外字段建议逐步统一为 `parameters`，兼容期可保留 `input_schema`。
- 每次执行工具前必须先做参数归一化和校验：
  - 非 object 参数直接返回可诊断错误。
  - 缺少 required 字段直接返回可诊断错误。
  - 基础类型错误尽量按 schema 安全转换，例如 `"3"` 转为 `3`。
  - 无法转换或不满足 enum/min/max/minLength 等约束时返回错误，不进入工具内部执行。
- `ToolRegistry` 负责 `prepare_call(name, args)`，集中处理工具查找、参数转换、参数校验和错误文本。
- `ToolExecutor` 只负责执行、计时和异常包装，不再直接承担参数边界判断。
- 工具 schema 输出必须稳定排序，先内置工具，再 MCP tools；同组内按工具名排序。
- 需要新增 `tools/loader.py`，自动加载内置工具：
  - 扫描 `tools/` 包中的工具类。
  - 跳过 `base.py`、`registry.py`、`executor.py`、`loader.py`、`schema.py` 等基础模块。
  - 只加载非抽象、可发现的工具类。
  - 支持 `enabled(settings/context)`，为后续按配置启停工具留入口。
  - 当前不做 entry_points 第三方插件机制。
- 当前不引入 nanobot 的完整 Schema 类体系；只保留最小 JSON Schema 校验函数，避免工具层过早复杂化。

下一阶段要做：

- 流式输出 trace 字段
- `ToolLoader` 自动加载内置工具
- `ToolRegistry.prepare_call()` 参数校验和稳定排序
- tool call observability 单独落盘
- tool call / tool result 的会话级查看入口
- 更细粒度的 provider 错误信息与 trace 字段

真实 LLM Provider 约束：

- 真实 provider 使用 OpenAI Python SDK 的 `client.chat.completions.create(...)` 作为主路径。
- 真实 provider 使用 OpenAI Python SDK 的异步 client，也就是 `AsyncOpenAI().chat.completions.create(...)`。
- 当前 LLM 接入层只保留 `openai_compatible` 一类；兼容 OpenAI Chat Completions 协议的厂商统一通过这一路径接入。
- `OpenAICompatibleLLM` 负责把 SDK 返回对象归一化为内部 `LLMResponse`。
- 当 `message.content` 为空但模型返回了 `tool_calls` 时，不应被视为无回复；应进入工具调用循环。
- 当 provider 返回兼容扩展字段，例如 `reasoning_content`，MVP 阶段只做兼容读取和调试保留，不直接把推理内容输出给用户。
- provider 必须返回真实 `usage`；无论是非流式还是流式，只要最终缺少有效 `usage`，本轮都进入错误响应，不写入 token 账本。
- tool call 解析采用严格模式：缺少 `tool_call.id`、`function.name`，或 `arguments` 不是合法 JSON object 时，本轮直接报错，不再静默降级成空参数。
- HTTP 细节、错误对象、超时和重试应交给 SDK 或 Provider 层处理，不在 AgentLoop 内扩散。

流式输出约束：

- 流式输出属于 Phase 2 的后半段能力。
- 流式输出必须通过集中配置控制，配置字段为 `settings.llm.streaming_enabled`，默认值为 `true`。
- 第一版支持 CLI 文本 delta 输出；tool call 参数 delta 只在 LLM 层内部合并，不作为独立事件向 channel 暴露。
- 当 `streaming_enabled = false` 时，回退到非流式完整回复行为。
- 当 `streaming_enabled = true` 时，LLM 层通过 OpenAI SDK `stream=True` 产出文本增量，CLI 逐段打印；如果模型返回 tool call delta，则先合并成完整 `ToolCall` 后交给现有工具循环。
- `messages.jsonl` 只保存最终完整 assistant message，不保存每个 chunk。
- `turn_traces.jsonl` 可以记录本轮是否启用 streaming，但不记录完整 chunk 序列。
- Web、微信、飞书的流式展示不属于 Phase 2，后续在 channel 阶段接入统一事件协议。

## 10. Skills

目标能力：

- `scan_skills`
- `list_skills`
- `load_skill`
- skill 格式校验
- skill 注入 context
- Anthropic 风格 skill creator

建议第一版格式：

```text
skills/
  <skill-name>/
    SKILL.md
    assets/
    scripts/
```

第一版不做自动生成 skill，先做扫描、校验、加载和注入。

## 11. MCP

目标能力：

- MCP client
- 支持 stdio、SSE、streamable HTTP 三类传输
- 发现 tools
- 发现 resources
- 发现 prompts
- `call_tool`

建议最小闭环：

1. 先实现 stdio transport
2. 支持 initialize
3. 支持 list_tools
4. 支持 call_tool
5. 再补 resources 和 prompts
6. 最后补 SSE / streamable HTTP

## 12. Hooks

目标能力：

- 顺序 hook：在固定阶段前后执行，例如 before_run、after_run、before_compact
- 事件 hook：根据事件触发，例如 conversation_completed、tool_failed、memory_compacted

当前已有 `hooks/` 骨架，后续应接入 Runtime。

## 13. Observability

最终 Web observability 面板至少包含：

- session 列表
- 单 session 完整消息
- summary
- token usage
- 状态 trace
- COMPACT 统计
- 请求延迟
- tool calls
- 错误信息

第一版优先读取本地 JSON/JSONL，不先引入数据库。

## 14. Proactive

主动能力目标：

- 主动询问用户需求
- 主动总结会话
- 根据事件创建提醒
- breakbeat：识别未完成任务
- dream：从会话抽取长期记忆
- cron：定时提醒
- agent 自进化：从会话中总结可复用 skill

当前已有 proactive 事件分发骨架，已接入 `CONVERSATION_COMPLETED`。

## 15. Multi-Agent

Multi-Agent 必须手动开启。

目标范式：

```text
Main Agent
-> Planner
-> Worker 1
-> Worker 2
-> Worker N
-> 汇总结果
```

约束：

- 默认单 agent，避免不必要 token 成本。
- planner 负责拆解任务，不直接执行所有细节。
- workers 只拿必要上下文。
- 主 agent 负责汇总和最终回复。

## 16. 阶段路线

1. Phase 1：Runtime MVP
2. Phase 2：真实 LLM SDK 化、tool calling 与 CLI 文本流式输出
3. Phase 3：MCP client MVP
4. Phase 4：Skills 机制
5. Phase 5：Web observability
6. Phase 6：主动能力与长期记忆
7. Phase 7：Multi-Agent 协作模式
8. Phase 8：多 Channel 接入

## 17. 更新规则

这份 spec 是持续更新文档。

当以下内容变化时必须更新：

- runtime 状态机
- session 文件结构
- memory 策略
- context 注入顺序
- tool schema 或工具调用协议
- MCP/skills/proactive/multi-agent 模块边界
- 阶段路线和完成状态

# Turning-Good-Agent 持续更新 Spec

> Last updated: 2026-07-24
> 状态：MVP 已可运行，真实 LLM SDK 化、基础 tool calling、LLM 摘要压缩、CLI 流式输出、Phase 3 Hooks、Phase 4 MCP Client/后台 Worker 与 Phase 5 Skills 已完成。

## 1. 产品目标

Turning-Good-Agent 的目标是做一个轻量化通用 Agent。它不是只面向单个任务的脚本，而是一个可扩展的 runtime-first agent 框架。

最终形态应支持：

- 多 channel：CLI、Web、微信、飞书
- 统一 runtime：用状态机控制单轮会话生命周期
- AgentLoop：负责 LLM 与 tools 的循环调用
- Memory：短期压缩、长期偏好、事件记忆
- Context：统一注入 system prompt、summary、memory、skills、MCP 等消息内容；tool schema 仅走 LLM API 参数
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
   所有核心参数集中到 `config/settings.py`，本地私有配置只从 `settings.local.json` 读取。

## 3. 当前 MVP 范围

当前已经完成：

- CLI 对话入口
- 7 状态 Runtime
- JSON/JSONL 文件存储
- 每个 session 独立目录
- `/history`、`/context`、`/new`、`/clear`、`/exit`
- `/tools`
- 会话 7 天保留期清理
- OpenAI-compatible SDK 对话
- OpenAI-compatible 基础 tool calling
- `echo`、`now` 内置工具
- ToolLoader 内置工具自动加载
- ToolRegistry 参数归一化、JSON Schema 校验和稳定排序
- CLI 文本流式输出开关
- AgentLoop 工具循环骨架
- token 驱动压缩触发 + LLM 摘要生成
- COMPACT 独立状态
- trace 和 token usage 文件记录
- 本地 `settings.local.json` 配置
- Phase 3 顺序 Hooks、会话级工具审批与 Channel 状态输出
- Phase 4 MCP Client：stdio、Streamable HTTP、后台 Worker、Catalog、显式 Tool 注册、当前轮附件与连接级重试
- `ToolCallRunner`、通用 `ContextAttachment`、统一 token 预算和短期压缩计划
- Phase 5 Skills：单目录 Catalog、根提示词元数据、当前轮完整加载与审批草稿发布

当前未完成：
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
- `/tools`：查看当前会话的工具调用明细。
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

- 读取完整持久化历史以恢复 `session.uncompacted_history`
- 注入完整 `summary + uncompacted_history`
- 将一次实际 `openai_tools` schema 只作为 LLM `tools` 参数发送，并据此统一计算 token 预算
- 构建模型输入上下文
- 当完整上下文超过 `max_context_tokens` 时，拒绝本轮并提示上下文过大

`BUILD` 不负责创建 session，只消费 `SESSION` 阶段得到的 `ctx.session`。

### RUN

职责：

- 执行 LLM 对话
- 执行 tool calling loop
- 得到最终 assistant 回复

工具循环达到 `max_tool_rounds` 时，RUN 会基于当前 working messages 发起一次不携带 tools 的最终总结请求。Provider 若在该请求中返回 DSML 工具调用格式，会归一化为 `protocol_error`；RUN 不展示该原始文本。最终请求返回 `protocol_error`、tool call 或空文本时，RUN 返回已完成工具次数和 `/tools` 查看提示。

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
- 在 `COMPACT` 后重新计算本轮结束后的上下文 token 观测
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
    true_token_usage.jsonl
    tool_calls.jsonl
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

`token_count` 记录当前消息自身内容的 tokenizer token 权重，用于短期压缩窗口计算，不等同于本轮 LLM `input_tokens/output_tokens`。

`turn_traces.jsonl` 保存状态流转：

- `turn_id`
- `state`
- `duration_ms`
- `event`
- `error`
- `metadata`

Runtime 在单轮结束后通过 `save_turn_traces()` 批量写入本轮 trace，文件格式仍保持一行一个状态。

`true_token_usage.jsonl` 保存 LLM token 账本：

- `input_tokens`
- `output_tokens`
- `turn_total_tokens`
- `total_tokens`
- `compacted`

token usage 账本必须使用 LLM SDK 返回的真实 `usage`。如果 provider 没有返回 usage，本轮会进入错误响应，不写入 `true_token_usage.jsonl`。
`input_tokens` 是本轮模型请求的完整输入 token，包含 system prompt、summary、uncompacted history、tool schema，以及 tool calling 循环中的后续请求；它不是单条用户消息 token，也不保证每轮单调递增。
Slash command 快捷路径不调用 LLM，因此不写入 `messages.jsonl` 和 `true_token_usage.jsonl`。
流式模式下，即使 CLI 已经输出过部分 delta，只要最终没有拿到有效 `usage`，本轮仍按失败处理，不保存成功 assistant message。
如果本轮触发 COMPACT，摘要 LLM 调用产生的 `usage` 会并入同一条 `true_token_usage.jsonl`，因此这一轮账本包含“正常回答 + 摘要生成”的总消耗。

`SAVE.metadata` 保存本轮上下文 token 观测，不包含 tool result：

- `system_tokens`
- `profile_memory_tokens`
- `summary_tokens`
- `history_tokens`
- `current_input_tokens`
- `output_tokens`
- `tool_schema_tokens`
- `tool_count`
- `current_context_tokens`

`SAVE` 会在 `COMPACT` 后重新计算这些字段，因此观测结果反映本轮结束后的 `summary + uncompacted_history` 状态，不包含 tool result。`history_tokens` 是本轮之前未压缩历史的 token，`current_input_tokens` 和 `output_tokens` 分别记录本轮新增输入与输出。只有本轮完整 user/assistant 仍保留在 `uncompacted_history` 时，它们才计入 `current_context_tokens`；如果本轮已经被压缩进 summary，就只通过 `summary_tokens` 体现。`tool_count` 是本轮实际工具调用次数。`current_context_tokens` 是本轮结束后的当前上下文 token 数，字段放在最后，方便人工查看。

压缩明细只写入 `turn_traces.jsonl` 的 `COMPACT` 状态 metadata：

- `compacted`
- `compacted_message_count`
- `compacted_token_count`
- `raw_window_message_count`
- `raw_window_token_count`

工具调用统计只写入 `turn_traces.jsonl` 的 `RUN` 状态 metadata：

- `tool_call_count`
- `tool_names`
- `loaded_skill_names`
- `loaded_skill_count`
- `loaded_skill_token_count`

`tool_calls.jsonl` 保存工具调用明细：

- `turn_id`
- `tool_call_id`
- `tool_name`
- `args`
- `content`
- `error`
- `duration_ms`
- `created_at`

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
- 更早的 `compact_source` 会和旧 `summary` 一起交给 LLM，生成新的 consolidated `summary`。
- 摘要输入由“旧 `summary` + 本轮 `compact_source`”组成，而不是简单原文拼接。
- 摘要 LLM 调用必须返回非空文本和真实 usage；该 usage 会合并进发生压缩的本轮 `true_token_usage.jsonl`。
- 如果摘要缺少 usage 或返回空文本，本轮进入错误响应，不保存新的 `summary`、消息或 token 账本。
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

- `system_prompt`：来自 `context/system_prompt.py`，定义 agent 基本行为、MCP 静态指导和全部有效 Skill Catalog。
- `long_memory`：来自长期记忆模块，当前主要是用户偏好。
- `summary`：来自 `session.json`，表示已压缩会话历史。
- `uncompacted_history`：来自 `session.json`，表示下一轮会和 summary 一起注入模型的未压缩原文窗口。
- `tool_schema`：来自 `ToolRegistry.openai_tools()`，只作为 OpenAI-compatible 请求的 `tools` 参数发送，不作为 system message 注入。
- `skill_catalog`：来自启动时扫描的内存 Catalog，每轮随根 system prompt 注入所有有效 Skill 的 `name + description`。
- `loaded_skill`：模型调用 `load_skill` 后，以低优先级 system Attachment 只附加到当前 AgentLoop working messages。
- `mcp`：来自 MCP Worker 更新的内存 Catalog；默认不注入消息上下文，显式启用的 MCP Tool 只进入 `openai_tools`。Runtime 启动时后台连接，未连接的 Server 不阻塞当前会话。
- `full_history`：来自 `messages.jsonl` 全量消息，用于 `/history`、观测和计算窗口。
- `recent_window`：只在 `COMPACT` 触发压缩时临时计算，从 `virtual_uncompacted_history` 尾部保留最近完整 user + assistant 对话原文。
- `current_input`：来自当前 `InboundMessage.content`。

目标消息上下文构建顺序：

1. system prompt
2. 长期记忆 / 用户偏好
3. 会话 summary
4. uncompacted_history
5. 当前用户消息

`openai_tools` 是与消息列表分离的 API 参数。MCP Catalog、Resource、Prompt 不会默认进入根 system prompt；只有经过控制 Tool 产生并通过校验的 `ContextAttachment` 会追加到当前 AgentLoop working messages。Skill Catalog 是唯一例外：全部有效元数据进入根 system prompt，完整 `SKILL.md` 仍按需加载。

预算规则：

- 最终发给模型的上下文受 `max_context_tokens = 300000` 约束。
- `BUILD` 不裁剪 `uncompacted_history`，未压缩历史在压缩前必须完整注入。
- 如果 `system prompt + skill catalog + long memory + summary + uncompacted_history + current input + openai_tools` 超过 `max_context_tokens`，当前策略是拒绝本轮并提示上下文过大。
- `BUILD` 和 `SAVE` 的上下文预算由 `context/token_budget.py` 统一计算，覆盖根 prompt、`skill_catalog_tokens`、长期偏好、summary、一次实际 `openai_tools` 序列化内容、未压缩历史和当前用户输入。
- `recent_window` 只在 `COMPACT` 需要压缩时临时计算。
- `recent_window` 受 `recent_window_token_limit = 20000` 约束，并在压缩后写入 `session.uncompacted_history`。
- system prompt、Skill Catalog、long memory、summary、uncompacted history、current input 和实际 `openai_tools` 都计入 BUILD/SAVE 上下文预算；完整 Skill 与 MCP 附件在 RUN 实际追加前独立检查当前 working messages 的剩余上限。
- tool result 与 `ContextAttachment` 当前只参与本轮 `AgentLoop` working messages，不会作为独立消息注入下一轮 `BUILD`。

后续要加入：

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
- `ToolCallRunner`
- `ContextAttachment`
- `ToolLoader`

当前内置工具：

- `echo`
- `now`
- `list_dir`
- `find_file`
- `read_file`
- `write_file`
- `edit_file`
- `grep`
- `exec`
- `write_stdin`
- `web_search`
- `web_fetch`
- `weather`

当前已经完成：

- `OpenAICompatibleLLM` 已使用 OpenAI Python SDK
- `openai_compatible` 接入族统一接入
- 真实模型返回空 `content` 但包含 `tool_calls` 时，会继续进入工具调用循环
- OpenAI-compatible tools schema 转换
- `AgentLoop` 已补齐 assistant tool call message 和 tool result message
- `tool_calls.jsonl` 已落盘精简工具调用明细
- `ToolRegistry.prepare_call()` 已集中处理工具查找、参数转换、参数校验和错误文本
- `ToolLoader` 已自动加载内置工具，并隔离单个坏工具模块
- 工具 schema 已稳定排序并缓存
- 可配置 CLI 文本流式输出已接入
- Phase 2 已完成文件、命令、网页和天气基础工具

Tools 当前约束：

- `BaseTool` 保持轻量，当前仍以 `name/description/input_schema/run` 为最小工具接口。
- 参数 schema 统一使用 JSON Schema object，当前内部字段仍为 `input_schema`。
- schema 字段必须带极简 `description`，帮助 LLM 正确赋值，同时避免长说明浪费 token。
- 每次执行工具前必须先做参数归一化和校验：
  - 非 object 参数直接返回可诊断错误。
  - 缺少 required 字段直接返回可诊断错误。
  - 基础类型错误尽量按 schema 安全转换，例如 `"3"` 转为 `3`。
  - 无法转换或不满足 enum/min/max/minLength 等约束时返回错误，不进入工具内部执行。
- `ToolRegistry` 负责 `prepare_call(name, args)`，集中处理工具查找、参数转换、参数校验和错误文本。
- `ToolCallRunner` 负责参数规范化、首次硬安全检查、审批 Hook、受限并发和结果 Hook；`ToolExecutor` 只执行已准备 Tool，并在实际调用前再次运行硬安全检查。
- 工具 schema 输出必须稳定排序，先内置工具，再 MCP tools；同组内按工具名排序。
- `tools/loader.py` 自动加载内置工具：
  - 扫描 `tools/` 包中的工具类。
  - 跳过 `base.py`、`registry.py`、`executor.py`、`loader.py`、`schema.py` 等基础模块。
  - 单个工具模块导入失败时记录错误并继续加载其他工具。
  - 只加载非抽象、可发现的工具类。
  - 支持 `enabled(settings/context)`，为后续按配置启停工具留入口。
  - 当前不做 entry_points 第三方插件机制。
- 当前不引入 nanobot 的完整 Schema 类体系；只保留最小 JSON Schema 校验函数，避免工具层过早复杂化。
- 文件类工具统一通过 `security.py` 和 `path_utils.py` 做 workspace 路径限制、设备文件拒绝和 `.sessions` 状态文件写入保护。
- 命令类工具统一通过 `security.py` 和 `exec_sessions.py` 做危险命令拒绝、超时、输出截断和长运行进程管理；`exec` 仅在 `background=true` 时创建长运行会话。
- 网络类工具只允许 http/https，返回外部内容时必须把网页内容视为数据而不是指令。
- `web_search` 使用 Yahoo Search。

下一阶段要做：

- 更细粒度的流式输出 trace 字段
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

Phase 5 已完成。所有正式和外部 Skill 只位于项目根目录 `.skills/<name>/SKILL.md`，草稿只位于 `.skills/.drafts/`。外部 Skill 可直接复制，或在用户明确请求和审批后由 `install_skill` 从无凭据 HTTPS Git URL 克隆到 `.staging/`、校验后原子发布；不支持 ZIP、Marketplace、自动更新或自动安装。内置 `skill-creator`、`skill-installer` 与 `grilling` 分别提供创建/修改、外部安装和方案压力测试工作流；`grilling` 逐题澄清决策并在用户确认共识前不执行后续操作。它可携带 `agents/openai.yaml` 界面元数据，但当前 Runtime 只读取 `SKILL.md`，不解析或注入该文件。实际文件写入和 Git 克隆仍由既有审批 Tool 执行。`runtime.start()` 扫描一次，非法、目录名不匹配和重名 Skill 被隔离到 `SkillManager.errors`，不影响其他 Catalog；CLI 不监听目录，也不提供 `/skill` 命令。

根 system prompt 每轮注入全部有效 `name + description`。`load_skill(name)` 读取完整正文，以固定低优先级 system Attachment 仅加入当前 AgentLoop working messages；下一轮不会重放，也不写入 `session.json`、`messages.jsonl`、summary 或新的 JSONL。单轮最多 3 个，单个正文最多 8,000 tokens，正文总量最多 16,000 tokens；超限返回 Tool 错误且不截断正文。MCP Attachment 仍严格只允许 user/assistant role。

内置 `skill-creator` 为用户明确创建或修改 Skill 的当前轮工作流模板；Agent 先加载它，再调用 `create_skill_draft(name, description, instructions)`。`create_skill_draft`、`publish_skill_draft(name)` 和 `install_skill(source, ref?, skill_path?)` 都仅供用户明确要求时调用，沿用现有工具审批和 `/approve on`；当前 Agent 直接生成参数，不嵌套第二个 LLM。Skill 不自动执行脚本、不自动创建、评估、发布或安装。

## 11. MCP

Phase 4 的 MCP Client 以 `McpManager` 作为 Runtime 的唯一边界，使用官方 Python MCP SDK，不在 AgentLoop 手写 JSON-RPC 或 transport。

第一版支持：

- `stdio` 与 Streamable HTTP；不支持旧 HTTP+SSE。
- initialize、initialized、按 Server 已声明 capabilities 进行 Tools/Resources/Resource Templates/Prompts 的分页发现、tools/call、resources/read 与 prompts/get。
- 每个 Server 独立连接和状态；单个失败不影响内置 Tool、其他 Server 或普通对话。
- `settings.local.json` 中的静态 stdio env 与 HTTP headers；不使用 `export`，不实现 OAuth。
- `McpManager` 的启动、关闭、手动刷新与 `list_changed` 自动刷新边界，供后续 Web 复用。

MCP Tool、Resource 与 Prompt 的控制权不同：

- 明确配置在 `enabled_tools` 的 MCP Tool 才包装为 `mcp_<server>_<tool>` 并进入 `ToolRegistry` / LLM schema；默认 `enabled_tools=[]`，避免大量 schema 消耗 Context。
- 所有 MCP Tool 默认审批；`/approve on` 是唯一统一跳过审批的条件，`/approve off` 恢复逐次确认。MCP annotations 仅作为元数据，不能自动免审批。
- Resource 与 Prompt 仅保存轻量 Catalog，不自动进 Context、不写入历史或 summary。
- 固定的 `search_mcp_capabilities` 让 LLM 以自然语言检索候选项；`attach_mcp_resource` 和 `apply_mcp_prompt` 经用户确认后才生成仅当前轮可见的 Context Attachment。
- Resource 头尾截断上限为 8,000 tokens；Prompt 上限为 4,000 tokens，超限拒绝；单轮 MCP Attachments 总上限为 12,000 tokens。Resource 不跨轮重放；Prompt 只保留 `user` / `assistant` text messages，不能覆盖根 system prompt。

完整实现计划见 `docs/phases/2026-06-15-phase-4-mcp-client.md`。

## 12. Hooks

Phase 3 提供四个能力：

- 工具调用前：会话级自动审批控制 `write_file`、`edit_file`、`exec`、`write_stdin`
- 工具调用后：按 token 上限截断模型可见结果
- 工具开始、会话压缩前后：通用状态 Hook 交给当前 Channel 输出实现
- 会话结束后：只读终态 Hook 将 outcome、总耗时、锁等待和失败工具数写入 `RESPOND.metadata`

多个 Hook 按注册顺序串行执行，单个 Hook 异常记录后继续，不击穿主流程。

设计边界：

- 第一版只加载代码内显式注册的可信 hook。
- 第一版不执行任意 shell hook、远程 hook 或第三方插件包。
- 工具 Hook 直接使用标准化 `ToolCall` 和工具记录，不依赖 `TurnContext`，也不增加专用上下文或决策对象。
- `before_tool_call` 返回 `None` 表示允许，返回字符串表示拒绝原因。
- Core security 仍由现有工具层负责，Hook 不能绕过路径、命令、URL 和参数校验。
- Runtime 按 `InboundMessage.channel` 通过 `ChannelRouter` 创建单轮 `ChannelAdapter`。CLI 已支持文本 delta、工具状态和审批；Web 可注册适配器，微信和飞书当前静默且尚未接入传输层。
- `Session.auto_approve_tools` 默认关闭并持久化在 `session.json`。`/approve on` 按需创建会话并开启；`/approve off` 不会创建不存在的会话。审批请求本身不持久化，不支持跨 Channel、超时或暂停恢复。
- `ToolPermissionHook` 对 `ToolPermissionSettings.approval_required_tools` 中的工具及任意 `approval_required=true` 的注册 Tool 请求确认。自动审批不绕过 `security.py` 与 `ToolExecutor` 的二次预检；启动时会拒绝未知或 `parallel_safe=true` 的审批工具配置。
- 连续并行安全工具调用已经实现；结果按模型原始顺序回注。`ChannelAdapter` 具有工具开始和结束方法，CLI 以调用 ID 渲染工具动画。

Phase 3 的实现范围和验收记录见 `docs/phases/2026-06-15-phase-3-hooks.md`。

`ToolCallRunner` 先完成 Schema 标准化和 security 预检，再进入 `ToolPermissionHook`；自动审批或当前 Channel 同意后由 ToolExecutor 再次执行硬安全校验。`after_tool_call` 在结果注入 LLM 前按 `max_tool_result_tokens = 8000` 统一截断；`AttachmentManager` 集中校验 MCP/Skill Attachment 的角色、独立 token 预算和实际 working messages 上限。任意 ToolResult 的 ContextAttachment 只进入当前 AgentLoop working messages，不落入历史、摘要或下一轮 Context。

`TurnMonitorHook` 不直接写入 JSONL。Runtime 是状态耗时与 `StateTrace` 的唯一生产者，在可持久化模型会话完成 RESPOND 后、创建该 trace 前调用终态 Hook；快捷命令不会触发该 Hook 或为监控创建 session。Web、SSE 与 WebSocket 仍属于后续观测阶段，尚未实现。

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
3. Phase 3：Hooks Runtime Extension
4. Phase 4：MCP client MVP 与审批/Runtime 收口（已完成）
5. Phase 5：Skills 机制（已完成）
6. Phase 6：Web observability
7. Phase 7：主动能力与长期记忆
8. Phase 8：Multi-Agent 协作模式
9. Phase 9：多 Channel 接入

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

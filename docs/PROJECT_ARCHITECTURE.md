# Turning-Good-Agent 项目架构文档

本文档描述当前 `/download/Turning-Good-Agent` 的真实目录结构，并说明每个目录、每个文件的职责。

## 1. 项目定位

Turning-Good-Agent 是一个轻量 Runtime-first 通用 Agent。当前仓库处于 MVP 阶段，主路径是 CLI 对话、会话存储、短期压缩、基础工具调用，以及基于 OpenAI Python SDK 的 OpenAI-compatible LLM 接入。Phase 2 的真实 LLM SDK 化、基础 tool calling、工具观测落盘和 CLI 纯文本流式输出范围已经完成，Phase 2.5 正在补齐文件、命令、网页和天气基础工具。

当前运行入口：

```bash
python -m Turning-Good-Agent chat
```

当前真实模型能力：

- `OpenAI-compatible`：真实 LLM 对话
- OpenAI-compatible tool calling：`AgentLoop` 已支持 assistant tool call 与 tool result 工作消息回注
- 工具轮数上限：执行一次 no-tools 最终总结，异常时降级到 `/tools` 提示
- CLI 文本流式输出：通过 `settings.llm.streaming_enabled` 控制，默认开启

## 2. 顶层目录与文件

| 路径 | 作用 |
| --- | --- |
| `Turning-Good-Agent/` | 主 Python 包，包含 runtime、session、context、tools、llm、memory 等核心代码。 |
| `docs/` | 项目文档目录，包含当前 spec、架构说明、阶段计划和历史文档。 |
| `settings.example.json` | 本地配置模板。 |
| `settings.local.json` | 本地私有配置文件，实际运行时优先读取，已被 `.gitignore` 忽略。 |
| `.sessions/` | 默认运行数据目录，保存 session、messages、trace、token usage，已被 `.gitignore` 忽略。 |
| `tests/` | 本地测试目录，已被 `.gitignore` 忽略，不上传 GitHub。 |
| `README.md` | 项目快速运行说明。 |
| `pyproject.toml` | Python 项目元数据和运行配置。 |

## 3. `docs/` 文档目录

| 路径 | 作用 |
| --- | --- |
| `docs/README.md` | 文档入口和阅读顺序。 |
| `docs/TURNING_GOOD_AGENT_SPEC.md` | 持续更新的完整 spec。 |
| `docs/PROJECT_ARCHITECTURE.md` | 当前文档，说明真实代码结构。 |
| `docs/phases/` | 每一阶段的项目骨架和实施计划。 |
| `docs/archive/2026-06-11-phase-1-runtime-mvp-design.md` | Phase 1 Runtime MVP 设计快照，已归档为历史记录。 |

## 4. `Turning-Good-Agent/` 主 Python 包

### 4.1 包根文件

| 路径 | 作用 |
| --- | --- |
| `Turning-Good-Agent/__init__.py` | 包标记文件。 |
| `Turning-Good-Agent/__main__.py` | `python -m Turning-Good-Agent` 入口。 |
| `Turning-Good-Agent/cli.py` | CLI channel，负责解析命令行参数、创建 settings、创建 LLM、驱动交互循环。 |

### 4.2 `bus/`

职责：定义 channel 与 runtime 之间的消息模型和异步队列。

| 路径 | 作用 |
| --- | --- |
| `bus/messages.py` | 定义 `InboundMessage` 和 `OutboundMessage`。 |
| `bus/queue.py` | 定义异步 message bus，后续用于多 channel 接入。 |

### 4.3 `config/`

职责：集中管理运行参数。

| 路径 | 作用 |
| --- | --- |
| `config/settings.py` | 定义 `RuntimeSettings`、`MemorySettings`、`SessionSettings`、`LLMSettings` 和 `Settings.load()`。 |

当前配置路径只有项目根目录的 `settings.local.json`。`Settings.load()` 不再支持 `TGA_*` 环境变量覆盖。

### 4.4 `runtime/`

职责：Agent 主状态机和工具调用循环。

| 路径 | 作用 |
| --- | --- |
| `runtime/state.py` | 定义状态机：`COMMAND -> SESSION -> BUILD -> RUN -> COMPACT -> SAVE -> RESPOND`。 |
| `runtime/runtime.py` | `AgentRuntime` 总控，串联会话、上下文、AgentLoop、存储、压缩、trace 和响应。 |
| `runtime/turn_context.py` | 单轮运行上下文，保存 state、full history、uncompacted history、model messages、tool calls、token usage 等中间状态。 |
| `runtime/agent_loop.py` | LLM 与 tools 的调用循环，负责追加 assistant tool call 和 tool result working messages。 |

### 4.5 `sessions/`

职责：会话生命周期和 JSON 文件存储。

| 路径 | 作用 |
| --- | --- |
| `sessions/types.py` | 定义 `Session` 和 `MessageRecord`。 |
| `sessions/store.py` | JSON/JSONL 文件存储实现。每个 session 使用独立目录。 |
| `sessions/manager.py` | 处理 `/history`、`/context`、`/tools`、`/clear`、`/new`、`/exit` 等命令。 |
| `sessions/locks.py` | 按 session_id 提供异步锁，避免同一会话并发写入。 |

默认数据结构：

```text
.sessions/
  <北京时间>_<session_id>/
    session.json
    messages.jsonl
    turn_traces.jsonl
    true_token_usage.jsonl
    tool_calls.jsonl
```

### 4.6 `context/`

职责：构建模型输入上下文。

| 路径 | 作用 |
| --- | --- |
| `context/system_prompt.py` | MVP system prompt。 |
| `context/builder.py` | 组装 system prompt、长期偏好、summary、tool schema、uncompacted history 和当前用户消息。 |

### 4.7 `memory/`

职责：短期压缩和长期记忆骨架。

| 路径 | 作用 |
| --- | --- |
| `memory/short_term.py` | token 驱动的短期记忆压缩策略、LLM 摘要提示和摘要 usage 校验。 |
| `memory/long_term.py` | 用户偏好/长期资料骨架。 |
| `memory/event_memory.py` | 事件记忆骨架，后续给 dream/breakbeat 使用。 |

当前短期策略：

- 未压缩原文 token 超过 `compact_token_threshold` 时触发压缩
- 压缩后保留不超过 `recent_window_token_limit` 的最近完整 user + assistant 原文窗口
- 其余内容通过 LLM 生成新的 `summary`
- 摘要调用的真实 usage 合并进发生压缩的本轮 token 账本
- 摘要调用如果缺少 usage 或返回空摘要，整轮按失败处理，不保存新摘要、消息或 token 账本
- 最终模型上下文受 `max_context_tokens = 300000` 约束
- BUILD 的上下文预算使用 tokenizer 计算，包含 `SYSTEM_PROMPT`
- 如果 BUILD 阶段完整上下文仍超过上限，当前策略是拒绝本轮并提示上下文过大

### 4.8 `tools/`

职责：工具抽象、注册、执行和内置工具。

| 路径 | 作用 |
| --- | --- |
| `tools/base.py` | 定义 `BaseTool` 协议、`ToolResult`、参数归一化和 JSON Schema 校验函数。 |
| `tools/registry.py` | 工具注册表，输出并缓存模型可见 schema，并通过 `prepare_call()` 集中处理工具查找、参数归一化、参数校验和稳定排序。 |
| `tools/executor.py` | 工具执行器，处理调用、耗时和结果序列化。 |
| `tools/loader.py` | 自动扫描并加载内置工具，隔离单个坏工具模块，当前不支持 entry_points 插件。 |
| `tools/builtin_tools.py` | 内置 `echo` 和 `now`。 |
| `tools/filesystem_tools.py` | 内置 `list_dir`、`find_file`、`read_file`、`write_file`、`edit_file` 和 `grep`。 |
| `tools/shell_tools.py` | 内置受限 `exec` 和 `write_stdin`，`exec` 通过 `background=true` 显式创建长运行会话。 |
| `tools/web_tools.py` | 内置 `web_search` 和 `web_fetch`，搜索首选 DuckDuckGo API，Yahoo Search 兜底。 |
| `tools/info_tools.py` | 内置 `weather`。 |
| `tools/security.py` | 文件、命令、URL 和输出截断的公共安全限制。 |
| `tools/path_utils.py` | workspace 路径解析和路径包含判断。 |
| `tools/exec_sessions.py` | 长运行 shell 命令 session 管理。 |

Tools 当前边界：

- 自动加载内置工具，避免每新增一个工具都修改 runtime 组装代码。
- `ToolRegistry` 负责工具查找、参数安全转换和参数校验。
- `ToolExecutor` 只负责执行和异常包装。
- 工具 schema 输出稳定排序：内置工具在前，MCP tools 在后，同组内按名称排序。
- 暂不搬入 nanobot 的完整 Schema 类体系，先使用最小 JSON Schema 校验函数。
- 文件工具默认限制在当前 workspace 内，拒绝危险设备路径和 `.sessions` 状态文件写入。
- shell 工具默认执行受限命令，拦截危险模式，限制超时和输出长度。
- web 工具只允许 http/https，并对外部内容做文本提取和输出截断。

### 4.9 `llm/`

职责：模型 Provider 抽象和具体接入。

| 路径 | 作用 |
| --- | --- |
| `llm/client.py` | `LLMProvider` 协议。 |
| `llm/types.py` | 定义 `LLMResponse`、`LLMUsage`、`ToolCall` 和 `LLMChunk`。 |
| `llm/openai_compatible.py` | 基于 OpenAI Python SDK 的 OpenAI-compatible Chat Completions 接入，负责解析文本、`tool_calls`、usage 和流式 chunk。 |

当前真实 LLM 接入边界：

- `OpenAICompatibleLLM` 使用 OpenAI Python SDK 的异步 client，也就是 `AsyncOpenAI().chat.completions.create(...)`。
- 当前只保留 `openai_compatible` 这一类接入；DeepSeek、Qwen 等兼容服务也统一通过这一路径接入。
- 真实模型返回 `content` 为空但包含 `tool_calls` 时，不会被当作无回复；会交给 `AgentLoop` 执行工具循环。
- 非流式和流式都强制要求 provider 返回真实 `usage`；如果最终缺少有效 `usage`，本轮会失败，不写入 token 账本。
- tool call 解析是严格模式：缺少 `id`、`function.name`，或 `arguments` 不是合法 JSON object 时直接报错，不再静默降级。
- 当禁用 tools 的最终总结请求仍返回 DSML 工具调用格式时，Provider 会标记 `protocol_error`，不把原始协议文本交给用户。
- 流式输出作为 `openai_compatible` 接入族的可选能力，通过 `settings.llm.streaming_enabled` 开启，默认开启。
- 第一版 CLI 会逐段打印文本 delta；tool call 参数片段只在 LLM 层内部合并，完整 tool call 仍交给现有 AgentLoop 执行。
- 多 channel 流式展示后置。
- 当前 tool call 观测会写入 `turn_traces.jsonl` 的 RUN 状态 metadata，字段为 `tool_call_count` 和 `tool_names`；同时精简明细会写入 `tool_calls.jsonl`。

工具轮数达到 `max_tool_rounds` 后，AgentLoop 会使用已有 working messages 发起一次不携带 tools 的总结请求。该请求会先缓冲，只有返回有效自然语言时才输出；若出现 `protocol_error`、继续返回 tool call 或空文本，则降级提示用户通过 `/tools` 查看已完成调用的完整结果。

### 4.10 `observability/`

职责：trace 和 token 记录。

| 路径 | 作用 |
| --- | --- |
| `observability/trace.py` | 定义状态级 trace 记录。 |
| `observability/token_monitor.py` | 归一化每轮 LLM token 账本，强制使用真实 LLM usage。 |

消息级 `token_count` 记录当前消息自身内容的 tokenizer token 权重，用于短期压缩窗口计算。`true_token_usage.jsonl.input_tokens` 则来自 LLM SDK usage，表示整次模型请求输入，不要求每轮单调递增。

`turn_traces.jsonl` 由 `JsonlSessionStore.save_turn_traces()` 在单轮结束后批量写入，文件格式仍保持一行一个状态，便于后续观测面板按状态时间线读取。

当前 COMPACT 公开观测字段只有：

- `compacted`
- `compacted_message_count`
- `compacted_token_count`
- `raw_window_message_count`
- `raw_window_token_count`

这些字段只写入 `turn_traces.jsonl` 的 `COMPACT.metadata`，不再重复写入 `true_token_usage.jsonl`。

当前 SAVE 公开上下文观测字段：

- `system_tokens`
- `profile_memory_tokens`
- `summary_tokens`
- `history_tokens`
- `current_input_tokens`
- `output_tokens`
- `tool_schema_tokens`
- `tool_count`
- `current_context_tokens`

这些字段只写入 `turn_traces.jsonl` 的 `SAVE.metadata`。`SAVE` 会在 `COMPACT` 后重新计算上下文观测，因此它反映的是本轮结束后的 `summary + uncompacted_history` 状态，不包含 tool result。`history_tokens` 是本轮之前未压缩历史的 token，`current_input_tokens` 和 `output_tokens` 分别记录本轮新增输入与输出。只有本轮完整 user/assistant 仍保留在 `uncompacted_history` 时，它们才计入 `current_context_tokens`；如果本轮已经被压缩进 summary，就只通过 `summary_tokens` 体现。`tool_count` 是本轮实际工具调用次数，`current_context_tokens` 放在最后，表示本轮结束后的当前上下文 token 数。

当前 RUN 公开工具观测字段只有：

- `tool_call_count`
- `tool_names`

这些字段只写入 `turn_traces.jsonl` 的 `RUN.metadata`。工具调用过程中的 assistant tool call message 和 tool result message 只参与本轮 `AgentLoop` working messages，不作为独立会话消息写入 `messages.jsonl`。

`tool_calls.jsonl` 保存每次工具调用的精简明细：

- `turn_id`
- `tool_call_id`
- `tool_name`
- `args`
- `content`
- `error`
- `duration_ms`
- `created_at`

### 4.11 `hooks/`

职责：提供工具审批、工具结果截断和 CLI 压缩提示三个顺序 Hook 功能。

| 路径 | 作用 |
| --- | --- |
| `hooks/events.py` | hook 事件定义骨架。 |
| `hooks/base.py` | 定义工具调用前后和压缩前后的 Hook 接口。 |
| `hooks/manager.py` | 按注册顺序执行 Hook，并隔离单个 Hook 异常。 |
| `hooks/cli.py` | 提供 CLI 同步工具审批和压缩状态提示。 |
| `hooks/tool_result_truncation.py` | 按工具类型和 token 上限截断模型可见结果。 |

第一版调用关系：

```text
AgentLoop: Schema -> security -> CLI approval -> ToolExecutor -> result truncation
COMPACT:   CLI before notice -> ShortTermMemory -> CLI after notice
```

架构边界：

- `before_tool_call` 返回 `None` 表示允许，返回字符串表示拒绝原因。
- `after_tool_call` 返回处理后的工具记录，最终内容同时用于 LLM 和 `tool_calls.jsonl`。
- 工具 Hook 直接使用标准化 `ToolCall` 和工具记录，不依赖 `TurnContext`。
- AgentLoop 主循环只负责模型循环，单次工具校验、审批、执行和结果处理由 `_execute_tool_call()` 收口。
- Runtime 默认注册结果截断 Hook；CLI 注册同步审批和压缩提示 Hook。
- hook 不替代 MessageBus。MessageBus 负责 channel 与 Runtime 通信，hook 负责 Runtime 内部生命周期扩展。
- hook 不替代 tools 或 skills。tools 执行动作，skills 提供上下文指令，hook 负责在明确时机执行策略或副作用。
- hook 不替代 `.sessions` 核心持久化。Session、message、tool call、token 和 trace 仍由现有 Store 可靠落盘。
- 当前不实现审批持久化、跨 Channel 审批和暂停恢复。

security 预检在 CLI 审批前执行，ToolExecutor 在真实执行前再次校验。用户允许也不能绕过路径、命令和 URL 的硬安全限制。

参考 nanobot 的顺序组合方式，但只保留当前三个实际功能，不建设完整 Hook 平台。

### 4.12 `proactive/`

职责：主动能力扩展入口。

| 路径 | 作用 |
| --- | --- |
| `proactive/base.py` | 主动能力 handler 基类骨架。 |
| `proactive/events.py` | 当前已有 `CONVERSATION_COMPLETED` 事件。 |
| `proactive/manager.py` | 主动事件分发管理器。 |

## 5. 当前建议阅读顺序

1. `README.md`
2. `docs/TURNING_GOOD_AGENT_SPEC.md`
3. `docs/PROJECT_ARCHITECTURE.md`
4. `Turning-Good-Agent/cli.py`
5. `Turning-Good-Agent/runtime/state.py`
6. `Turning-Good-Agent/runtime/runtime.py`
7. `Turning-Good-Agent/runtime/agent_loop.py`
8. `Turning-Good-Agent/sessions/store.py`
9. `Turning-Good-Agent/memory/short_term.py`
10. `Turning-Good-Agent/config/settings.py`

# Turning-Good-Agent 项目架构文档

本文档描述当前 `/download/Turning-Good-Agent` 的真实目录结构，并说明每个目录、每个文件的职责。

## 1. 项目定位

Turning-Good-Agent 是一个轻量 Runtime-first 通用 Agent。当前仓库处于 MVP 阶段，主路径是 CLI 对话、会话存储、短期压缩、基础工具调用和 OpenAI-compatible 纯文本模型接入。

当前运行入口：

```bash
python -m Turning-Good-Agent chat
```

当前真实模型能力：

- `FakeLLM`：本地开发与工具调用模拟
- `OpenAI-compatible`：真实 LLM 纯文本对话，暂未接入真实 tool calling

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
| `docs/2026-06-11-turning-good-agent-mvp.md` | 早期历史实施计划。 |

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

当前主配置路径是项目根目录的 `settings.local.json`。环境变量覆盖仍保留，但不是推荐主路径。

### 4.4 `runtime/`

职责：Agent 主状态机和工具调用循环。

| 路径 | 作用 |
| --- | --- |
| `runtime/state.py` | 定义 6 状态状态机：`PREPARE -> RUN -> SAVE -> COMPACT -> RESPOND -> DONE`。 |
| `runtime/runtime.py` | `AgentRuntime` 总控，串联会话、上下文、AgentLoop、存储、压缩、trace 和响应。 |
| `runtime/turn_context.py` | 单轮运行上下文，保存 state、history、model messages、tool calls、token usage 等中间状态。 |
| `runtime/agent_loop.py` | LLM 与 tools 的调用循环。当前 FakeLLM 可模拟工具调用，真实 LLM tool calling 尚未完成。 |

### 4.5 `sessions/`

职责：会话生命周期和 JSON 文件存储。

| 路径 | 作用 |
| --- | --- |
| `sessions/types.py` | 定义 `Session` 和 `MessageRecord`。 |
| `sessions/store.py` | JSON/JSONL 文件存储实现。每个 session 使用独立目录。 |
| `sessions/manager.py` | 处理 `/history`、`/clear`、`/new`、`/exit` 等会话命令。 |
| `sessions/locks.py` | 按 session_id 提供异步锁，避免同一会话并发写入。 |

默认数据结构：

```text
.sessions/
  <北京时间>_<session_id>/
    session.json
    messages.jsonl
    turn_traces.jsonl
    token_usage.jsonl
```

### 4.6 `context/`

职责：构建模型输入上下文。

| 路径 | 作用 |
| --- | --- |
| `context/system_prompt.py` | MVP system prompt。 |
| `context/builder.py` | 组装 system prompt、长期偏好、summary、tool schema、历史消息和当前用户消息。 |
| `context/budget.py` | token 估算工具。优先使用 `tiktoken`，不可用时使用本地估算。 |

### 4.7 `memory/`

职责：短期压缩和长期记忆骨架。

| 路径 | 作用 |
| --- | --- |
| `memory/short_term.py` | token 驱动的短期记忆压缩策略。 |
| `memory/long_term.py` | 用户偏好/长期资料骨架。 |
| `memory/event_memory.py` | 事件记忆骨架，后续给 dream/breakbeat 使用。 |

当前短期策略：

- 未压缩原文 token 超过 `compact_token_threshold` 时触发压缩
- 压缩后保留不超过 `raw_window_token_limit` 的最近完整原文窗口
- 其余内容进入 `summary`

### 4.8 `tools/`

职责：工具抽象、注册、执行和内置工具。

| 路径 | 作用 |
| --- | --- |
| `tools/base.py` | 定义 `BaseTool` 协议和 `ToolResult`。 |
| `tools/registry.py` | 工具注册表，输出模型可见 schema。 |
| `tools/executor.py` | 工具执行器，处理调用和结果序列化。 |
| `tools/builtin_tools.py` | 当前内置 `echo` 和 `now`。 |

### 4.9 `llm/`

职责：模型 Provider 抽象和具体接入。

| 路径 | 作用 |
| --- | --- |
| `llm/client.py` | `LLMProvider` 协议。 |
| `llm/types.py` | 定义 `LLMResponse` 和 `ToolCall`。 |
| `llm/fake.py` | 本地 FakeLLM，支持简单工具调用模拟。 |
| `llm/openai_compatible.py` | OpenAI-compatible Chat Completions 纯文本接入。 |

### 4.10 `observability/`

职责：trace 和 token 记录。

| 路径 | 作用 |
| --- | --- |
| `observability/trace.py` | 定义状态级 trace 记录。 |
| `observability/token_monitor.py` | 计算并记录每轮 token 使用数据。 |

当前 COMPACT 公开观测字段只有：

- `compacted`
- `compacted_message_count`
- `compacted_token_count`
- `raw_window_message_count`
- `raw_window_token_count`

### 4.11 `hooks/`

职责：hook 机制骨架。

| 路径 | 作用 |
| --- | --- |
| `hooks/events.py` | hook 事件定义骨架。 |
| `hooks/manager.py` | hook manager 骨架。 |

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

# Turning-Good-Agent MVP Design

## 1. 目标

Turning-Good-Agent 的第一阶段目标不是一次性做全，而是先做一个能真实测试、能持续迭代的最小闭环 Agent Runtime。

当前 MVP 只解决三件事：

1. 用户可以通过 CLI 与 Agent 连续对话。
2. Runtime 能稳定完成单轮消息处理、会话持久化、工具调用和短期压缩。
3. 所有关键行为都能通过 JSON 文件和测试直接观察。

这个阶段优先保证代码简洁、边界清楚、文件可读，不追求大而全。

## 2. 非目标

本阶段不实现以下内容：

- Web Channel、微信、飞书 Channel
- 真实 Multi-Agent planner / worker
- MCP 三种传输的完整 client
- 长期记忆 RAG 检索
- cron、dream、breakbeat 的完整调度闭环
- 完整 Dashboard

这些能力会保留扩展点，但不进入当前主路径。

## 3. 当前架构

### 3.1 总体链路

```text
CLI
-> InboundMessage
-> AgentRuntime
-> PREPARE -> RUN -> SAVE -> COMPACT -> RESPOND -> DONE
-> OutboundMessage
-> CLI 输出
```

### 3.2 模块划分

```text
Turning-Good-Agent/
  cli.py

  bus/
    messages.py

  config/
    settings.py

  context/
    builder.py
    system_prompt.py

  llm/
    client.py
    fake.py
    openai_compatible.py
    types.py

  memory/
    short_term.py
    long_term.py

  observability/
    token_monitor.py
    trace.py

  proactive/
    events.py
    manager.py

  runtime/
    agent_loop.py
    runtime.py
    state.py
    turn_context.py

  sessions/
    locks.py
    manager.py
    store.py
    types.py

  tools/
    base.py
    builtin_tools.py
    registry.py
```

### 3.3 设计原则

- Runtime-first：Channel 只负责收发消息，不直接碰 AgentLoop。
- JSON 可见性优先：会话、消息、trace、token 都直接落地到文件。
- 最小抽象：只在稳定边界上抽象，例如 `LLMProvider`、`Tool`、`SessionStore`。
- 测试跟行为走：验证用户可见行为和关键状态，不为了测试拆出无意义层次。

## 4. Runtime 状态机

### 4.1 状态定义

```text
PREPARE -> RUN -> SAVE -> COMPACT -> RESPOND -> DONE
```

### 4.2 各状态职责

#### PREPARE

- 清理过期会话
- 处理 `/history`、`/clear`、`/new`、`/exit`
- 加载会话
- 读取已有 `summary`
- 读取未压缩原文历史
- 构建模型输入上下文

#### RUN

- 执行 LLM 对话
- 执行工具调用循环
- 得到最终 assistant 输出

#### SAVE

- 保存当前 user / assistant 消息
- 记录本轮 token 基础数据
- 判断本轮结束后是否需要压缩
- 单轮结束后批量保存 trace

#### COMPACT

- 基于“保存后的完整历史”判断并执行压缩
- 更新 `summary`
- 更新 `session.uncompacted_history`
- 保存带压缩统计的 token 使用记录

#### RESPOND

- 构造 `OutboundMessage`
- 返回给当前 Channel

#### DONE

- 生命周期结束态

### 4.3 状态拆分原因

`COMPACT` 必须独立存在，原因是：

- 压缩判断依赖本轮完整结果，不能放在 `PREPARE`
- 压缩不是普通消息保存逻辑的一部分，不能和 `SAVE` 混成一块
- 后续要扩展压缩重试、异步压缩、压缩观测时，独立状态更稳定

## 5. 会话与存储

### 5.1 目录结构

```text
data/
  sessions/
    <UTC时间>_<session_id>/
      session.json
      messages.jsonl
      turn_traces.jsonl
      token_usage.jsonl
```

### 5.2 会话规则

- 每个 session 使用独立目录
- 目录名包含 UTC 时间，便于人工区分会话
- `/new` 只切换逻辑会话，不创建空目录
- `/clear` 删除整个 session 目录
- 默认按 `updated_at` 做 7 天时间闸门清理

### 5.3 核心文件

#### session.json

保存：

- `id`
- `created_at`
- `updated_at`
- `summary`
- `metadata`

Runtime 在单轮结束后通过 `save_turn_traces()` 批量写入本轮 trace，文件格式仍保持一行一个状态。

#### messages.jsonl

逐条保存：

- `role`
- `content`
- `token_count`
- `created_at`

`token_count` 记录消息自身内容的 token 权重，用于短期压缩窗口计算；LLM SDK 返回的真实 usage 单独写入 `token_usage.jsonl`。

#### turn_traces.jsonl

逐状态保存：

- `state`
- `duration_ms`
- `event`
- `error`
- `metadata`

#### token_usage.jsonl

逐轮保存：

- `input_tokens`
- `output_tokens`
- `turn_total_tokens`
- `total_tokens`
- `compacted`

Slash command 快捷路径不调用 LLM，因此不写入 `messages.jsonl` 和 `token_usage.jsonl`。

## 6. 短期记忆与 COMPACT 策略

### 6.1 当前策略

压缩由 token 驱动：

- 当未压缩原文历史 token 超过 `compact_token_threshold` 时触发压缩
- 压缩后只保留不超过 `recent_window_token_limit` 的最近完整原文窗口
- 其余内容追加进入 `summary`

默认值：

```text
compact_token_threshold = 200000
recent_window_token_limit = 20000
```

### 6.2 为什么放在 COMPACT

因为当前轮的 assistant 回复只有在 `RUN` 结束后才完整。

所以当前轮的正确顺序必须是：

```text
先保存完整历史
再判断是否压缩
再把压缩结果留给下一轮使用
```

这保证：

- 当前轮上下文不被“回写式压缩”污染
- 压缩统计与真实历史一致
- `summary` 总是表示“上一轮结束后的稳定结果”

### 6.3 当前 session 快照

当前 `session.json` 保存可读上下文快照：

- `summary`
- `uncompacted_history`

压缩相关的外部观测字段不再写进 `session.json` 或 `token_usage.jsonl`，而是只出现在：

- `turn_traces.jsonl` 的 `COMPACT.metadata`

当前公开暴露的压缩观测字段只有 5 个：

- `compacted`
- `compacted_message_count`
- `compacted_token_count`
- `raw_window_message_count`
- `raw_window_token_count`

## 7. 工具与模型

### 7.1 当前 LLM

当前支持：

- `FakeLLM`
- `OpenAI-compatible` 纯文本对话

### 7.2 当前工具

当前内置：

- `echo`
- `now`

工具通过 `ToolRegistry` 注册，AgentLoop 统一调度。

## 8. 可观测性

当前最小可观测性包括两层：

### 8.1 状态级 trace

每轮每个状态都记录：

- 状态名
- 耗时
- 事件结果
- 错误

其中 `COMPACT` 会额外记录压缩统计 metadata。

### 8.2 token 级记录

每轮都会记录：

- 输入 token
- 输出 token
- 单轮总 token
- 会话累计 token
- 本轮是否发生压缩
- 本轮压缩掉的消息数 / token 数
- 本轮保留原文窗口的消息数 / token 数

这为后续 Dashboard、会话调试、压缩评估提供直接数据源。

## 9. 配置

所有参数集中在：

```text
Turning-Good-Agent/config/settings.py
```

并通过项目根目录的：

```text
settings.local.json
```

进行本地永久配置。

当前主路径是 `settings.local.json`，环境变量覆盖仍然保留，但只是可选覆盖方式，不是推荐主路径。

当前配置分组：

- `runtime`
- `memory`
- `sessions`
- `llm`

## 10. 当前完成范围

截至这版 spec，对应实现已经具备：

- CLI 对话入口
- 6 状态 Runtime
- JSON 文件持久化
- 会话隔离与清理
- `/history`、`/new`、`/clear`、`/exit`
- FakeLLM
- OpenAI-compatible 纯文本对话
- 内置工具 `echo` / `now`
- token 驱动短期压缩
- `COMPACT` 独立状态
- 压缩 metadata 与 observability 收口

## 11. 下一阶段路线

下一阶段按以下顺序推进：

1. 真实 LLM tool calling
2. MCP client 最小闭环
3. skills 最小机制
4. Web 可观测面板 MVP
5. 主动能力第一批能力
6. Multi-Agent planner / worker

顺序原则很明确：

先把单 Agent 的 session、tool、memory、trace 基础打稳，再往上叠更复杂能力。

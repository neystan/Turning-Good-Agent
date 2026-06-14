# Turning-Good-Agent 持续更新 Spec

> Last updated: 2026-06-15  
> 状态：MVP 已可运行，下一阶段进入真实 LLM tool calling。

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
- 6 状态 Runtime
- JSON/JSONL 文件存储
- 每个 session 独立目录
- `/history`、`/new`、`/clear`、`/exit`
- 会话 7 天保留期清理
- FakeLLM
- OpenAI-compatible 纯文本对话
- `echo`、`now` 内置工具
- AgentLoop 工具循环骨架
- token 驱动短期压缩
- COMPACT 独立状态
- trace 和 token usage 文件记录
- 本地 `settings.local.json` 配置

当前未完成：

- 真实 LLM tool calling
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
PREPARE -> RUN -> SAVE -> COMPACT -> RESPOND -> DONE
```

### PREPARE

职责：

- 清理过期 session
- 处理 slash command
- 加载或创建 session
- 读取 summary
- 读取未压缩原文窗口
- 构建模型输入上下文

### RUN

职责：

- 执行 LLM 对话
- 执行 tool calling loop
- 得到最终 assistant 回复

### SAVE

职责：

- 保存当前 user message
- 保存当前 assistant message
- 保存 SAVE 前已产生的 trace
- 计算本轮 token usage 基础数据
- 判断是否需要进入压缩

### COMPACT

职责：

- 基于保存后的完整历史执行压缩
- 更新 `summary`
- 更新内部压缩游标
- 写入 token usage
- 在 trace metadata 中记录压缩观测字段

COMPACT 的事件结果固定为 `ok`。是否真的发生压缩只通过 metadata 表示。

### RESPOND

职责：

- 构造 `OutboundMessage`
- 返回给 channel

### DONE

职责：

- 结束单轮生命周期

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
data/
  sessions/
    <UTC时间>_<session_id>/
      session.json
      messages.jsonl
      turn_traces.jsonl
      token_usage.jsonl
```

规则：

- 每个 session 一个独立目录。
- 目录名包含 UTC 时间，方便人工区分。
- `/new` 只切换到新 session，不创建空目录。
- `/clear` 删除当前 session 整个目录。
- `retention_days` 默认 7 天，超期 session 在后续请求前清理。

`session.json` 保存内部状态：

- `id`
- `created_at`
- `updated_at`
- `summary`
- `metadata.compacted_message_count`
- `metadata.session_total_tokens`

`messages.jsonl` 保存原文消息：

- `role`
- `content`
- `token_count`
- `created_at`

`turn_traces.jsonl` 保存状态流转：

- `turn_id`
- `session_id`
- `state`
- `duration_ms`
- `event`
- `error`
- `metadata`

`token_usage.jsonl` 保存 token 与压缩观测：

- `input_tokens`
- `output_tokens`
- `turn_total_tokens`
- `total_tokens`
- `compacted`
- `compacted_message_count`
- `compacted_token_count`
- `raw_window_message_count`
- `raw_window_token_count`

## 7. Memory

### 7.1 短期记忆

当前策略是 token 驱动压缩：

```text
compact_token_threshold = 200000
raw_window_token_limit = 20000
```

语义：

- 当未压缩原文历史 token 超过阈值，触发压缩。
- 压缩后保留最近不超过窗口限制的完整原文对话。
- 更早消息进入 `summary`。
- 压缩在 `COMPACT` 状态执行，影响下一轮上下文。

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

当前上下文构建顺序：

1. system prompt
2. 长期偏好
3. 会话 summary
4. tool schema
5. 未压缩历史消息
6. 当前用户消息

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

当前内置工具：

- `echo`
- `now`

下一阶段要做：

- OpenAI-compatible tools schema 转换
- assistant tool call 消息落盘
- tool result 消息落盘
- 真实模型调用工具

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
2. Phase 2：真实 LLM tool calling
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

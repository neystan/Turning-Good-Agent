# Phase 3 Hooks 派生会话 Handoff

## 1. 本派生会话目标

本派生会话只负责完整理解和收口 TGA Hook 设计，不修改生产代码。最终目标是把可执行设计交回原会话，由原会话按计划实现和测试。

## 2. 已确认的核心判断

Hook 不替代状态机。现有状态机继续负责：

```text
COMMAND -> SESSION -> BUILD -> RUN -> COMPACT -> SAVE -> RESPOND
```

Hook 只用于稳定生命周期旁边的扩展逻辑。第一版采用“语义生命周期成对”，但不机械覆盖每个状态。

## 3. 第一版必须实现的顺序 Hook

```text
before_turn       / after_turn
before_tool_call  / after_tool_call
before_compact    / after_compact
```

暂不实现：

```text
before_context_build / after_context_build
before_llm_call      / after_llm_call
before_save          / after_save
before_respond       / after_respond
```

这些 Hook 等 Skills、MCP、模型路由、Web observability 等真实需求出现后再增加。

## 4. 已确认的最小语义

- Hook 是可信的进程内 Python 对象，按注册顺序串行执行。
- 第一版不增加通用 `HookContext`、`HookResult` 或复杂 patch 协议。
- `before_turn` 和 `before_tool_call` 返回 `None` 表示继续，返回非空字符串表示阻断原因。
- 其他 Hook 不返回控制结果。
- 单个 Hook 异常使用标准日志记录并继续，不击穿主流程。
- Hook 不能绕过 ToolRegistry、ToolExecutor 和 `security.py` 的核心安全校验。
- 所有新增文件和函数必须包含精简中文注释。
- 不使用 `uv`。
- 本地测试必须执行，但 `tests/` 不提交 GitHub。

## 5. 三对 Hook 的准确边界

### Turn

`before_turn` 在 `TurnContext` 创建后、状态机前触发。它也覆盖斜杠命令。阻断时直接进入 RESPOND，不运行 SESSION、BUILD、RUN、COMPACT 和 SAVE。实现时必须让 `save_remaining_traces()` 在 `ctx.session is None` 时跳过落盘，避免生成缺少 `session.json` 的孤立目录。

`after_turn` 在状态机结束、剩余 trace 保存完成后触发。它可以观察当前 turn 的 session、messages、tool calls、token、compact stats 和 traces，但不能替代 `.sessions` 核心落盘，也不能代表 Channel 已成功送达消息。

### Tool Call

`before_tool_call` 在 `ToolCall` 解析后、ToolExecutor 执行前触发。阻断只影响当前工具调用，不终止整个 AgentLoop；阻断原因作为 tool result 注入 working messages，让模型继续处理。

`after_tool_call` 在成功、失败、阻断三种结果后都触发。建议传入工具记录浅拷贝，防止 Hook 改写模型上下文和落盘事实。

### Compact

`before_compact` 只在确认需要压缩且 `compact_source` 非空时触发。

`after_compact` 在摘要成功、summary/recent window/usage/统计更新完成后触发。

当前 `runtime/state.py` 直接调用 `print_status()`，使 Runtime 与 CLI 耦合。实现时把两条提示移入 CLI 注册的 Compact Hook；压缩阈值、摘要调用和 session 更新继续留在 COMPACT 核心逻辑。

## 6. `.sessions` 与观测边界

`after_turn` 可以把本轮汇总数据推送给 Web 面板或主动能力，但以下文件仍由 SAVE/SessionStore 直接可靠写入：

```text
session.json
messages.jsonl
tool_calls.jsonl
true_token_usage.jsonl
turn_traces.jsonl
```

完整历史查询继续使用 SessionStore，不让 Hook 每轮重新读取或复制完整会话。

## 7. 事件 Hook 决策

事件 Hook 不与第一版顺序 Hook 同时实现，避免 `after_turn`/`turn.completed`、`after_tool_call`/`tool.completed`、`after_compact`/`compact.completed` 立即重复。

顺序 Hook 稳定后，首批事件候选为：

```text
turn.completed
turn.failed
session.created
session.cleared
session.expired
token.recorded
```

Web observability 阶段再考虑 tool、compact、LLM 和 context 事件。可靠提醒和 cron 必须先持久化任务，再发布事件，不能只依赖内存 Hook。

## 8. 原会话下一步

1. 阅读 `docs/phases/2026-06-15-phase-3-hooks.md`。
2. 按 `docs/superpowers/plans/2026-07-15-phase-3-sequential-hooks.md` 使用 TDD 实现。
3. 先完成 Hook 基类和 manager，再依次接入 Turn、Tool Call、Compact。
4. 运行 Hook 局部测试、全量 `pytest -q`、CLI 冒烟和 `git diff --check`。
5. 检查 `tests/` 未进入 Git 提交后再推送 GitHub。

## 9. 本派生会话文档改动

- 收缩 Phase 3：从完整 Hook 平台改为 3 对轻量顺序 Hook。
- 明确事件 Hook 延后实现。
- 修正 Phase 3 文档链接和 Phase 4-9 路线编号。
- 同步 README、项目架构、持续更新 Spec 和文档索引。
- 新增可直接交给原会话执行的 Phase 3 编码计划。

本派生会话没有修改 `Turning-Good-Agent/` 下的生产代码。

# Turning-Good-Agent Phase 3 轻量顺序 Hooks 设计

> 状态：设计已收口，等待原会话按实施计划编码。

**目标：** 在不改变现有状态机职责的前提下，为整轮执行、工具调用和上下文压缩增加 3 对可信、轻量、按顺序执行的扩展点。

**第一版范围：**

```text
before_turn       / after_turn
before_tool_call  / after_tool_call
before_compact    / after_compact
```

第一版不实现 Context、LLM、SAVE、RESPOND 顺序 hook，也不把事件 hook 接入运行链路。后续只在出现真实扩展需求时新增 hook point。

---

## 1. 阶段定位

Phase 3 位于基础工具扩展之后、MCP 之前。它只建立稳定扩展点，不建设 Claude Code 式的完整 hook 平台。

Hook 不替代状态机。现有主流程继续保持：

```text
COMMAND -> SESSION -> BUILD -> RUN -> COMPACT -> SAVE -> RESPOND
```

Hook 只挂在主流程旁边：

```text
before_turn
  状态机
    AgentLoop: before_tool_call -> ToolExecutor -> after_tool_call
    COMPACT:   before_compact   -> ShortTermMemory -> after_compact
after_turn
```

## 2. 为什么只实现 3 对

### Turn

`before_turn` 在 `TurnContext` 创建后、状态机运行前触发。第一版允许返回阻断原因；返回 `None` 继续执行。

阻断发生在 SESSION 之前，因此不能创建孤立会话目录；`save_remaining_traces()` 在 `ctx.session is None` 时跳过落盘，trace 仍可由 `after_turn` 从内存读取。

`after_turn` 在状态机结束、剩余 trace 保存完成后触发，用于读取本轮最终结果。它可以承接整轮观测和主动能力通知，但不负责核心 JSONL 持久化。

斜杠命令同样经过 Turn hook：

```text
before_turn -> COMMAND -> RESPOND -> after_turn
```

### Tool Call

`before_tool_call` 在模型 tool call 已解析、工具尚未执行时触发。第一版允许返回阻断原因；返回 `None` 执行工具。

`after_tool_call` 在工具成功、失败或被阻断后触发。它读取现有工具记录，不修改 ToolExecutor 的核心结果。

工具被 Hook 阻断时不终止整轮 AgentLoop，而是生成明确的 tool result 交给模型继续处理。

### Compact

`before_compact` 只在确认需要压缩且 `compact_source` 非空时触发，不在每轮无压缩时触发。

`after_compact` 在压缩成功并完成统计更新后触发。CLI 压缩提示由 CLI 注册的 Hook 输出，`runtime/state.py` 不再直接打印终端内容。

Compact hook 不替代压缩阈值判断、recent window 计算、摘要 LLM 调用、summary 更新和持久化。

## 3. 最小接口

第一版不新增通用 `HookContext`、`HookResult` 或复杂 patch 协议，直接复用现有对象。

```python
class AgentHook:
    """定义第一版顺序 Hook 生命周期。"""

    async def before_turn(self, ctx: TurnContext) -> str | None: ...
    async def after_turn(self, ctx: TurnContext) -> None: ...
    async def before_tool_call(self, call: ToolCall) -> str | None: ...
    async def after_tool_call(self, record: dict[str, Any]) -> None: ...
    async def before_compact(self, ctx: TurnContext) -> None: ...
    async def after_compact(self, ctx: TurnContext) -> None: ...
```

返回规则：

```text
before_turn / before_tool_call:
  None       -> 继续
  非空字符串 -> 阻断，字符串作为原因

其他方法:
  不返回控制结果
```

## 4. 执行规则

- Hook 按注册顺序串行执行，第一版不并行。
- `before_turn` 和 `before_tool_call` 遇到第一个阻断原因后停止该 hook chain。
- `after_*` 执行所有已注册 Hook。
- 单个 Hook 异常使用标准日志记录并继续，不击穿主流程。
- Hook 是可信的进程内 Python 对象，不扫描 shell 脚本、不调用远程 Hook、不加载第三方插件包。
- Core security 仍由 ToolRegistry、ToolExecutor 和 `security.py` 保证；Hook 返回允许不能绕过底线校验。
- `after_*` 按只读约定使用接收到的对象，不负责 session、message、tool call、token 或 trace 的核心落盘。

## 5. Runtime 与 Channel 解耦

当前 COMPACT 状态直接调用 `print_status()`，使 Runtime 知道终端展示方式。第一版新增 CLI Hook：

```text
Runtime COMPACT
  -> before_compact
      -> CLI Hook 打印“正在压缩”
  -> ShortTermMemory.compact
  -> after_compact
      -> CLI Hook 打印压缩统计
```

未来 Web、微信和飞书可以注册各自处理方式，Runtime 只表达压缩开始和完成，不依赖具体 Channel。

## 6. 与 `.sessions` 的边界

`after_turn` 可以读取 `TurnContext` 中本轮已经汇集的数据：

- session 当前状态；
- 用户输入和最终回复；
- tool calls；
- token usage；
- compact stats；
- state traces。

它适合把本轮摘要推送给 Web 面板或主动能力，但不能替代：

- `session.json`；
- `messages.jsonl`；
- `tool_calls.jsonl`；
- `true_token_usage.jsonl`；
- `turn_traces.jsonl`。

完整历史查询仍由 SessionStore 负责，Hook 不重复读取或复制整个会话。

## 7. 事件 Hook 后续设计

事件 Hook 只通知已经发生的事实，不能阻断或修改当前 turn。第一版顺序 Hook 完成并测试稳定后，再单独设计事件分发。

首批候选事件：

```text
turn.completed
turn.failed
session.created
session.cleared
session.expired
token.recorded
```

后续 Web observability 阶段可增加：

```text
tool.started / tool.completed / tool.failed / tool.blocked
compact.started / compact.completed / compact.failed
llm.completed / llm.failed / llm.retried
context.built / context.rejected
```

可靠提醒、cron 和主动任务必须先持久化，再发布事件，不能只依赖内存 Hook。

## 8. 第一版文件范围

Create: `Turning-Good-Agent/hooks/base.py`

定义带 6 个默认空实现的 `AgentHook`。

Modify: `Turning-Good-Agent/hooks/manager.py`

维护 Hook 注册顺序并触发 6 个生命周期方法。

Modify: `Turning-Good-Agent/runtime/runtime.py`

持有唯一 `HookManager`，触发 `before_turn` 和 `after_turn`，并把同一 manager 传给 AgentLoop。

Modify: `Turning-Good-Agent/runtime/agent_loop.py`

在工具调用前后触发 Hook，阻断时向 working messages 注入明确的工具结果。

Modify: `Turning-Good-Agent/runtime/state.py`

在实际压缩前后触发 Hook，删除 Runtime 内的 CLI `print_status()`。

Modify: `Turning-Good-Agent/cli.py`

注册 CLI 压缩状态 Hook。

第一版不修改核心 `.sessions` 文件结构，也不增加新的 JSONL 文件。

## 9. 完成标准

- 多个 Hook 严格按注册顺序执行。
- `before_turn` 可以阻断整轮并返回可理解的错误响应。
- `before_turn` 在 SESSION 前阻断时不会产生缺少 `session.json` 的孤立目录。
- `before_tool_call` 可以阻断单次工具执行，AgentLoop 仍能继续。
- 工具成功、失败、阻断后都会触发 `after_tool_call`。
- 只有真实压缩才触发 Compact hook。
- Runtime 不再直接打印 CLI 压缩提示。
- Hook 异常不会破坏状态机、工具执行或会话核心落盘。
- 现有 CLI、流式输出、工具调用、压缩和 JSONL 行为保持兼容。
- 所有新增文件和函数包含精简中文注释。
- 项目继续使用现有 Python 环境和 `pytest`，不引入 `uv`。

## 10. 参考与取舍

TGA 借鉴 `/download/learn-claude-code/s04_hooks` 的“挂在循环上，不写进循环里”和简单阻断返回值，也借鉴 nanobot 的 Hook 基类与顺序组合方式。

第一版不复制 Claude Code 的 matcher、command/http/MCP/prompt/agent Hook、配置层级和完整决策协议。需要这些能力时，再基于真实需求扩展。

详细编码步骤见：`docs/superpowers/plans/2026-07-15-phase-3-sequential-hooks.md`。

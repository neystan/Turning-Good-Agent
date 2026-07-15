# Turning-Good-Agent Phase 3 Hooks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 实现 CLI 工具审批、工具结果截断和 CLI 会话压缩状态提示三个轻量 Hook 功能。

**Architecture:** Hook 只扩展既有的 AgentLoop 和 COMPACT 生命周期，不替代状态机、ToolExecutor 或会话持久化。Runtime 默认注册跨 Channel 的工具结果截断 Hook；CLI 注册审批和压缩提示 Hook。

**Tech Stack:** Python 3.11+、asyncio、pytest、JSON/JSONL。

---

## Current Completion Status

代码核对结论：Phase 3 实现范围已经完成。

已完成：

- 工具调用在 Schema 标准化和 security 预检后进入 CLI 审批。
- `write_file`、`edit_file`、`exec`、`write_stdin` 在执行前提示用户输入 `y/N`。
- 用户拒绝时，AgentLoop 将拒绝原因作为 tool result 注入模型上下文，工具不执行。
- `ToolResultTruncationHook` 在工具结果注入 LLM 和写入 `tool_calls.jsonl` 前按 token 上限截断内容。
- CLI 在真实压缩开始与完成时输出状态和压缩统计。
- `HookManager` 按注册顺序调用 Hook，单个 Hook 异常记录后继续。
- 工具 Hook 只接收标准化 `ToolCall` 与工具记录，不使用额外上下文对象。
- AgentLoop 使用 `_execute_tool_call()` 收口工具校验、审批、执行和结果处理，主循环只控制 LLM 与工具轮次。

已明确的 Phase 3 边界：

- 不实现审批持久化、跨 Channel 审批或暂停后的恢复执行。
- 不实现事件 Hook、远程 Hook、shell Hook、HTTP Hook 或第三方插件 Hook。
- Hook 不能绕过 `security.py` 的路径、命令、URL 和参数安全限制。

## Scope

本阶段实现：

- 工具调用前的 CLI 同步审批
- 工具调用后的模型侧结果截断
- 会话压缩前后的 CLI 状态提示
- 进程内顺序 Hook 注册、执行和异常隔离

本阶段不实现：

- 通用 `HookContext`、`HookResult` 或审批状态机
- 审批请求 JSON/JSONL 持久化
- Web、微信、飞书渠道的审批交互
- 事件 Hook 和外部 Hook 执行环境

## Target File Map

Create: `Turning-Good-Agent/hooks/base.py`

定义工具调用前后与压缩前后的最小 Hook 接口。

Create: `Turning-Good-Agent/hooks/manager.py`

按注册顺序执行 Hook，处理阻断、结果管道和异常隔离。

Create: `Turning-Good-Agent/hooks/cli.py`

实现 CLI 工具同步审批和压缩状态提示。

Create: `Turning-Good-Agent/hooks/tool_result_truncation.py`

按工具类型和 token 上限截断模型可见结果。

Modify: `Turning-Good-Agent/runtime/agent_loop.py`

在工具执行前后调用 Hook，并保持工具结果进入本轮 working messages。

Modify: `Turning-Good-Agent/runtime/state.py`

在真实压缩前后调用 CLI 状态 Hook。

Modify: `Turning-Good-Agent/runtime/runtime.py`

默认注册跨 Channel 的工具结果截断 Hook。

Modify: `Turning-Good-Agent/cli.py`

注册 CLI 审批与压缩状态 Hook。

## Task 1: Hook Foundation

- [x] **Step 1: 定义最小 Hook 接口**

```python
class AgentHook:
    async def before_tool_call(self, call: ToolCall) -> str | None: ...
    async def after_tool_call(self, call: ToolCall, record: dict) -> dict: ...
    async def before_compact(self, ctx: TurnContext) -> None: ...
    async def after_compact(self, ctx: TurnContext) -> None: ...
```

- [x] **Step 2: 实现顺序 HookManager**

要求：

- `before_tool_call` 返回首个非空阻断原因。
- `after_tool_call` 将上一个 Hook 的结果传递给下一个 Hook。
- 压缩 Hook 只做通知，按顺序全部执行。
- 单个 Hook 抛出异常时记录日志并继续。

## Task 2: Tool Hooks

- [x] **Step 1: 实现 CLI 工具审批**

审批范围：`write_file`、`edit_file`、`exec`、`write_stdin`。

```text
Schema 标准化
-> security 预检
-> CLI y/N 审批
-> ToolExecutor 再次安全检查
-> 执行工具
```

- [x] **Step 2: 实现工具结果截断**

默认 `max_tool_result_tokens = 8000`。列表和搜索结果保留前部，命令结果保留尾部，文件和网页正文保留头尾，并提示模型使用更精确的条件再次查询。

## Task 3: Compact Hook

- [x] **Step 1: 实现 CLI 压缩状态提示**

仅在确认需要真实压缩且存在可压缩消息时调用 `before_compact`；摘要、recent window、usage 和统计更新成功后调用 `after_compact`。

## Task 4: Validation and Documentation

- [x] **Step 1: 补充 Hook、AgentLoop 和压缩行为测试**

- [x] **Step 2: 同步 README、架构、总 Spec 和文档索引**

## Completion Criteria

- CLI 可以允许或拒绝副作用工具。
- 用户拒绝时工具不执行，AgentLoop 可以继续回答。
- 工具结果截断后的同一内容用于 LLM 和 `tool_calls.jsonl`。
- CLI 只在真实压缩前后输出状态。
- 专项测试与全量测试通过，CLI 启动退出正常。

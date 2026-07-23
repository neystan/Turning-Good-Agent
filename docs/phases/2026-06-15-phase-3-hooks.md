# Turning-Good-Agent Phase 3 Hooks Implementation Plan

状态：已完成实现。

Goal：以最小、进程内、顺序执行的 Hook 机制扩展工具权限、工具结果处理、按 Channel 的交互状态与会话终态观测；不替代 Runtime 状态机、ToolExecutor 或 Session Store。

Architecture：Runtime 在启动时按固定顺序注册 `ToolPermissionHook`、`ToolResultTruncationHook`、`ChannelStatusHook`、`TurnMonitorHook`。工具生命周期由 AgentLoop 调用 HookManager，压缩生命周期由 COMPACT 状态调用 HookManager，终态监控由 Runtime 在 RESPOND trace 创建前调用 HookManager。Session Store 仍是 JSON/JSONL 的唯一持久化者，Runtime 仍是 StateTrace 与状态耗时的唯一生产者。

Tech Stack：Python 3.11+、asyncio、pytest、现有 JSON/JSONL Session 存储。无新增第三方依赖。

---

## 完成范围

- [x] `AgentHook` 与 `HookManager` 提供顺序注册、异常隔离、工具前阻断、工具结果管道、压缩通知与终态元数据合并。
- [x] `ToolPermissionHook` 为副作用工具提供会话级自动审批。
- [x] `ToolResultTruncationHook` 在模型注入与 `tool_calls.jsonl` 落盘前统一截断工具结果。
- [x] `ChannelStatusHook` 通过当前 `ChannelAdapter` 显示工具与压缩生命周期状态。
- [x] `ChannelAdapter` 收口 CLI 的流式输出、工具动画、最终成功/失败输出与 `y/N` 审批。
- [x] 连续 `parallel_safe=true` 工具调用可在开关开启时并发执行，结果保持模型请求顺序。
- [x] `TurnMonitorHook` 将四个只读终态字段写入 `RESPOND.metadata`，不新增监控 JSONL。

## Hook 基础

Hook 是代码内显式注册的可信扩展点，按注册顺序执行。单个 Hook 抛出异常时记录日志并继续；`before_tool_call` 返回首个非空阻断原因，`after_tool_call` 将前一个 Hook 的结果传给下一个 Hook。

当前生命周期：

```text
before_tool_call(call, channel_adapter, auto_approve_tools) -> str | None
on_tool_started(call, channel_adapter)
after_tool_call(call, record) -> record
before_compact(ctx)
after_compact(ctx)
after_turn(ctx, turn_duration_ms, session_lock_wait_ms) -> metadata
```

工具 Hook 只接收标准化 `ToolCall`、工具记录和实际需要的 Channel/审批参数，不使用大型通用 Context。只有终态监控需要读取完成后的 trace、错误和工具记录，因此 `after_turn` 接收只读 `TurnContext`。

## Channel 输出

Runtime 根据 `InboundMessage.channel` 通过 `ChannelRouter` 创建单轮 `ChannelAdapter` 并保存到 `TurnContext`。Runtime 与 AgentLoop 不导入 CLI、Web、微信或飞书具体模块。

```python
class ChannelAdapter(Protocol):
    """定义 Channel 的输出与工具审批能力。"""

    async def on_delta(self, text: str) -> None: ...
    async def on_status(self, text: str) -> None: ...
    async def on_tool_started(self, tool_call_id: str, tool_name: str) -> None: ...
    async def on_tool_finished(self, tool_call_id: str, tool_name: str, failed: bool) -> None: ...
    async def on_completed(self, content: str) -> None: ...
    async def on_error(self, content: str) -> None: ...
    async def request_tool_approval(self, call: ToolCall) -> str | None: ...
```

`CliChannelAdapter` 负责：

- 流式 delta 的换行和最终回复去重。
- 工具开始、完成或失败提示。
- 按 `tool_call_id` 区分并行工具的动态 `...` 状态行；完成时清理并重绘剩余状态行。
- 压缩前后状态提示。
- 审批类工具的 CLI `y/N` 输入；`y`、`yes`、`允许` 代表批准。

未注册 Channel 使用 `SilentChannelAdapter`：忽略中间 delta 和状态，仍返回最终 `OutboundMessage`；对于审批类工具确定性返回“当前 Channel 不支持工具审批。”。

运行链路：

```text
InboundMessage.channel
  -> ChannelRouter.create(channel)
  -> TurnContext.channel_adapter
  -> AgentLoop delta / ChannelStatusHook
  -> ChannelAdapter
  -> CLI 或未来 Channel 传输层

Runtime final response
  -> ChannelAdapter.on_completed / on_error
  -> OutboundMessage
```

## 会话级工具审批

审批类工具由 `ToolPermissionSettings.approval_required_tools` 集中配置，默认是：

```text
write_file
edit_file
exec
write_stdin
```

`Session.auto_approve_tools` 默认 `false`，显式保存于 `session.json`。旧会话缺少该字段时安全默认 `false`。

| 命令 | 已存在会话 | 不存在会话 |
| --- | --- | --- |
| `/approve` | 显示当前状态。 | 显示关闭，不创建目录。 |
| `/approve on` | 保存 `true`。 | 创建当前 session 并保存 `true`。 |
| `/approve off` | 保存 `false`。 | 返回关闭，不创建目录。 |

`/new` 创建的新会话默认关闭；`/clear` 删除会话目录与审批设置。重启后重新打开同一 session 时，已保存的审批开关继续生效。

工具执行顺序固定为：

```text
LLM tool_call
  -> ToolRegistry 参数标准化
  -> security.py 硬安全预检
  -> ToolPermissionHook
       -> 自动批准，或 ChannelAdapter.request_tool_approval()
  -> ChannelStatusHook 工具开始提示
  -> ToolExecutor 二次安全预检
  -> 执行工具
  -> ChannelStatusHook 工具结束提示
  -> ToolResultTruncationHook
  -> role=tool 注入本轮 LLM 上下文
  -> SAVE 写入 tool_calls.jsonl
```

用户拒绝或当前 Channel 不支持审批时，工具不执行；系统构造 error tool record 并以 `role=tool` 注入 LLM，使模型能选择替代路径或向用户说明。安全预检拒绝时不会请求审批。自动审批仅跳过人工确认，不能绕过危险命令、路径越界、危险设备路径或 `.sessions` 写入限制。

Runtime 启动后校验审批配置：工具必须已注册，且 `parallel_safe` 必须为 `false`，避免并行 CLI 输入审批。

## 工具结果与并行调用

`ToolResultTruncationHook` 在工具结果注入模型前按 `max_tool_result_tokens` 截断。截断后的同一 `content` 同时用于：

- 本轮 LLM 的 `role=tool` 消息。
- `tool_calls.jsonl`。
- `/tools` 命令与后续 Web 观测。

不会额外保存未截断的大结果，也不新增 Hook 专用 JSONL。

`parallel_tool_calls_enabled` 开启时，AgentLoop 仅将连续且 `parallel_safe=true` 的调用放入 `asyncio.gather()` 批次；副作用工具、审批工具和未标记安全的工具保持串行。每批 `gather()` 结果按输入顺序返回，再按模型原始 `tool_calls` 顺序回注 working messages。

```text
web_search, weather, write_file, read_file, web_fetch
-> [web_search, weather] 并行
-> write_file 串行
-> [read_file, web_fetch] 并行
```

## 压缩状态

只有确认需要真实压缩且存在 `compact_source` 时，COMPACT 才调用 `before_compact`。摘要、recent window、usage 与压缩统计均成功更新后，才调用 `after_compact`。`ChannelStatusHook` 通过 `on_status()` 显示压缩开始和完成信息，不改写压缩算法。

## 终态监控

`TurnMonitorHook` 只读取完成态 `TurnContext`，不调用 LLM、工具、Session Store 或 Channel，不直接写入 JSONL。Runtime 在 `RESPOND` 已完成、确定状态机没有下一状态、不是 slash command 且存在 session 时调用它，并在创建 RESPOND trace 前合并返回字段。

`RESPOND.metadata` 仅新增：

| 字段 | 含义 |
| --- | --- |
| `outcome` | `completed`、`rejected` 或 `failed`。BUILD 返回 `rejected` 优先于 `ctx.error`。 |
| `turn_duration_ms` | 从 `run_turn()` 进入到 RESPOND 完成的总耗时，包含锁等待和状态机执行，不含 trace 落盘和最终 Channel 展示。 |
| `session_lock_wait_ms` | 等待当前 session `asyncio.Lock` 的耗时。 |
| `tool_failure_count` | 本轮最终工具记录中 `error` 非空的数量。 |

成功工具数可由 `RUN.metadata.tool_call_count - tool_failure_count` 得出，因此不重复保存 `tool_success_count`。

唯一事实数据仍在既有文件中：消息/摘要在 `session.json` 与 `messages.jsonl`，token 在 `true_token_usage.jsonl`，工具明细在 `tool_calls.jsonl`，状态耗时与元数据在 `turn_traces.jsonl`。不创建 `monitor.jsonl`、`monitor_events.jsonl` 或 `llm_calls.jsonl`。

## 实现文件

| 文件 | 职责 |
| --- | --- |
| `hooks/base.py` | 声明六个最小 Hook 生命周期。 |
| `hooks/manager.py` | 按顺序调度 Hook、隔离异常、合并工具记录和终态元数据。 |
| `hooks/tool_permission.py` | 对审批类工具读取会话自动审批状态并委托 Channel 确认。 |
| `hooks/tool_result_truncation.py` | 按 token 上限处理模型可见工具结果。 |
| `hooks/channel_status.py` | 转发工具与压缩生命周期状态。 |
| `hooks/turn_monitor.py` | 计算只读终态监控字段。 |
| `channels/base.py` | 定义 `ChannelAdapter`、静默实现和路由器。 |
| `channels/cli.py` | 实现 CLI 流式渲染、工具动画和审批输入。 |
| `runtime/agent_loop.py` | 组织模型工具循环、并行批次与工具 Hook 链路。 |
| `runtime/state.py` | 在 COMPACT 触发压缩 Hook。 |
| `runtime/runtime.py` | 创建 ChannelAdapter、注册 Hook、创建 trace 并在终态合并监控字段。 |
| `runtime/turn_context.py` | 保存单轮 ChannelAdapter、工具记录与 trace。 |
| `sessions/types.py` / `sessions/store.py` / `sessions/manager.py` | 保存审批开关并处理 `/approve` 命令。 |
| `config/settings.py` | 保存审批工具列表和并行调用配置。 |
| `cli.py` | 注册 CLI ChannelAdapter。 |

## 明确不实现

- Web、SSE、WebSocket、FastAPI、Dashboard、微信或飞书传输与审批交互。
- MessageBus 流式消费、delta 持久化、取消、重连或事件回放。
- 审批请求持久化、超时、恢复、跨 Channel 审批或跨进程等待。
- 用户级审批偏好、临时授权、白名单、黑名单、RBAC 或动态策略语言。
- 通用 `HookContext`、`HookResult`、事件 Hook、远程 Hook、shell Hook、HTTP Hook 或第三方 Hook 插件系统。
- 额外监控文件、LLM 单请求耗时、重试次数或流式 delta 落盘。
- 副作用工具并行、跨轮工具并行或多 Agent 调度。

## 验证记录

已覆盖：工具审批的开关与持久化、CLI 批准/拒绝、静默 Channel 拒绝、security 先于审批、并行工具状态、流式 CLI 输出与终态监控的完成/拒绝/失败/锁竞争路径。

最终验证：

```bash
pytest -q
git diff --check
printf '/exit\n' | python -m Turning-Good-Agent chat
```

最近一次 Phase 3 完整回归结果：`146 passed`。

## 后续关系

Phase 4 MCP 已通过同一 `ToolRegistry` 接入，并复用 `ToolPermissionHook`、`ToolResultTruncationHook` 与 Channel 状态输出；其审批和 Runtime 收口见 `2026-07-23-phase-4-mcp-runtime-refactor.md`。Phase 6 Dashboard 应按 `turn_id` 聚合既有 `RESPOND`、RUN、COMPACT、SAVE trace 与 token/tool/message 文件。若未来需要实时推送，再独立设计 `observability/hub.py` 与 SSE/WebSocket，不放入当前 Hook 或 Channel 实现。

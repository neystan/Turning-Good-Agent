# MCP 后台 Worker 设计

状态：已完成。

## 目标

MCP Server 在 Runtime 启动时并行后台连接，不阻塞 CLI、Web、微信或飞书的正常会话。单个 Server 连接失败后按配置重试，最终失败不影响其他 Server、内置工具或普通 LLM 对话。

## 范围

- stdio 与 Streamable HTTP MCP Server 都使用后台连接。
- 每个 Server 独立重试、独立状态、独立关闭。
- 所有 `McpClient` 生命周期操作在同一个 Worker Task 中执行。
- Web 可读取内存状态；CLI、微信、飞书不因后台状态输出打断用户会话。
- 已连接 Server 在工具调用时断开，会返回本次调用错误并进入后台重连。

不实现持久化重试任务、跨进程共享连接、OAuth、定时无限重试、额外监控 JSONL 或 Channel 专用 MCP 协议。

## 架构

新增 `mcp/server_worker.py`，每个启用 Server 对应一个 `McpServerWorker`。Worker 是 `McpClient` 的唯一所有者，负责以下操作：

- `connect`：创建 Client、初始化、发现 Catalog。
- `refresh_catalog`：复用已连接 Client 重新发现 Catalog。
- `call_tool`、`read_resource`、`get_prompt`：转发远端请求。
- `reconnect`：在 Worker 内关闭旧 Client 后重新连接。
- `close`：在创建 transport 的同一个 Task 内关闭 Client 并退出。

Worker 内部使用一个 `asyncio.Queue` 串行消费命令。每个 `call_tool`、`read_resource`、`get_prompt`、`refresh_catalog`、`reconnect` 与 `close` 命令携带自己的 Future；Worker 在自身 Task 内完成请求后写入 Future。`list_changed` 仅入队一个无等待的 `refresh_catalog` 命令。

`McpManager` 不再直接持有或关闭 `McpClient`。它只持有 Worker、内存 Catalog、`McpServerStatus` 与 ToolRegistry 更新逻辑。Worker 通过 Future 返回请求结果，通过回调提交 Catalog 或状态变化。

```text
Channel Host startup
        |
        v
AgentRuntime.start()
        |
        v
McpManager.start_background()
        |
        +-- McpServerWorker(modular_rag)
        +-- McpServerWorker(github)
        +-- ...
```

## 启动与 Channel 生命周期

`AgentRuntime.start()` 只创建后台启动任务并立即返回，不等待任何 MCP Server。

- CLI：在进入输入循环前调用 `await runtime.start()`。
- Web：在应用 lifespan startup 调用 `await runtime.start()`。
- 微信、飞书：在机器人进程 startup 调用 `await runtime.start()`。
- 所有 Host shutdown 都调用 `await runtime.close()`，由 Manager 等待全部 Worker 在自身 Task 中关闭。

`run_turn()` 不再负责首次启动 MCP。首轮 BUILD 仅使用当时已注册的 Tool schema；某个 MCP Server 完成发现后，其显式启用的工具从后续模型调用开始可见。

## 状态与重试

`McpServerStatus` 增加以下内存字段：

- `state`：`connecting`、`connected`、`retry_wait`、`failed`、`closed`。
- `attempt`：当前连接尝试次数。
- `next_retry_at`：下一次重试的 UTC 时间，无重试时为空。
- `error`：最近一次错误的简短文本。

每 Server 配置以下字段：

```json
{
  "connect_retry_attempts": 3,
  "connect_retry_delay_seconds": 1,
  "connect_retry_max_delay_seconds": 8
}
```

`connect_retry_attempts` 表示首次尝试失败后的额外重试次数。延迟采用指数退避：`1s`、`2s`、`4s`，但不超过 `connect_retry_max_delay_seconds`。达到上限后状态为 `failed`，本进程不继续无限重试。每次新启动和手动 `refresh_server(name)` 都会开始新的重试周期。

任何连接或调用异常都不得以 `CancelledError`、`ExceptionGroup` 或 SDK traceback 形式击穿 Runtime。Worker 将其转换为状态和明确的工具错误；只有 Worker 自己处理 Client 的关闭。

## Catalog、ToolRegistry 与请求

Worker 完成 `connect` 或 `refresh_catalog` 后，Manager 在每 Server 锁内替换该 Server 的 Catalog，并重新注册 `enabled_tools` 白名单中的 `mcp_<server>_<tool>`。

`list_changed` 通知只向所属 Worker 投递 `refresh_catalog` 命令，不重建连接。手动 `refresh_server(name)` 向 Worker 投递 `reconnect` 命令。

调用 `call_tool`、`read_resource`、`get_prompt` 时，Manager 将请求交给对应 Worker 并等待其 Future：

- `connected`：执行远端调用。
- `connecting` 或 `retry_wait`：返回“正在连接或重试”。
- `failed` 或 `closed`：返回最近失败原因。

远端调用出现连接级错误时，当前工具调用返回错误；Worker 随后开始新的重连周期。连接级错误包括网络、DNS、TLS、HTTP transport、stdio 子进程退出和 MCP Session 关闭。权限不足、参数无效、资源不存在或 MCP Tool 返回业务错误时，只返回当前调用失败，不触发重连。审批、`security.py` 和 `ToolExecutor` 的硬安全检查保持不变。

## 可观测性与 Channel 表现

不增加监控 JSONL。`McpManager.statuses` 是 Web 面板读取连接状态的唯一运行时来源。CLI 后续可通过 `/mcp` 查询该状态；本次不在输入行或聊天正文中异步插入连接日志。微信和飞书同样只在用户主动查询或工具调用失败时呈现 MCP 状态。

## 验证

- 两个 Server 的连接任务同时启动，慢或失败 Server 不延迟 `run_turn()`。
- 一个 stdio Server 成功、一个 Streamable HTTP Server 不可达时，普通会话和 stdio 工具正常。
- 连接失败按 `1s`、`2s`、`4s` 重试，达到次数后进入 `failed`。
- Worker 关闭时由创建 transport 的 Task 调用 `McpClient.close()`，不再出现 AnyIO cancel scope 跨任务错误。
- `list_changed` 刷新 Catalog 不重连 Client；手动刷新会在 Worker 内重新连接。
- CLI、Web lifecycle 与模拟办公软件 Host 均调用同一组 `runtime.start()` / `runtime.close()`。
- 全量 `pytest -q`、`git diff --check` 和 CLI `/exit` 冒烟通过。

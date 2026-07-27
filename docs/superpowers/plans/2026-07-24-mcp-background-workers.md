# MCP 后台 Worker 实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 MCP Server 在所有 Channel Host 启动时后台并行连接，并以每 Server 独立、仅连接级错误触发的有限重试维持可用性。

**Architecture:** 每个启用 Server 对应一个 `McpServerWorker`。Worker 的单一 Task 通过命令队列串行拥有 `McpClient` 的连接、发现、请求、重连和关闭；`McpManager` 只管理 Worker、状态、Catalog 与 ToolRegistry。Runtime 提供无阻塞的 `start()` 生命周期入口，CLI 立即调用它，未来 Web、微信和飞书 Host 使用同一入口。

**Tech Stack:** Python 3.11+、asyncio、官方 Python MCP SDK、pytest。

## 全局约束

- 所有新增函数添加精简中文注释。
- 只支持 stdio 与 Streamable HTTP；不新增 OAuth、SSE、持久化重试或额外监控 JSONL。
- `McpClient` 生命周期操作只能在其 Worker Task 中执行。
- 仅网络、DNS、TLS、HTTP transport、stdio 子进程退出、MCP Session 关闭等连接级错误触发重连。
- 权限、参数、资源不存在和 Tool 业务错误不触发重连。
- `connect_retry_attempts=3` 表示首次失败后的额外三次重试；退避为 1、2、4 秒，最大值可配置。
- 测试仅本地验证，不得暂存 `tests/`；不得提交 `settings.local.json`、`.sessions/` 或无关改动。
- `docs/` 受版本控制；更新文档时只暂存本阶段相关文件。

## 文件职责

- `Turning-Good-Agent/config/settings.py`：读取 Server 重试配置。
- `Turning-Good-Agent/mcp/types.py`：连接状态与错误分类。
- `Turning-Good-Agent/mcp/client.py`：官方 SDK 调用和连接错误归一化。
- `Turning-Good-Agent/mcp/server_worker.py`：Client 唯一所有者、命令队列与重试状态机。
- `Turning-Good-Agent/mcp/manager.py`：Worker、Catalog、ToolRegistry 和调用转发。
- `Turning-Good-Agent/runtime/runtime.py`：无阻塞启动入口。
- `Turning-Good-Agent/cli.py`：CLI Host 生命周期调用。

### Task 1：配置与连接错误分类

**Files:**
- Modify: `Turning-Good-Agent/config/settings.py`
- Modify: `Turning-Good-Agent/mcp/types.py`
- Modify: `Turning-Good-Agent/mcp/client.py`
- Test: `tests/test_mcp_settings.py`
- Test: `tests/test_mcp_client.py`

**Produces:** `McpServerSettings.connect_retry_attempts`、`connect_retry_delay_seconds`、`connect_retry_max_delay_seconds`；`is_mcp_connection_error(error)`。

- [x] 写失败测试：读取 `3`、`1.0`、`8.0` 三个配置；负次数、零延迟和零最大延迟抛出 `ValueError`；`OSError`、`httpx.HTTPError` 和对应 `ExceptionGroup` 为连接级错误，普通 `RuntimeError` 不是。
- [x] 运行 `pytest tests/test_mcp_settings.py tests/test_mcp_client.py -q`，确认新字段和分类函数缺失而失败。
- [x] 最小实现：配置默认值为 `3`、`1.0`、`8.0`；递归检查 `BaseExceptionGroup` 的叶子异常，只把 transport 相关异常归为连接级错误。
- [x] 再次运行上述测试，确认通过。
- [x] 仅暂存生产代码并提交：`git add Turning-Good-Agent/config/settings.py Turning-Good-Agent/mcp/types.py Turning-Good-Agent/mcp/client.py && git commit -m "feat: configure mcp connection retries"`。

### Task 2：每 Server Worker 与重试状态机

**Files:**
- Create: `Turning-Good-Agent/mcp/server_worker.py`
- Modify: `Turning-Good-Agent/mcp/types.py`
- Test: `tests/test_mcp_worker.py`

**Produces:** `McpServerWorker.start()`、`call_tool()`、`read_resource()`、`get_prompt()`、`refresh_catalog()`、`reconnect()`、`close()`；`McpServerStatus.state`、`attempt`、`next_retry_at`、`error`。

- [x] 写失败测试：Fake Client 前两次 `connect()` 抛 `OSError`、第三次成功时状态为 `connecting -> retry_wait -> connected`；业务 Tool 错误不增加连接次数；连接级 Tool 错误完成当前请求错误后重试；关闭记录由 Worker Task 调用 Client close。
- [x] 运行 `pytest tests/test_mcp_worker.py -q`，确认因 Worker 不存在而失败。
- [x] 最小实现：Worker 在单个 `asyncio.Task` 内消费命令队列；首次失败后等待 `min(base * 2 ** retry_index, maximum)`；成功后串行处理远端请求；`list_changed` 只入队 Catalog 刷新；关闭命令在同一 Task 内执行 `client.close()`。
- [x] 再次运行 `pytest tests/test_mcp_worker.py -q`，确认通过。
- [x] 仅暂存生产代码并提交：`git add Turning-Good-Agent/mcp/server_worker.py Turning-Good-Agent/mcp/types.py && git commit -m "feat: run mcp servers in background workers"`。

### Task 3：Manager 改为 Worker 管理与并行启动

**Files:**
- Modify: `Turning-Good-Agent/mcp/manager.py`
- Modify: `Turning-Good-Agent/mcp/adapter.py`
- Test: `tests/test_mcp_manager.py`

**Consumes:** `McpServerWorker` 的 Catalog/状态回调与请求接口。

**Produces:** `McpManager.start_background(registry)`、Worker 驱动的 `refresh_server()`、`close()`、`call_tool()`、`read_resource()`、`get_prompt()`。

- [x] 写失败测试：慢 Server 连接中时 `start_background()` 立即返回；快 Server 可独立注册 Tool；失败 Server 重试不影响快 Server；`list_changed` 仅调用 Worker 刷新；Manager 关闭等待所有 Worker。
- [x] 运行 `pytest tests/test_mcp_manager.py -q`，确认缺少后台启动和 Worker 转发而失败。
- [x] 最小实现：移除 `clients` 字典；Manager 保存 `workers` 与注册表引用。每个 enabled Server 创建独立 Worker；Catalog 回调在每 Server 锁内注销旧前缀、替换 Catalog、注册 `enabled_tools`；未连接 Server 的请求立即返回状态错误，不等待。
- [x] 运行 `pytest tests/test_mcp_manager.py tests/test_mcp_worker.py -q`，确认通过。
- [x] 仅暂存生产代码并提交：`git add Turning-Good-Agent/mcp/manager.py Turning-Good-Agent/mcp/adapter.py && git commit -m "refactor: manage mcp workers asynchronously"`。

### Task 4：Runtime 与 Channel Host 生命周期

**Files:**
- Modify: `Turning-Good-Agent/runtime/runtime.py`
- Modify: `Turning-Good-Agent/cli.py`
- Test: `tests/test_runtime_mcp_lifecycle.py`
- Test: `tests/test_cli.py`

**Produces:** 可重复调用且不等待连接的 `AgentRuntime.start()`。

- [x] 写失败测试：`runtime.start()` 调用 Manager 的 `start_background()` 一次；普通消息和 slash command 都不再决定 MCP 启动；CLI 在打印输入提示前调用 `runtime.start()`；`close()` 始终关闭 Manager。
- [x] 运行 `pytest tests/test_runtime_mcp_lifecycle.py tests/test_cli.py -q`，确认新生命周期 API 缺失而失败。
- [x] 最小实现：在 Runtime 现有启动锁内调用 `mcp.start_background()` 并立即返回；从 `run_turn()` 删除首条消息启动逻辑；CLI 建立 Runtime、注册 Channel 后先调用 `await runtime.start()`。Web、微信、飞书目前没有 Host 代码，后续 Host 仅调用同一组 `start()` / `close()`，不在 ChannelAdapter 内管理 MCP。
- [x] 再次运行上述测试，确认通过。
- [x] 仅暂存生产代码并提交：`git add Turning-Good-Agent/runtime/runtime.py Turning-Good-Agent/cli.py && git commit -m "feat: start mcp workers with runtime lifecycle"`。

### Task 5：文档与完整验证

**Files:**
- Modify: `README.md`
- Modify: `docs/PROJECT_ARCHITECTURE.md`
- Modify: `docs/TURNING_GOOD_AGENT_SPEC.md`
- Modify: `docs/phases/2026-06-15-phase-4-mcp-client.md`

- [x] 同步 Runtime 后台启动、每 Server Worker、连接级退避重试、延迟 Tool 注册和内存状态边界；不得声称已实现 Web、微信或飞书 Host。
- [x] 运行：`pytest -q`、`git diff --check`、`printf '/exit\n' | python -m Turning-Good-Agent chat`、`git status --short`。
- [x] 确认测试通过、CLI 正常退出、暂存区不含 `tests/`、`settings.local.json`、`.sessions/` 或忽略文档。
- [x] 仅暂存受跟踪文档和生产代码，不使用 `git add -f docs/`，并提交：`git commit -m "feat: run mcp servers in background"`。

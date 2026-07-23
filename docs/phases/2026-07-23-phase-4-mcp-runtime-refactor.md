# Turning-Good-Agent Phase 4 MCP Approval And Runtime Refactor Plan

状态：已完成实现。

> **For agentic workers:** 已按 Task 1 至 Task 5 实现并完成验证；本文件保留为实施记录。

**Goal:** 收口 MCP 审批为“默认逐次审批，只有会话 `/approve on` 全局跳过”，并在不改变既有用户可见功能的前提下，降低 Runtime、状态机和 AgentLoop 的职责耦合。

**Architecture:** `AgentRuntime` 保持生命周期、会话锁和状态机驱动；`state.py` 只保留状态处理与转移；上下文 token 预算、压缩计划、工具批次执行和本轮上下文附件各自归属单一模块。MCP 只生产 Tool 与通用 Attachment，不再反向参与审批策略。

**Tech Stack:** Python 3.11+、asyncio、官方 MCP Python SDK、现有 ToolRegistry、HookManager、OpenAI-compatible LLM、pytest。

## 完成记录

本轮收口完成于 2026-07-23，保留以下提交作为实现证据：

- `05bd0d3 refactor: simplify mcp approval policy`
- `5d3059f refactor: centralize context token budget`
- `60aecdc refactor: move compaction planning to memory`
- `7b6efde refactor: isolate tool call execution`
- `5b99cba docs: clarify mcp runtime lifecycle`

完成后的行为如下：

- MCP Server 配置出现 `auto_approve_tools` 会报错；只有当前会话 `/approve on` 跳过人工确认。
- `ContextBuilder` 不再写入工具 schema system message；`context/token_budget.py` 统一计算 BUILD 拒绝判断和 SAVE 观测。
- `ShortTermMemory.plan_compaction()` 生成压缩源、recent window 与 COMPACT 监控统计；`state.py` 只协调状态流程。
- `ToolCallRunner` 接管参数规范化、审批、受限并发、双重安全检查和工具结果 Hook；`AgentLoop` 只维护 LLM 循环与当前轮 working messages。
- 任意 ToolResult 都可携带 `ContextAttachment`，但附件不落入 `messages.jsonl`、summary、长期记忆或下一轮 Context。
- Runtime 仅在首个普通输入前启动 MCP；CLI `/exit` 和 EOF 都经 `finally` 调用最终 `close()`，关闭后的 Runtime 不承诺可重启。

最终验证：`pytest -q` 为 `175 passed`，`git diff --check` 通过，`printf '/exit\n' | python -m Turning-Good-Agent chat` 正常退出。

## 全局约束

- 所有新增函数都添加精简中文注释；保持现有命名、异步模式和代码风格。
- 不使用 `uv`；不为测试建立无业务价值的 mock 抽象。
- `tests/` 只保留本地验证，绝不 `git add`、提交或上传；README、Spec 与架构文档需要与代码同步。
- 不修改 MCP transport、Catalog、Resource/Prompt 当前轮附件、显式 `enabled_tools`、MCP Server 生命周期或现有内置工具安全检查的既有语义。
- 每个任务先写失败测试，确认失败后做最小实现；每个任务结束执行相关测试并仅提交受跟踪源代码。
- 提交与推送由用户明确要求决定。

## 最终行为契约

### MCP 审批

1. 所有包装后的远端 MCP Tool `mcp_<server>_<tool>` 默认审批。
2. `attach_mcp_resource`、`apply_mcp_prompt` 默认审批，因为它们会读取远端内容并影响本轮模型上下文。
3. `search_mcp_capabilities` 不审批：它只查询本地内存 Catalog，不请求远端、不读取内容、不注入上下文。
4. Session `auto_approve_tools=true`（由 `/approve on` 设置）是唯一的 MCP 自动放行条件；`/approve off` 恢复默认审批。
5. 完全删除 MCP Server 的 `auto_approve_tools` 配置、解析、示例、文档和运行时策略。
6. `readOnlyHint`、`destructiveHint` 等 MCP annotations 只保存在 Tool metadata，供未来 Channel/Web 显示，永不参与审批决策。

### Context 与 Tool schema

1. `ContextBuilder` 仅构造 system prompt、长期记忆、摘要、未压缩历史和当前用户输入。
2. Tool schema 只通过 OpenAI-compatible 请求的 `tools` 参数传给 LLM，不再复制为 `system` message 的“可用工具”文本。
3. Context token 预算仍然计算 Tool schema，但只计算一次实际发送的 `openai_tools` 序列化内容。

### Runtime 与 AgentLoop

1. `AgentRuntime` 负责 Runtime 创建、MCP 启停、会话锁、状态调度、trace、Channel 完成/错误回调；不承载业务策略。
2. `state.py` 只保存状态处理函数、状态转换、trace metadata 与轻量状态编排；不直接实现复杂 token 预算和压缩规划算法。
3. `AgentLoop` 只负责模型调用循环、working messages、Tool result message、合法 Attachment 的本轮注入和工具轮数上限收口。
4. `ToolCallRunner` 负责一批 LLM ToolCall 的参数规范化、审批 Hook、并行调度、执行、结果 Hook 和结果记录。
5. `ContextAttachment` 是 ToolResult 可携带的通用本轮上下文对象。MCP 是当前生产者，但 Attachment 类型不属于 `mcp/`；合法附件不落入 `messages.jsonl`、summary、长期记忆或下一轮 Context。

## 目标文件边界

| 文件 | 最终职责 |
| --- | --- |
| `config/settings.py` | 仅加载 MCP Server 连接、超时和 `enabled_tools`；不含 MCP 自动审批配置。 |
| `hooks/tool_permission.py` | 依据 Session 全局开关和 Tool 的 `approval_required` 做审批，不依赖 `McpManager`。 |
| `mcp/manager.py` | Server 生命周期、Catalog、Tool 注册、调用、Resource/Prompt Attachment；不包含审批策略。 |
| `tools/context_attachment.py` | 定义 `ContextAttachment` 与其格式/token 预算校验。 |
| `tools/executor.py` | 执行已准备 Tool，并在执行前再次运行硬安全检查。 |
| `runtime/tool_call_runner.py` | 完成一批 ToolCall 的准备、审批、并发、执行与 Hook。 |
| `runtime/agent_loop.py` | 完成 LLM - ToolCallRunner - working messages 的循环。 |
| `context/token_budget.py` | 计算 BUILD 拒绝判断与 SAVE 观测使用的唯一 token 分解。 |
| `memory/short_term.py` | 给出压缩计划和执行 LLM 摘要；不依赖 Runtime。 |
| `runtime/state.py` | 七个状态处理、轻量编排、trace metadata 与保存状态结果。 |
| `runtime/turn_context.py` | 仅保存跨状态真正需要的临时数据。 |
| `runtime/runtime.py` | 运行时驱动与 MCP 资源生命周期。 |

## Task 1：收口 MCP 审批策略

**Files:**
- Modify: `Turning-Good-Agent/config/settings.py`
- Modify: `Turning-Good-Agent/mcp/manager.py`
- Modify: `Turning-Good-Agent/hooks/tool_permission.py`
- Modify: `Turning-Good-Agent/runtime/runtime.py`
- Modify: `settings.example.json`
- Modify: `README.md`
- Test: `tests/test_mcp_settings.py`
- Test: `tests/test_mcp_permissions.py`
- Test: `tests/test_simple_hooks.py`

**Interfaces:**

```python
class ToolPermissionHook(AgentHook):
    def __init__(self, approval_required_tools: frozenset[str], tools: ToolRegistry | None = None) -> None: ...
```

`McpManager.requires_approval()` 必须删除。`McpServerSettings` 不再暴露 `auto_approve_tools`。

- [x] **Step 1：写失败测试**
  - `McpServerSettings` 不接受或不保留 `auto_approve_tools`。
  - MCP 配置含有 `auto_approve_tools` 时，`Settings.load()` 明确抛出 `ValueError`，避免用户误以为该配置生效。
  - 远端 `mcp_demo_lookup`、`attach_mcp_resource`、`apply_mcp_prompt` 在 `/approve off` 时请求审批；Session 自动审批时全部直接通过。
  - 远端 Tool 即使 metadata 包含 `readOnlyHint=true`，仍要求审批。
  - `search_mcp_capabilities` 不请求审批。

- [x] **Step 2：运行失败测试**

  ```bash
  pytest tests/test_mcp_settings.py tests/test_mcp_permissions.py tests/test_simple_hooks.py -q
  ```

  Expected: 新断言在旧的 `auto_approve_tools` 和 Manager 审批分支上失败。

- [x] **Step 3：实现最小策略收口**
  - 从 `McpServerSettings`、`_load_mcp_server()` 和示例 JSON 删除 `auto_approve_tools`。
  - 如果本地 JSON 出现该键，在 `_load_mcp_server()` 中抛出明确错误：`mcp.servers.<name>.auto_approve_tools 已不支持，请使用 /approve on。`。
  - 删除 `McpManager._tool_servers` 中只为审批服务的部分及 `requires_approval()`；保留 Tool 注册、刷新和调用职责。
  - `ToolPermissionHook` 仅依赖 Session `auto_approve_tools`、内置审批名集合和已注册 Tool 的 `approval_required` 属性。移除 `mcp_manager` 构造参数与 MCP 名称分支。
  - `AgentRuntime.create_default()` 按新构造函数注册 Hook。

- [x] **Step 4：运行测试并提交**

  ```bash
  pytest tests/test_mcp_settings.py tests/test_mcp_permissions.py tests/test_simple_hooks.py -q
  git diff --check
  git add Turning-Good-Agent/config/settings.py Turning-Good-Agent/mcp/manager.py Turning-Good-Agent/hooks/tool_permission.py Turning-Good-Agent/runtime/runtime.py settings.example.json README.md
  git commit -m "refactor: simplify mcp approval policy"
  ```

## Task 2：消除重复 Context 与 TurnContext 历史字段

**Files:**
- Create: `Turning-Good-Agent/context/token_budget.py`
- Modify: `Turning-Good-Agent/context/builder.py`
- Modify: `Turning-Good-Agent/runtime/state.py`
- Modify: `Turning-Good-Agent/runtime/turn_context.py`
- Test: `tests/test_context_builder.py`
- Test: `tests/test_runtime_flow.py`

**Interfaces:**

```python
def build_context_token_breakdown(
    *, summary: str, history: list[MessageRecord], current_input: str,
    output: str, profile_memory: str, openai_tools: list[dict[str, object]],
    include_current_turn: bool,
) -> dict[str, int]: ...
```

该函数是 BUILD 上下文上限判断和 SAVE 观测的唯一 token 分解来源。它必须只计算一次 `openai_tools`，并保持既有观测字段名。

- [x] **Step 1：写失败测试**
  - ContextBuilder 输出不包含 `可用工具：` system message。
  - 对同一工具集合，`tool_schema_tokens` 只等于一次 OpenAI tool schema 序列化 token 数。
  - BUILD 的上限判断和 SAVE 的 `current_context_tokens` 均使用同一计算函数。
  - `TurnContext` 不再具有 `full_history`、`history` 字段，压缩前临时历史仍从 `uncompacted_history` 构建。

- [x] **Step 2：运行失败测试**

  ```bash
  pytest tests/test_context_builder.py tests/test_runtime_flow.py -q
  ```

  Expected: 旧实现仍注入 Tool schema 文本且 `TurnContext` 仍有冗余字段。

- [x] **Step 3：实现唯一预算入口**
  - 从 `ContextBuilder.build()` 删除 `tool_schemas` 参数及对应 system message。
  - 在 `context/token_budget.py` 提取 `context_token_count()`、`context_token_breakdown()` 与 SAVE 相关重复计算；函数参数全部显式，不接受 `AgentRuntime`。
  - BUILD 传入当前 `summary + uncompacted_history + user input + profile memory + openai_tools` 做拒绝判断。
  - SAVE 使用相同函数、最终 summary 与最终未压缩历史生成保持原字段语义的观测字典。
  - 删除 `TurnContext.full_history` 与 `TurnContext.history`；`build_virtual_history()` 直接基于 `ctx.uncompacted_history`。

- [x] **Step 4：运行测试并提交**

  ```bash
  pytest tests/test_context_builder.py tests/test_runtime_flow.py -q
  git diff --check
  git add Turning-Good-Agent/context/token_budget.py Turning-Good-Agent/context/builder.py Turning-Good-Agent/runtime/state.py Turning-Good-Agent/runtime/turn_context.py
  git commit -m "refactor: centralize context token budget"
  ```

## Task 3：将压缩计划归还短期记忆模块

**Files:**
- Modify: `Turning-Good-Agent/memory/short_term.py`
- Modify: `Turning-Good-Agent/runtime/state.py`
- Test: `tests/test_compaction.py`

**Interfaces:**

```python
@dataclass(slots=True)
class CompactionPlan:
    should_compact: bool
    compact_source: list[MessageRecord]
    recent_window: list[MessageRecord]
    compacted_message_count: int
    compacted_token_count: int
    raw_window_message_count: int
    raw_window_token_count: int

def plan_compaction(self, messages: list[MessageRecord], force: bool = False) -> CompactionPlan: ...
```

`force=True` 表示外层已经判断总 Context 超过 `max_context_tokens`；仍由 `ShortTermMemory` 负责计算完整轮次的 recent window 与统计值。

- [x] **Step 1：写失败测试**
  - 未达到阈值且 `force=False` 时，计划不压缩，recent window 为完整历史，统计值与完整历史一致。
  - 达到阈值或 `force=True` 时，计划仅压缩 recent window 之外的完整 user/assistant 对话。
  - recent window 中不包含不完整的单条 user 或 assistant 消息。
  - COMPACT 状态仍保持现有摘要调用、Hook 顺序、token usage 累计和 trace 字段。

- [x] **Step 2：运行失败测试**

  ```bash
  pytest tests/test_compaction.py -q
  ```

  Expected: `CompactionPlan` 与 `plan_compaction()` 尚不存在。

- [x] **Step 3：实现压缩计划**
  - 在 `memory/short_term.py` 定义 `CompactionPlan`，将 recent window、压缩源和统计计算迁入 `plan_compaction()`。
- `state.compact()` 只负责：建立本轮虚拟历史、判断是否因总 Context 强制压缩、取得计划、调用 before/after compact Hook、调用摘要 LLM、把结果写回 Session、累计 usage。
  - 删除 `state.py` 中重复的 `build_compaction_stats()`，但保留 trace 所需的 `ctx.compact_stats` 数据来源。

- [x] **Step 4：运行测试并提交**

  ```bash
  pytest tests/test_compaction.py tests/test_runtime_flow.py -q
  git diff --check
  git add Turning-Good-Agent/memory/short_term.py Turning-Good-Agent/runtime/state.py
  git commit -m "refactor: move compaction planning to memory"
  ```

## Task 4：提取 ToolCallRunner 与通用 ContextAttachment

**Files:**
- Create: `Turning-Good-Agent/tools/context_attachment.py`
- Create: `Turning-Good-Agent/runtime/tool_call_runner.py`
- Modify: `Turning-Good-Agent/tools/base.py`
- Modify: `Turning-Good-Agent/tools/executor.py`
- Modify: `Turning-Good-Agent/mcp/types.py`
- Modify: `Turning-Good-Agent/mcp/manager.py`
- Modify: `Turning-Good-Agent/mcp/control_tools.py`
- Modify: `Turning-Good-Agent/runtime/agent_loop.py`
- Test: `tests/test_tools_loop.py`
- Test: `tests/test_mcp_attachments.py`
- Test: `tests/test_mcp_control_tools.py`

**Interfaces:**

```python
@dataclass(slots=True)
class ContextAttachment:
    source: str
    messages: list[dict[str, object]]
    token_count: int

def validate_context_attachment(
    attachment: ContextAttachment | object | None,
    used_tokens: int,
    token_limit: int,
) -> str | None: ...

class ToolCallRunner:
    async def execute_calls(
        self, calls: list[ToolCall], channel_adapter: ChannelAdapter,
        auto_approve_tools: bool,
    ) -> list[dict[str, object]]: ...
```

`ToolCallRunner` 持有 ToolRegistry、ToolExecutor、HookManager 和并行限制。它在审批前完成参数规范化及第一次安全检查，在真正执行前由 `ToolExecutor` 再次运行硬安全检查；不得让 `/approve on` 绕过任一安全检查。

- [x] **Step 1：写失败测试**
  - `ContextAttachment` 可由非 MCP ToolResult 承载；格式错误、非 user/assistant role、token 计数不一致、超过总预算时被拒绝。
  - MCP Resource/Prompt Attachment 继续仅在当前 AgentLoop working messages 可见，工具调用落盘记录中不包含 Attachment 对象。
  - 连续 `parallel_safe=true` Tool 仍受并发上限约束、保留模型原始顺序；审批和副作用 Tool 保持串行。
  - `ToolCallRunner` 在审批前展示规范化参数；执行前后的 Hook 顺序、Channel 状态和 ToolResultTruncationHook 语义不变。
  - 硬安全检查在审批前和实际执行前均生效。

- [x] **Step 2：运行失败测试**

  ```bash
  pytest tests/test_tools_loop.py tests/test_mcp_attachments.py tests/test_mcp_control_tools.py -q
  ```

  Expected: 新类型和 `ToolCallRunner` 尚不存在，AgentLoop 仍含工具批次执行实现。

- [x] **Step 3：实现清晰的工具调用边界**
  - 创建 `tools/context_attachment.py`，定义通用 Attachment 与纯校验函数；错误文案使用“本轮上下文附件”，不出现 MCP 专属逻辑。
  - `ToolResult.context_attachment` 使用 `ContextAttachment | None` 类型；从 `mcp/types.py` 删除 `McpContextAttachment`。
  - `McpManager.attach_resource()`、`apply_prompt()` 返回 `ContextAttachment`；MCP 控制 Tool 不改变用户可见文本。
  - `ToolExecutor` 接收已准备的 Tool 与规范化参数执行；实际调用前再次进行 `security.precheck`，并产生现有记录字段。
  - 创建 `ToolCallRunner`，迁入 AgentLoop 的 `_execute_tool_calls()`、并发批处理、参数校验、第一次安全检查、审批 Hook、Channel started、after-tool Hook 和错误记录构建。
  - AgentLoop 使用 `ToolCallRunner.execute_calls()`，只追加 Tool result message，并调用 `validate_context_attachment()` 后追加合法附件。保留 LLM 调用、流式输出、工具轮数上限 FINALIZE 和最终 fallback。

- [x] **Step 4：运行测试并提交**

  ```bash
  pytest tests/test_tools_loop.py tests/test_mcp_attachments.py tests/test_mcp_control_tools.py -q
  git diff --check
  git add Turning-Good-Agent/tools/context_attachment.py Turning-Good-Agent/runtime/tool_call_runner.py Turning-Good-Agent/tools/base.py Turning-Good-Agent/tools/executor.py Turning-Good-Agent/mcp/types.py Turning-Good-Agent/mcp/manager.py Turning-Good-Agent/mcp/control_tools.py Turning-Good-Agent/runtime/agent_loop.py
  git commit -m "refactor: isolate tool call execution"
  ```

## Task 5：Runtime 生命周期收口与文档同步

**Files:**
- Modify: `Turning-Good-Agent/runtime/runtime.py`
- Modify: `Turning-Good-Agent/cli.py`
- Modify: `README.md`
- Modify: `docs/PROJECT_ARCHITECTURE.md`
- Modify: `docs/TURNING_GOOD_AGENT_SPEC.md`
- Modify: `docs/phases/2026-06-15-phase-4-mcp-client.md`
- Test: `tests/test_runtime_mcp_lifecycle.py`
- Test: `tests/test_cli.py`
- Test: `tests/test_runtime_flow.py`

**Acceptance:** Runtime Host 启动时后台启动 MCP Worker，不等待 Server 连接；CLI 的 `/exit` 和 EOF 都会调用 `close()`；`close()` 保持最终销毁语义，不承诺同一 Runtime 可重启。每个 Worker 必须在创建 transport 的同一 Task 中调用 Client `close()`。

- [x] **Step 1：写失败测试**
  - `runtime.start()` 只启动一次后台 MCP Worker；普通消息和 slash command 不再决定 MCP 生命周期。
  - CLI `/exit` 与 EOF 均调用 Runtime `close()`。
  - 已启动的多个 MCP Client 在 Runtime `close()` 时都关闭；未启动时关闭不报错。
  - README、架构和 Spec 移除 Server `auto_approve_tools`，说明 `/approve on` 是唯一自动放行开关，并说明 Tool schema 不再作为 system message 重复注入。

- [x] **Step 2：运行失败测试**

  ```bash
  pytest tests/test_runtime_mcp_lifecycle.py tests/test_cli.py tests/test_runtime_flow.py -q
  ```

  Expected: 至少一个测试因旧审批文案、旧 Runtime 接线或旧 Context 描述失败。

- [x] **Step 3：实现最小生命周期收口并同步文档**
- 保持 `AgentRuntime.close()` 只做最终资源释放，不将其设计成可重启操作。
- 不为 MCP 增加新的 Runtime 状态；仍由 CLI 的 `finally` 和未来 Web 应用 shutdown 调用 `close()`。
- 每个启用 Server 使用独立 Worker 后台连接；连接级错误按配置退避重试，权限、参数和 Tool 业务错误不触发重连。
  - 同步所有受影响文档的当前行为与架构边界。文档忽略规则不变，不强制提交。

- [x] **Step 4：完整验证与提交**

  ```bash
  pytest -q
  git diff --check
  printf '/exit\n' | python -m Turning-Good-Agent chat
  git status --short
  git add Turning-Good-Agent/runtime/runtime.py Turning-Good-Agent/cli.py README.md
  git commit -m "docs: clarify mcp runtime lifecycle"
  ```

实现阶段的源代码提交不包含 `tests/`、`settings.local.json`、`.sessions/` 或无关文件；文档最终同步由用户明确要求时单独提交。

## 完成标准

- MCP 自动审批只由会话 `/approve on` 决定；Server 配置不能绕过审批。
- MCP annotations 不改变审批结果。
- Tool schema 不重复进入模型 messages 与 API tools 参数。
- `TurnContext` 不再保存 `full_history`、`history` 两份冗余历史。
- `state.py` 不再实现 token 预算和压缩计划细节。
- `AgentLoop` 不再实现 Tool 批次调度、审批、预检和 Hook 编排。
- Context Attachment 是通用 Tool 结果能力，MCP 内容仍只对当前 AgentLoop 可见。
- `pytest -q`、`git diff --check`、CLI `/exit` 冒烟全部通过。

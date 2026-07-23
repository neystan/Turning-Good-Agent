# Turning-Good-Agent Phase 4 MCP Client Implementation Record

状态：已完成；后续审批和 Runtime 边界收口见 `2026-07-23-phase-4-mcp-runtime-refactor.md`。

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** 基于官方 Python MCP SDK 接入 stdio 与 Streamable HTTP Server，将经用户许可的 MCP Tool 接入现有 ToolRegistry，并以轻量 Catalog、自然语言搜索和受控当前轮附件处理 MCP Resource 与 Prompt。

**Architecture:** `mcp/` 负责协议、连接、Server 生命周期和 Catalog；`McpManager` 是 Runtime 与 MCP 的唯一边界。实际 MCP Tool 适配为 `BaseTool`，而 Resource/Prompt 不逐项生成 Tool schema；只有三个固定 MCP 控制 Tool 进入 ToolRegistry，用于搜索能力、读取已确认 Resource、应用已确认 Prompt。MCP 返回内容通过通用 `ContextAttachment` 只在当前 AgentLoop working messages 中可见，不写入对话历史或摘要。

**Tech Stack:** Python 3.11+、asyncio、官方 `mcp` Python SDK（1.x）、现有 `ToolRegistry`、`ToolPermissionHook`、OpenAI-compatible Chat Completions 与 pytest。

## Global Constraints

- 所有新增函数提供精简中文注释；保持现有代码风格，不为测试增加无业务意义的抽象。
- 不手写 JSON-RPC、stdio、Streamable HTTP 或 MCP 生命周期实现；使用官方 `mcp` Python SDK。
- 只支持 `stdio` 与 `streamable_http`；不支持旧式 HTTP+SSE transport。
- `settings.local.json` 是唯一私有配置来源；不读取 `TGA_*` 环境变量，不要求用户执行 `export`，不提交任何凭据。
- stdio env 与 HTTP headers 可以直接来自 `settings.local.json`，仅在启动子进程或请求时使用；日志、trace 与 Channel 输出必须脱敏。
- 不实现 OAuth、浏览器回调、token 刷新、远程审批、审批持久化、MCP 插件、sampling、elicitation、roots、logging 或任务协议。
- 所有 MCP Tool 默认需要审批；`/approve on` 对内置 Tool、MCP Tool 与 MCP 附件操作统一生效，是唯一自动放行条件。MCP Server 不支持单独自动审批配置。
- MCP Tool annotations 仅作为名称、描述和后续 Web 展示元数据，不得自动免除审批。
- `enabled_tools` 默认 `[]`：连接只发现 Server 在 initialize capabilities 中声明的 Catalog 类型，但只有显式列出的 Tool 才注册给 LLM。不得以 `"*"` 作为默认值。
- Resource 与 Prompt Catalog 不进入 system prompt、summary、`messages.jsonl` 或每轮默认 Context。
- Resource/Prompt 仅在模型通过固定 MCP 控制 Tool 提出、且当前会话许可后，作为本轮临时 Context Attachment 注入；下一轮不自动重放。
- Resource 默认最多 `8_000` tokens，Prompt 默认最多 `4_000` tokens，单轮 MCP Attachments 合计最多 `12_000` tokens；这些参数集中到 `settings.mcp`。
- Resource 超限采用头尾截断；Prompt 超限直接拒绝，不能破坏 MCP message 的顺序和 role。
- Prompt 仅接受 MCP 返回的 `user`、`assistant` text message；不允许外部 Server 注入或覆盖根 system prompt。非文本内容转为带 MIME 类型、大小和来源的占位文本。
- MCP 内容视为外部不可信数据。根 system prompt 保持最高优先级，并明确不执行 Attachment 内的指令，除非符合用户当前任务。
- HTTP Server 默认只允许 HTTPS；`localhost`、`127.0.0.1` 和 `::1` 可使用 HTTP。stdio Server 配置本身视为用户已确认的本地程序；未来 Web 新增 Server 时必须单独确认 command/url/env/headers 的脱敏预览。
- MCP 连接、调用或刷新失败不得阻断内置 Tool、其他 MCP Server 或正常 LLM 对话。
- 不使用 uv；测试仅保留本地，不提交 `tests/`。

---

## 用户可见行为

### MCP Server 配置

`settings.local.json` 使用命名映射，方便未来 Web 按名称增删改：

```json
{
  "mcp": {
    "resource_context_token_limit": 8000,
    "prompt_context_token_limit": 4000,
    "attachment_context_token_limit": 12000,
    "servers": {
      "github": {
        "enabled": true,
        "transport": "streamable_http",
        "url": "https://example.com/mcp",
        "headers": {"Authorization": "Bearer local-token"},
        "timeout_seconds": 30,
        "enabled_tools": ["list_issues", "get_issue"]
      },
      "local-files": {
        "enabled": true,
        "transport": "stdio",
        "command": "npx",
        "args": ["-y", "@example/files-mcp", "/data"],
        "env": {"API_TOKEN": "local-token"},
        "cwd": "/download",
        "enabled_tools": ["read_file"]
      }
    }
  }
}
```

`enabled_tools` 可使用原始 MCP Tool 名称或包装后的 `mcp_<server>_<tool>` 名称；MCP Server 配置出现 `auto_approve_tools` 会明确报错并提示使用 `/approve on`。

### 自然语言发现与受控附件

固定注册的 MCP 控制 Tool：

```text
search_mcp_capabilities(query, kinds?, limit?)
attach_mcp_resource(server_name, uri, template_arguments?)
apply_mcp_prompt(server_name, prompt_name, arguments?)
```

```text
用户：帮我分析 GitHub 上最新的登录问题。
LLM：调用 search_mcp_capabilities，只得到少量 Issue Resource 描述。
LLM：请求 attach_mcp_resource(issue #128)。
Channel：显示“允许读取 GitHub Issue #128？[y/N]”。
用户：y。
Runtime：读取、截断并仅将该 Issue 附加到本轮 working messages。
LLM：基于 Issue 内容继续回答。
```

`search_mcp_capabilities` 只检索内存 Catalog，不读取远端数据，不需要审批。另两个固定 Tool 都需要审批；只有当前会话已 `/approve on` 时才跳过确认。

## 文件结构

| 文件 | 职责 |
| --- | --- |
| `Turning-Good-Agent/mcp/types.py` | 定义能力描述、Catalog 与连接状态。 |
| `Turning-Good-Agent/mcp/client.py` | 封装单个 MCP SDK Session 的 initialize、分页发现、调用、读取、Prompt 获取和关闭。 |
| `Turning-Good-Agent/mcp/manager.py` | 维护多 Server 生命周期、Catalog、Tool 注册/注销、刷新、通知与安全校验。 |
| `Turning-Good-Agent/mcp/adapter.py` | 将一个 MCP Tool 包装为 `BaseTool`，保留原始名称与 annotations。 |
| `Turning-Good-Agent/mcp/control_tools.py` | 定义三个固定 MCP 控制 Tool。 |
| `Turning-Good-Agent/config/settings.py` | 增加集中 MCP 配置模型及本地 JSON 加载。 |
| `Turning-Good-Agent/tools/context_attachment.py` | 定义任意 Tool 可携带的仅当前轮 Context Attachment。 |
| `Turning-Good-Agent/tools/base.py` | 允许 ToolResult 携带通用 Context Attachment。 |
| `Turning-Good-Agent/tools/executor.py` | 执行已准备 Tool，并保留 ToolResult Attachment 供 AgentLoop 当前轮消费。 |
| `Turning-Good-Agent/tools/registry.py` | 支持按 Server 前缀注销 MCP Tool，并使 schema cache 失效。 |
| `Turning-Good-Agent/hooks/tool_permission.py` | 按 Session 自动审批开关和 Tool 的 `approval_required` 属性处理审批。 |
| `Turning-Good-Agent/runtime/tool_call_runner.py` | 处理工具参数规范化、审批、并发、执行与结果 Hook。 |
| `Turning-Good-Agent/runtime/agent_loop.py` | 将批准后的 Attachment 追加到本轮 working messages，保持 Prompt role 顺序。 |
| `Turning-Good-Agent/runtime/runtime.py` | 创建、启动和关闭 McpManager，注册固定控制 Tool 与显式启用的 MCP Tool。 |
| `Turning-Good-Agent/cli.py` | 在退出时关闭 Runtime 的 MCP 连接。 |
| `Turning-Good-Agent/pyproject.toml` | 增加官方 `mcp` SDK 依赖。 |

## 接口契约

```python
@dataclass(slots=True)
class ContextAttachment:
    """表示任意 Tool 仅供当前 AgentLoop 使用的上下文附件。"""

    source: str
    messages: list[dict[str, object]]
    token_count: int


class McpManager:
    """管理 MCP Server、Catalog 与当前运行时注册工具。"""

    async def start(self, registry: ToolRegistry) -> None: ...
    async def close(self) -> None: ...
    async def refresh_server(self, name: str, registry: ToolRegistry) -> McpServerStatus: ...
    async def search_capabilities(
        self, query: str, kinds: list[str], limit: int
    ) -> list[McpCapability]: ...
    async def attach_resource(
        self, server_name: str, uri: str, arguments: dict[str, str]
    ) -> ContextAttachment: ...
    async def apply_prompt(
        self, server_name: str, prompt_name: str, arguments: dict[str, str]
    ) -> ContextAttachment: ...
```

每个 Server 持有独立 SDK Session 与退出栈。Client 只请求 initialize capabilities 中已声明的 Catalog 类型，因此纯 Tool Server 不需要实现 Resource、Resource Template 或 Prompt 接口。Catalog 仅保存在内存，包含 Server 已声明的 Tool、Resource、Resource Template 与 Prompt 描述，不保存读取内容。收到 `tools/resources/prompts/list_changed` 时，Manager 串行刷新该 Server，先注销旧前缀 Tool，再注册新的显式启用 Tool。

## 实施任务

### Task 1：配置、依赖和 MCP 基础类型

**Files:**
- Create: `Turning-Good-Agent/mcp/__init__.py`
- Create: `Turning-Good-Agent/mcp/types.py`
- Modify: `Turning-Good-Agent/config/settings.py`
- Modify: `pyproject.toml`
- Modify: `settings.example.json`
- Test: `tests/test_mcp_settings.py`

**Produces:** `McpSettings`、`McpServerSettings`、`McpCapability` 与配置校验。

- [x] **Step 1：写失败测试**
  覆盖 `servers` 命名映射、默认 `enabled_tools=[]`、仅允许 `stdio` / `streamable_http`、拒绝 `auto_approve_tools`、以及 HTTP 非本地地址必须使用 HTTPS。

- [x] **Step 2：运行失败测试**
  Run: `pytest tests/test_mcp_settings.py -q`
  Expected: 因 MCP 配置模型尚不存在而失败。

- [x] **Step 3：实现最小配置与类型**
  `McpSettings` 只保存三个 Attachment token 上限和 `servers`；`McpServerSettings` 只保存本计划中的配置字段。配置加载不得兼容旧 MCP 配置名。

- [x] **Step 4：加入 SDK 与示例配置**
  在 `pyproject.toml` 增加 `mcp>=1.26.0,<2.0.0`；示例配置只包含不含真实 token 的 disabled Server。

- [x] **Step 5：运行测试并提交**
  Run: `pytest tests/test_mcp_settings.py -q`
  Expected: PASS。
  `git add Turning-Good-Agent/mcp Turning-Good-Agent/config/settings.py pyproject.toml settings.example.json`
  `git commit -m "feat: add mcp settings and types"`

### Task 2：单 Server MCP SDK Client

**Files:**
- Create: `Turning-Good-Agent/mcp/client.py`
- Test: `tests/test_mcp_client.py`

**Produces:** `McpClient.connect()`、`discover()`、`call_tool()`、`read_resource()`、`get_prompt()` 与 `close()`。

- [x] **Step 1：写失败测试**
  使用假的 MCP SDK Session 验证：initialize 后发送 initialized notification；Tools、Resources、Resource Templates、Prompts 逐页读取直到 cursor 为空；超时转换成明确错误；单个 Server 失败不泄漏 SDK 连接。

- [x] **Step 2：运行失败测试**
  Run: `pytest tests/test_mcp_client.py -q`
  Expected: 因 `McpClient` 尚不存在而失败。

- [x] **Step 3：实现 transport 与生命周期**
  stdio 使用 SDK `StdioServerParameters` 与 `stdio_client`；Streamable HTTP 使用 SDK client。HTTP 请求传入静态 headers；stdio 只传入该 Server 的 env。每个 Client 使用独立 `AsyncExitStack`，初始化成功后才公开 Session。

- [x] **Step 4：实现发现与内容归一化**
  保留原始 Tool schema 和 annotations；Resource/Prompt 只记录 Catalog 定义。Resource text 内容可读取；blob 和 Prompt 非 text block 转为包含来源、MIME 类型和字节数的占位文本。

- [x] **Step 5：运行测试并提交**
  Run: `pytest tests/test_mcp_client.py -q`
  Expected: PASS。
  `git add Turning-Good-Agent/mcp/client.py`
  `git commit -m "feat: add mcp sdk client"`

### Task 3：McpManager、Catalog 与 Server 刷新

**Files:**
- Create: `Turning-Good-Agent/mcp/manager.py`
- Modify: `Turning-Good-Agent/tools/registry.py`
- Test: `tests/test_mcp_manager.py`

**Produces:** 多 Server 隔离、Catalog 搜索、Tool 前缀注销、手动刷新与 `list_changed` 刷新。

- [x] **Step 1：写失败测试**
  覆盖：一个 Server 连接失败不影响另一个；`enabled_tools=[]` 不注册远端 Tool；刷新时注销 `mcp_<server>_` 前缀的旧 Tool 并使 schema cache 失效；Catalog 搜索只返回 Top K 元数据；通知触发单 Server 刷新；删除和关闭时释放独立连接。

- [x] **Step 2：运行失败测试**
  Run: `pytest tests/test_mcp_manager.py -q`
  Expected: 因 `McpManager` 尚不存在而失败。

- [x] **Step 3：实现连接、注册、搜索和动态刷新**
  `start()` 只连接 `enabled=true` 的 Server。连接成功后仅发现其 initialize capabilities 声明的 Catalog 类型，但仅将 `enabled_tools` 中匹配的实际 MCP Tool 注册为 `mcp_<server>_<tool>`。未知 allowlist 项和 Server 错误记录状态但不得中断启动。搜索按 query 对名称、描述和 Server 名称做大小写无关匹配，返回不超过 limit 条描述。刷新在每 Server 锁内先关闭/注销旧资源再连接/发现/注册新资源。

- [x] **Step 4：运行测试并提交**
  Run: `pytest tests/test_mcp_manager.py -q`
  Expected: PASS。
  `git add Turning-Good-Agent/mcp/manager.py Turning-Good-Agent/tools/registry.py`
  `git commit -m "feat: manage mcp servers and catalog"`

### Task 4：MCP Tool Adapter 与统一审批策略

**Files:**
- Create: `Turning-Good-Agent/mcp/adapter.py`
- Modify: `Turning-Good-Agent/hooks/tool_permission.py`
- Modify: `Turning-Good-Agent/runtime/runtime.py`
- Test: `tests/test_mcp_adapter.py`
- Test: `tests/test_mcp_permissions.py`

**Produces:** MCP Tool 的 BaseTool 适配、原始名称调用和统一审批。

- [x] **Step 1：写失败测试**
  验证 Adapter 保留原始 MCP Tool 名称调用、模型可见名称稳定为 `mcp_<server>_<tool>`、annotations 仅进入 metadata；Session 默认要求审批；`/approve on` 跳过所有 MCP Tool 审批；read-only annotation 不得跳过审批。

- [x] **Step 2：运行失败测试**
  Run: `pytest tests/test_mcp_adapter.py tests/test_mcp_permissions.py -q`
  Expected: 因 Adapter 和策略尚不存在而失败。

- [x] **Step 3：实现 Adapter 与策略查询**
  Adapter 的 `run()` 仅委托 `McpManager.call_tool()`，并将 text 内容交给现有 `ToolResultTruncationHook`。`ToolPermissionHook` 只使用 Tool 的 `approval_required` 元数据和 session `auto_approve_tools=true` 决策；后者必须优先允许全部 Tool。

- [x] **Step 4：运行测试并提交**
  Run: `pytest tests/test_mcp_adapter.py tests/test_mcp_permissions.py -q`
  Expected: PASS。
  `git add Turning-Good-Agent/mcp/adapter.py Turning-Good-Agent/hooks/tool_permission.py Turning-Good-Agent/runtime/runtime.py`
  `git commit -m "feat: register mcp tools with approval"`

### Task 5：通用 MCP 控制 Tool 与当前轮 Context Attachment

**Files:**
- Create: `Turning-Good-Agent/mcp/control_tools.py`
- Modify: `Turning-Good-Agent/tools/base.py`
- Modify: `Turning-Good-Agent/tools/executor.py`
- Modify: `Turning-Good-Agent/runtime/agent_loop.py`
- Modify: `Turning-Good-Agent/runtime/runtime.py`
- Test: `tests/test_mcp_control_tools.py`
- Test: `tests/test_mcp_attachments.py`

**Produces:** 三个固定 MCP 控制 Tool 和仅当前轮 Context Attachment。

- [x] **Step 1：写失败测试**
  覆盖：search 只返回 Catalog 元数据且不读远端内容；attach/apply 默认请求批准；批准的 Resource 以最多 8,000 tokens 的头尾文本作为本轮附件；Prompt 保留 `user` / `assistant` 顺序且超过 4,000 tokens 被拒绝；多个附件超过 12,000 tokens 被拒绝；附件不写入 assistant/user message、summary 或下一轮 Context。

- [x] **Step 2：运行失败测试**
  Run: `pytest tests/test_mcp_control_tools.py tests/test_mcp_attachments.py -q`
  Expected: 因控制 Tool 与 Attachment 支持尚不存在而失败。

- [x] **Step 3：实现 Attachment 传递与控制 Tool**
  扩展 `ToolResult` 以携带可选 `context_attachment`，`ToolExecutor` 保留该字段。AgentLoop 在 Tool result 已完成审批、已执行结果处理后，检查 Attachment，验证 role 仅为 `user` / `assistant`，检查每附件和总预算，然后按返回顺序追加到本轮 `working` messages。普通 Tool Result 仍按现有 `role=tool` 规则回注。三个控制 Tool 分别调用 Manager 搜索、读取与 Prompt 获取；search 标记 `approval_required=false`，其余两个标记 `true`。

- [x] **Step 4：运行测试并提交**
  Run: `pytest tests/test_mcp_control_tools.py tests/test_mcp_attachments.py -q`
  Expected: PASS。
  `git add Turning-Good-Agent/mcp/control_tools.py Turning-Good-Agent/tools/base.py Turning-Good-Agent/tools/executor.py Turning-Good-Agent/runtime/agent_loop.py Turning-Good-Agent/runtime/runtime.py`
  `git commit -m "feat: add mcp context attachments"`

### Task 6：Runtime 生命周期、CLI 收口与文档

**Files:**
- Modify: `Turning-Good-Agent/runtime/runtime.py`
- Modify: `Turning-Good-Agent/cli.py`
- Modify: `README.md`
- Modify: `docs/TURNING_GOOD_AGENT_SPEC.md`
- Modify: `docs/PROJECT_ARCHITECTURE.md`
- Test: `tests/test_runtime_mcp_lifecycle.py`

**Produces:** Runtime 可启动/关闭 MCP，CLI 退出无遗留 stdio 子进程，文档与实现一致。

- [x] **Step 1：写失败测试**
  验证 Runtime 默认创建 Manager、启动后注册控制 Tool 与已启用 MCP Tool、关闭时关闭全部 Server、CLI `/exit` 调用关闭流程；MCP 启动失败不阻断普通 CLI 对话。

- [x] **Step 2：运行失败测试**
  Run: `pytest tests/test_runtime_mcp_lifecycle.py -q`
  Expected: 因 Runtime MCP 生命周期尚不存在而失败。

- [x] **Step 3：实现启动与关闭**
  `AgentRuntime.create_default()` 创建 Manager，异步启动在第一次真实 Runtime 使用前完成，避免同步构造器启动连接。新增 `AgentRuntime.close()` 并由 CLI 的 `finally` 调用。Runtime 关闭失败只记录日志，不能覆盖用户最终回复。

- [x] **Step 4：同步文档并完整验证**
  README 只说明用户配置、审批与当前支持范围；不得声称已支持 OAuth、旧 SSE、Web MCP 管理或多模态附件。
  Run:
  ```bash
  pytest -q
  git diff --check
  printf '/exit\\n' | python -m Turning-Good-Agent chat
  ```
  Expected: 全部测试通过、差异检查通过、CLI 正常退出。

- [x] **Step 5：提交**
  `git add README.md Turning-Good-Agent/runtime/runtime.py Turning-Good-Agent/cli.py`
  `git commit -m "feat: integrate mcp runtime lifecycle"`

## 验收标准

- 可以连接一个 stdio 与一个 Streamable HTTP MCP Server，失败互不影响。
- 每个 Server 完成 initialize、initialized，并只对已声明的 Tools/Resources/Resource Templates/Prompts 执行分页发现。
- 明确启用的 MCP Tool 出现在 OpenAI-compatible tool schema；默认不将 Server 的全部 Tool schema 注入 Context。
- MCP Tool 使用 `mcp_<server>_<tool>` 名称，对远端调用保留原始 Tool 名称。
- 所有 MCP Tool 默认审批；`/approve on` 是唯一统一跳过条件。
- `search_mcp_capabilities` 只返回少量元数据；不读取或注入 Resource/Prompt 内容。
- `attach_mcp_resource` 与 `apply_mcp_prompt` 在批准后仅影响当前 AgentLoop，遵守 8,000 / 4,000 / 12,000 token 限额。
- Resource 不进入 `messages.jsonl`、summary 或下一轮 Context；Prompt 不允许引入 system role。
- `list_changed` 和手动刷新都能更新 Catalog 与 ToolRegistry，并释放旧连接。
- CLI 退出关闭所有 MCP SDK Session 和 stdio 子进程。

## 明确不实现

- 旧 HTTP+SSE transport、OAuth、浏览器授权与 token 刷新。
- Web MCP 商店、预设目录、OAuth UI、Server 安装器与远程审批。
- Resource/Prompt 的全量预注入、持久化、RAG、自动长期记忆或跨轮重放。
- 将每个 Resource/Prompt 转换为独立 Tool schema。
- 多模态 Attachment、MCP sampling、elicitation、roots、logging、任务、订阅内容推送。
- Server 级无限重连、后台轮询或跨进程连接恢复。

## 后续关系

- Phase 5 Skills 可使用 MCP Catalog 描述作为 Skill 的按需参考，但不得自动加载全部 MCP 内容。
- Phase 6 Web 复用 `McpManager` 提供一键增删启停、状态、Catalog 搜索、候选卡片和凭据表单；不重写 MCP transport。
- Phase 9 Web/微信/飞书 Channel 可为 MCP 附件审批提供各自 UI，但审批语义仍由现有 Hook 与 Session 开关统一控制。

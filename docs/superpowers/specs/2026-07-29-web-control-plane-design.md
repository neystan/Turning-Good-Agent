# Web Control Plane Design

状态：已确认，待实施。本文件定义 Phase 6 Web 工作台的新增后端控制面、REST 契约与 Runtime 生命周期边界；它不是前端视觉或组件设计稿。

## 1. 目标

在保持本机单用户、Runtime-first 和 Session JSON/JSONL 唯一业务持久化边界不变的前提下，增加：

1. 可编辑配置的控制面、候选 LLM 连通性测试和空闲 Runtime 重载。
2. 后端唯一真相的 Command Catalog，供 Web 输入 `/` 时查询。
3. Tool Catalog、Context、分页 Tool Call 与 MCP Server 的只读 Read Model。

Web 继续是 Channel Host。React 只保存未应用的表单值、命令面板选择和阅读位置；不复制配置校验、MCP 生命周期、Session 命令、Tool 权限或 Runtime 规则。

## 2. 范围与非目标

### 2.1 本次实现

- 配置字段级编辑、一次“应用配置”、完整候选校验、原子写入和状态查询。
- 全局空闲后自动替换 Runtime；下一条 turn 只使用同一个版本的 Runtime、LLM、Hook、Skill Catalog 和 MCP Manager。
- 显式 LLM 连通性测试，测试未应用的候选字段且不写会话或 token 账本。
- 动态 Command Catalog：`/context`、`/tools`、具体有效 Skill、具体已连接 MCP Server。
- Tool Catalog Read Model、Context Read Model、最新优先 cursor Tool Call Read Model、只读 MCP Server Read Model。

### 2.2 明确不实现

- 配置档案、导入/导出、配置草稿文件、配置历史或跨服务重启保存未应用编辑。
- Web 修改 MCP、`data_dir`、`user_id`、`channel`、Web host/port/并发/事件缓冲。
- 新 Provider、MCP Tool schema 按轮过滤、MCP 自动调用、Skill 强制预加载或新的 Slash Runtime 命令。
- 把 Context、Tool 或测试连接结果写入 `messages.jsonl`、`tool_calls.jsonl`、`true_token_usage.jsonl` 或新增观测 JSONL。
- 前端视觉、布局、动效、快捷键、组件库或样式决策。

## 3. 控制面架构

```text
React control surfaces
  ├─ GET Command / Tool Catalog / Context / Tool Call / MCP Read Models
  ├─ POST Config apply / LLM test
  └─ browser-only unapplied form values
             ↓
FastAPI control routes
             ↓
WebConfigControlService ── atomically reads/writes settings.local.json
             ↓ desired revision
RuntimeSupervisor ── waits for global Web idleness
  ├─ build and start replacement AgentRuntime
  ├─ atomically publish it to WebSessionCoordinator
  └─ close the old AgentRuntime only after replacement starts
             ↓
WebSessionCoordinator → AsyncMessageBus → AgentRuntime
```

`RuntimeSupervisor` is the only owner allowed to swap a Web Runtime. `WebSessionCoordinator` remains the only Web turn scheduler and exposes global idleness. `AgentRuntime` remains the only owner of the state machine, Session persistence, Hook execution and MCP lifecycle.

`web/backend/config_control.py` owns `WebConfigControlService`, which implements the Web-only saved desired configuration flow: allowlisted merge, complete-candidate validation, redaction, revision calculation and atomic file replacement. `RuntimeSupervisor` owns the active Runtime, its active revision and the lifecycle state; it composes both views for `GET /api/control/config`. This prevents either layer from mutating the other's responsibility.

`config/settings.py` only parses local JSON and applies defaults. `config/validate.py` is the single home for configuration rules and exposes field-addressable `SettingsValidationError`. `Settings.load` invokes static settings validation; the CLI, WebConfigControlService and Runtime bootstrap invoke the same module for their additional validation context, including registered native Tool names and canonical configured MCP Tool names. Each caller renders the same error differently, never reimplements a rule.

## 4. Configuration model

### 4.1 Editable and read-only fields

The configuration response returns only these editable paths:

| Group | Editable fields |
| --- | --- |
| `llm` | `base_url`, `model`, `timeout_seconds`, `max_retries`, `retry_delay_seconds`, `streaming_enabled`, plus write-only API Key replacement/clear |
| `runtime` | `max_tool_rounds`, `max_tool_calls_per_round`, `parallel_tool_calls_enabled`, `max_parallel_tool_calls`, `turn_timeout_seconds`, `max_context_tokens`, `max_tool_result_tokens` |
| `memory` | `compact_token_threshold`, `recent_window_token_limit` |
| `sessions` | `retention_days` |
| `skills` | `max_loaded_skills_per_turn`, `max_skill_tokens`, `max_loaded_skill_tokens_per_turn` |
| `tool_permissions` | membership operations for `approval_required_tools` only |

`llm.provider` is returned as the read-only value `openai-compatible`, because it is currently the sole supported Provider. The API never returns `llm.api_key`. It returns only `api_key_configured: boolean`.

`tool_permissions.auto_approve_tools` remains the existing global, cross-session immediate policy exposed through the Composer and `/api/settings/ui`. It approves every Tool that otherwise requires approval. The control plane only changes which individual Tools require approval, then reloads at global idleness.

`data_dir`, `default_session_id`, `user_id`, `channel`, all `web` fields and all MCP configuration fields are not returned as editable configuration. MCP receives a separate redacted read model in section 7.

The approval editor never accepts hand-written Tool names. It first reads the live Tool Catalog in section 6, then sends only membership changes selected from that Catalog. The server still validates the merged candidate so a stale browser Catalog cannot introduce an unknown name.

### 4.2 Browser editing and apply semantics

The browser owns unapplied edits. Closing, refreshing or navigating away without applying them discards them and leaves the active configuration unchanged.

The browser sends only changed scalar paths and Tool approval membership operations to one apply endpoint. The server serializes apply requests. It starts from the newest saved configuration, merges non-overlapping paths, and lets the last successfully processed request win for the same scalar path. Tool approval membership is updated through add/remove operations; a request may not add and remove the same name.

The complete merged candidate is parsed through the same `Settings.load` validation path used at startup. If any type, range, registered Tool name or cross-field invariant is invalid, the server writes nothing and returns field errors. A valid candidate is written to a temporary sibling file and atomically replaced as `settings.local.json`.

Removing `write_file`, `edit_file`, `exec` or `write_stdin` from approval requirements is allowed. The UI may warn, but the backend does not require a separate risk acknowledgement. Hard checks in `security.py` and `ToolExecutor` remain mandatory.

### 4.3 Config REST contract

#### `GET /api/control/config`

Returns the currently saved desired configuration, the currently active Runtime configuration and lifecycle state. Both views are redacted and only contain the editable fields described above.

```json
{
  "desired_revision": "sha256:...",
  "active_revision": "sha256:...",
  "state": "active",
  "last_apply_error": null,
  "desired": {
    "llm": {
      "provider": "openai-compatible",
      "api_key_configured": true,
      "base_url": "https://api.openai.com/v1",
      "model": "example-model",
      "timeout_seconds": 60,
      "max_retries": 2,
      "retry_delay_seconds": 0.5,
      "streaming_enabled": true
    },
    "runtime": {},
    "memory": {},
    "sessions": {},
    "skills": {}
  },
  "active": { "llm": {}, "runtime": {}, "memory": {}, "sessions": {}, "skills": {} }
}
```

`state` is one of `active`, `pending`, `applying` or `failed`. `failed` means the desired revision was valid on disk but a replacement Runtime could not start; the previous active Runtime remains available and `last_apply_error` explains why.

#### `POST /api/control/config/apply`

Accepts a partial change set. Omitted paths are untouched. `api_key` is write-only; an empty string is rejected, omission preserves the existing key, and `clear_api_key: true` explicitly clears it.

```json
{
  "changes": {
    "llm": { "model": "example-model", "streaming_enabled": true },
    "runtime": { "max_tool_rounds": 6 }
  },
  "approval_required_tools": {
    "add": ["web_fetch"],
    "remove": ["exec"]
  }
}
```

Success returns the same revision and state fields as `GET /api/control/config`. Invalid candidates return HTTP 422 with `{ "field_errors": { "runtime.max_tool_rounds": "..." } }`. The endpoint never accepts unknown paths or arbitrary raw JSON.

#### `POST /api/control/config/test-llm`

Accepts a candidate subset of the LLM fields, including an optional replacement Key. The server combines omitted fields with the saved configuration and performs one minimal OpenAI-compatible Chat Completion using no tools and a minimal output limit. It does not write files, publish a revision, replace Runtime, create Session data or add TGA token accounting.

Success returns `{ "ok": true, "latency_ms": 123 }`. Failure returns HTTP 422 for invalid candidate fields or HTTP 502 with a sanitized Provider failure. The Key is never returned or logged in the response.

### 4.4 Runtime replacement

Applying a valid configuration updates the desired revision immediately. If no Web turn is queued, running, stopping or awaiting approval, `RuntimeSupervisor` starts the replacement immediately. Otherwise the state is `pending` until `WebSessionCoordinator` reports global idleness.

The supervisor constructs a fresh `Settings`, LLM Provider and `AgentRuntime`, registers the Web Channel adapter, starts Skills and MCP lifecycle, then atomically makes the new Runtime visible to the coordinator. Only after that succeeds does it close the previous Runtime. A failure leaves the previous active Runtime untouched. New turns wait behind a short supervisor gate while `state == applying`; no turn may start against a half-replaced instance.

`GET /api/settings/ui` returns `{ "auto_approve_tools": boolean }`; `PATCH /api/settings/ui` accepts exactly that boolean and updates the current Runtime plus `settings.local.json` immediately. It does not request a Runtime reload, cannot alter per-Tool membership and remains the Composer's only global “默认权限 / 完全访问” control. Control-plane apply starts from the latest file and therefore preserves a global toggle changed while an earlier desired revision is pending.

## 5. Command Catalog

### 5.1 `GET /api/control/commands`

The browser requests a fresh Catalog when the user first enters `/` for a newly opened command panel. The browser may cache it only while that panel remains open.

```json
{
  "entries": [
    {
      "id": "inspect.context",
      "kind": "inspect",
      "icon": "context",
      "slash": "/context",
      "label": "查看上下文",
      "description": "打开当前会话的结构化上下文",
      "action": "open_context"
    },
    {
      "id": "skill.release-review",
      "kind": "skill",
      "icon": "skill",
      "slash": "/release-review",
      "label": "release-review",
      "description": "检查发布前变更、验证结果和风险。",
      "action": "insert_text",
      "insert_text": "请优先参考 Skill「release-review」："
    }
  ]
}
```

The Catalog contains exactly:

- fixed inspect entries `/context` and `/tools`;
- every valid Skill from the active `SkillManager`;
- every currently connected MCP Server from the active `McpManager`.

It does not contain `/help`, `/history`, `/approve`, `/clear`, `/new`, `/exit`, abstract `/skills` or `/mcp` groups, disconnected MCP Servers, or any CLI-only lifecycle command. Entries have stable `id` and `kind`; Skill/MCP icons distinguish sources, and a source label is added only when displayed names collide.

Selecting `/context` or `/tools` executes the corresponding read action immediately, clears the slash input and never sends `message.send`. Selecting a Skill or MCP Server replaces the slash text with `insert_text`; the user may edit it and it becomes an ordinary user message only if they later send it. Selection itself writes no Session data, trace or metadata. Skill text is advice only: it does not preload the Skill or change `load_skill`. MCP text is advice only: it does not filter schemas, call a Server or change Tool visibility.

## 6. Tool Catalog, Context and Tool Call Read Models

### 6.1 `GET /api/control/tools`

Returns every Tool currently exposed by the active Runtime and the effective approval state. It is the only source for the configuration center's Tool approval editor; it never executes a Tool or exposes a Tool schema body, credential or implementation detail.

```json
{
  "active_revision": "sha256:...",
  "auto_approve_tools": false,
  "tools": [
    {
      "name": "exec",
      "description": "在受控环境中执行命令。",
      "source": { "kind": "core" },
      "approval_required": true,
      "effective_approval": "manual"
    },
    {
      "name": "mcp_github_search",
      "description": "搜索 GitHub 仓库内容。",
      "source": { "kind": "mcp", "server_name": "github" },
      "approval_required": true,
      "effective_approval": "manual"
    }
  ],
  "unavailable_approval_required": []
}
```

`source.kind` is `core` or `mcp`. Core Tools and Tools from currently connected MCP Servers are visible; disconnected MCP Servers are not presented as normal editable Tools. `approval_required` is the persisted membership of `approval_required_tools`. `effective_approval` is `not_required`, `manual` or `automatic`: when `auto_approve_tools` is true, a persisted approval requirement has effective value `automatic`.

The UI may use drag-and-drop, toggles or batch controls, but it converts the final difference into `approval_required_tools.add` and `.remove` in one Config Apply request. It must not write Tool names directly to another endpoint or mutate the active Runtime.

An approval entry can outlive a live MCP connection. Such entries are preserved, returned only in `unavailable_approval_required`, and may be removed but not added from a stale name. They do not make a saved configuration invalid merely because their MCP Server is disconnected; they take effect again if the same canonical Tool is registered later. Native Tool names and canonical enabled MCP Tool names are validated during startup/reload so a typo still fails before a Runtime is published.

### 6.2 `GET /api/control/sessions/{session_id}/context`

Returns the structured equivalent of the existing CLI `/context`, plus a token breakdown calculated from the active Runtime configuration.

```json
{
  "session_id": "web-...",
  "summary": "...",
  "full_history_count": 42,
  "uncompacted_history_count": 8,
  "uncompacted_history_tokens": 1234,
  "uncompacted_messages": [
    { "id": "...", "role": "user", "content": "...", "token_count": 12, "created_at": "..." }
  ],
  "token_breakdown": {
    "system_tokens": 0,
    "skill_catalog_tokens": 0,
    "profile_memory_tokens": 0,
    "summary_tokens": 0,
    "history_tokens": 0,
    "tool_schema_tokens": 0,
    "current_context_tokens": 0,
    "max_context_tokens": 300000
  },
  "active_revision": "sha256:..."
}
```

It never returns system prompt text, profile-memory text, Skill bodies, MCP attachments, Tool schema bodies, API keys or MCP headers/env. `/context` opens the inspector’s Context section and does not write a chat message.

### 6.3 `GET /api/control/sessions/{session_id}/tool-calls`

Returns only persisted `tool_calls.jsonl` records. It never creates an in-memory duplicate of running Tool calls; the existing activity cluster remains the real-time surface.

Query parameters are `limit` (default 50, maximum 100) and optional opaque `cursor`. Results sort by newest `created_at` then `tool_call_id` and retain a snapshot boundary inside the cursor so later appended rows do not shift later pages.

```json
{
  "items": [
    {
      "turn_id": "...",
      "tool_call_id": "...",
      "tool_name": "read_file",
      "args": {},
      "content": "...",
      "error": null,
      "duration_ms": 12.3,
      "created_at": "..."
    }
  ],
  "next_cursor": "opaque-or-null",
  "snapshot": "opaque"
}
```

`/tools` opens the inspector’s Tool Call section and invokes this Read Model; it never enters the Runtime command state or chat history.

## 7. MCP Server Read Model

#### `GET /api/control/mcp/servers`

Returns redacted configured Server summaries and current status. It includes `name`, connection state, non-secret transport label, error text, catalog counts and enabled Tool names. It excludes commands, args, cwd, URL query credentials, env and headers.

#### `GET /api/control/mcp/servers/{name}`

Returns the selected Server’s redacted status, catalog entries and enabled Tool names. It is read-only and does not call or reconnect the Server.

The command catalog includes only statuses that represent a completed live connection. The Config Read Model may show all configured Servers for diagnostics, but it exposes no secret connection material.

## 8. Error and persistence rules

- Unknown Session or Server: HTTP 404.
- The Tool Catalog never contains unavailable Tools as editable entries; an attempted add outside the current Catalog returns HTTP 422 with `approval_required_tools.add` field errors.
- Invalid catalog/action identifiers: HTTP 422; the browser must re-read the Catalog rather than invent an action.
- Config validation errors: HTTP 422 with field paths; no file write and no Runtime change.
- Runtime replacement failure: previous Runtime remains active, `GET /api/control/config` reports `failed`, and a later apply may replace the desired revision.
- Read-model data is derived from existing Session files and active in-memory managers. No new JSON/JSONL files are introduced.

## 9. Frontend handoff constraints

The frontend implementation may choose its own visual design, components and interaction polish, but it must:

1. keep un-applied configuration values browser-local until apply succeeds;
2. call only Catalog-declared actions and use `id`/`kind`, not copied command rules;
3. treat Skill/MCP insertion text as ordinary editable text;
4. keep `/context` and `/tools` outside `message.send` and outside chat history;
5. render Config lifecycle state from the Config Read Model; and
6. build Tool approval editing only from the live Tool Catalog and submit its diff through Config Apply; and
7. never expose, persist, log or synthesize secret configuration values.

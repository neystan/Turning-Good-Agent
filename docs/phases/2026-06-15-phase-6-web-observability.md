# Turning-Good-Agent Phase 6 Web Channel、控制面与会话观测实施设计

状态：已实现。本文是 Phase 6 的唯一权威设计、实施边界、完成记录与验收标准，替代旧版只读 Dashboard 方案，并合并 `docs/superpowers/` 下 Web 工作台、控制面、Slash、检查器和桌面视觉收口文档中的当前有效事实。历史 spec/plan 仅保留决策与实施过程；与本文或当前代码冲突时，以本文和当前实现为准。

2026-08 Gateway 拓扑收口：Web 不再是独立 Host。`tga gateway` 是唯一 Runtime/Bus/Proactive Host，FastAPI 作为 Gateway 内服务运行；`tga web` 已移除。本文中任何“Web Host 自行创建 Runtime、Bus、RuntimeSupervisor 或主动所有权”的历史描述均已由本文的 Gateway 内服务边界及 Phase 7 的 Gateway 契约取代。Phase 6 保留 Web 工作台、控制面、会话观测和前端交互契约；Gateway 拓扑和部署验收不在本文夸大为已完成。

2026-07-30 控制面与桌面前端收口：设置已成为独立 `#settings` 工作面，支持字段级配置编辑、候选 LLM 测试、字段错误、Tool 审批规则和“应用配置”；有效配置写入 desired revision，并在全 Gateway 的 turn 与主动任务达到安全空闲点后由 `RuntimeSupervisor` 原子替换 Runtime。Composer 的 Slash 面板由后端 Command Catalog 驱动，可在空白边界后的任意 `/` 位置触发；`/context`、`/tools` 直接打开会话检查器，Skill 与已连接 MCP 以可删除、可移动的内联语义片段进入输入内容，发送时仍使用服务端原始 `insert_text`。加载态使用与最终参数行、检查器摘要和分组几何一致的骨架；样式已按 Sidebar、Timeline、Composer、Slash、Inspector、Settings、Overlay 和 Icons 分域，`components.css` 仅保留跨域共享规则。桌面视觉验收范围为 1024、1280、1440 与 1920px；本轮不宣称新增移动端视觉改造。
2026-08-06 Catalog 控制动作收口：Command Catalog 新增当前会话上下文压缩、`run_skill_evolution`、`run_dream` 当前会话/全局和 `run_breakbeat` 当前会话/全局；选择后经当前 `/ws/web` 的 `catalog.execute` 直接执行。主动动作只在发起 Web 会话显示 `tool.started`/`tool.finished` 与任务终态，不产生 `response.*`、聊天气泡或会话消息；执行期间 Composer 的编辑和 Stop 均锁定，完成或失败后恢复。压缩复用现有短期记忆摘要器，更新摘要与近期未压缩窗口，并将真实压缩 Token 写入 `true_token_usage.jsonl`（`compacted=1`）和 `COMPACT` Trace；不写 `messages.jsonl` 或 `tool_calls.jsonl`。

2026-07-27 工作台视觉系统收口：WebSocket 动作使用 `client_action_id` 关联乐观消息、错误与有限受理回执，前端以历史加载版本闸门、有限退避重连和 `after_event_id` 回放保持会话稳定；会话栏仅按置顶优先排序，三点操作、搜索、重命名和删除确认由 Radix Primitive 处理定位、键盘、外部关闭与焦点回归。工作台采用石墨深浅主题，聊天区与侧栏独立滚动，短 user 消息按内容宽度显示。常态区域以背景层级、留白和克制阴影区分，可点击控件使用统一圆角 token。桌面检查器打开时，中央会话区通过 Grid 为右侧检查器预留宽度，消息列与 Composer 在剩余区域重新布局；平板和移动端改为全屏覆盖阅读。真实事件按 `request_id` 显示在同轮 user 与 assistant 消息之间的可折叠“思考中”活动簇，运行中 guidance 立即显示为 user 消息并由既有 `task.status` 标记“已引导”。输入区显示“默认权限 / 完全访问”菜单，以及只读上下文占用环；后者从最近一次 `SAVE.metadata.current_context_tokens` 与 `runtime.max_context_tokens` 读取，并在悬浮或键盘聚焦时显示读数。底层仍使用全局 `tool_permissions.auto_approve_tools`；检查器先展示累计 token、当前上下文、压缩次数与工具失败，再以结构化条目按需展开原始记录。此收口不新增 JSONL，也不新增 `guidance.consumed`，且“思考中”不表示或暴露模型内部思维链。浏览器视觉体验以真实页面、真实交互和截图为判断依据；Playwright 用于稳定复现与回归，但不以单一像素断言替代人工视觉判断。

## 1. 目标与定位

Phase 6 提供本机单用户的 Web Agent 工作台：用户可实时对话、在运行中引导 Agent、审批工具、停止任务、管理会话，并查看单个会话的完整执行追踪。

Web 是 Gateway 内的一个 Channel 服务，不是第二个 Runtime，也不是独立监控产品。它复用 Gateway 注入的 Runtime、Session、Hook、MessageBus、JSON/JSONL 和 MCP 生命周期；业务规则不能复制到 FastAPI 或 React 中。

核心目标是“Codex 的对话优先 + 克制的控制台级过程可见性”：聊天是主工作面，任务过程可折叠，完整观测按需进入会话检查器。

## 2. 已确认范围

### 2.1 本阶段实现

- Gateway 内的 FastAPI 本机 Web 服务、REST API、WebSocket 和 React 单页工作台。
- 会话列表、搜索、新建草稿、恢复、置顶、重命名、归档、恢复归档和删除。
- 当前会话聊天历史、安全 Markdown、流式文本、工具/压缩状态、Stop 和工具审批。
- 同会话串行执行、运行中 guidance queue、跨会话最多 6 个并行任务和全局等待队列。
- 断线重连、每会话有界内存事件缓冲和运行中任务快照。
- 断线中的本轮消息可在浏览器内显示“网络连接失败”并原动作重试；即时消息、活动簇和 Web 端隐藏记录只保留在当前浏览器标签页。
- 会话检查器：trace、工具调用、token、上下文和压缩统计。
- Composer 只读上下文占用环与悬浮读数，复用最近一次保存后的真实上下文统计。
- 全局工具自动批准按钮，持久化到 `settings.local.json`。
- 独立设置工作面：字段级配置修改、API Key 替换/清除意图、候选 LLM 连通性测试、字段级错误和一次应用。
- desired/active revision、全局空闲 Runtime 重载、失败时保留旧 Runtime，以及仅通过 REST 轮询读取重载状态。
- 后端唯一真相的 Command Catalog、Tool Catalog、Context/Tool Call/MCP Read Model。
- Composer Slash 命令面板、键盘选择、检查器直读动作，以及 Skill/MCP 的内联可编辑选择片段。
- 默认深色与可持久化浅色主题。

### 2.2 明确不实现

- 登录、账号、用户隔离、多租户、云同步、远程部署或公网暴露。
- Web 修改 Provider 类型、MCP Server 连接、部署、身份、存储路径或 Web host/port/并发/事件缓冲配置。
- 配置档案、导入/导出、配置历史、跨刷新保留未应用编辑，或任意原始 JSON 配置编辑。
- 文件上传、图片生成、多模态附件、复杂工具日志、文件 diff。
- 会话分类、标签、手动排序、LLM 自动命名、自动归档。
- 流式 delta 的 JSONL 落盘、重复监控 JSONL、跨服务重启恢复在途任务。
- 微信、飞书 Channel；Phase 8 后续负责这两个 IM Adapter。

MCP Server 仅提供脱敏只读状态和 Catalog 检查；连接参数继续由本地配置文件管理。当前控制面不新增 Provider，也不允许浏览器修改任何未列入 allowlist 的字段。

## 3. 安全与部署边界

- 这是完全本机、单用户、数据不上云的个人 Agent。
- 默认监听 `127.0.0.1`；不提供公网启动说明、认证或跨域开放策略。
- CLI 与 Gateway 的本机 WebSocket 使用 Gateway 首启自动生成的 Bearer 凭据；该 `gateway.auth_token` 不是 Web 设置项，浏览器控制面不读取或编辑它。
- Web 不绕过 `security.py`、`ToolExecutor` 二次预检、Tool 权限 Hook 或 MCP 审批规则。
- FastAPI 控制服务可读取本机配置完成合并与验证，但绝不向浏览器返回 LLM API Key、MCP headers、MCP env、命令参数或连接凭据；Key 只允许 write-only 替换或显式清除，读取只返回 `api_key_configured`。
- assistant 消息只渲染安全 Markdown，禁止原始 HTML。
- 生产前端静态资源、字体和图标均从本地服务，不请求外部字体、分析或 CDN。

## 4. 用户工作流

### 4.1 草稿与会话

1. 用户点击新建，浏览器进入 `/` 草稿页；不创建 `.sessions/` 文件夹。
2. 用户发送首条非空消息，后端创建真实 session，并将 URL 替换为 `/sessions/<id>`。
3. 已持久化会话加载完整消息历史；刷新、浏览器前进/后退和重连都以 URL 为准恢复。
4. 空草稿离开、刷新或切换会话即丢弃，不留下空会话。

### 4.2 运行中引导

```text
用户输入任务
  -> 当前 session 进入运行
  -> 用户继续发送补充
  -> UI 立即显示该补充，Coordinator 写入 guidance queue
  -> AgentLoop 下一次 LLM/Tool 安全检查点读取 queue
  -> 以固定 user 包装追加到本轮 working messages
  -> 继续同一个任务，不创建并发 turn
```

固定包装：

```text
【用户正在引导当前任务】
用户在任务执行中补充了以下方向。请在系统约束和安全审批不变的前提下调整后续计划；不要重复已经完成的操作。

<用户补充内容>
```

- 同一 session 绝不同时运行两个 Agent turn。
- 不强行中断在飞的 LLM 请求或 Tool；guidance 只在安全检查点生效。
- 已注入 guidance 正常保存为 user message；未注入 guidance 只保留在浏览器草稿，不自动启动下一任务。

### 4.3 Stop

- Stop 是独立动作，不等价于发送 guidance。
- 点击后任务状态立刻变为 `stopping`，后续 LLM/Tool 调用在下一安全检查点停止。
- 在飞的 Tool 不被强杀，避免外部副作用状态不确定。
- 等待审批是安全检查点：Stop 立即按“拒绝”完成审批，并结束任务。
- 已经推送到浏览器的 assistant 文本以 `incomplete=true`、`outcome=cancelled` 保存；用户可看到任务停在何处。
- 用户可以在终态后重新发送或编辑未注入 guidance 草稿，开始新任务。

### 4.4 双层工具权限

- 审批卡只展示工具名与标准化参数，提供“允许一次”和“拒绝”。
- 输入区左下角的“默认权限 / 完全访问”菜单控制 `tool_permissions.auto_approve_tools`，影响所有会话的后续审批点并立即更新，不等待 Runtime 重载。
- 设置页的“工具权限”仅修改 `approval_required_tools` 成员关系：可见 Tool 来自当前 Runtime Tool Catalog；断线 MCP 的既有规则保留但只允许移除。修改必须点击“应用配置”，并在全局空闲后随 Runtime 重载生效。
- 已经弹出的审批卡不因打开全局开关而自动通过。
- 删除 `Session.auto_approve_tools`，CLI `/approve on|off` 也修改同一个全局策略，避免双重真相。
- 两层策略都不绕过 `security.py`、Tool Permission Hook 或 `ToolExecutor` 二次预检。

### 4.5 会话管理

`session.json` 保留已有 `title`，新增：

```json
{
  "title": "检查仓库鉴权逻辑",
  "pinned": false,
  "archived": false
}
```

- 新 session 使用首条用户消息的截断摘要作为初始标题，不调用 LLM。
- 活跃列表先显示 `pinned=true`，其余按最后活动时间倒序；置顶条目显示图标。
- `archived=true` 默认隐藏在“已归档”可展开分组，可恢复或删除。
- 已归档会话可只读打开；发送前必须恢复。
- 运行中、等待审批和 `stopping` 会话禁止归档和删除；置顶、重命名不受影响。
- 删除要求二次确认，直接删除 session 整个目录；Web 不复用 `/clear`、`/exit` 等 CLI 交互。

### 4.6 设置、应用与 Slash

1. 用户从侧栏左下角进入 `#settings`；聊天树卸载，设置成为独立主工作面，返回时恢复原会话路径。
2. 未应用配置只存在于当前 React 组件。返回聊天、刷新或离开页面会丢弃编辑；只有点击“应用配置”才提交局部字段与 Tool add/remove 差异。
3. 后端从最新保存配置合并请求，复用 `config/validate.py` 校验完整候选。HTTP 422 以 dotted path `field_errors` 回到对应字段；失败不写文件、不改变 Runtime。
4. 成功保存后状态可能是 `active`、`pending`、`applying` 或 `failed`。前端没有控制面 WebSocket 事件，只在 `pending/applying` 时以单一、可取消、带版本闸门的轮询读取 `GET /api/control/config`，终态、请求失败、新 Apply 或离开设置页时停止。
5. Composer 在光标前方为行首或空白、当前 token 以 `/` 开始时打开 Slash 面板。没有匹配项时面板完整消失；Arrow Up/Down 循环选择并同步滚动，Enter 选择，Escape 或点击 Composer 外关闭但保留输入。Catalog 动作直接执行，不弹二次确认；执行期间编辑器不可输入。
6. `/context` 与 `/tools` 删除当前 Slash token、保留其余输入，并直接打开当前会话检查器；关闭检查器、刷新或切换会话会清除临时控制面阅读状态，重新打开恢复常规会话观测。
7. Skill 与已连接 MCP 使用不同专用图标和颜色，以一个不可拆分、可删除、可移动的内联语义片段替换 Slash token。它在视觉上与正文共用基线；发送给 Runtime 时仍展开为 Command Catalog 返回的原始 `insert_text`，不创建额外元数据或执行语义。

## 5. 系统架构

```text
React + TypeScript + Vite
  ├─ REST: 会话、观测、配置、Catalog 与脱敏 Read Model
  └─ WebSocket: 发送、guidance、Stop、审批、实时事件、重连
          ↓
FastAPI（Gateway 内 Web 服务）
  ├─ WebConfigControlService（合并、校验、脱敏、revision、写入）
  ├─ WebSessionCoordinator
  │   ├─ 每 session 串行控制 / guidance queue / Stop / 审批 future
  │   └─ SessionEventHub（快照、有界 replay buffer、连接订阅）
          ↓
GatewayHost
  ├─ RuntimeSupervisor（全 Gateway 空闲闸门、替换、失败回退）
  ├─ GatewayTurnCoordinator（唯一入站消费者、共享运行槽位）
  ├─ AsyncMessageBus
  ├─ ChannelManager（唯一出站消费者、按收件人定向投递）
  └─ 唯一 AgentRuntime + ProactiveService
          ↓
WebChannelAdapter
  └─ 把 delta、状态、工具、审批、完成/错误转换为 OutboundMessage
          ↓
AsyncMessageBus.outbound -> ChannelManager -> WebChannelTransport -> SessionEventHub / WebSocket
```

### 5.1 Gateway 内 Web 服务的边界

- FastAPI 只做协议适配、连接管理、会话控制与读取模型；不重写 Context、Tool、Memory 或 MCP 逻辑，也不拥有 Runtime 生命周期。
- `config/settings.py` 只负责解析、默认值与数据模型；`config/validate.py` 是 CLI、Web、测试和 Runtime 重载共享的唯一配置规则与字段错误来源。
- `web/backend/config_control.py` 只拥有 Web 配置应用流程；`gateway/runtime_supervisor.py` 是唯一允许发布 replacement Runtime 的位置；`web/backend/read_models.py` 只从 Gateway 当前 Runtime manager 与既有 Session 文件构建脱敏读取模型。
- `WebSessionCoordinator` 只管理 Web 会话的串行控制、guidance、Stop、审批 Future 和事件缓存；共享运行槽位与入站消费由 Gateway 统一拥有，不能在 React 或 Web 内另建 Runtime/Bus Dispatcher。
- replacement 完整启动并由 Gateway 激活后才对 Web Coordinator 可见；失败时旧 Runtime 保持可用，新 turn 不会进入半替换实例。
- CLI 已改为 Gateway Client，不再保留 Web/CLI 各自装配 Runtime 的路径。

### 5.2 MessageBus 与 Channel Adapter

现有 MessageBus 由 Gateway 唯一创建：Web 请求经 `WebSessionCoordinator` 提交到共享 `inbound`，`GatewayTurnCoordinator` 调用唯一 Runtime；所有面向 Web 的输出经共享 `outbound`、`ChannelManager` 和 `WebChannelTransport` 回到 EventHub。

为使 Web adapter 持有精确的 session/request 关联，`ChannelRouter` 工厂接收当前 `InboundMessage`，而不是无参数工厂。Gateway 注册 CLI 和 Web 的单轮 Adapter：Web 工厂使用规范路由、`InboundMessage.id` 与 EventHub 构造 `WebChannelAdapter`；它们都只向共享 outbound Bus 发布定向消息。

`WebChannelAdapter` 只实现现有 Channel 协议的 Web 映射：

- `on_delta`：`response.delta`
- `on_status`：`task.status`
- `on_tool_started` / `on_tool_finished`：`tool.started` / `tool.finished`
- `request_tool_approval`：`approval.requested` 并等待 Coordinator Future
- `on_completed` / `on_error`：最终 Channel 事件

它不直接写 JSON/JSONL；Runtime 的 `SAVE` 仍是持久化唯一真相。

### 5.3 运行控制接口

Web 需要最小的跨 Channel 控制协议，由单轮 ChannelAdapter 实现：

```python
class TurnControl(Protocol):
    async def consume_guidance(self) -> list[str]: ...
    def is_stop_requested(self) -> bool: ...
```

- CLI/静默 adapter 返回空 guidance 和 `False`。
- AgentLoop 每次发起 LLM 请求前、每批 Tool 前后检查控制器。
- 读取到 guidance 时，按固定包装追加 user working message，同时回传已消费内容给 TurnContext，供 SAVE 按顺序持久化。
- `is_stop_requested()` 为真时，不再开始新的 LLM/Tool 调用，返回取消结果。

这是通用 Channel 控制接口，不让 AgentLoop 导入 `web/`。

### 5.4 Gateway 内主动工作面

`#proactive` 是 Gateway 内 Web 主动中心的简洁概览；`#proactive/cron`、`#proactive/breakbeat`、`#proactive/memory`、`#proactive/skills` 与 `#proactive/incidents` 是五个可直接从侧栏或深链接到达的领域页，而不是 tabbed workspace。它们继续读取完整领域快照和独立运行时投影；Context 与 Tools 检查器保持独立。`/ws/proactive` 是应用级订阅，不再接收 CLI loopback Bridge 的镜像。

确定性主动 REST 操作保留既有路径和响应结构，但只通过 Gateway 注入的服务执行，不再出现“另一个 Host 持有主动能力”的只读冲突。`WebChannelTransport` 收到定向 `proactive_notification` 后发布 notice 与最新领域快照；notice 顶部居中、最多三条，不阻塞 Composer、Stop 或 HITL，也不会移动焦点；info/success/warning 约 4 秒消失，error 约 7 秒消失，悬浮或聚焦时暂停计时，点击可进入对应领域页。该过程不写入聊天历史、聊天上下文或模型提示词，浏览器关闭也不补做系统 Push。

## 6. WebSocket 与 REST 契约

### 6.1 REST

这些会话 REST 端点只接受 Gateway 当前 `principal_id` 且 `channel="web"` 的派生会话；CLI 或未来 IM 的 opaque session ID 不可通过 Web API 读取或修改。

```text
GET    /api/sessions?archived=false
GET    /api/sessions/{session_id}
GET    /api/sessions/{session_id}/messages
GET    /api/sessions/{session_id}/observability
GET    /api/sessions/{session_id}/context-window
PATCH  /api/sessions/{session_id}             # title / pinned / archived
DELETE /api/sessions/{session_id}
GET    /api/settings/ui                       # 即时全局 auto_approve_tools
PATCH  /api/settings/ui                       # 即时全局 auto_approve_tools
GET    /api/control/config
POST   /api/control/config/apply
POST   /api/control/config/test-llm
GET    /api/control/commands
GET    /api/control/tools
GET    /api/control/sessions/{session_id}/context
GET    /api/control/sessions/{session_id}/tool-calls?limit=&cursor=
GET    /api/control/mcp/servers
GET    /api/control/mcp/servers/{name}
```

- `observability` 聚合既有 `session.json`、`turn_traces.jsonl`、`true_token_usage.jsonl` 和 `tool_calls.jsonl`，不创建新文件。
- `context-window` 只读取最近一次 `SAVE.metadata.current_context_tokens`，并返回当前 `runtime.max_context_tokens`；没有已保存轮次时返回中性零值。
- `DELETE` 仅接受终态会话；活动会话返回明确冲突错误。
- `PATCH archived=true` 仅接受终态会话；直接打开归档 URL 可只读，发送前要求恢复。
- `settings/ui` 绝不返回私密 LLM/MCP 字段，且不参与延迟 Config Apply。
- `control/config` 只返回 allowlist 内的 desired/active 脱敏配置、revision、状态和安全错误；`apply` 只接收变更字段和 Tool add/remove，不接受未知路径或任意 JSON。
- `test-llm` 对当前浏览器候选 LLM 字段执行最小无 Tool 请求，不写配置、Session、trace 或 token 账本；Provider 失败返回脱敏 502。
- `commands` 每次面板打开时从 active Runtime 读取 `/context`、`/tools`、有效 Skill 与已连接 MCP；不包含 `/help`、`/history`、`/approve`、`/clear`、`/new`、`/exit` 或抽象 `/skills`、`/mcp`。
- `tools` 返回 active Runtime 当前注册 Tool 与有效审批状态；不可用既有规则仅列入 `unavailable_approval_required`。
- Context Read Model 不返回 system prompt、profile memory、Skill body、MCP attachment 或 Tool schema body；Tool Calls 只分页读取 `tool_calls.jsonl`，cursor 固定首屏快照边界；MCP Read Model 只返回脱敏状态、数量与已启用 Tool。

### 6.2 WebSocket 客户端动作

Web chat 只使用 `/ws/web`；不存在 `/ws` 兼容路由。`guidance.send` 是 Web 服务端能力，CLI 不提供 `/guide`。`GatewayTurnCoordinator` 对同一路由的 turn 保持 FIFO 呈现。

```json
{"type":"session.subscribe","session_id":"...","after_event_id":42}
{"type":"message.send","draft_id":"...","content":"分析这个仓库"}
{"type":"guidance.send","session_id":"...","content":"重点检查鉴权"}
{"type":"task.stop","session_id":"..."}
{"type":"approval.resolve","session_id":"...","approval_id":"...","approved":true}
{"type":"catalog.execute","session_id":"...","catalog_action":"run_dream:session","client_action_id":"..."}
```

### 6.3 WebSocket 服务端事件

每个事件都包含 `event_id`、`session_id`、`request_id`、`type` 和 `created_at`。至少支持：

```text
session.snapshot
task.queued
task.running
task.stopping
task.completed
task.failed
task.cancelled
response.delta
task.status
tool.started
tool.finished
approval.requested
approval.resolved
session.updated
catalog.accepted
```

- 浏览器重连带 `after_event_id`；EventHub 补发窗口内事件后继续订阅。
- 若事件已过期，服务端发送 `session.snapshot`，前端通过 REST 重新拉取已落盘历史和观测。
- 在途任务只存在内存中；服务重启后不会恢复，前端从持久化终态历史继续。
- Coordinator 对最近有限数量的 `client_action_id` 保留受理回执。浏览器因重连、刷新或切换会话重发同一动作时，复用原 `session_id` 与 `request_id`，不重复启动任务或重复加入 guidance。
- `catalog.execute` 的活动事件与普通 turn 共用 EventHub，但动作 `task.queued` 带 `kind=catalog`；这类 turn 没有 `response.delta` 或 `response.completed`。当前会话 scope 动作与同会话普通 turn 互斥，全局主动动作可独立执行但按能力去重；超时复用 `runtime.turn_timeout_seconds`，只发送失败终态。

## 7. React 工作台

### 7.1 工程边界

```text
web/
  backend/                 # FastAPI、Coordinator、EventHub、Web Adapter
  frontend/
    src/
      api/                 # REST 与 WebSocket client
      components/          # 会话栏、聊天、任务过程、审批、检查器
      features/            # sessions、chat、observability、settings
      state/               # 单一浏览器状态与事件 reducer
      styles/              # token、布局与组件领域样式
        components/        # Sidebar/Timeline/Composer/Slash/Inspector/Settings/Overlay/Icons
    package.json
    vite.config.ts
  static/                  # 前端构建产物，Git 忽略
```

- 使用 React、TypeScript、Vite、`lucide-react`、安全 Markdown 渲染、本地字体与 Slash 专用本地 SVG 资产。
- 不使用 Tailwind、通用 UI kit、外部 CDN 或远程字体。
- `tokens.css` 只定义语义 token，`layout.css` 只定义应用骨架，组件领域文件维护各自规则；`components.css` 只容纳真实跨域共享样式，不以跨文件覆盖继续累积历史债务。
- FastAPI 在生产模式托管 `web/static/`；开发模式由 Vite 提供热更新并代理 API/WebSocket。

### 7.2 信息架构

```text
┌──────────────────┬───────────────────────────────────────┬───────────────┐
│ Turning Good     │ 当前会话标题 / 状态 / 会话检查          │ 检查器抽屉     │
│ + 新建            │───────────────────────────────────────│ 概览          │
│ 活跃会话          │ 用户与 assistant 对话                  │ 状态机时间线  │
│ 置顶会话          │ 可折叠任务过程                         │ 工具调用      │
│ 已归档            │                                       │ Token/上下文  │
│                  │───────────────────────────────────────│ 压缩          │
│                  │ 自动批准 | 输入框 | 发送 | Stop         │               │
└──────────────────┴───────────────────────────────────────┴───────────────┘
```

- 左栏固定为会话管理：新建、活跃/置顶/归档分组、状态、菜单操作；桌面端可收起为稳定的品牌入口，再次点击品牌即可恢复。
- 中栏是唯一主工作面：聊天、任务过程和输入。
- 右侧检查器默认关闭；桌面端打开时作为中央 Grid 的右列占用响应式宽度，关闭时收回该列；窄屏改为覆盖阅读。会话条目悬停区和当前标题各有图标入口。
- 窄屏将左栏和检查器切换为覆盖层，聊天和输入仍保持可用。
- 设置页通过 Hash 切换为独立工作面：左侧导航使用独立背景色面，右侧编辑列在扣除导航列后的剩余空间内严格居中，Apply 栏与编辑列同宽、同轴。

会话操作由紧凑图标触发器打开，并保留可访问名称；二级浮层以图标加文字展示置顶、重命名、归档/恢复和删除，不显示额外鼠标悬浮说明。检查器的累计指标与分类组采用连续信息面，只有展开的单条记录与原始 JSON 进入更深的圆角层级，避免观测面板成为卡片堆叠。

### 7.3 聊天与任务过程

- assistant 使用标准 Markdown；代码块显示语言和复制图标，长代码横向滚动。
- user 消息保持纯文本；Tool 参数、trace、token 使用结构化 JSON/表格显示。
- 每个任务有一个默认折叠的过程条目；实时显示排队、运行、工具、压缩、审批、Stop、完成/失败。
- 展开任务过程显示工具顺序、标准化参数和耗时；完整 Tool result 不进入主聊天流。
- 未完成 assistant 消息在恢复时显示“已停止”标识。

### 7.4 输入与键盘

- `Enter` 发送，`Shift+Enter` 换行。
- 运行中输入保留可用，发送后变为 guidance；未注入 guidance 在 Stop 后成为可编辑草稿。
- Stop 使用独立图标按钮，绝不绑定 `Escape`。
- “默认权限 / 完全访问”菜单位于输入区左下角；完全访问状态明确可见。
- Composer 默认预留两行文本高度，第三行后再向上扩展，发送/Stop、权限和上下文读数的工具栏基线不随输入或运行状态跳动。
- `contentEditable` 粘贴只接受 `text/plain`，保留换行与光标位置，禁止继承外部颜色、背景、边框、字体或内联样式。

### 7.5 视觉系统

受众是长期使用本机 Agent 的个人开发者。跨页面通用视觉与交互原则以项目根目录 [`DESIGN.md`](../../DESIGN.md) 为准；本节只记录 Web 工作台的专属实现约束。

- 默认深色，主题切换保存为浏览器本地偏好；浅色与深色分别校验，同一语义使用同一套 Token。
- 使用中性石墨灰阶作为常态表面；蓝色仅表示连接或运行，绿色表示完成，琥珀表示待审批，橙红或红色仅用于完全访问、失败、停止和删除。
- CSS 定义三档基础圆角 token：紧凑控件 `14px`、面板 `18px`、图标与短操作胶囊 `999px`。新增主要组件优先复用这些 token；现有细节样式保留必要的局部圆角值。
- 常态区域、输入、点击、选中和展开状态通过背景层级、留白和克制阴影区分，不使用细边框或蓝色描边光晕；代码、JSON 和危险操作也优先使用实体背景语义。
- 阴影只表达可操作性、浮层层级和当前命中元素。父容器、子按钮和图标不得因一次悬停产生叠加阴影。
- 系统操作图标使用 `lucide-react`，Slash 的 context/tools/Skill/MCP 使用项目内四枚专用 SVG：统一 24×24 viewBox、`currentColor`、1.9 圆角线宽和透明背景，在 16–18px 槽位显示；所有图标统一光学框、尺寸、颜色和垂直基线。Skill/MCP 保持来源区分，但视觉重量与系统图标一致。
- 不使用浏览器原生确认框、选择框、滚动条或突兀默认焦点样式；使用产品内的 Radix 菜单、对话框、确认框与低对比度圆角滚动滑块。
- 动效必须反映真实状态和布局变化。侧栏、检查器、弹层和折叠区过渡尺寸、位置或可见性；`prefers-reduced-motion` 下取消或简化动效。

### 7.6 最终工作台交互与布局收口

#### 三栏与滚动

- 左侧会话栏、中央主工作面、右侧检查器是独立滚动和独立职责的区域。一个区域的滚动、浮层或长内容不能改变其他区域的尺寸、焦点或滚动位置。
- 左右侧栏共用同一响应式宽度策略和顶部基线。左栏可收起为稳定位置的品牌头像；展开、收起时头像位置不移动，收起态不保留会话滚动滑块。
- 检查器默认关闭。桌面端打开时通过真实 Grid 增加右侧检查器列，中央消息列和 Composer 按剩余区域重新居中并同步收窄；关闭时恢复可用宽度而不重置聊天滚动位置。窄屏改为全屏覆盖阅读。
- 聊天时间线为每个 session 记忆滚动位置。用户上滚阅读时，新的流式内容不能抢夺视口，只显示“有新消息”入口；仅在用户发送、停留底部或主动点击时自动跟随。

#### 会话、菜单与浮层

- 会话标题左对齐，置顶标记和三点操作占据稳定右侧槽位，标题不得覆盖图标。会话分组和归档分组都支持折叠。
- 会话三点菜单、搜索、重命名和删除确认使用 Radix 的 Portal、键盘导航、外部点击关闭、Escape 和焦点回归能力，不能被侧栏裁切。
- 会话操作触发器与侧栏共用底色；悬停或打开时升为轻微不同的圆形浮面。二级菜单保留图标和可读文字，菜单项仅在直接悬停或键盘高亮时出现阴影。
- 删除、归档恢复和重命名必须使用应用内对话框，不调用浏览器原生 `confirm`。

#### 聊天、任务、审批与输入

- user 短消息按内容紧凑包裹；assistant 使用无气泡 Markdown 正文，优先保证阅读和长文本换行。完整 Tool result 不进入主聊天流。
- `ActivityCluster` 只展示已有事件可证明的排队、运行、引导、工具/MCP/Skill、审批、压缩、Stop、完成、失败或取消。不得展示、保存、推断或伪造模型 reasoning、思维链或计划文本。
- 任务过程默认折叠。运行时使用“思考中”和真实最新动作表达状态；工具完成后回到“思考中”，不单独显示“工具调用完成”。终态只保留一个结论摘要，历史步骤仅在展开后显示。审批不会自动展开步骤；审批卡仅保留工具名、规范化参数预览、拒绝与允许一次，完整参数和结果进入检查器。
- Composer 与消息列共用横向规则，可稳定容纳多行输入。Enter 发送，Shift+Enter 换行；运行中输入作为 guidance，Stop 与发送在同一圆形操作槽切换，始终完整可见。发送/Stop 左侧的上下文圆环仅供阅读，不打开检查器；悬浮或键盘聚焦显示“当前 token / 最大 token、已用、剩余”，最大值始终来自集中 Runtime 配置。
- WebSocket 断开时，正在运行的 Web turn 在浏览器内变为可重试的“网络连接失败”消息；不伪造 Runtime 终态，也不写入 JSONL。点击重试会用原 `client_action_id` 重连并重新发送；成功后仅在 Web 聊天流隐藏被替代的旧 user/assistant 与活动簇，Runtime 上下文和会话文件仍保留原轮，用户也可继续基于原上下文输入“继续”。
- 输入区左下角使用“默认权限 / 完全访问”菜单控制已有全局 `auto_approve_tools`。完全访问使用橙红警示语义，不展示冗余说明；自动批准绝不绕过现有安全预检和 ToolExecutor 二次检查。

#### 检查器与观测阅读

- 检查器只读取既有 session、token、trace 和工具调用记录，不承担任务过程展示，也不新建任何 JSONL。
- 先以连续信息面展示累计输入、累计输出、当前上下文、压缩次数和工具失败；再以同一连续分组展示 Token、压缩与上下文、工具调用和状态 Trace。
- 状态 Trace 按轮次折叠收拢；Token 标题使用“第 N 轮 · N Token”，压缩与上下文使用 `Compaction check`、`Save context`，超过一秒的 Trace 时长使用 `s`，短耗时保留 `ms`。原始 `turn_id`、字段和 JSON 均在单条记录展开后可见。
- 只有用户展开的单条记录进入更深的圆角层级；原始 JSON 位于单条记录的最内层按需展开，避免检查器形成卡片堆叠。

#### 验收与实施记录

- 交互收口已完成：Portal 会话浮层、搜索与确认对话框、稳定聊天滚动、真实事件活动簇、权限/审批控制、双主题、独立滚动、可收起侧栏和 Grid 检查器均已实现。
- 标签页缓存只保存未落盘消息、活动簇和 Web 端重试隐藏标记；它用于刷新或切换会话时恢复即时界面，关闭标签页后自然失效，不替代 `SAVE`。
- 视觉验收使用本机真实交互与截图，而非静态 CSS、单一屏幕像素位置或 Playwright。人工验收应覆盖深浅主题、长文本、空态、运行/错误/审批/停止状态、菜单和抽屉遮挡、滚动、焦点及不同窗口宽度。
- 自动化验证继续覆盖 Python 测试、前端构建、Python 编译、CLI 与 Web REST 冒烟、`git diff --check`；浏览器视觉体验由本机用户人工确认。
- 本轮桌面视觉验收已覆盖深浅主题、空状态、真实长会话、检查器展开、搜索弹层和会话操作菜单；工作区、Composer 与检查器在各自可用宽度中保持对齐，浮层未发生裁切或遮挡。

### 7.7 当前交付基线（2026-07）

本节合并 `docs/superpowers/` 下 Web 工作台、固定布局、视觉系统与 Docker 设计/计划中的已实现事实。原计划文档保留为过程记录；与当前实现冲突时，以本节及前述工作台约束为准。

#### 桌面工作台

- 会话栏按置顶、最近更新时间排序；搜索弹层可检索活跃与归档会话。三点触发器在悬停或打开时显示圆形浮面，固定定位的二级菜单在视口边界内呈现置顶、重命名、归档/恢复和删除。
- 中央聊天、活动簇和 Composer 构成唯一主工作面。消息、活动簇及未落盘重试状态只在浏览器标签页缓存，不能改变既有 Session JSON/JSONL 或 Runtime 上下文。
- 检查器关闭时不占桌面列；打开时中央对话列与 Composer 在剩余空间内同步居中。摘要、分类、轮次和 Trace 记录按连续阅读层级组织，单条详情与原始 JSON 按需展开。
- 当前桌面验收范围不包含新的移动端设计改造；既有窄屏覆盖层行为保留，但不作为本轮视觉优化的结论。

#### 配置与读取控制面

- 设置页只暴露后端 allowlist 字段；每个标量独立编辑，非同字段并发修改从最新文件合并，同字段以最后成功请求为准。未点击“应用配置”不会保存。
- 配置候选由共享 validator 完整校验，成功后写 desired revision；空闲时 replacement Runtime 启动、发布并关闭旧实例，失败时保留旧 Runtime。Docker 单文件绑定挂载若拒绝 `os.replace`，使用 flush/fsync 的同步写入回退，仍以 `desired_revision == active_revision` 验证生效。
- Slash 命令与 Tool 权限都由 active Runtime Catalog 驱动。前端不复制 Runtime 命令、Skill、MCP 或 Tool 名称规则；检查器读动作不发送聊天消息，也不写 Session 文件。
- Settings 与 Inspector 的加载骨架复刻最终布局几何，不使用转圈动画；普通弹窗、输入与按钮不依赖细描边，危险删除操作保留实体危险色。

#### 本机部署与开发

- 生产入口为根目录 `compose.yaml`：多阶段构建先编译 Vite/React，再以 Python/FastAPI 运行静态 Web；宿主机仅映射 `127.0.0.1:8000`，会话使用命名卷 `/app/.sessions` 持久化，`settings.local.json` 以可写绑定挂载提供本地配置且不进入镜像或 Git。
- 开发入口为 `compose.dev.yaml`：Vite 前端仅向宿主机暴露 `127.0.0.1:8000` 并代理 API/WebSocket 到内部后端；Docker Desktop 绑定挂载通过轮询提供前端 HMR，Python 代码由 Uvicorn reload 重启。依赖、Dockerfile 或锁文件变化时需要带 `--build` 重启。
- 部署不新增公网/局域网暴露、认证、多租户、反向代理或新的 Runtime/协议边界。

#### 已验证边界

- 前端 `pnpm run build` 通过；`web/frontend/tests/workbench_visual.spec.ts` 覆盖空状态与 Composer 间距、浅色侧栏/工作区分层、三个起步示例及深色输入表面，当前为 4 项通过。
- 真实会话验收已检查高密度 Trace、检查器展开、深浅主题、搜索、会话操作菜单和浮层视口边界；`git diff --check` 无差异错误。
- 2026-07-30 当前树重新验证：前端构建通过；默认 Playwright 为 `34 passed / 11 skipped`，显式启用 `TGA_REAL_PAGE=1` 后 11 项真实页面测试全部通过，因此 45 个浏览器场景均有通过记录；Python 为 `11 passed`，完整 `compileall` 与 CLI `/exit` 冒烟通过。
- 本阶段继续不实现自动 LLM 会话标题、会话列表虚拟化、移动端视觉重做或任何模型 reasoning 展示。

## 8. 持久化、观测与 Hook

### 8.1 唯一事实来源

`SAVE` 继续可靠写入：

- `session.json`：会话元数据、summary、uncompacted history、title、pinned、archived。
- `messages.jsonl`：user/assistant 原始消息；取消回复保留 `incomplete=true` 元数据。
- `turn_traces.jsonl`：各状态 trace、RUN/COMPACT/SAVE/RESPOND metadata。
- `true_token_usage.jsonl`：真实 LLM usage 与会话累计 token。
- `tool_calls.jsonl`：精简工具调用记录。

Web 只读取或通过既有 Store 写入这些事实，不新建 `web_events.jsonl`、`monitor.jsonl` 或流式 delta 日志。

配置控制面只写已有 `settings.local.json`，revision、重载状态和 Catalog 属于内存/读取模型，不写入 Session JSON/JSONL。Context、Tool Calls 与 MCP 检查器结果也不会进入聊天历史或观测账本。

断线失败、重试中的旧消息隐藏、活动簇和 pending draft 都是浏览器标签页状态，不属于 Session 持久化事实。SAVE 的唯一可靠持久化边界不因 Web 重试而改变。

### 8.2 Hook 边界

- 保留 `TurnMonitorHook` 作为轻量 post-turn 扩展点，只补充 `outcome`、`turn_duration_ms`、`session_lock_wait_ms` 和 `tool_failure_count`。
- 已有 Channel/Tool/Compact Hook 仍是工具、压缩状态的 Runtime 事实来源。
- WebChannelAdapter 把已有回调映射成实时 WebSocket 事件；Web 不新增大而全的观测 Hook。
- Hook 不拥有 JSON/JSONL 持久化。Hook 异常隔离不能承担会话事实写入责任。

## 9. 配置

集中配置继续由 `settings.local.json` 管理。Web 控制面可编辑的 allowlist 为：

- `llm`：`base_url`、`model`、timeout/retry、streaming，以及 write-only API Key 替换/清除；`provider` 只读为 `openai-compatible`。
- `runtime`：工具轮数/数量、并行调用、turn timeout、上下文与工具结果 token 上限。
- `memory`：压缩阈值与最近窗口 token。
- `sessions.retention_days`。
- `skills`：每轮加载数量与 token 限额。
- `tool_permissions.approval_required_tools`：只通过 Tool Catalog add/remove。

部署、身份、数据目录、Gateway/Web 与 MCP 连接字段继续只允许本地文件管理。`gateway.auth_token` 由 Gateway 首次启动时自动写入本地配置，不属于 Web allowlist。当前配置示例：

```json
{
  "web": {
    "host": "127.0.0.1",
    "port": 8000,
    "max_concurrent_sessions": 6,
    "event_buffer_size": 512
  },
  "gateway": {
    "host": "127.0.0.1",
    "port": 8000,
    "principal_id": "local-user"
  },
  "tool_permissions": {
    "approval_required_tools": ["write_file", "edit_file", "exec", "write_stdin"],
    "auto_approve_tools": false
  }
}
```

- `web.max_concurrent_sessions` 现在是 Gateway 共享的执行槽位上限，CLI 与 Web 共同使用；排队、等待审批和停止中不占运行槽位。
- `event_buffer_size` 是每 session 的内存事件上限，不落盘。
- 浏览器主题保存在浏览器本地存储，不写入 Agent 配置。
- 未应用设置不写 `localStorage`、Session cache 或 URL；配置重载状态通过 REST 查询，不新增 WebSocket 事件。

## 10. 实施任务

### Task 1：会话与全局审批模型

- [x] 从 `Session`、Session Store、SessionManager 和 Context 中移除会话级 `auto_approve_tools`。
- [x] 在集中 Settings 和可运行时更新的全局审批策略中实现 `auto_approve_tools`。
- [x] 增加 `pinned`、`archived`、标题更新、会话列表筛选、归档恢复和活动状态保护。
- [x] 保持 JSON 文件可读，旧 session 缺失新字段时使用明确默认值。

### Task 2：通用运行控制与 Web 事件基础

- [x] 将 Channel Router 工厂改为接收 `InboundMessage`。
- [x] 为 ChannelAdapter 增加最小 TurnControl；CLI/静默实现无操作版本。
- [x] 让 AgentLoop 在安全检查点消费 guidance 和检查 Stop。
- [x] 扩展 TurnContext/SAVE，以正确保存引导消息和取消回复。
- [x] 增加 EventHub、事件模型、有界 replay、审批 Future 和 session 状态快照。

### Task 3：Web Host 与调度（历史实现记录；现由 Gateway 收敛）

- [x] 创建 FastAPI lifespan、REST API、WebSocket endpoint、异常映射和静态文件托管。
- [x] 创建 WebSessionCoordinator、MessageBus Dispatcher、全局 6 槽位和 per-session worker。
- [x] 创建 WebChannelAdapter，将 Runtime 过程映射为 OutboundMessage/EventHub 事件。
- [x] 原 `python -m Turning-Good-Agent web` Host 已由 `tga gateway` 取代；Web 通过 Gateway lifespan 启动。

### Task 4：React 工作台

- [x] 创建 Vite React TypeScript 工程、类型化 API/WebSocket client 与状态 reducer。
- [x] 实现会话栏、草稿/路由恢复、聊天、任务过程、审批、Stop、guidance 草稿和自动批准按钮。
- [x] 实现会话检查器、token/trace/工具/压缩视图、主题和响应式布局。
- [x] 实现安全 Markdown、键盘操作、错误和空状态；代码块可直接选择和复制。
- [x] 实现断线轮次的浏览器内重试、动作幂等回执、标签页即时状态恢复和 Composer 上下文占用环。

### Task 5：验证、审查与文档

- [x] 验证队列、持久化、静态托管和 WebSocket 首轮实时流；测试目录只作本地验证。
- [x] 完成 React TypeScript 构建与关键交互冒烟。
- [x] 浏览器视觉体验采用本机真实页面、Playwright 实机流程与人工截图审查结合验收，不以静态 CSS 或单一像素位置作为质量结论。
- [x] 依据现有前端设计约束完成实现后审查：使用深/浅主题语义 token、无 UI kit、无远程资产、可访问图标、移动端覆盖层与减少动效支持。
- [x] 同步 README、架构文档、总 Spec、Phase 9 边界和文档索引。

### Task 6：Web 控制面与读取模型

- [x] 将配置规则集中到 `config/validate.py`，并由 CLI、Web Apply、测试与 Runtime bootstrap 复用字段错误。
- [x] 实现 `WebConfigControlService` 的 allowlist 合并、密钥脱敏、完整候选校验、revision 与安全写入。
- [x] 实现 `RuntimeSupervisor` 的全局空闲闸门、replacement readiness、原子发布和失败回退。
- [x] 实现 Command/Tool Catalog、Context/Tool Call/MCP Read Model 与 REST 契约，不新增控制面 WebSocket 或观测文件。
- [x] 实现独立 Settings Workspace、字段编辑、LLM 测试、Tool 审批差异、字段错误和可取消轮询。
- [x] 实现 Slash Catalog、键盘选择、检查器直读动作与内联 Skill/MCP 语义片段。

### Task 7：桌面视觉与可维护性收口

- [x] 使用背景色面、留白和轻阴影统一顶栏、检查器、Slash、设置、弹窗与深浅主题层级，删除普通状态细描边。
- [x] 设置与检查器加载态改为最终几何骨架；检查器改为摘要、分组、记录、原始 JSON 的连续阅读层级。
- [x] 所有产品级滚动区复用 `ScrollArea` 自绘圆角滑块，隐藏原生三角按钮。
- [x] CSS 按组件领域拆分，保留 token、layout 与跨域共享层的单一职责。
- [x] Composer 保留两行稳定高度，纯文本粘贴不继承外部样式；Slash 无匹配残余、键盘滚动与关闭状态完成回归覆盖。

## 11. 验收标准

- `tga gateway` 启动唯一 Gateway 与本机 Web 服务；`tga chat --session ...` 作为 CLI Client 连接后，CLI 行为不回归。`tga web` 不再是有效入口。
- 用户可创建草稿、发送首条消息生成 session、切换/恢复历史、重命名/置顶/归档/恢复/删除会话。
- assistant 文本、工具状态、压缩、审批、Stop 与终态通过 WebSocket 实时显示。
- 断线后能通过 replay 或 snapshot 恢复当前会话；完成后的历史与 JSON 文件一致。
- 断线期间的未完成 Web turn 可显示失败并重试；重试不会创建重复 Runtime turn，成功后 Web 只隐藏被替代的旧展示记录，Session JSON/JSONL 与上下文仍保持完整。
- 运行中 guidance 在安全检查点生效；同一 session 无并发 turn；不同 session 最多 6 个运行。
- Stop 不启动新的 LLM/Tool，等待审批会被拒绝；已输出文本以取消消息保存，未注入 guidance 不丢失也不自动运行。
- 审批卡只显示工具名与标准化参数；全局自动批准策略跨会话生效，安全预检仍不可绕过。
- 会话检查器准确展示既有持久化 trace、tool calls、真实 token、上下文与压缩数据，不新增重复 JSONL。
- Composer 上下文圆环准确读取最近一次 SAVE 后的 `current_context_tokens` 与集中 `max_context_tokens`，没有已保存数据时保持中性状态。
- 设置只提交已修改字段与 Tool 差异，422 错误贴近字段；`pending/applying` 只通过单轮询读取至 `active/failed`，旧 Runtime 在失败时继续可用。
- Command Catalog 只包含 `/context`、`/tools`、有效 Skill 与已连接 MCP；检查器直读不发送消息，Skill/MCP 选择保持可编辑并按原始 `insert_text` 发送。
- 桌面端 1024/1280/1440/1920px 应确认无溢出、遮挡、不可操作控件或缺失焦点；深浅主题、加载态、检查器展开、Slash、设置与弹窗可用。本轮不把新增移动端视觉重做列为验收结论。
- 所有新增类/函数有精简中文注释；`pytest -q`、前端构建、Python 编译、Playwright、`git diff --check` 和 CLI/Web 冒烟通过。Playwright 提供行为与几何回归证据，最终视觉质量仍需结合真实页面审查。

# Turning-Good-Agent Phase 6 Web Channel 与会话观测实施设计

状态：已实现。本文是 Phase 6 的唯一权威设计、实施边界、完成记录与人工验收标准，替代旧版只读 Dashboard 方案。

2026-07-27 工作台视觉系统收口：WebSocket 动作使用 `client_action_id` 关联乐观消息、错误与有限受理回执，前端以历史加载版本闸门、有限退避重连和 `after_event_id` 回放保持会话稳定；会话栏仅按置顶优先排序，三点操作、搜索、重命名和删除确认由 Radix Primitive 处理定位、键盘、外部关闭与焦点回归。工作台采用石墨深浅主题，聊天区与侧栏独立滚动，短 user 消息按内容宽度显示。常态区域以背景层级、留白和克制阴影区分，可点击控件使用统一圆角 token。桌面检查器打开时，中央会话区通过 Grid 为右侧检查器预留宽度，消息列与 Composer 在剩余区域重新布局；平板和移动端改为全屏覆盖阅读。真实事件按 `request_id` 显示在同轮 user 与 assistant 消息之间的可折叠“思考中”活动簇，运行中 guidance 立即显示为 user 消息并由既有 `task.status` 标记“已引导”。输入区显示“默认权限 / 完全访问”菜单，以及只读上下文占用环；后者从最近一次 `SAVE.metadata.current_context_tokens` 与 `runtime.max_context_tokens` 读取，并在悬浮或键盘聚焦时显示读数。底层仍使用全局 `tool_permissions.auto_approve_tools`；检查器先展示累计 token、当前上下文、压缩次数与工具失败，再以结构化条目按需展开原始记录。此收口不新增 JSONL，也不新增 `guidance.consumed`，且“思考中”不表示或暴露模型内部思维链。浏览器视觉体验以本机人工操作验收为准，不将 Playwright 或 Chromium 作为 Phase 6 的验收前置条件。

## 1. 目标与定位

Phase 6 提供本机单用户的 Web Agent 工作台：用户可实时对话、在运行中引导 Agent、审批工具、停止任务、管理会话，并查看单个会话的完整执行追踪。

Web 是 Turning-Good-Agent 的一个 Channel Host，不是第二个 Runtime，也不是独立监控产品。它复用既有 Runtime、Session、Hook、MessageBus、JSON/JSONL 和 MCP 生命周期；业务规则不能复制到 FastAPI 或 React 中。

核心目标是“Codex 的对话优先 + 克制的控制台级过程可见性”：聊天是主工作面，任务过程可折叠，完整观测按需进入会话检查器。

## 2. 已确认范围

### 2.1 本阶段实现

- FastAPI 本机 Web Host、REST API、WebSocket 和 React 单页工作台。
- 会话列表、搜索、新建草稿、恢复、置顶、重命名、归档、恢复归档和删除。
- 当前会话聊天历史、安全 Markdown、流式文本、工具/压缩状态、Stop 和工具审批。
- 同会话串行执行、运行中 guidance queue、跨会话最多 6 个并行任务和全局等待队列。
- 断线重连、每会话有界内存事件缓冲和运行中任务快照。
- 断线中的本轮消息可在浏览器内显示“网络连接失败”并原动作重试；即时消息、活动簇和 Web 端隐藏记录只保留在当前浏览器标签页。
- 会话检查器：trace、工具调用、token、上下文和压缩统计。
- Composer 只读上下文占用环与悬浮读数，复用最近一次保存后的真实上下文统计。
- 全局工具自动批准按钮，持久化到 `settings.local.json`。
- 默认深色与可持久化浅色主题。

### 2.2 明确不实现

- 登录、账号、用户隔离、多租户、云同步、远程部署或公网暴露。
- Web Provider/API Key、模型、MCP Server、Skill 和运行参数设置页。
- 文件上传、图片生成、多模态附件、复杂工具日志、文件 diff。
- 会话分类、标签、手动排序、LLM 自动命名、自动归档。
- 流式 delta 的 JSONL 落盘、重复监控 JSONL、跨服务重启恢复在途任务。
- 微信、飞书 Channel；Phase 9 后续只负责办公软件 Channel。

未来将单独设计 Web 设置中心。届时允许本地编辑 LLM、模型、密钥和 MCP 配置，但必须先定义密钥保护、配置校验、Runtime 热更新与 MCP 生命周期，不在 Phase 6 提供半成品入口。

## 3. 安全与部署边界

- 这是完全本机、单用户、数据不上云的个人 Agent。
- 默认监听 `127.0.0.1`；不提供公网启动说明、认证或跨域开放策略。
- Web 不绕过 `security.py`、`ToolExecutor` 二次预检、Tool 权限 Hook 或 MCP 审批规则。
- FastAPI 不读取或向浏览器返回 LLM API Key、MCP headers、MCP env 等私密配置。
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

### 4.4 工具审批与完全访问

- 审批卡只展示工具名与标准化参数，提供“允许一次”和“拒绝”。
- 输入区左下角的“默认权限 / 完全访问”菜单默认处于默认权限；完全访问影响所有会话的后续审批点。
- 全局状态保存到 `settings.local.json` 的 `tool_permissions.auto_approve_tools`，运行中策略立即更新。
- 已经弹出的审批卡不因打开全局开关而自动通过。
- 删除 `Session.auto_approve_tools`，CLI `/approve on|off` 也修改同一个全局策略，避免双重真相。
- 自动批准不绕过任何硬安全检查。

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

## 5. 系统架构

```text
React + TypeScript + Vite
  ├─ REST: 列表、历史、会话管理、持久化观测读取
  └─ WebSocket: 发送、guidance、Stop、审批、实时事件、重连
          ↓
FastAPI Web Host
  ├─ WebSessionCoordinator
  │   ├─ 每 session 串行 worker / guidance queue / Stop / 审批 future
  │   ├─ 全局最多 6 个运行槽位与等待队列
  │   └─ SessionEventHub（快照、有界 replay buffer、连接订阅）
  └─ AsyncMessageBus
          ↓
Runtime Dispatcher
  └─ AgentRuntime.run_turn(InboundMessage)
          ↓
WebChannelAdapter
  └─ 把 delta、状态、工具、审批、完成/错误转换为 OutboundMessage
          ↓
AsyncMessageBus.outbound -> SessionEventHub -> WebSocket
```

### 5.1 Web Host 的边界

- FastAPI 只做协议适配、连接管理、会话调度与读取模型；不重写 Context、Tool、Memory 或 MCP 逻辑。
- `WebSessionCoordinator` 是 Web 唯一的并发控制位置，不能在 React 或 `AgentRuntime` 外另建 session 锁。
- `runtime.start()` 在 FastAPI lifespan startup 仅调用一次，`runtime.close()` 在 shutdown 仅调用一次。
- CLI 继续使用当前路径；本阶段不为表面统一重写 CLI。

### 5.2 MessageBus 与 Channel Adapter

现有 MessageBus 在 Web Host 中正式启用：Web 请求写入 `inbound`，Runtime Dispatcher 执行 `run_turn()`，所有面向 Web 的输出通过 `outbound` 回到 EventHub。

为使 Web adapter 持有精确的 session/request 关联，`ChannelRouter` 工厂改为接收当前 `InboundMessage`，而不是无参数工厂。CLI 工厂忽略该参数；Web 工厂使用 `session_id`、`InboundMessage.id` 与 EventHub 构造单轮 `WebChannelAdapter`。

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

## 6. WebSocket 与 REST 契约

### 6.1 REST

```text
GET    /api/sessions?archived=false
GET    /api/sessions/{session_id}
GET    /api/sessions/{session_id}/messages
GET    /api/sessions/{session_id}/observability
GET    /api/sessions/{session_id}/context-window
PATCH  /api/sessions/{session_id}             # title / pinned / archived
DELETE /api/sessions/{session_id}
GET    /api/settings/ui                       # 仅主题与自动批准状态
PATCH  /api/settings/ui                       # 仅主题与自动批准状态
```

- `observability` 聚合既有 `session.json`、`turn_traces.jsonl`、`true_token_usage.jsonl` 和 `tool_calls.jsonl`，不创建新文件。
- `context-window` 只读取最近一次 `SAVE.metadata.current_context_tokens`，并返回当前 `runtime.max_context_tokens`；没有已保存轮次时返回中性零值。
- `DELETE` 仅接受终态会话；活动会话返回明确冲突错误。
- `PATCH archived=true` 仅接受终态会话；直接打开归档 URL 可只读，发送前要求恢复。
- `settings/ui` 绝不返回私密 LLM/MCP 字段。

### 6.2 WebSocket 客户端动作

```json
{"type":"session.subscribe","session_id":"...","after_event_id":42}
{"type":"message.send","draft_id":"...","content":"分析这个仓库"}
{"type":"guidance.send","session_id":"...","content":"重点检查鉴权"}
{"type":"task.stop","session_id":"..."}
{"type":"approval.resolve","session_id":"...","approval_id":"...","approved":true}
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
```

- 浏览器重连带 `after_event_id`；EventHub 补发窗口内事件后继续订阅。
- 若事件已过期，服务端发送 `session.snapshot`，前端通过 REST 重新拉取已落盘历史和观测。
- 在途任务只存在内存中；服务重启后不会恢复，前端从持久化终态历史继续。
- Coordinator 对最近有限数量的 `client_action_id` 保留受理回执。浏览器因重连、刷新或切换会话重发同一动作时，复用原 `session_id` 与 `request_id`，不重复启动任务或重复加入 guidance。

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
      styles/              # token、布局、组件、主题
    package.json
    vite.config.ts
  static/                  # 前端构建产物，Git 忽略
```

- 使用 React、TypeScript、Vite、`lucide-react`、安全 Markdown 渲染与本地打包字体。
- 不使用 Tailwind、通用 UI kit、外部 CDN 或远程字体。
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

### 7.5 视觉系统

受众是长期使用本机 Agent 的个人开发者。跨页面通用视觉与交互原则以项目根目录 [`DESIGN.md`](../../DESIGN.md) 为准；本节只记录 Web 工作台的专属实现约束。

- 默认深色，主题切换保存为浏览器本地偏好；浅色与深色分别校验，同一语义使用同一套 Token。
- 使用中性石墨灰阶作为常态表面；蓝色仅表示连接或运行，绿色表示完成，琥珀表示待审批，橙红或红色仅用于完全访问、失败、停止和删除。
- CSS 定义三档基础圆角 token：紧凑控件 `14px`、面板 `18px`、图标与短操作胶囊 `999px`。新增主要组件优先复用这些 token；现有细节样式保留必要的局部圆角值。
- 常态区域优先通过背景层级、留白和克制阴影区分；细边框仅用于输入、代码和高风险操作。
- 阴影只表达可操作性、浮层层级和当前命中元素。父容器、子按钮和图标不得因一次悬停产生叠加阴影。
- 所有图标使用 `lucide-react`。不显示鼠标悬浮说明文字；图标仍保留可访问名称、键盘焦点和正确焦点顺序。
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

#### 本机部署与开发

- 生产入口为根目录 `compose.yaml`：多阶段构建先编译 Vite/React，再以 Python/FastAPI 运行静态 Web；宿主机仅映射 `127.0.0.1:8000`，会话使用命名卷 `/app/.sessions` 持久化，`settings.local.json` 以可写绑定挂载提供本地配置且不进入镜像或 Git。
- 开发入口为 `compose.dev.yaml`：Vite 前端仅向宿主机暴露 `127.0.0.1:8000` 并代理 API/WebSocket 到内部后端；Docker Desktop 绑定挂载通过轮询提供前端 HMR，Python 代码由 Uvicorn reload 重启。依赖、Dockerfile 或锁文件变化时需要带 `--build` 重启。
- 部署不新增公网/局域网暴露、认证、多租户、反向代理或新的 Runtime/协议边界。

#### 已验证边界

- 前端 `pnpm run build` 通过；`web/frontend/tests/workbench_visual.spec.ts` 覆盖空状态与 Composer 间距、浅色侧栏/工作区分层、三个起步示例及深色输入表面，当前为 4 项通过。
- 真实会话验收已检查高密度 Trace、检查器展开、深浅主题、搜索、会话操作菜单和浮层视口边界；`git diff --check` 无差异错误。
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

断线失败、重试中的旧消息隐藏、活动簇和 pending draft 都是浏览器标签页状态，不属于 Session 持久化事实。SAVE 的唯一可靠持久化边界不因 Web 重试而改变。

### 8.2 Hook 边界

- 保留 `TurnMonitorHook` 作为轻量 post-turn 扩展点，只补充 `outcome`、`turn_duration_ms`、`session_lock_wait_ms` 和 `tool_failure_count`。
- 已有 Channel/Tool/Compact Hook 仍是工具、压缩状态的 Runtime 事实来源。
- WebChannelAdapter 把已有回调映射成实时 WebSocket 事件；Web 不新增大而全的观测 Hook。
- Hook 不拥有 JSON/JSONL 持久化。Hook 异常隔离不能承担会话事实写入责任。

## 9. 配置

新增集中配置：

```json
{
  "web": {
    "host": "127.0.0.1",
    "port": 8000,
    "max_concurrent_sessions": 6,
    "event_buffer_size": 512
  },
  "tool_permissions": {
    "approval_required_tools": ["write_file", "edit_file", "exec", "write_stdin"],
    "auto_approve_tools": false
  }
}
```

- `web.max_concurrent_sessions` 只限制正在执行 LLM/Tool 的 session；排队、等待审批和停止中不占运行槽位。
- `event_buffer_size` 是每 session 的内存事件上限，不落盘。
- 浏览器主题保存在浏览器本地存储，不写入 Agent 配置。

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

### Task 3：Web Host 与调度

- [x] 创建 FastAPI lifespan、REST API、WebSocket endpoint、异常映射和静态文件托管。
- [x] 创建 WebSessionCoordinator、MessageBus Dispatcher、全局 6 槽位和 per-session worker。
- [x] 创建 WebChannelAdapter，将 Runtime 过程映射为 OutboundMessage/EventHub 事件。
- [x] 增加 `python -m Turning-Good-Agent web` 命令。

### Task 4：React 工作台

- [x] 创建 Vite React TypeScript 工程、类型化 API/WebSocket client 与状态 reducer。
- [x] 实现会话栏、草稿/路由恢复、聊天、任务过程、审批、Stop、guidance 草稿和自动批准按钮。
- [x] 实现会话检查器、token/trace/工具/压缩视图、主题和响应式布局。
- [x] 实现安全 Markdown、键盘操作、错误和空状态；代码块可直接选择和复制。
- [x] 实现断线轮次的浏览器内重试、动作幂等回执、标签页即时状态恢复和 Composer 上下文占用环。

### Task 5：验证、审查与文档

- [x] 验证队列、持久化、静态托管和 WebSocket 首轮实时流；测试目录只作本地验证。
- [x] 完成 React TypeScript 构建与关键交互冒烟。
- [x] 浏览器视觉体验采用本机人工交互验收；不将 Playwright 或 Chromium 作为验收前置条件。
- [x] 依据现有前端设计约束完成实现后审查：使用深/浅主题语义 token、无 UI kit、无远程资产、可访问图标、移动端覆盖层与减少动效支持。
- [x] 同步 README、架构文档、总 Spec、Phase 9 边界和文档索引。

## 11. 验收标准

- `python -m Turning-Good-Agent web` 启动本机 Web Host；CLI 行为不回归。
- 用户可创建草稿、发送首条消息生成 session、切换/恢复历史、重命名/置顶/归档/恢复/删除会话。
- assistant 文本、工具状态、压缩、审批、Stop 与终态通过 WebSocket 实时显示。
- 断线后能通过 replay 或 snapshot 恢复当前会话；完成后的历史与 JSON 文件一致。
- 断线期间的未完成 Web turn 可显示失败并重试；重试不会创建重复 Runtime turn，成功后 Web 只隐藏被替代的旧展示记录，Session JSON/JSONL 与上下文仍保持完整。
- 运行中 guidance 在安全检查点生效；同一 session 无并发 turn；不同 session 最多 6 个运行。
- Stop 不启动新的 LLM/Tool，等待审批会被拒绝；已输出文本以取消消息保存，未注入 guidance 不丢失也不自动运行。
- 审批卡只显示工具名与标准化参数；全局自动批准策略跨会话生效，安全预检仍不可绕过。
- 会话检查器准确展示既有持久化 trace、tool calls、真实 token、上下文与压缩数据，不新增重复 JSONL。
- Composer 上下文圆环准确读取最近一次 SAVE 后的 `current_context_tokens` 与集中 `max_context_tokens`，没有已保存数据时保持中性状态。
- 桌面与移动端的人工验收应确认无溢出、遮挡、不可操作控件或缺失焦点；主题、键盘、Markdown 与代码复制可用。
- 所有新增类/函数有精简中文注释；`pytest -q`、前端构建、Python 编译、`git diff --check` 和 CLI/Web 冒烟通过。浏览器视觉体验不以 Playwright 作为验收标准。

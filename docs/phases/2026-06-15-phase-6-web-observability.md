# Turning-Good-Agent Phase 6 Web Channel 与会话观测实施设计

状态：已完成。本文是 Phase 6 的唯一权威设计、实施边界、完成记录与验收标准，替代旧版只读 Dashboard 方案。

2026-07-27 工作台视觉系统收口：WebSocket 动作使用 `client_action_id` 关联乐观消息与错误，前端以历史加载版本闸门、有限退避重连和 `after_event_id` 回放保持会话稳定；会话栏仅按置顶优先排序，三点操作、搜索、重命名和删除确认改由 Radix Primitive 处理定位、键盘、外部关闭与焦点回归。工作台采用石墨深浅主题，聊天区与侧栏独立滚动，短 user 消息按内容宽度显示。常态区域以背景层级、留白和克制阴影区分，可点击控件使用统一大圆角，鼠标悬浮不显示说明文字。桌面检查器打开时保持消息列左锚点并收紧右侧安全区，绝不遮挡消息或 Composer；平板和移动端改为全屏阅读。真实事件按 `request_id` 显示在同轮 user 与 assistant 消息之间的可折叠“思考中”活动簇，运行中 guidance 立即显示为 user 消息并由既有 `task.status` 标记“已引导”。输入区显示“默认权限 / 完全访问”菜单，底层仍使用全局 `tool_permissions.auto_approve_tools`；检查器先展示累计 token、当前上下文、压缩次数与工具失败，再以结构化条目按需展开原始记录。此收口不新增 JSONL，也不新增 `guidance.consumed`，且“思考中”不表示或暴露模型内部思维链。当前环境无 Chromium，Playwright 截图验证待后续具备浏览器的环境补充。

## 1. 目标与定位

Phase 6 提供本机单用户的 Web Agent 工作台：用户可实时对话、在运行中引导 Agent、审批工具、停止任务、管理会话，并查看单个会话的完整执行追踪。

Web 是 Turning-Good-Agent 的一个 Channel Host，不是第二个 Runtime，也不是独立监控产品。它复用既有 Runtime、Session、Hook、MessageBus、JSON/JSONL 和 MCP 生命周期；业务规则不能复制到 FastAPI 或 React 中。

核心目标是“Codex 的对话优先 + 克制的控制台级过程可见性”：聊天是主工作面，任务过程可折叠，完整观测按需进入会话检查器。

## 2. 已确认范围

### 2.1 本阶段实现

- FastAPI 本机 Web Host、REST API、WebSocket 和 React 单页工作台。
- 会话列表、新建草稿、恢复、置顶、重命名、归档、恢复归档和删除。
- 当前会话聊天历史、安全 Markdown、流式文本、工具/压缩状态、Stop 和工具审批。
- 同会话串行执行、运行中 guidance queue、跨会话最多 6 个并行任务和全局等待队列。
- 断线重连、每会话有界内存事件缓冲和运行中任务快照。
- 会话检查器：trace、工具调用、token、上下文和压缩统计。
- 全局工具自动批准按钮，持久化到 `settings.local.json`。
- 默认深色与可持久化浅色主题。

### 2.2 明确不实现

- 登录、账号、用户隔离、多租户、云同步、远程部署或公网暴露。
- Web Provider/API Key、模型、MCP Server、Skill 和运行参数设置页。
- 文件上传、图片生成、多模态附件、复杂工具日志、文件 diff。
- 会话搜索、分类、标签、手动排序、LLM 自动命名、自动归档。
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
PATCH  /api/sessions/{session_id}             # title / pinned / archived
DELETE /api/sessions/{session_id}
GET    /api/settings/ui                       # 仅主题与自动批准状态
PATCH  /api/settings/ui                       # 仅主题与自动批准状态
```

- `observability` 聚合既有 `session.json`、`turn_traces.jsonl`、`true_token_usage.jsonl` 和 `tool_calls.jsonl`，不创建新文件。
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

- 左栏固定为会话管理：新建、活跃/置顶/归档分组、状态、菜单操作。
- 中栏是唯一主工作面：聊天、任务过程和输入。
- 右侧检查器默认关闭；会话条目悬停区和当前标题各有图标入口。
- 窄屏将左栏和检查器切换为覆盖层，聊天和输入仍保持可用。

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

受众是长期使用本机 Agent 的个人开发者。界面以高密度、低干扰和可靠状态为优先。

```text
Dark canvas     #111714  墨绿炭黑
Dark surface    #1A231E  深苔工作面
Light canvas    #F3F4EE  雾白纸面
Primary text    #E8ECE4 / #17201B
Signal amber    #E4A84A  审批、运行与注意
Signal teal     #55B9A8  成功、连接与活动
Signal coral    #D96C59  失败、停止与危险操作
```

- 默认深色，主题切换保存为浏览器本地偏好；浅色使用同一语义 token。
- 字体本地打包，正文和数据字体分工明确；不依赖访问者系统或网络字体。
- 唯一标志性元素是“turn rail”：活动任务旁的细信号轨迹按真实状态推进，用于表达 Agent 从思考、工具到完成的过程，不作为装饰动画。
- 只在 turn rail 使用克制动效；尊重 `prefers-reduced-motion`。
- 所有图标使用 `lucide-react`，不显示鼠标悬浮说明，仍保留键盘焦点和可访问标签。

## 8. 持久化、观测与 Hook

### 8.1 唯一事实来源

`SAVE` 继续可靠写入：

- `session.json`：会话元数据、summary、uncompacted history、title、pinned、archived。
- `messages.jsonl`：user/assistant 原始消息；取消回复保留 `incomplete=true` 元数据。
- `turn_traces.jsonl`：各状态 trace、RUN/COMPACT/SAVE/RESPOND metadata。
- `true_token_usage.jsonl`：真实 LLM usage 与会话累计 token。
- `tool_calls.jsonl`：精简工具调用记录。

Web 只读取或通过既有 Store 写入这些事实，不新建 `web_events.jsonl`、`monitor.jsonl` 或流式 delta 日志。

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

### Task 5：验证、审查与文档

- [x] 验证队列、持久化、静态托管和 WebSocket 首轮实时流；测试目录只作本地验证。
- [x] 完成 React TypeScript 构建与关键交互冒烟。
- [ ] Playwright 截图检查受当前环境未安装浏览器影响，留待具备 Chromium 的环境执行。
- [x] 依据现有前端设计约束完成实现后审查：使用深/浅主题语义 token、无 UI kit、无远程资产、可访问图标、移动端覆盖层与减少动效支持。
- [x] 同步 README、架构文档、总 Spec、Phase 9 边界和文档索引。

## 11. 验收标准

- `python -m Turning-Good-Agent web` 启动本机 Web Host；CLI 行为不回归。
- 用户可创建草稿、发送首条消息生成 session、切换/恢复历史、重命名/置顶/归档/恢复/删除会话。
- assistant 文本、工具状态、压缩、审批、Stop 与终态通过 WebSocket 实时显示。
- 断线后能通过 replay 或 snapshot 恢复当前会话；完成后的历史与 JSON 文件一致。
- 运行中 guidance 在安全检查点生效；同一 session 无并发 turn；不同 session 最多 6 个运行。
- Stop 不启动新的 LLM/Tool，等待审批会被拒绝；已输出文本以取消消息保存，未注入 guidance 不丢失也不自动运行。
- 审批卡只显示工具名与标准化参数；全局自动批准策略跨会话生效，安全预检仍不可绕过。
- 会话检查器准确展示既有持久化 trace、tool calls、真实 token、上下文与压缩数据，不新增重复 JSONL。
- 桌面与移动端无溢出、遮挡、不可操作控件或缺失焦点；主题、键盘、Markdown 与代码复制可用。
- 所有新增类/函数有精简中文注释；`pytest -q`、前端检查、Playwright、`git diff --check` 和 CLI/Web 冒烟通过。

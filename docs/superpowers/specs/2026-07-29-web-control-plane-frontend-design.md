# Web Control Plane Frontend Design

状态：已确认视觉与交互方向，待用户审阅后实施。本文件只定义 Phase 6 Web 工作台如何消费既有控制面 REST 契约；不定义或变更任何后端规则。

## 1. 目标与边界

在不改变聊天工作面的前提下，为本机单用户提供：

1. 一个独立的设置工作面，用于所有可编辑配置的浏览器本地修改、LLM 连通性测试和一次应用。
2. 一个仅位于 Composer 上方的 Slash Command Catalog，用于 `/context`、`/tools`、Skill 与已连接 MCP Server 的选择。
3. 对 Context、持久化 Tool Calls 和 MCP 状态的按需检查器阅读入口。

实现必须遵守 `DESIGN.md`：聊天是默认唯一主工作面；设置是按需切换的独立工作面；背景层级与留白优先于描边和卡片堆叠；长内容拥有独立滚动；动画只表达真实可见性、尺寸或状态变化。

前端不得：

- 修改 Runtime、MCP 生命周期、Session 命令、Tool 规则或 WebSocket 协议；
- 在浏览器持久化 API Key、未应用配置、Tool 审批名称或任何控制面结果；
- 将 Composer 的全局自动审批开关混入设置页的延迟 Apply；
- 将 `/context`、`/tools`、LLM 测试结果或 Tool Catalog 内容写入聊天流。

## 2. 信息架构

### 2.1 两个主工作面

`chat` 是默认视图，保持现有会话侧栏、聊天时间线、Composer 和按需右侧检查器。左下角新增具文字与图标的“设置”入口。

点击设置后切换到 `settings` 视图。为不修改 FastAPI 静态路由，视图状态使用浏览器 Hash，例如 `/#settings`；“返回聊天”恢复之前的会话 URL。刷新设置页不会恢复未应用编辑，符合控制面契约。

设置页是完整工作面，不是覆盖聊天内容的抽屉或模态框：

```text
┌──────────设置导航──────────┬──────────当前配置主区──────────┐
│ ← 返回聊天                 │ 当前配置                       │
│                            │ 状态与 revision                │
│ 当前配置 [当前项]          │ 模型连接                        │
│                            │ Runtime 限制                    │
│                            │ 记忆、会话与 Skill              │
│                            │ 工具权限                        │
│                            │                                 │
│                            │ [测试连接]       [应用配置]      │
└───────────────────────────┴─────────────────────────────────┘
```

左栏不拆分模型、Runtime、记忆或权限等二级导航。所有可修改项统一属于唯一的“当前配置”分区；内容分组只用于表单扫描和可访问的标题层级。

### 2.2 设置主区

主区使用一条窄而稳定的阅读列，桌面宽度约 720-820px；页面背景、导航背景、分组表面和可编辑控件使用现有三层 surface token。每个字段行显示标签、必要的简短说明及编辑控件，字段错误紧随控件下方。分组之间使用留白与表面变化，而不对每行堆叠描边。

按以下顺序呈现：

1. **模型连接**：只显示 `provider`、API Key 已配置状态以及契约允许编辑的 LLM 字段。Key 永不回显；替换与清除使用明确的写入意图。
2. **Runtime 限制**：显示所有 `runtime` 标量字段，保持数值单位和范围说明。
3. **记忆、会话与 Skill**：连续呈现 `memory`、`sessions.retention_days` 与 `skills` 的可编辑字段。
4. **工具权限**：从实时 Tool Catalog 构建审批列表，不允许手工输入 Tool 名称。

只读 MCP 连接概况属于检查器 MCP 阅读入口，不在“当前配置”中伪装成可配置项。

### 2.3 固定应用栏

设置主区底部固定操作栏只承载真实控制面状态：

- `active`：显示已生效 revision；
- `pending`：说明保存成功，等待所有 Web turn 结束；
- `applying`：禁用重复应用，显示正在替换 Runtime；
- `failed`：保留编辑结果和服务端安全错误，明确旧 Runtime 仍可用。

“测试连接”只提交当前 LLM 候选字段至 `POST /api/control/config/test-llm`，测试结果显示为就地成功、失败或延迟读数。“应用配置”只在存在本地差异且无本地请求进行时可用；提交 `POST /api/control/config/apply`，成功后以返回的 desired 配置替换浏览器基线并清空草稿。HTTP 422 的 `field_errors` 必须定位到对应字段，不能仅显示全局通知。

控制面不增加 WebSocket 状态事件。Apply 响应或后续读取出现 `pending`、`applying` 时，`ConfigEditor` 启动一个单例、可取消的 `GET /api/control/config` 轮询；页面只保留最新请求结果。轮询在读取到 `active` 或 `failed` 时停止，并在离开设置页、组件卸载、发起新的 Apply 或请求失败后清理。请求失败显示可重试的读取错误，不伪造 Runtime 已生效；用户重试后从最新配置重新开始状态读取。

关闭、刷新、返回聊天或切换会话时，未应用编辑直接丢弃。前端不显示“草稿已保存”或任何虚假持久化提示。

## 3. Tool Catalog 与权限编辑

设置页请求 `GET /api/control/tools`，把当前 `tools` 渲染为可搜索的连续列表。每项显示已有 Tool 名、描述、核心/MCP 来源和有效审批状态；它不展示 Tool schema、实现细节或密钥。

- 可见 Tool 的审批交互只累计浏览器内的最终差异。
- “需要审批”移除会产生 `approval_required_tools.remove`，新增产生 `.add`。
- `auto_approve_tools` 只由 Composer 的“默认权限 / 完全访问”菜单通过 `/api/settings/ui` 即时提交；它不属于 Config Apply 请求。
- `unavailable_approval_required` 单独显示为“不可用，仅可移除”，既不能重新添加，也不作为普通 Tool 卡片出现。

高风险原生 Tool 移除审批要求时可显示低干扰的前端风险说明，但不额外要求确认，也不更改后端验证规则。

## 4. Composer Slash Command Catalog

Slash Command Catalog 与 Tool Catalog 是不同数据源和不同用途：前者来自 `GET /api/control/commands`，只在 Composer 的输入内容以 `/` 开始且命令面板新打开时读取；后者只服务设置页的审批编辑。

命令面板固定锚定在 Composer 上缘、向上展开，并受 Composer 最大宽度限制。它不遮挡输入、发送或停止按钮。面板呈现为单个背景层，使用列表高亮而非一组独立卡片；按 `kind` 使用不同的 Skill/MCP 图标，并仅在名称碰撞时显示来源标签。

行为严格由 Catalog 条目 `action` 决定：

| 条目 | 前端行为 |
| --- | --- |
| `/context` | 清除 slash 文本，打开右侧检查器 Context 分区，读取 Context Read Model，不发送 WebSocket 消息。 |
| `/tools` | 清除 slash 文本，打开右侧检查器 Tool Calls 分区，读取首个 cursor 页，不发送 WebSocket 消息。 |
| Skill | 以 `insert_text` 替换 slash 文本，保留输入焦点。 |
| 已连接 MCP Server | 以 `insert_text` 替换 slash 文本，保留输入焦点。 |

浏览器不复制 `/history`、`/new`、`/clear`、`/exit`、`/approve` 或 `/help` 规则；这些项目不会被渲染。选择 Skill/MCP 只改变可编辑 Composer 文本，不留下元数据、Trace 或会话消息。

## 5. 检查器阅读入口

右侧检查器保持会话专属和按需打开，不成为设置页的常驻第二工作面。

- **Context**：读取 `GET /api/control/sessions/{session_id}/context`，先显示摘要、历史数量和 token breakdown；未压缩消息逐层展开。
- **Tool Calls**：读取 `GET /api/control/sessions/{session_id}/tool-calls`，默认只加载最新页；“加载更多”使用服务端 cursor，运行中 Tool 仍由既有活动簇展示。
- **MCP**：读取 Server 列表与详情，只显示契约允许的经脱敏状态、Catalog 摘要与启用 Tool 名。

检查器的空、加载与错误状态都位于对应分区中。原始 JSON 只在记录详情中展开，不与摘要争夺视觉优先级。

## 6. 组件与状态边界

前端实现保持既有 React/Vite 架构，并新增下列聚合边界：

- `SettingsWorkspace`：Hash 视图、加载/不可用状态和返回聊天。
- `ConfigEditor`：浏览器本地基线、字段差异、测试、应用、字段错误与可取消的空闲重载状态轮询。
- `ToolPermissionEditor`：仅消费 Tool Catalog 并输出 add/remove 差异。
- `SlashCommandMenu`：按面板生命周期读取 Command Catalog，处理选择但不实现命令规则。
- `ControlInspectorSections`：按需读取 Context、Tool Calls 与 MCP Read Models。
- `api.ts`：只增加 REST DTO 与请求函数；不修改 WebSocket client。

所有草稿状态位于 `App` 或 SettingsWorkspace 的 React state。任何设置工作面卸载都会自然销毁草稿。会话缓存继续只缓存聊天与实时事件，不扩展为控制面缓存。

当控制面后端尚未部署、返回 404 或网络失败时，设置页显示明确的“控制面暂不可用”内容和重试入口；不会调用旧接口或伪造成功状态。

## 7. 视觉与无障碍约束

- 复用 `DESIGN.md` 定义的 graphite 双主题、低饱和语义色、三档圆角和连续信息面。
- 使用现有 `lucide-react` 图标族，不新增第三方视觉依赖，也不手绘 SVG。
- 仅对设置页进入/返回、命令面板显隐和真实请求状态使用短时过渡；尊重 `prefers-reduced-motion`。
- 表单标签置于控件上方，错误位于控件下方；每个图标按钮具备可访问名称和键盘焦点。
- 设置页与聊天页各自拥有独立的可预测滚动容器。窄视口下设置导航折叠为顶部返回与分区触发器，内容列变为单列；Composer 命令面板仍向上展开并限制在可视区域内。

## 8. 验收场景

1. 深浅主题下，进入/退出设置不破坏原会话路由、聊天滚动或未发送消息。
2. 修改不同字段、同一字段、Tool 审批 add/remove 后，只有点击应用才请求 Apply；422 错误准确显示。
3. Apply 返回或读取到 `pending`、`applying` 后，仅轮询 `GET /api/control/config`；在 `active`、`failed`、离开设置页或请求错误时停止，且不会等待或伪造 WebSocket 事件。
4. LLM 测试成功、422 和 502 均不改变配置基线或聊天记录。
5. Tool Catalog 仅允许从实时 Tool 项创建 add；断线 MCP 遗留项仅可 remove。
6. 输入 `/` 仅出现 Catalog 宣告的条目；`/context` 和 `/tools` 不调用 `message.send`，Skill/MCP 插入文本可继续编辑。
7. Context、Tool Calls 与 MCP 详情在检查器中按需加载，长记录独立滚动、分页稳定。
8. 控制面 REST 不可用时，设置页给出可恢复错误状态且不使用旧审批接口。

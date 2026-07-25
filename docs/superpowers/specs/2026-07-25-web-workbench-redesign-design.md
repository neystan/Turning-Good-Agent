# Web Workbench Interaction Redesign

状态：已完成。Playwright 截图验证待具备 Chromium 的环境补充。

## 目标

在不修改 FastAPI、Runtime、Memory、Tool、MCP、Session JSON/JSONL 或既有 REST/WebSocket 协议的前提下，将 Phase 6 Web 工作台改造为对话优先、本机单用户使用的 Agent 工作台。

本次以 nanobot WebUI 的交互完成度为参考，但不迁移其技术栈、功能范围或模型 reasoning 展示。重点解决会话菜单裁切、聊天滚动跳动、检查器挤压主对话、短消息气泡宽度、权限交互与任务过程可读性。

## 范围与边界

### 包含

1. 侧栏 Portal 会话菜单、受控弹层、会话搜索与会话状态位。
2. 独立聊天滚动、会话滚动位置记忆、仅在合适场景自动跟随与“有新消息”按钮。
3. 短 user 消息内容宽度气泡、无卡片 assistant Markdown 正文和低干扰消息操作。
4. 基于真实事件的 `ActivityCluster`，替代当前 `ThinkingTrail`。
5. 与消息列对齐的 Composer、紧凑权限选择、统一 Stop 与危险操作对话框。
6. 深浅主题 token、可访问性、减少动态偏好、移动端与流式 Markdown 性能收口。

### 不包含

1. 不展示、保存、推断或伪造模型 reasoning 或思维链。
2. 不新增 Runtime 状态、JSONL、WebSocket 事件、后端持久化或第二套观测数据。
3. 不引入图片生成、CLI App 市场、MCP 预设管理、设置中心或其他 nanobot 扩展功能。
4. 不迁移到 Tailwind、Radix 或大型 UI Kit；保留 React、TypeScript、Vite、lucide-react 与现有原生 CSS 架构。

## 信息架构

页面维持三个独立层：左侧会话栏、中央对话与右侧检查器。

1. 侧栏拥有自己的纵向滚动，不影响对话滚动。
2. 中央对话拥有自己的 `ChatViewport`，消息列和 Composer 共享最大宽度。
3. 检查器在桌面端覆盖右侧，不改变中央消息列坐标；只在 Composer 自身预留可见的操作空间。
4. 菜单、对话框与移动端抽屉统一通过 `OverlayPortal` 渲染到 `document.body`，不受侧栏 `overflow`、`content-visibility` 或层叠上下文裁切。

## 组件与职责

| 组件 | 职责 |
| --- | --- |
| `OverlayPortal` | 统一处理 Portal 根节点、视口边界定位、外部点击与 Escape 关闭。 |
| `SessionSidebar` | 会话列表、固定状态位、侧栏操作、归档折叠与搜索入口。 |
| `SessionSearchDialog` | 搜索会话、键盘上下选择、Enter 打开与空态展示。 |
| `ChatTimeline` | 记忆每个会话滚动位置、自动跟随、未读提示与消息列渲染。 |
| `ActivityCluster` | 按 turn 聚合真实任务事件，呈现可折叠的执行过程。 |
| `Composer` | 文本输入、运行中 guidance、Stop、全局审批策略与归档恢复。 |
| `SessionInspector` | 继续只读取已有观测数据，不承担任务过程展示。 |
| `NoticeRegion` | 显示可访问的非阻塞错误与操作反馈。 |

不重写 `SessionState`、`SessionSocketClient`、`SessionHistoryLoader` 或后端协调器；组件只消费其已有会话、turn 和事件状态。

## 会话与浮层交互

### 侧栏

1. 会话按置顶优先、更新时间倒序展示，不额外分出“置顶”区域。
2. 标题始终左对齐；右侧固定放置置顶、运行中或完成状态标记，避免状态变化造成标题横向跳动。
3. 三点菜单默认弱化，仅在 hover、键盘聚焦或当前行显示。
4. 菜单通过 `createPortal(document.body)` 和 `position: fixed` 定位。打开时根据触发按钮的 `DOMRect` 修正右侧与底部溢出。
5. 点击外部、Escape、切换会话或执行操作后关闭菜单。菜单不得影响主对话的点击、滚动与布局。
6. 删除、重命名和归档恢复均使用应用内 Portal 对话框，不调用浏览器原生确认框。

### 搜索

1. 搜索入口位于侧栏顶部操作区。
2. 搜索使用受控对话框，不在长侧栏中常驻输入框。
3. 打开后聚焦输入框；支持上下键、Enter、Escape 和鼠标悬浮高亮。
4. 展示标题、可选预览及当前会话标记；处理加载、无会话和无结果状态。

## 聊天与滚动

1. 每个 session 维护独立 `scrollTop`。
2. 切换会话时恢复该会话原位置；新草稿从顶部开始。
3. 只有以下情况自动滚动到最新消息：用户刚发送消息、当前本就在底部附近、显式点击“有新消息”。
4. 用户上滚阅读时，新流式内容不得抢夺滚动位置，只显示“有新消息”按钮。
5. Composer 高度变化时，使用 `ResizeObserver` 保持消息视口稳定。
6. user 消息采用 `inline-block`、`fit-content` 和消息列最大宽度；assistant 保持无卡片 Markdown 正文。长文本必须正常换行，不产生水平滚动。
7. assistant 消息完成后才显示复制、延迟等低优先级操作。

## 真实任务过程

### 事件边界

`ActivityCluster` 只显示已有 `TaskEvent` 可证明的内容：

1. 排队、运行、已引导。
2. 本地工具、MCP 工具与 Skill 加载。
3. 审批请求与审批结果。
4. 上下文压缩。
5. Stop、完成、失败或取消。

不展示模型 reasoning、工具完整输出、虚构的规划步骤或事件之外的执行状态。

### 显示规则

1. 运行中标题为 `思考中 · 已用 N 秒`，仅使用旋转标识和文字细光效表示活动状态。
2. 标题会优先显示最新真实动作，例如“正在调用 MCP”“正在加载 Skill”“正在整理上下文”。
3. 运行中默认展开；完成、失败或停止后短暂显示终态，再自动收拢为状态、工具数和耗时摘要。
4. 展开后用细时间线表示步骤，步骤区有受限最大高度和独立滚动。
5. 仅当用户停留在步骤区底部时自动跟随后续事件。
6. 审批卡作为时间线的当前操作呈现，仅展示工具名、规范化参数、允许一次与拒绝。

## Composer、审批与检查器

1. Composer 的最大宽度始终与中央消息列一致。
2. 右侧检查器打开时，中央消息不移动；Composer 单独在桌面宽度下收窄，发送或 Stop 按钮必须可见。
3. 输入框自动增高且有最大高度；Enter 发送，Shift+Enter 换行。
4. 运行中可继续发送 guidance；发送成功后立即以 user 消息显示，并由已有 `task.status` 显示“已引导”。
5. 发送按钮和 Stop 共享固定位置，避免运行状态切换造成布局跳动。
6. 自动批准是紧凑菜单：`默认审批` 与 `完全访问权限` 两项；后者明确写出“仍受安全检查限制”。已出现的审批卡不自动通过。
7. 检查器只展示完整工具结果、trace、token、上下文与压缩统计；其内容不回流主聊天区。

## 视觉与可访问性

1. 默认深色、可切换浅色，选择存入 `localStorage`；两套主题共享语义 token。
2. 色彩为低饱和中性灰阶：蓝色仅表示连接或运行，绿色表示完成，琥珀表示待审批，红色仅用于危险操作与失败。
3. 使用 6--8px 圆角、弱边框与克制阴影；不使用装饰性渐变、浮夸卡片或大面积状态色。
4. 所有图标按钮必须包含 tooltip、`aria-label` 和 `:focus-visible` 状态。
5. Toast 与连接状态使用 `aria-live`；对话框具有正确的焦点约束与 Escape 关闭。
6. 所有动效仅变换 `opacity`、`transform` 或旋转，并在 `prefers-reduced-motion` 下静止或简化。
7. 侧栏、抽屉、对话框与检查器设置独立滚动和 `overscroll-behavior: contain`；移动端支持安全区域。
8. 流式 Markdown 使用 80--200ms 批量刷新，避免每个 token 都触发 Markdown 重渲染。

## 验证

1. 本地前端单测覆盖 Portal 菜单关闭与定位、搜索键盘选择、活动事件归类、自动跟随与未读提示。
2. `npm run build`、`pytest -q`、Python `compileall`、CLI `/exit`、REST/WebSocket 冒烟和 `git diff --check`。
3. 具备 Chromium 时运行 Playwright：桌面和移动端截图检查菜单层级、长侧栏、检查器、短消息、Composer、审批、Stop、搜索与主题。
4. 最后按 Web Interface Guidelines 与 frontend-design-review 复核，修复实际发现的问题。

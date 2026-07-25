# Web Workbench Redesign Implementation Plan

Goal: 将本机 Web 工作台重构为主题完整、滚动稳定、按 turn 展示真实执行步骤的对话优先界面。

Architecture: 保留 FastAPI、Runtime、Session JSON/JSONL 与 EventHub。FastAPI 只补 WebSocket action 关联字段和错误回包；React 使用状态模块管理历史加载、连接重试、滚动位置和 turn reducer，组件只渲染其职责范围的数据。前端以语义 CSS token 实现完整深浅主题。

Tech Stack: Python 3.13、FastAPI、asyncio、React、TypeScript、Vite、lucide-react、react-markdown、Playwright。

## Global Constraints

- 所有新增或修改函数使用精简中文注释。
- 不修改 Agent Runtime、Memory、Tool、MCP、Session JSON/JSONL 格式，不新增持久化文件。
- 不新增 guidance.consumed 事件；引导成功发送后只在当前 turn 显示“已引导”。
- “思考中”仅表示任务仍在执行，不渲染或推断模型内部推理。
- 默认深色、可切换完整浅色主题，并持久化最后选择。
- 权限 UI 只有“默认权限”和“完全访问权限”；完全访问权限仍不能绕过安全预检。
- tests 只作本地验证，绝不暂存、提交或上传。
- 不提交 web/static、node_modules、本地配置、虚拟环境或会话数据。

## File Map

- Modify: Turning-Good-Agent/web/backend/app.py。回显 WebSocket client_action_id，并将动作错误回包而非关闭连接。
- Modify: web/frontend/src/App.tsx、api.ts、types.ts、main.tsx、styles.css。移除集中式 UI 代码，接入新状态和样式入口。
- Create: web/frontend/src/state/session_state.ts。历史加载版本、turn reducer、滚动位置、pending action 与通知状态。
- Create: web/frontend/src/state/socket_client.ts。有限退避重连、after_event_id 重订阅、显式 close。
- Create: web/frontend/src/components/SessionSidebar.tsx、ChatTimeline.tsx、ThinkingTrail.tsx、Composer.tsx、SessionInspector.tsx、NoticeRegion.tsx。
- Create: web/frontend/src/styles/tokens.css、layout.css、components.css。
- Create locally only: tests/test_web_host.py、web/frontend/e2e/workbench.spec.ts。

## Task 1: WebSocket Action Correlation

- [ ] 写本地 FastAPI/WebSocket 测试：空 message.send 带 client_action_id 时返回 error、相同 action ID；首条草稿成功时返回 message.accepted、真实 session_id、request_id 和 action ID；归档会话拒绝但连接保持可用。
- [ ] 运行该测试，确认当前 endpoint 没有回显 action ID 且异常路径不符合预期。
- [ ] app.py 读取 action 的可选 client_action_id；message.send、guidance.send、task.stop、approval.resolve 捕获 ValueError 和 RuntimeError，返回 error event；成功消息返回 message.accepted，替代旧 session.created 回包。
- [ ] 运行 tests/test_web_host.py，预期全部通过。
- [ ] 仅提交 Turning-Good-Agent/web/backend/app.py，提交信息：fix: correlate web socket actions。

## Task 2: Session State and Reconnect

- [ ] 写本地 reducer 断言：匹配的 action error 只标记对应 optimistic 消息失败；tool event 只进入同 request_id 的 turn。
- [ ] types.ts 定义 ConnectionState、PendingAction、TurnState；TurnState 包含 requestId、状态、事件、guidanceCount 和开始时间。
- [ ] api.ts 定义 ApiError，保存 REST status 与后端 detail。
- [ ] session_state.ts 按 request_id 建立 turn；task.status 内容“已加入运行中引导”增加 guidanceCount，映射为“已引导”，不添加新事件。
- [ ] socket_client.ts 按 250ms、500ms、1s、2s、4s、最多 5s 重连；成功后按最后 event ID 重订阅当前会话；close 取消计时器。
- [ ] 构建前端并提交 types.ts、api.ts 和 state 目录，提交信息：feat: add resilient web session state。

## Task 3: Navigation, Scrolling and Notices

- [ ] 本地 Playwright 用例延迟会话 A 历史响应，先选择 A 再选择 B，断言 B 不被 A 覆盖；向上滚动后接收新消息，断言不自动回底部。
- [ ] App.tsx 的历史加载使用 AbortController 和递增版本号；仅最新 session 的响应可更新状态；终态 WebSocket event 不再全量 reload 历史。
- [ ] ChatTimeline 为每个会话保存 scrollTop；距底部 96px 内或用户发送才自动滚动；其他情况显示“有新消息”按钮。
- [ ] SessionSidebar 删除置顶分组，保持 pinned-first 排序；标题右侧固定 28px 标记位，置顶才显示 Pin。
- [ ] 三点菜单使用 openMenuId；点击外部、Escape、选择会话和完成动作都关闭；重命名使用受控弹层；错误进入 NoticeRegion 的 aria-live 区域。
- [ ] 构建前端并提交 App.tsx、SessionSidebar.tsx、ChatTimeline.tsx、NoticeRegion.tsx，提交信息：feat: improve web session navigation。

## Task 4: Thinking Trail and Composer

- [ ] ThinkingTrail 根据真实事件显示步骤：已引导、正在调用工具、MCP server/tool、Skill name、等待你的批准、正在整理上下文、已完成、失败、已停止。
- [ ] 运行状态标题固定为“思考中”并显示动态点；终态折叠为“状态、工具数、耗时”；动态点只使用 opacity 和 transform，减少动效模式下静止。
- [ ] ChatTimeline 将每个 turn 放在对应 assistant 回复下方；运行中默认展开，完成后折叠；完整工具结果只留在检查器。
- [ ] 发送 guidance 后立即写入 user optimistic 消息和当前 turn 的“已引导”；message.accepted 标记 sent，关联 error 标记 failed 并提供重试。
- [ ] Composer 采用输入在上、工具栏在下；左侧为附件入口和手掌图标权限下拉，右侧为发送或 Stop。下拉只有默认权限和完全访问权限，后者显示“仍受安全检查限制”。归档会话显示“恢复并继续”。
- [ ] 审批条紧贴 ThinkingTrail，仅显示工具名、参数摘要、允许一次与拒绝。
- [ ] 构建前端并提交 ChatTimeline.tsx、ThinkingTrail.tsx、Composer.tsx，提交信息：feat: render web turns and permissions。

## Task 5: Themes, Layout and Inspector

- [ ] tokens.css 定义深色石墨 token：#0f1115、#171a21、#222734、#6ea8fe、#e7b45b、#e06c75、#eef1f6、#9aa4b5，并定义完整浅色对应 token；组件不硬编码颜色。
- [ ] layout.css 将 app-shell 固定为 100dvh 且 overflow hidden；sidebar、conversation、inspector 使用 min-height 0 和独立纵向滚动；移动抽屉加入 safe area 与 overscroll containment。
- [ ] SessionInspector 顶部展示累计 input/output token、当前上下文、压缩次数、工具失败数；下方 token、压缩、工具与 trace 分区默认展示摘要、按需展开 JSON。
- [ ] Composer、会话侧栏、步骤、审批、通知和抽屉使用 components.css；保留所有图标 tooltip、可访问名称、焦点状态与可读长文本。
- [ ] 本地 Playwright 断言主题刷新后保持、长左栏不影响主聊天滚动、移动端无横向页面滚动、检查器先展示摘要。
- [ ] 构建前端并提交主题、布局、检查器、App.tsx 和 main.tsx，提交信息：feat: redesign web workbench interface。

## Task 6: Verification and Documentation

- [ ] 本地 Playwright 覆盖主题、长左栏、切换竞态、外部点击关闭菜单、发送、已引导、审批、Stop、归档恢复、检查器、重连和移动视口。
- [ ] 运行 pytest -q、前端构建、Python compileall、CLI exit 冒烟和 git diff check。
- [ ] 在具备 Chromium 的环境运行 Playwright 截图，确认无主页面滚动条、遮挡和残留菜单。
- [ ] 使用 web-design-guidelines 与 frontend-design-review 审查完成后的 UI。
- [ ] 同步 README、PROJECT_ARCHITECTURE、TURNING_GOOD_AGENT_SPEC、Phase 6 文档和设计文档，明确没有新 JSONL、没有 guidance.consumed，思考中不是思维链。
- [ ] 只提交源码与文档；确认 git status 不含 tests、构建产物、本地配置或会话数据后推送。

## Self-Review

- 深浅主题、Composer、权限菜单、独立滚动、置顶标记、菜单关闭、稳定滚动、引导、按 turn 思考步骤、检查器和移动端均有任务覆盖。
- 只有 Task 1 修改后端，且不改变 Runtime 或持久化边界。
- 本地测试均明确不提交。

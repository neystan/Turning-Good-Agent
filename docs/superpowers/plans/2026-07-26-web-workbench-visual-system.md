# Web Workbench Visual System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 TGA Web 改造成统一、可信且不遮挡对话内容的本机 Agent 工作台，并以 Radix Primitive 收口浮层与审批交互。

**Architecture:** 保留 React、TypeScript、Vite、既有 REST/WebSocket、会话状态和原生 CSS。Radix 只处理菜单、对话框、确认框、Tooltip 与 Switch 的语义和焦点行为，视觉继续由 TGA Token 与布局控制。活动簇和检查器只读取现有消息、TaskEvent 与观测记录。

**Tech Stack:** React、TypeScript、Vite、原生 CSS、lucide-react、Radix DropdownMenu、Dialog、AlertDialog、Tooltip、Switch。

## Global Constraints

- 只修改 Web 前端和相关文档，不修改 Runtime、Session、Memory、Tool、MCP、REST、WebSocket 或 JSON/JSONL。
- 保留 `lucide-react` 为唯一图标库；不引入 Tailwind、shadcn、Fluent、Material 或完整 UI Kit。
- 每个新增或修改函数必须带精简中文注释。
- `tests/` 与 `web/frontend/e2e/` 只用于本地验证，绝不暂存、提交或上传。
- 不提交 `web/static/`、`web/frontend/node_modules/`、`.venv/`、`settings.local.json` 或 `.sessions/`。
- 不修改、暂存或提交 `jump-jump.html` 与中文命名的无关 HTML 文件。
- 不展示、推断、保存或伪造模型 reasoning；活动簇只使用已有 `TaskEvent`。
- 文档目录受忽略规则影响，相关文档须使用 `git add -f`。

## File Map

- `web/frontend/package.json`、`package-lock.json`：新增最小 Radix 依赖。
- `web/frontend/src/components/IconTooltip.tsx`：统一图标按钮 Tooltip。
- `SessionSidebar.tsx`、`SessionSearchDialog.tsx`：迁移菜单、重命名、删除和搜索浮层。
- 删除 `OverlayPortal.tsx`、`overlay_position.ts`：移除手写 Portal 与文档级监听。
- `Composer.tsx`：以 Radix Switch 表达工具审批策略。
- `state/timeline_entries.ts`：将消息和 turn 以真实顺序交错为可渲染条目。
- `state/observability_view.ts`：格式化统计数字并构建结构化观测摘要。
- `ActivityCluster.tsx`、`ChatTimeline.tsx`、`SessionInspector.tsx` 与 `tokens.css`、`layout.css`、`components.css`：完成视觉与布局收口。
- 本地测试：`tests/web_timeline_entries.test.mjs`、`tests/web_observability_view.test.mjs`、`web/frontend/e2e/visual-system.spec.ts`。

---

### Task 1: 接入 Radix 与图标提示

**Files:** `package.json`、`package-lock.json`、`App.tsx`、`IconTooltip.tsx`、`components.css`，本地 `visual-system.spec.ts`。

**Interfaces:** `IconTooltip({ label, children }: { label: string; children: React.ReactElement })`；`App` 只注入一个 Radix `TooltipProvider`。

- [ ] 写失败浏览器用例：hover 名为“切换主题”的按钮，断言 `role="tooltip"` 的“切换主题”可见。
- [ ] 运行 `cd web/frontend && npx playwright test e2e/visual-system.spec.ts -g "主题提示"`，预期失败，因为当前只有原生 `title`。
- [ ] 运行 `cd web/frontend && npm install @radix-ui/react-alert-dialog @radix-ui/react-dialog @radix-ui/react-dropdown-menu @radix-ui/react-switch @radix-ui/react-tooltip`。
- [ ] 用 Radix `Tooltip.Root` 与 `Tooltip.Trigger asChild` 实现 `IconTooltip`，设置 `sideOffset={6}`，以精简中文注释解释保留原按钮为触发器。
- [ ] 迁移主题、检查器、移动侧栏、搜索、会话操作、消息复制、关闭类图标按钮；保留 `aria-label`，移除 `title`，文本按钮不包 Tooltip。
- [ ] 运行 `cd web/frontend && npm run build`，预期通过。
- [ ] 提交源码：`git add web/frontend/package.json web/frontend/package-lock.json web/frontend/src/App.tsx web/frontend/src/components/IconTooltip.tsx web/frontend/src/components/SessionSidebar.tsx web/frontend/src/components/SessionInspector.tsx web/frontend/src/components/ChatTimeline.tsx web/frontend/src/components/NoticeRegion.tsx web/frontend/src/styles/components.css && git commit -m "feat: add web interaction primitives"`。不暂存 E2E 用例。

### Task 2: 迁移会话菜单、搜索与危险操作浮层

**Files:** `SessionSidebar.tsx`、`SessionSearchDialog.tsx`、`components.css`、`layout.css`；删除 `OverlayPortal.tsx` 与 `overlay_position.ts`；本地 `visual-system.spec.ts`。

**Interfaces:** `SessionSidebar` 和 `SessionSearchDialog` 的 public props 保持不变。`DropdownMenu` 接管 Portal、键盘导航、点击外部、Escape 与视口碰撞。

- [ ] 写失败用例：打开“会话操作”后 `role="menu"` 可见；按 Escape 后隐藏；点击“删除”后 `role="alertdialog"` 可见。
- [ ] 运行 `cd web/frontend && npx playwright test e2e/visual-system.spec.ts -g "会话菜单"`，预期失败，因为自定义 Portal 未提供这些 Radix 语义。
- [ ] 将 `MenuState`、`toggleMenu`、`SessionMenu` 替换为 `DropdownMenu`。顺序固定为置顶、重命名、归档、删除，删除前有分隔线。
- [ ] 使用 `Dialog` 实现重命名和搜索，使用 `AlertDialog` 实现删除。重命名有可见 label、拒绝空标题并回焦到触发器；删除说明本地记录将永久删除。
- [ ] 删除手写 Portal、定位函数、相关 CSS 和文档级 pointer/keyboard listener。保留移动侧栏 scrim，因为它是布局控制。
- [ ] 运行 `cd web/frontend && npm run build`，预期通过。
- [ ] 提交源码：`git add web/frontend/src/components/SessionSidebar.tsx web/frontend/src/components/SessionSearchDialog.tsx web/frontend/src/styles/components.css web/frontend/src/styles/layout.css && git rm web/frontend/src/components/OverlayPortal.tsx web/frontend/src/state/overlay_position.ts && git commit -m "feat: migrate web session overlays"`。不暂存测试。

### Task 3: 重做 Composer 的工具审批策略

**Files:** `Composer.tsx`、`components.css`，本地 `visual-system.spec.ts`。

**Interfaces:** 保持 `autoApprove: boolean` 与 `onAutoApproveChange(enabled: boolean)`。显示文案固定为“工具审批”“每次工具操作都需要确认”“自动批准后续工具操作”“安全限制始终生效”。

- [ ] 写失败用例：取得 `role="switch"`、名称“自动批准后续工具操作”，验证默认未选中、点击后选中且“安全限制始终生效”可见。
- [ ] 运行 `cd web/frontend && npx playwright test e2e/visual-system.spec.ts -g "工具审批"`，预期失败，因为当前是“完全访问权限”菜单。
- [ ] 删除 `permissionOpen`、`permissionRef`、document dismiss effect 和 `selectPermission`。用策略说明和 Radix `Switch` 替代，保留 `App.setApproval` 及其 API。
- [ ] 保留文本框自动高度与 220px 上限。发送与 Stop 使用固定操作槽，窄屏允许策略文字换行但不得遮挡发送或 Stop。
- [ ] 运行 `cd web/frontend && npm run build`，预期通过。
- [ ] 提交源码：`git add web/frontend/src/components/Composer.tsx web/frontend/src/styles/components.css && git commit -m "feat: clarify web tool approval"`。不暂存测试。

### Task 4: 按真实顺序渲染任务活动簇

**Files:** 新增 `state/timeline_entries.ts`；修改 `state/activity_steps.ts`、`ChatTimeline.tsx`、`ActivityCluster.tsx`、`components.css`；本地 `web_timeline_entries.test.mjs` 与 `web_activity_steps.test.mjs`。

**Interfaces:** `buildTimelineEntries(messages: ChatMessage[], turns: Record<string, TurnState>): TimelineEntry[]`，其中 `TimelineEntry` 为 message 或 turn。新增 `latestActivityStep(steps: ActivityStep[]): ActivityStep | null`。

- [ ] 写失败状态用例：给定 user、turn、assistant，断言 kind 顺序为 `message`、`turn`、`message`；给定无 assistant 的运行中 turn，断言它只出现一次且位于对应 user 后。扩展现有步骤测试，断言 `weather` 是最新步骤 detail。
- [ ] 运行 `./web/frontend/node_modules/.bin/esbuild web/frontend/src/state/timeline_entries.ts --bundle --platform=node --format=esm --outfile=/tmp/tga-timeline-entries.mjs && node --test tests/web_timeline_entries.test.mjs`，预期因模块不存在失败。
- [ ] 实现 mapper：每个 turn 只在匹配 assistant 前插入一次；无 assistant 的 turn 在消息末尾追加；禁止跨 turn 重排或推断事件。
- [ ] `ChatTimeline` 渲染 `TimelineEntry[]`。`ActivityCluster` 优先显示最新真实动作，只有没有可展示事件时才写“思考中”；终态摘要使用“已完成，4 秒，调用 1 个工具”。
- [ ] 运行 `./web/frontend/node_modules/.bin/esbuild web/frontend/src/state/timeline_entries.ts --bundle --platform=node --format=esm --outfile=/tmp/tga-timeline-entries.mjs && ./web/frontend/node_modules/.bin/esbuild web/frontend/src/state/activity_steps.ts --bundle --platform=node --format=esm --outfile=/tmp/tga-activity-steps.mjs && node --test tests/web_timeline_entries.test.mjs tests/web_activity_steps.test.mjs && cd web/frontend && npm run build`，预期通过。
- [ ] 提交源码：`git add web/frontend/src/state/timeline_entries.ts web/frontend/src/state/activity_steps.ts web/frontend/src/components/ChatTimeline.tsx web/frontend/src/components/ActivityCluster.tsx web/frontend/src/styles/components.css && git commit -m "feat: order web activity by turn"`。不暂存测试。

### Task 5: 统一主题并重做检查器安全区与观测阅读

**Files:** 新增 `state/observability_view.ts`；修改 `SessionInspector.tsx`、`App.tsx`、`tokens.css`、`layout.css`、`components.css`；本地 `web_observability_view.test.mjs`、`visual-system.spec.ts`。

**Interfaces:** `formatTokenCount(value: number): string` 使用 `Intl.NumberFormat("zh-CN")`。`buildInspectorSections(data: Observability): InspectorSectionView[]` 产生 `title`、`count` 与结构化 `InspectorRecordView[]`。`SessionInspector({ data, onClose })` 不请求数据。

- [ ] 写失败用例：断言 `formatTokenCount(202828) === "202,828"`，token 行的 `turn_id` 出现在记录标题；Playwright 断言长 user 消息 bounding box 不与 inspector 相交。
- [ ] 运行 `./web/frontend/node_modules/.bin/esbuild web/frontend/src/state/observability_view.ts --bundle --platform=node --format=esm --outfile=/tmp/tga-observability-view.mjs && node --test tests/web_observability_view.test.mjs`，预期因模块不存在失败。
- [ ] 将格式化和记录构建移出 `SessionInspector`。使用既有 `turn_id`、`tool_call_id`、`tool_name`、`state`、`created_at`、行序号作为身份。每条记录显示简洁字段，内层 `<details>` 的“查看原始记录”才显示 JSON。
- [ ] 应用设计稿石墨主题，删除 `LOCAL AGENT`。检查器打开时保持聊天左锚点，只缩小右侧可用宽度，让消息与 Composer 不进入固定检查器；移动端保持全屏检查器，关闭不重置滚动。
- [ ] 运行 `./web/frontend/node_modules/.bin/esbuild web/frontend/src/state/observability_view.ts --bundle --platform=node --format=esm --outfile=/tmp/tga-observability-view.mjs && node --test tests/web_observability_view.test.mjs && cd web/frontend && npm run build`，预期通过。
- [ ] 提交源码：`git add web/frontend/src/state/observability_view.ts web/frontend/src/components/SessionInspector.tsx web/frontend/src/App.tsx web/frontend/src/styles/tokens.css web/frontend/src/styles/layout.css web/frontend/src/styles/components.css && git commit -m "feat: refine web inspector layout"`。不暂存测试。

### Task 6: 收口文档、审查与最终验证

**Files:** `README.md`、`docs/README.md`、`docs/PROJECT_ARCHITECTURE.md`、`docs/TURNING_GOOD_AGENT_SPEC.md`、`docs/phases/2026-06-15-phase-6-web-observability.md`、本设计稿及本计划。

**Interfaces:** 文档明确 Radix 是交互基础层，不是完整设计系统；活动簇仅用真实事件且在 assistant 前显示；检查器只读取既有记录。

- [ ] 同步文档：写入 Radix 边界、审批 Switch、事件顺序、检查器安全区、结构化观测、主题行为和未修改后端的范围。所有验证通过后才将设计稿和计划标为完成。
- [ ] 运行最终验证：`pytest -q`、`cd web/frontend && npm run build`、`python -m compileall -q Turning-Good-Agent`、`printf '/exit\n' | python -m Turning-Good-Agent chat`、`git diff --check`。全部预期退出码为 0。
- [ ] 运行 Task 4 与 Task 5 的本地状态测试；若有 Chromium，运行 `cd web/frontend && npx playwright test` 并审查桌面与移动截图。
- [ ] 使用 `web-design-guidelines` 审查 `web/frontend/src/**/*.{ts,tsx,css}`，再使用 `frontend-design-review` 审查视觉、键盘焦点、深浅主题、响应式布局和审批/错误文案。修复可复现问题并重复最终验证。
- [ ] 提交文档：`git add README.md docs/README.md docs/PROJECT_ARCHITECTURE.md docs/TURNING_GOOD_AGENT_SPEC.md docs/phases/2026-06-15-phase-6-web-observability.md && git add -f docs/superpowers/specs/2026-07-26-web-workbench-visual-system-design.md docs/superpowers/plans/2026-07-26-web-workbench-visual-system.md && git commit -m "docs: finalize web visual system"`。确认 `git rev-list --left-right --count origin/main...main` 后推送。

## Plan Self-Review

1. Task 1 提供 Radix 基础；Task 2 迁移会话浮层；Task 3 收口审批表达；Task 4 修正真实事件顺序；Task 5 统一颜色、检查器安全区与观测阅读；Task 6 验证并同步文档。
2. 没有任务改变后端请求、WebSocket 事件、持久化、Runtime、Memory、Tool 或 MCP 逻辑。
3. Task 2、3、5 在 Chromium 可用时使用浏览器断言；Task 4、5 使用确定性的本地状态测试；每个源码任务在提交前运行 `npm run build`。
4. 每个任务明确排除了本地测试和无关文件的暂存。

# Web Workbench Interaction Redesign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 Phase 6 Web 工作台改造成滚动稳定、过程可读、菜单可靠且适合长期使用的本机 Agent 对话界面。

**Architecture:** 保留现有 FastAPI、WebSocket、`SessionState`、`SessionSocketClient` 和 JSON/JSONL 边界。新增的前端能力只落在 Portal 浮层、会话搜索、聊天视口和真实事件活动簇；`App` 继续只负责组装状态与组件。侧栏、聊天、检查器和所有浮层各自拥有独立层级与滚动边界。

**Tech Stack:** React、TypeScript、Vite、react-dom、lucide-react、react-markdown、原生 CSS、Playwright（仅本地验证）。

**状态：** 已完成 Task 1--6。当前环境无 Chromium，Playwright 截图验证待后续具备浏览器的环境补充。

## Global Constraints

- 所有新增或修改函数添加精简中文注释。
- 不修改 FastAPI、Runtime、Memory、Tool、MCP、Session JSON/JSONL、既有 REST/WebSocket 协议或观测数据格式。
- 不展示、保存、推断或伪造模型 reasoning；活动簇只显示已有 `TaskEvent`。
- 不引入 Tailwind、Radix、大型 UI Kit、图片生成、CLI App 或 MCP 预设管理功能。
- 保留默认深色与持久化浅色主题；使用既有语义 CSS token。
- `tests/`、`web/frontend/e2e/` 仅本地验证，绝不暂存、提交或上传。
- 不提交 `web/static/`、`node_modules/`、`.venv/`、`settings.local.json` 或 `.sessions/`。
- 工作区根目录的 `jump-jump.html` 与中文命名 HTML 是无关文件，禁止修改、暂存或提交。

## File Map

- Create: `web/frontend/src/components/OverlayPortal.tsx`。为菜单和对话框提供 `document.body` Portal、焦点与外部关闭边界。
- Create: `web/frontend/src/components/SessionSearchDialog.tsx`。提供受控会话搜索及键盘导航。
- Create: `web/frontend/src/components/ActivityCluster.tsx`。聚合真实任务事件并呈现紧凑时间线。
- Create: `web/frontend/src/state/overlay_position.ts`。计算 Portal 菜单的视口安全位置。
- Create: `web/frontend/src/state/activity_steps.ts`。把 `TaskEvent[]` 转为不含 reasoning 的可渲染活动步骤。
- Modify: `web/frontend/src/App.tsx`。接入搜索与新的 `ActivityCluster`，不改变请求和 WebSocket 逻辑。
- Modify: `web/frontend/src/components/SessionSidebar.tsx`。使用 Portal 菜单与对话框，增加搜索入口和固定状态位。
- Modify: `web/frontend/src/components/ChatTimeline.tsx`。收口滚动跟随、未读提示、消息密度与 composer 高度变化。
- Modify: `web/frontend/src/components/Composer.tsx`。实现自动高度、固定操作位和紧凑权限菜单。
- Delete: `web/frontend/src/components/ThinkingTrail.tsx`。被 `ActivityCluster.tsx` 完整替代。
- Modify: `web/frontend/src/styles/tokens.css`、`layout.css`、`components.css`。拆除旧样式覆盖，定义布局、浮层、活动簇、消息与移动端规则。
- Create locally only: `web/frontend/e2e/overlay-and-sidebar.spec.ts`、`chat-viewport.spec.ts`、`activity-and-composer.spec.ts`。用于 Playwright 端到端验证。

---

### Task 1: Portal 浮层与会话操作

**Files:**
- Create: `web/frontend/src/state/overlay_position.ts`
- Create: `web/frontend/src/components/OverlayPortal.tsx`
- Modify: `web/frontend/src/components/SessionSidebar.tsx`
- Modify: `web/frontend/src/styles/components.css`
- Test: `web/frontend/e2e/overlay-and-sidebar.spec.ts`（仅本地）

**Interfaces:**
- Produces `placeOverlay(anchor: DOMRect, overlay: { width: number; height: number }, viewport: { width: number; height: number }): { top: number; left: number }`。
- Produces `OverlayPortal({ children, onDismiss, labelledBy }: { children: ReactNode; onDismiss: () => void; labelledBy?: string })`。
- `SessionSidebar` 继续接收现有 `onUpdate`、`onDelete` 和 `onError`，不改变父组件 API。

- [ ] **Step 1: 写本地失败用例**

```ts
test("会话菜单渲染到 body 且不会被侧栏滚动裁切", async ({ page }) => {
  await page.getByRole("button", { name: /会话操作/ }).click();
  await expect(page.locator("body > [data-overlay-root] [role=menu]")).toBeVisible();
  await page.mouse.click(1000, 600);
  await expect(page.getByRole("menu")).toHaveCount(0);
});
```

- [ ] **Step 2: 运行失败用例**

Run: `cd web/frontend && npx playwright test e2e/overlay-and-sidebar.spec.ts`

Expected: FAIL，因为现有 `.session-menu` 是侧栏滚动容器的后代，且不存在 `data-overlay-root`。

- [ ] **Step 3: 实现最小 Portal 与位置计算**

```ts
export function placeOverlay(anchor: DOMRect, overlay: { width: number; height: number }, viewport: { width: number; height: number }) {
  const gap = 6;
  return {
    top: Math.max(gap, Math.min(anchor.bottom + gap, viewport.height - overlay.height - gap)),
    left: Math.max(gap, Math.min(anchor.right - overlay.width, viewport.width - overlay.width - gap)),
  };
}
```

- [ ] **Step 4: 改造侧栏操作与对话框**

将 `openMenuId` 改为保存 `{ session, anchorRect }`，从触发按钮 `currentTarget.getBoundingClientRect()` 读取位置。菜单、重命名和删除确认通过 `OverlayPortal` 渲染；`pointerdown`、Escape、选中会话与完成操作均调用同一个关闭函数。保留固定的置顶槽位，并新增搜索入口按钮。

- [ ] **Step 5: 运行局部验证**

Run: `cd web/frontend && npm run build`

Expected: PASS；若本机已有 Chromium，再运行 Step 2 的 Playwright 用例并确认 PASS。

- [ ] **Step 6: 提交源码**

```bash
git add web/frontend/src/components/OverlayPortal.tsx \
  web/frontend/src/state/overlay_position.ts \
  web/frontend/src/components/SessionSidebar.tsx \
  web/frontend/src/styles/components.css
git commit -m "feat: add web overlay menus"
```

### Task 2: 会话搜索与侧栏状态信息

**Files:**
- Create: `web/frontend/src/components/SessionSearchDialog.tsx`
- Modify: `web/frontend/src/App.tsx`
- Modify: `web/frontend/src/components/SessionSidebar.tsx`
- Modify: `web/frontend/src/styles/components.css`
- Test: `web/frontend/e2e/overlay-and-sidebar.spec.ts`（仅本地）

**Interfaces:**
- Produces `SessionSearchDialog({ open, sessions, currentId, onClose, onSelect }: SessionSearchDialogProps)`。
- `SessionSidebar` 增加 `onOpenSearch: () => void`。
- `App` 在内存中合并 active 与 archived session，搜索选择后调用现有 `navigate(id)`。

- [ ] **Step 1: 写本地失败用例**

```ts
test("搜索会话支持键盘选择和 Enter 打开", async ({ page }) => {
  await page.getByRole("button", { name: "搜索会话" }).click();
  await page.getByRole("textbox", { name: "搜索会话" }).fill("部署");
  await page.keyboard.press("ArrowDown");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/sessions\//);
});
```

- [ ] **Step 2: 运行失败用例**

Run: `cd web/frontend && npx playwright test e2e/overlay-and-sidebar.spec.ts -g "搜索会话"`

Expected: FAIL，因为没有搜索入口、搜索对话框和键盘选择逻辑。

- [ ] **Step 3: 实现搜索组件**

```ts
const results = sessions.filter((session) => {
  const text = `${session.title}`.toLocaleLowerCase();
  return text.includes(query.trim().toLocaleLowerCase());
});
```

使用 `useEffect` 在打开时聚焦输入框；`ArrowUp`、`ArrowDown` 循环更新选中索引，`Enter` 打开选中会话，Escape 关闭。长标题使用截断，显示空结果与当前会话标记。

- [ ] **Step 4: 接入 App 与侧栏状态位**

`App` 添加 `searchOpen`，把会话合并列表传给搜索对话框；侧栏为运行中和完成 session 留出固定 28px 状态槽位，避免 Pin 和标题位置跳动。

- [ ] **Step 5: 运行局部验证**

Run: `cd web/frontend && npm run build`

Expected: PASS；若本机已有 Chromium，再运行 Step 2 用例并确认 PASS。

- [ ] **Step 6: 提交源码**

```bash
git add web/frontend/src/App.tsx \
  web/frontend/src/components/SessionSidebar.tsx \
  web/frontend/src/components/SessionSearchDialog.tsx \
  web/frontend/src/styles/components.css
git commit -m "feat: add web session search"
```

### Task 3: 稳定聊天视口与消息排版

**Files:**
- Modify: `web/frontend/src/components/ChatTimeline.tsx`
- Modify: `web/frontend/src/components/Composer.tsx`
- Modify: `web/frontend/src/styles/layout.css`
- Modify: `web/frontend/src/styles/components.css`
- Test: `web/frontend/e2e/chat-viewport.spec.ts`（仅本地）

**Interfaces:**
- `ChatTimeline` 增加可选 `composerRef?: RefObject<HTMLElement | null>`，用于观察输入区高度。
- `ChatTimeline` 保留现有 `sessionId`、`messages`、`turns` 和 `renderTurn` API。
- `Composer` 增加可选 `rootRef?: Ref<HTMLElement>`，不改变发送、Stop 和审批回调。

- [ ] **Step 1: 写本地失败用例**

```ts
test("阅读历史时新消息不抢夺滚动位置", async ({ page }) => {
  await page.locator(".chat-scroll").evaluate((node) => { node.scrollTop = 0; });
  await page.getByTestId("emit-stream-delta").click();
  await expect(page.getByRole("button", { name: "有新消息" })).toBeVisible();
  await expect(page.locator(".chat-scroll")).toHaveJSProperty("scrollTop", 0);
});
```

- [ ] **Step 2: 运行失败用例**

Run: `cd web/frontend && npx playwright test e2e/chat-viewport.spec.ts -g "不抢夺滚动位置"`

Expected: FAIL，因为现有 ChatTimeline 没有 Composer 高度观察和完整的流式滚动边界断言。

- [ ] **Step 3: 收口滚动策略**

保留每 session 的 `scrollTop`，新增 `ResizeObserver` 观察 Composer；只在 `nearBottom`、刚发送 user 消息或用户点击未读按钮时滚到底部。新消息出现且用户不在底部时只设置 `hasUnread`。

- [ ] **Step 4: 改造消息与 Composer 尺寸**

```css
.message.user { display: flex; flex-direction: column; align-items: flex-end; }
.message.user .markdown { width: fit-content; max-width: min(100%, 42rem); }
.message.assistant { max-width: 780px; }
.composer { width: min(780px, calc(100% - 28px)); }
```

textarea 使用 `scrollHeight` 自动调整到定义的最大高度；禁止用户拖拽改变高度。检查器打开时中央消息列不改变，Composer 使用专用 class 在可用空间内缩窄，确保发送按钮可见。

- [ ] **Step 5: 运行局部验证**

Run: `cd web/frontend && npm run build`

Expected: PASS；若本机已有 Chromium，执行 Step 2 用例，并用 390px 与 1440px 视口确认无水平页面滚动。

- [ ] **Step 6: 提交源码**

```bash
git add web/frontend/src/components/ChatTimeline.tsx \
  web/frontend/src/components/Composer.tsx \
  web/frontend/src/styles/layout.css \
  web/frontend/src/styles/components.css
git commit -m "feat: stabilize web chat viewport"
```

### Task 4: 真实事件活动簇

**Files:**
- Create: `web/frontend/src/state/activity_steps.ts`
- Create: `web/frontend/src/components/ActivityCluster.tsx`
- Modify: `web/frontend/src/App.tsx`
- Modify: `web/frontend/src/components/ChatTimeline.tsx`
- Delete: `web/frontend/src/components/ThinkingTrail.tsx`
- Modify: `web/frontend/src/styles/components.css`
- Test: `web/frontend/e2e/activity-and-composer.spec.ts`（仅本地）

**Interfaces:**
- Produces `buildActivitySteps(events: TaskEvent[]): ActivityStep[]`。
- `ActivityStep` 固定为 `{ key: string; label: string; detail?: string; tone: "running" | "waiting" | "done" | "failed" | "stopped" }`。
- Produces `ActivityCluster({ turn, onResolveApproval }: { turn: TurnState; onResolveApproval: (approvalId: string, approved: boolean) => void })`。

- [ ] **Step 1: 写本地失败用例**

```ts
test("活动簇只显示真实事件而不显示 reasoning", async ({ page }) => {
  await page.getByTestId("emit-tool-and-skill-events").click();
  await expect(page.getByText("正在调用工具")).toBeVisible();
  await expect(page.getByText("正在加载 Skill")).toBeVisible();
  await expect(page.getByText(/reasoning|思维链/i)).toHaveCount(0);
});
```

- [ ] **Step 2: 运行失败用例**

Run: `cd web/frontend && npx playwright test e2e/activity-and-composer.spec.ts -g "真实事件"`

Expected: FAIL，因为当前 `ThinkingTrail` 没有独立事件归类模块、受限步骤滚动或终态收拢策略。

- [ ] **Step 3: 编写纯事件归类模块**

```ts
export function buildActivitySteps(events: TaskEvent[]): ActivityStep[] {
  return events.flatMap((event, index) => toActivityStep(event, index) ? [toActivityStep(event, index)!] : []);
}
```

`toActivityStep` 只能映射 `task.*`、`tool.*`、`approval.*` 与已有压缩状态。`load_skill` 映射为“正在加载 Skill”；MCP 工具映射为“正在调用 MCP”。未知事件返回 `null`，不得把 payload 文本当作 reasoning 展示。

- [ ] **Step 4: 实现 ActivityCluster 并替换 ThinkingTrail**

运行时默认展开，标题显示最新动作或“思考中 · 已用 N 秒”；完成后保留终态 900ms，再自动收起。步骤区设置最大高度、独立滚动和仅在底部时自动跟随。审批卡置于当前步骤末尾，仅显示工具名、参数摘要和两个操作。

- [ ] **Step 5: 运行局部验证**

Run: `cd web/frontend && npm run build`

Expected: PASS；若本机已有 Chromium，运行 Step 2 用例并确认 PASS。

- [ ] **Step 6: 提交源码**

```bash
git add web/frontend/src/state/activity_steps.ts \
  web/frontend/src/components/ActivityCluster.tsx \
  web/frontend/src/App.tsx \
  web/frontend/src/components/ChatTimeline.tsx \
  web/frontend/src/components/ThinkingTrail.tsx \
  web/frontend/src/styles/components.css
git commit -m "feat: add web activity cluster"
```

### Task 5: Composer、审批、检查器与主题收口

**Files:**
- Modify: `web/frontend/src/App.tsx`
- Modify: `web/frontend/src/components/Composer.tsx`
- Modify: `web/frontend/src/components/SessionInspector.tsx`
- Modify: `web/frontend/src/styles/tokens.css`
- Modify: `web/frontend/src/styles/layout.css`
- Modify: `web/frontend/src/styles/components.css`
- Test: `web/frontend/e2e/activity-and-composer.spec.ts`（仅本地）

**Interfaces:**
- `Composer` 保持 `autoApprove: boolean` 和 `onAutoApproveChange(enabled: boolean)` 作为唯一全局审批开关。
- `App` 在 `inspectorOpen` 时只传递布局 class，不重置历史、turn 或 WebSocket 状态。
- `SessionInspector` 继续只消费现有 `Observability`。

- [ ] **Step 1: 写本地失败用例**

```ts
test("检查器打开后发送按钮仍可见且主消息列不移动", async ({ page }) => {
  const before = await page.locator(".message.assistant").first().boundingBox();
  await page.getByRole("button", { name: "打开会话检查器" }).click();
  await expect(page.getByRole("button", { name: "发送消息" })).toBeVisible();
  const after = await page.locator(".message.assistant").first().boundingBox();
  expect(after?.x).toBe(before?.x);
});
```

- [ ] **Step 2: 运行失败用例**

Run: `cd web/frontend && npx playwright test e2e/activity-and-composer.spec.ts -g "发送按钮仍可见"`

Expected: FAIL，因为现有窄窗口未定义 Composer 专用可用宽度约束。

- [ ] **Step 3: 实现布局和权限细节**

定义 `--inspector-width` 与 `--chat-max-width`，仅在足够宽的视口为 `.app-shell.is-inspector-open .composer` 设置可用宽度；移动端仍为全宽。权限菜单保留两项、明确安全限制、使用 `role="menuitemradio"` 与焦点状态。发送与 Stop 使用同尺寸固定操作位。

- [ ] **Step 4: 收口检查器与主题**

检查器顶部只显示 token、当前上下文、压缩次数和工具失败数，完整记录保留 `<details>`。清除 CSS 中被新规则覆盖的旧 `.permission-select`、旧 `.thinking-trail` 与重复侧栏 z-index 规则；保留深浅主题 token、`color-scheme`、安全区域和减少动态偏好。

- [ ] **Step 5: 运行局部验证**

Run: `cd web/frontend && npm run build`

Expected: PASS；若本机已有 Chromium，运行 Step 2 用例，并检查 390px、1024px、1440px 视口。

- [ ] **Step 6: 提交源码**

```bash
git add web/frontend/src/App.tsx \
  web/frontend/src/components/Composer.tsx \
  web/frontend/src/components/SessionInspector.tsx \
  web/frontend/src/styles/tokens.css \
  web/frontend/src/styles/layout.css \
  web/frontend/src/styles/components.css
git commit -m "feat: refine web composer and inspector"
```

### Task 6: 最终审查、文档与发布

**Files:**
- Modify: `README.md`
- Modify: `docs/README.md`
- Modify: `docs/PROJECT_ARCHITECTURE.md`
- Modify: `docs/TURNING_GOOD_AGENT_SPEC.md`
- Modify: `docs/phases/2026-06-15-phase-6-web-observability.md`
- Modify: `docs/superpowers/specs/2026-07-25-web-workbench-redesign-design.md`
- Modify: `docs/superpowers/plans/2026-07-26-web-workbench-redesign.md`
- Test: `web/frontend/e2e/*.spec.ts`（仅本地）

**Interfaces:**
- 文档必须说明 Portal 浮层、独立滚动、真实事件活动簇、检查器覆盖而非推移主对话，以及没有新增持久化或 reasoning 展示。

- [ ] **Step 1: 写最终本地验证清单**

```ts
test("移动端没有页面横向滚动", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
});
```

- [ ] **Step 2: 运行端到端与质量验证**

Run:

```bash
pytest -q
cd web/frontend && npm run build
python -m compileall -q Turning-Good-Agent
printf '/exit\n' | python -m Turning-Good-Agent chat
git diff --check
```

Expected: 所有命令退出码为 0。具备 Chromium 时另运行 `cd web/frontend && npx playwright test`，检查桌面与移动端截图。

- [ ] **Step 3: 执行两次前端审查**

先按 `web-design-guidelines` 审查最新前端文件，再按 `frontend-design-review` 审查任务完成、质量工艺和可信性；修复两次审查中可复现的问题后重复 Step 2。

- [ ] **Step 4: 同步文档状态**

将 Phase 6 与设计文档改为“已完成”，更新 README、文档索引、架构文档和总 Spec。使用 `git add -f` 暂存被 `.gitignore` 忽略的 `docs/` 文件；不暂存本地 e2e、构建产物或无关 HTML。

- [ ] **Step 5: 提交并推送**

```bash
git add README.md \
  docs/README.md \
  docs/PROJECT_ARCHITECTURE.md \
  docs/TURNING_GOOD_AGENT_SPEC.md \
  docs/phases/2026-06-15-phase-6-web-observability.md
git add -f docs/superpowers/specs/2026-07-25-web-workbench-redesign-design.md \
  docs/superpowers/plans/2026-07-26-web-workbench-redesign.md
git commit -m "docs: finalize web workbench interaction redesign"
git push origin main
git rev-list --left-right --count origin/main...main
```

Expected: 最后一条输出 `0\t0`。

## Self-Review

- 侧栏 Portal、菜单关闭、搜索、固定状态位由 Task 1--2 覆盖。
- 会话独立滚动、未读提示、短 user 气泡、Composer 高度和检查器布局由 Task 3 与 Task 5 覆盖。
- 真实事件活动簇、审批、终态自动收拢和 reasoning 边界由 Task 4 覆盖。
- 深浅主题、减少动态偏好、移动端、安全区域、审查、文档和发布由 Task 5--6 覆盖。
- 所有测试目录都仅用于本地验证；所有提交步骤只包含源码和受跟踪文档。

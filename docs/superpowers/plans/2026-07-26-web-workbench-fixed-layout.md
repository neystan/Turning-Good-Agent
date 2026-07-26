# Web Workbench Fixed Layout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** 将 Web 工作台改为随检查器开关重新居中的稳定会话布局，并以大圆角控制面完成权限菜单、工具审批、活动流和 Composer 的第三轮精修。

**Architecture:** 桌面端保留左侧会话栏和主工作区；检查器关闭时主工作区使用单列，消息列与 Composer 居中。打开时内部 Grid 增加右侧检查器列，主会话容器左边界不动、右边界收缩，消息列与 Composer 在新的可用区域内居中。React 继续使用已有 REST、WebSocket、TaskEvent、SessionInspector 与全局 auto_approve_tools API；本次不增加事件、后端接口或持久化字段。

**Tech Stack:** React、TypeScript、Vite、原生 CSS、lucide-react、Radix DropdownMenu、Radix Tooltip、Playwright（本地可选）。

## Global Constraints

- 只修改 Web 前端和相关文档；不得修改 Runtime、Session、Memory、Tool、MCP、REST、WebSocket 或 JSON/JSONL。
- 桌面端检查器关闭时不占列；打开时检查器列使用相对 CSS 约束适应可用空间，主会话容器左边界不动、右边界收缩，聊天列与 Composer 在新的区域内居中且等宽。
- 窄屏检查器保持全屏覆盖层，左侧会话栏维持既有移动抽屉行为。
- 权限菜单仅显示“默认权限”和“自动批准”；使用现有 onAutoApproveChange(boolean)，不展示额外安全说明，不改变二次安全检查。
- 活动簇只渲染真实 TaskEvent，不得展示、推断、保存或伪造模型 reasoning。
- 保留 lucide-react 为唯一图标库；不引入 Tailwind、shadcn、Fluent、Material 或完整 UI Kit。
- 每个新增或修改函数必须带精简中文注释；纯 JSX/CSS 结构不新增无意义注释。
- tests/、web/frontend/e2e/ 与 web/frontend/playwright.config.ts 仅本地验证，绝不暂存、提交或上传。
- 不修改、暂存或提交 jump-jump.html 与中文命名的无关 HTML 文件，也不提交 web/static/、web/frontend/node_modules/、.venv/、settings.local.json 或 .sessions/。
- 文档目录受忽略规则影响，所有文档更新必须使用 git add -f 暂存。

## File Map

- web/frontend/src/App.tsx：根据检查器状态切换主工作区内部 Grid；保留现有读取与关闭语义。
- web/frontend/src/components/SessionInspector.tsx：只渲染已打开的检查器内容，不自行请求数据。
- web/frontend/src/components/Composer.tsx：用 Radix DropdownMenu.RadioGroup 替换 Switch，并保留输入、发送和 Stop 行为。
- web/frontend/src/components/ActivityCluster.tsx：把真实事件收敛成低对比度活动流，并重做审批面板的 JSX 结构。
- web/frontend/src/styles/tokens.css：统一相对检查器列、圆角和深浅主题控制面 token。
- web/frontend/src/styles/layout.css：定义检查器开关下的单列/双列工作区与移动端全屏检查器。
- web/frontend/src/styles/components.css：实现菜单、Composer、活动簇、审批和检查器的大圆角视觉语言。
- web/frontend/e2e/visual-system.spec.ts：本地 Playwright 几何与菜单语义断言，不提交。
- README.md、docs/README.md、docs/PROJECT_ARCHITECTURE.md、docs/TURNING_GOOD_AGENT_SPEC.md、docs/phases/2026-06-15-phase-6-web-observability.md：同步 Web UI 边界与验证记录。

---

### Task 1: 检查器开关下的会话居中布局

**Files:**
- Modify: web/frontend/src/App.tsx
- Modify: web/frontend/src/components/SessionInspector.tsx
- Modify: web/frontend/src/styles/tokens.css
- Modify: web/frontend/src/styles/layout.css
- Test (local only): web/frontend/e2e/visual-system.spec.ts

**Interfaces:**
- SessionInspector({ data, onClose }) 继续只接受当前观测数据与关闭回调。
- 新增内部组件 InspectorLayer({ open, data, onClose })；关闭时不占 Grid 列，打开时输出 SessionInspector。
- App 的 inspectorOpen 仍决定是否已经成功取得当前会话的观测数据。

- [x] **Step 1: 写失败的桌面几何用例**

在本地 visual-system.spec.ts 增加如下用例。测试使用可注入的现有会话 fixture 或启动后的真实测试会话；不得伪造 WebSocket 事件：

    test("检查器开关不改变中央会话几何", async ({ page }) => {
      await page.goto("/sessions/test-session");
      const message = page.locator(".message").first();
      const composer = page.locator(".composer");
      const before = {
        message: await message.boundingBox(),
        composer: await composer.boundingBox(),
      };
      await page.getByRole("button", { name: "打开会话检查器" }).click();
      await expect(page.locator(".inspector")).toBeVisible();
      expect(await message.boundingBox()).toEqual(before.message);
      expect(await composer.boundingBox()).toEqual(before.composer);
    });

- [x] **Step 2: 运行用例并确认旧实现失败**

运行：cd web/frontend && npx playwright test e2e/visual-system.spec.ts -g "检查器开关"

预期：若 Chromium 已安装，旧版会因为 .inspector 固定覆盖并改变 Composer/消息可用区域而失败；若浏览器未安装，记录缺少 Chromium，不下载浏览器。

- [x] **Step 3: 实现检查器开关下的单列/双列布局**

在 App.tsx 中将条件渲染：

    {inspectorOpen && <SessionInspector data={inspector} onClose={() => setInspectorOpen(false)} />}

替换为始终存在的检查器层：

    <InspectorLayer
      open={inspectorOpen}
      data={inspector}
      onClose={() => setInspectorOpen(false)}
    />

InspectorLayer 在关闭时不显示且不占列；打开时渲染 SessionInspector。打开入口只保留现有顶部栏图标。为该内部函数添加“渲染检查器显示层”的中文注释。

将 token 设为相对检查器列约束：

    --inspector-reserve: clamp(16rem, 20vw, 24rem);

在 layout.css 让 app-shell 只划分侧栏和工作区。关闭检查器时 conversation 使用单列；打开后增加右侧检查器列：

    .app-shell { grid-template-columns: var(--sidebar-width) minmax(0, 1fr); }
    .conversation { grid-template-columns: minmax(0, 1fr); }
    .conversation.is-inspector-open { grid-template-columns: minmax(0, 1fr) var(--inspector-reserve); }
    .topbar { grid-column: 1 / -1; }
    .chat-scroll, .composer { grid-column: 1; }
    .inspector-layer { display: none; }
    .conversation.is-inspector-open .inspector-layer { display: block; grid-column: 2; grid-row: 1 / -1; }

在 max-width: 1099px 让检查器层不占内部 Grid；打开的 .inspector 以 position: fixed; inset: 0; width: 100vw 覆盖。max-width: 760px 保持一列和既有移动侧栏。

- [x] **Step 4: 运行构建与可选浏览器验证**

运行：cd web/frontend && npm run build

预期：通过。若 Chromium 已安装，再运行 Step 2 的用例并预期通过；检查关闭、打开、关闭后中央消息和 Composer 的 bounding box 一致。

- [x] **Step 5: 提交布局源码**

    git add web/frontend/src/App.tsx web/frontend/src/components/SessionInspector.tsx web/frontend/src/styles/tokens.css web/frontend/src/styles/layout.css
    git commit -m "feat: fix web inspector layout"

不要暂存 Playwright 文件。

### Task 2: 将 Composer 改为紧凑权限菜单与单一操作槽

**Files:**
- Modify: web/frontend/src/components/Composer.tsx
- Modify: web/frontend/src/styles/components.css
- Test (local only): web/frontend/e2e/visual-system.spec.ts

**Interfaces:**
- 保持 ComposerProps.autoApprove: boolean 与 onAutoApproveChange(enabled: boolean): void。
- PermissionMenu({ autoApprove, onChange }) 为 Composer.tsx 私有组件，输出一个 DropdownMenu.RadioGroup。

- [ ] **Step 1: 写失败的权限菜单语义用例**

在本地 Playwright 文件中替换旧 Switch 用例：

    test("权限菜单提供两种可键盘选择的策略", async ({ page }) => {
      await page.goto("/");
      await page.getByRole("button", { name: "工具权限：默认权限" }).click();
      await expect(page.getByRole("menuitemradio", { name: "默认权限" }))
        .toHaveAttribute("data-state", "checked");
      await expect(page.getByRole("menuitemradio", { name: "自动批准" })).toBeVisible();
      await expect(page.getByText("安全限制始终生效")).toHaveCount(0);
    });

- [ ] **Step 2: 运行用例并确认旧组件失败**

运行：cd web/frontend && npx playwright test e2e/visual-system.spec.ts -g "权限菜单"

预期：旧实现找不到名称为“工具权限：默认权限”的按钮；若 Chromium 缺失则只记录环境阻塞。

- [ ] **Step 3: 实现 Radix Radio 菜单和操作槽**

删除 @radix-ui/react-switch 与 Switch.Root。使用：

    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>
        <button className="permission-trigger" aria-label={"工具权限：" + permissionLabel}>
          <Hand size={16} aria-hidden="true" />
          <span>{permissionLabel}</span>
          <ChevronDown size={14} aria-hidden="true" />
        </button>
      </DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content className="permission-menu" side="top" align="start" sideOffset={8}>
          <DropdownMenu.RadioGroup
            value={autoApprove ? "auto" : "default"}
            onValueChange={(value) => onAutoApproveChange(value === "auto")}
          >
            <DropdownMenu.RadioItem value="default">
              默认权限<DropdownMenu.ItemIndicator><Check size={15} /></DropdownMenu.ItemIndicator>
            </DropdownMenu.RadioItem>
            <DropdownMenu.RadioItem className="is-warning" value="auto">
              <TriangleAlert size={15} />自动批准<DropdownMenu.ItemIndicator><Check size={15} /></DropdownMenu.ItemIndicator>
            </DropdownMenu.RadioItem>
          </DropdownMenu.RadioGroup>
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>

permissionLabel 的值为 autoApprove ? "自动批准" : "默认权限"，按钮 aria-label 前缀为“工具权限：”。为 PermissionMenu 加“渲染全局工具权限选择菜单”的中文注释。发送按钮改为带 IconTooltip 的 40px 圆形 button，使用 aria-label="发送消息"；运行时同一位置渲染带图标和“停止”的圆角 Stop 按钮，aria-label="停止任务"。保留 Enter 发送、Shift+Enter 换行、220px 高度限制与已有 API 错误处理。

- [ ] **Step 4: 编写 Composer 控制面样式**

在 components.css 新增/替换以下选择器：.composer、.composer:focus-within、.composer-toolbar、.permission-trigger、.permission-menu、.permission-menu [role="menuitemradio"]、.composer-action.is-send、.composer-action.is-stop。输入面与控制面之间仅使用细边线；默认阴影弱，聚焦使用 var(--accent) 描边；不写 transition: all。

- [ ] **Step 5: 验证并提交 Composer 源码**

运行：cd web/frontend && npm run build

若 Chromium 可用，再运行 Step 2 用例并预期通过。提交：

    git add web/frontend/src/components/Composer.tsx web/frontend/src/styles/components.css
    git commit -m "feat: refine web composer controls"

### Task 3: 重构真实活动流与琥珀审批面板

**Files:**
- Modify: web/frontend/src/components/ActivityCluster.tsx
- Modify: web/frontend/src/styles/components.css
- Test (local only): tests/web_activity_steps.test.mjs
- Test (local only): web/frontend/e2e/visual-system.spec.ts

**Interfaces:**
- buildActivitySteps(turn.events) 与 pendingApproval(events) 的数据来源不变。
- ApprovalBar({ approval, onResolve }) 仍只调用 onResolve(approvalId, boolean)，不写入任何新增状态。

- [ ] **Step 1: 扩展真实事件状态测试**

在 tests/web_activity_steps.test.mjs 增加一个只覆盖纯函数的回归断言：给定 tool.started、tool.completed、task.completed，latestActivityStep(buildActivitySteps(events)) 返回最后一个真实终态步骤；不要为视觉文案创建“模型思考”事件。

    assert.equal(latestActivityStep(buildActivitySteps(events)).label, "任务已完成");

- [ ] **Step 2: 运行测试并确认旧断言失败**

运行：

    ./web/frontend/node_modules/.bin/tsc --target ES2022 --module NodeNext --moduleResolution NodeNext --outDir /tmp/tga-activity web/frontend/src/state/activity_steps.ts
    ln -sf /tmp/tga-activity/state/activity_steps.js /tmp/tga-activity-steps.mjs
    node --test tests/web_activity_steps.test.mjs

预期：在新增事件映射前失败；如果现有映射已经输出终态，则将断言改为覆盖缺少的真实事件分支，而非为了失败破坏正确实现。

- [ ] **Step 3: 实现低对比度活动流与审批 JSX**

将 ActivityCluster 的默认 open 改为 false；仅当 approval 出现时通过 effect 展开，完成态不再用 900ms 自动开合改变用户阅读。运行态标题固定以“思考中”开头，后接最近的真实工具、MCP 或 Skill 动作；无动作时使用已有耗时表达。保留 aria-expanded 与步骤列表的底部跟随逻辑。

将 ApprovalBar 调整为：

    <section className="activity-approval" aria-label={"等待允许 " + approval.toolName}>
      <header>
        <ShieldAlert size={17} aria-hidden="true" />
        <div><span>等待你的批准</span><strong>{approval.toolName}</strong></div>
      </header>
      <code>{approval.args}</code>
      <div className="activity-approval-actions">
        <button className="secondary" onClick={() => onResolve(approval.approvalId, false)}>拒绝</button>
        <button className="primary" onClick={() => onResolve(approval.approvalId, true)}>
          <Check size={15} aria-hidden="true" />允许一次
        </button>
      </div>
    </section>

为修改后的 effect 添加“审批出现时展开真实事件列表”的中文注释。不要显示 reasoning、原始 tool result 或额外权限说明。

- [ ] **Step 4: 完成活动与审批样式**

让 .activity-cluster 无外层卡片，标题为贴近背景的半透明圆角状态行；运行转圈仅使用 transform 动画，prefers-reduced-motion 下关闭。activity-steps 使用低对比度细竖线与真实状态点；审批面板使用 18px 圆角、淡琥珀底色和受限高度等宽参数预览。允许一次使用琥珀实底，拒绝保持低强调描边，窄屏可换行但不得重叠。

- [ ] **Step 5: 验证并提交活动源码**

运行 Step 2 的状态测试及 cd web/frontend && npm run build，预期通过。若 Chromium 可用，增加并运行审批面板的截图/可见性断言。提交：

    git add web/frontend/src/components/ActivityCluster.tsx web/frontend/src/styles/components.css
    git commit -m "feat: refine web activity approval"

不要暂存 tests/ 或 E2E 文件。

### Task 4: 统一大圆角视觉 token、检查器与响应式细节

**Files:**
- Modify: web/frontend/src/styles/tokens.css
- Modify: web/frontend/src/styles/layout.css
- Modify: web/frontend/src/styles/components.css
- Modify: web/frontend/src/components/SessionInspector.tsx
- Test (local only): web/frontend/e2e/visual-system.spec.ts

**Interfaces:**
- SessionInspector({ data, onClose }) 的 props 不变。
- CSS token 新增 --radius-control: 14px、--radius-panel: 18px、--radius-round: 999px，组件只能使用对应 token，不另起不一致的 4px/6px 圆角。

- [ ] **Step 1: 写响应式与溢出浏览器用例**

在本地 E2E 增加：

    test("窄屏检查器覆盖而不产生横向溢出", async ({ page }) => {
      await page.setViewportSize({ width: 390, height: 844 });
      await page.goto("/sessions/test-session");
      await page.getByRole("button", { name: "打开会话检查器" }).click();
      await expect(page.locator(".inspector")).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBe(390);
    });

- [ ] **Step 2: 运行用例并确认旧视觉规则不满足新断言**

运行：cd web/frontend && npx playwright test e2e/visual-system.spec.ts -g "窄屏检查器"

预期：若 Chromium 可用，旧固定宽度或布局规则会暴露横向溢出；若浏览器缺失则只记录阻塞。

- [ ] **Step 3: 收口 token 和组件样式**

在深浅主题中保留石墨灰/中性灰为主和单一操作蓝，琥珀仅用于审批与自动批准风险。把普通按钮、会话行、对话框、检查器指标、菜单、输入面统一为 --radius-control 或 --radius-panel；圆形图标按钮使用 --radius-round。仅 Composer、菜单和对话框保留低透明阴影。

重写受影响 CSS 时删除失效的 .app-shell.is-inspector-open、旧 Switch、旧 .tool-approval 选择器和重复的检查器覆盖规则。保留独立滚动边界：.sidebar、.chat-scroll、.inspector-body；不得将页面滚动条交给 body。为检查器关闭入口设置 min-height: 56px 和清晰焦点状态；打开检查器后不能移除右列边界。

- [ ] **Step 4: 检查 session 菜单、检查器与主题细节**

检查 SessionSidebar 的 Radix DropdownMenu.Portal 不受检查器显示层裁切；SessionInspector 的关闭按钮保留 Tooltip 和 aria-label。在浅色主题验证正文、次级文字、琥珀审批与按钮焦点有足够对比度；在深色主题验证 Composer 不使用大面积高饱和蓝色。

- [ ] **Step 5: 验证并提交视觉收口源码**

运行：cd web/frontend && npm run build

若 Chromium 可用，运行所有本地 visual-system.spec.ts 用例，并截取 1440px 和 390px 截图审查。提交：

    git add web/frontend/src/styles/tokens.css web/frontend/src/styles/layout.css web/frontend/src/styles/components.css web/frontend/src/components/SessionInspector.tsx
    git commit -m "style: polish web workbench surfaces"

### Task 5: 文档、设计审查与最终验证

**Files:**
- Modify: README.md
- Modify: docs/README.md
- Modify: docs/PROJECT_ARCHITECTURE.md
- Modify: docs/TURNING_GOOD_AGENT_SPEC.md
- Modify: docs/phases/2026-06-15-phase-6-web-observability.md
- Modify: docs/superpowers/specs/2026-07-26-web-workbench-fixed-layout-design.md
- Modify: docs/superpowers/plans/2026-07-26-web-workbench-fixed-layout.md

**Interfaces:** 文档只描述已存在的 UI 行为：桌面检查器开关下的单列/双列居中布局、窄屏检查器覆盖层、全局工具权限菜单、真实事件活动流和已存在观测读取边界。

- [ ] **Step 1: 同步实现边界**

将本设计稿和本计划状态改为“已完成”，并在 Phase 6 文档、README、文档索引、架构文档和总 Spec 中明确：检查器不新增 JSONL 或 WebSocket 事件；关闭时主工作区单列居中，打开时右侧检查器列出现并使主会话容器右侧收缩；auto_approve_tools 仍是全局策略；审批和活动流只使用真实事件。

- [ ] **Step 2: 执行 UI 审查并修复真实问题**

依次使用 web-design-guidelines 审查当前 web/frontend/src/**/*.{ts,tsx,css} 的语义、焦点、键盘、动效、溢出与深浅主题；再使用 frontend-design-review 审查信息层级、控制面一致性、桌面/移动布局和审批可读性。只修复审查中可定位的问题；若修复代码，重复该任务前的 npm run build 与相关状态/E2E 测试。

- [ ] **Step 3: 运行完整验证**

运行：

    pytest -q
    cd web/frontend && npm run build
    cd ../..
    python -m compileall -q Turning-Good-Agent
    printf '/exit\n' | python -m Turning-Good-Agent chat
    git diff --check
    git rev-list --left-right --count origin/main...main

同时运行 Task 3 的本地状态测试；若 Chromium 已安装，运行 cd web/frontend && npx playwright test。缺失 Chromium 时报告该环境限制，不下载浏览器。

- [ ] **Step 4: 提交文档并推送**

    git add README.md
    git add -f docs/README.md docs/PROJECT_ARCHITECTURE.md docs/TURNING_GOOD_AGENT_SPEC.md docs/phases/2026-06-15-phase-6-web-observability.md docs/superpowers/specs/2026-07-26-web-workbench-fixed-layout-design.md docs/superpowers/plans/2026-07-26-web-workbench-fixed-layout.md
    git commit -m "docs: finalize web workbench layout"
    git push origin main
    git rev-list --left-right --count origin/main...main

预期最后一条输出为 0 0。不得暂存测试、本地 Playwright 配置或无关 HTML 文件。

## Plan Self-Review

1. 检查器开关下的单列/双列布局、移动覆盖层与“消息列和 Composer 始终等宽且居中”由 Task 1 和 Task 4 覆盖。
2. 两项权限菜单、全局 API 复用、无冗余说明和同槽发送/Stop 由 Task 2 覆盖。
3. 真实 TaskEvent、低对比度活动流、无 reasoning 和琥珀审批卡由 Task 3 覆盖。
4. 大圆角、深浅主题、菜单/检查器溢出、独立滚动区域由 Task 4 覆盖。
5. 文档同步、两次 UI 审查、Python/前端/CLI/可选浏览器验证、窄范围提交与推送由 Task 5 覆盖。
6. 计划未引入后端状态、事件、接口或持久化变更；所有本地测试明确排除暂存。

# Workbench Inspector, Slash, and Settings Visual Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use \`superpowers:executing-plans\` to implement this plan task-by-task. Steps use checkbox (\`- [ ]\`) syntax for tracking.

**Goal:** Refine the existing Phase 6 desktop workbench so the inspector reads continuously, the Slash catalog is a keyboard-stable Composer surface, and Settings has clear editing and Apply hierarchy.

**Architecture:** This is a frontend-only refinement of existing components and REST consumers. \`SessionInspector\` retains its observability and control read models; \`SlashCommandMenu\` continues consuming the server-owned Command Catalog; \`SettingsWorkspace\`, \`ConfigEditor\`, and \`ToolPermissionEditor\` retain their existing loading, field error, and polling behavior. Styles stay centralized in \`components.css\`; markup changes only add semantic state or keyboard support.

**Tech Stack:** React, TypeScript, Vite, \`lucide-react\`, Playwright.

## Global Constraints

- Modify only \`web/frontend\` source and Playwright tests. Do not modify Python, REST contracts, Runtime behavior, or WebSocket flows.
- Follow \`DESIGN.md\`: regions, inputs, hover, selection, and disclosure use backgrounds, whitespace, and restrained shadows. Do not add visible fine borders or focus outlines.
- Preserve the sidebar, empty state, topbar, Composer permission control, Composer send/stop controls, settings Hash route, and config polling behavior.
- Keep Settings as the independent \`#settings\` work surface with a single editor column; do not turn it into a drawer or modal.
- Keep catalog contents server-owned. Do not synthesize slash commands or disconnected MCP entries.
- \`/context\` and \`/tools\` keep their inspector-read actions without a chat send. Skill and connected-MCP selections insert only catalog-provided \`insert_text\`.
- Use the current icon family and theme tokens. Add no dependency, gradient, decoration, or persistent browser state.
- Extend Mock Playwright coverage before source changes. Real backend end-to-end validation is outside this plan.
- Do not commit; the current working tree contains user-owned changes.

---

### Task 1: Make the inspector a continuous reading surface

**Files:**
- Modify: \`web/frontend/src/components/SessionInspector.tsx\`
- Modify: \`web/frontend/src/styles/components.css\`
- Modify: \`web/frontend/tests/workbench_visual.spec.ts\`
- Modify: \`web/frontend/tests/control_plane_mock.spec.ts\`

**Interfaces:**
- Consumes existing \`Observability\`, \`SessionContextReadModel\`, and \`ToolCallPage\` props.
- Produces the same \`.inspector-summary\`, \`.inspector-sections\`, \`.inspector-section\`, \`.inspector-record\`, and \`.inspector-raw\` hierarchy with no new network request.
- Must not change \`App.openInspector\`, \`App.selectSlashCommand\`, existing pagination, or raw-record payloads.

- [ ] **Step 1: Write Mock tests for inspector reads and surface hierarchy**

Extend \`control_plane_mock.spec.ts\` with routes for Context and Tool Calls. Select \`/context\` and \`/tools\` through the Command Catalog, then assert the result appears in \`.inspector-body\`, Composer has no replacement message, and no chat-send request is made.

Add this visual assertion to \`workbench_visual.spec.ts\` after opening the topbar inspector button:

\`\`\`ts
const metrics = await page.locator(".inspector").evaluate((element) => {
  const summary = element.querySelector<HTMLElement>(".inspector-summary")!;
  const sections = element.querySelector<HTMLElement>(".inspector-sections")!;
  return {
    summaryBorder: getComputedStyle(summary).borderTopWidth,
    sectionsBorder: getComputedStyle(sections).borderTopWidth,
  };
});
expect(metrics.summaryBorder).toBe("0px");
expect(metrics.sectionsBorder).toBe("0px");
\`\`\`

- [ ] **Step 2: Run the focused regression baseline**

Run:

\`\`\`powershell
$env:PATH = 'C:\Users\stan\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin;C:\Users\stan\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback;' + $env:PATH
pnpm exec playwright test tests/workbench_visual.spec.ts tests/control_plane_mock.spec.ts
\`\`\`

Expected: existing tests pass. Newly introduced assertions identify any remaining border or individually elevated record surface.

- [ ] **Step 3: Refine the inspector markup and CSS hierarchy**

Keep the header limited to title and close control. Keep the summary definition list, but style it as one compact band. Keep first-level \`details.inspector-section\` as the primary background transition. Keep record fields hidden until the record is opened. Keep \`details.inspector-raw\` as the only deepest disclosure for JSON.

Render Context and Tool Calls with the same section/record/raw hierarchy, including local loading, empty, and error text. Do not create a separate card design for either read model.

Replace competing inspector overrides in \`components.css\` with one final hierarchy:

\`\`\`css
.inspector-summary,
.inspector-sections {
  border: 0;
  box-shadow: none;
  background: var(--surface);
}

.inspector-section > summary,
.inspector-record > summary {
  border: 0;
  box-shadow: none;
  background: transparent;
}

.inspector-section[open] > summary,
.inspector-section > summary:hover,
.inspector-record[open] > summary,
.inspector-record > summary:hover {
  background: var(--surface-hover);
}

.inspector-raw {
  border: 0;
  box-shadow: none;
  background: var(--surface-raised);
}
\`\`\`

Use nesting padding and surface depth, not borders or per-record shadows, to distinguish summary, section, record, fields, and raw JSON.

- [ ] **Step 4: Verify the inspector in both themes**

Run:

\`\`\`powershell
pnpm exec playwright test tests/workbench_visual.spec.ts tests/control_plane_mock.spec.ts
\`\`\`

Expected: Context and Tool Call reads remain inspector-only; summary, sections, records, and raw JSON no longer look like competing containers; existing desktop workbench tests stay green.

### Task 2: Add stable keyboard selection to the Composer Slash catalog

**Files:**
- Modify: \`web/frontend/src/components/SlashCommandMenu.tsx\`
- Modify: \`web/frontend/src/styles/components.css\`
- Modify: \`web/frontend/tests/control_plane_mock.spec.ts\`

**Interfaces:**
- Consumes current \`draft: string\`, \`onSelect(entry: CommandEntry)\`, and \`api.commands()\`.
- Produces pointer or keyboard selection of a catalog \`CommandEntry\`; \`onSelect\` remains the only bridge to \`App.selectSlashCommand\`.
- Must not change \`CommandEntry\`, Composer WebSocket send behavior, or catalog \`action\` semantics.

- [ ] **Step 1: Add failing Command Catalog interaction tests**

Use a mocked response with one direct command, one Skill, and one connected MCP Server. Assert exactly three options appear in one \`role="listbox"\`, with no synthetic category options. Then test this keyboard sequence:

\`\`\`ts
await page.getByLabel("消息内容").fill("/");
await page.keyboard.press("ArrowDown");
await page.keyboard.press("Enter");
await expect(page.locator(".inspector")).toBeVisible();
await expect(page.getByLabel("消息内容")).toHaveValue("");
\`\`\`

Add separate cases for ArrowUp wrap-around, Skill/MCP \`insert_text\`, and Escape. Escape must hide the panel but preserve the slash draft. Skill/MCP selection must not send a WebSocket message.

- [ ] **Step 2: Run the focused Slash baseline**

Run:

\`\`\`powershell
pnpm exec playwright test tests/control_plane_mock.spec.ts -g "Slash"
\`\`\`

Expected: the current mouse selection case passes; keyboard cases fail because the menu has no active option or key handling.

- [ ] **Step 3: Implement local active selection and dismissal**

In \`SlashCommandMenu.tsx\`:

1. Retain the current server fetch and client-side filtering.
2. Add local \`activeIndex\` and \`dismissedDraft\` state. Reset the index when query or entries change. Clear dismissal only after \`draft\` changes.
3. Register one cleanup-safe \`keydown\` listener while the menu is visible and the Composer textarea owns focus. Handle ArrowDown, ArrowUp, Enter, and Escape only, and prevent default for those keys.
4. Wrap the index across filtered entries. Enter invokes the existing selected entry. Escape dismisses exactly the current draft without editing it.
5. Give options stable ids, set \`aria-activedescendant\` on the listbox, and set \`aria-selected\` on the active option. Do not move focus out of Composer.

Continue to use \`Wrench\` for direct commands, \`Braces\` for Skill, and \`Plug\` for MCP. Keep all entries in one flat list; source is communicated only by icon and existing secondary text.

Retain the upward anchor and Composer width in \`components.css\`. The menu is one elevated surface; active or hovered rows use background only:

\`\`\`css
.slash-command-menu {
  border: 0;
  background: var(--surface-raised);
}

.slash-command-menu button:is(:hover, [aria-selected="true"], :focus-visible) {
  border: 0;
  box-shadow: none;
  background: var(--surface-hover);
}
\`\`\`

- [ ] **Step 4: Verify mouse, keyboard, and bounds behavior**

Run:

\`\`\`powershell
pnpm exec playwright test tests/control_plane_mock.spec.ts -g "Slash"
\`\`\`

Expected: pointer selection, Arrow keys, Enter, and Escape work while Composer stays focused; direct commands open existing inspector reads; Skill/MCP entries only insert text; the panel remains above Composer at narrow desktop widths.

### Task 3: Clarify Settings editing, status, and Apply priority

**Files:**
- Modify: \`web/frontend/src/components/SettingsWorkspace.tsx\`
- Modify: \`web/frontend/src/components/ConfigEditor.tsx\`
- Modify: \`web/frontend/src/components/ToolPermissionEditor.tsx\`
- Modify: \`web/frontend/src/styles/components.css\`
- Modify: \`web/frontend/tests/control_plane_mock.spec.ts\`
- Modify: \`web/frontend/tests/workbench_visual.spec.ts\`

**Interfaces:**
- Consumes current \`ControlConfig\`, \`ToolCatalog\`, \`ApiError.fieldErrors\`, and existing Config Apply/test/poll API functions.
- Produces \`data-state\` and \`data-dirty\` hooks on \`.settings-apply-bar\` without changing API payloads or polling.
- Must not change configuration field names, error keys, API-key write semantics, or Composer immediate permission behavior.

- [ ] **Step 1: Write failing Settings state and layout tests**

Add these assertions to \`control_plane_mock.spec.ts\`:

\`\`\`ts
await page.goto(baseUrl + "/#settings");
const applyBar = page.locator(".settings-apply-bar");
await expect(applyBar).toHaveAttribute("data-state", "active");
await expect(applyBar).toHaveAttribute("data-dirty", "false");

await page.getByLabel("模型名称").fill("new-model");
await expect(applyBar).toHaveAttribute("data-dirty", "true");
await expect(applyBar.getByText("未保存的修改")).toBeVisible();
\`\`\`

Mock an Apply result in \`pending\` followed by an \`active\` config read. Assert the bar changes from \`保存成功，等待当前任务结束\` to \`已生效 revision\`. Retain the 422 test and assert the invalid input has an \`aria-describedby\` reference to its nearby field error.

Add a \`workbench_visual.spec.ts\` case that checks \`borderTopWidth === "0px"\` for \`.settings-group\`, \`.tool-permission-row\`, and \`.settings-apply-bar\`, and verifies the Apply bar remains in the Settings scroll viewport after scrolling to the Tool rows.

- [ ] **Step 2: Run the focused Settings baseline**

Run:

\`\`\`powershell
pnpm exec playwright test tests/control_plane_mock.spec.ts -g "settings|configuration"
pnpm exec playwright test tests/workbench_visual.spec.ts -g "settings"
\`\`\`

Expected: current routing and polling tests remain green; dirty-state hooks, exact status copy, input-to-error linkage, and visual assertions fail before refinement.

- [ ] **Step 3: Implement state hooks and single-column surface hierarchy**

In \`ConfigEditor.tsx\`, retain the current \`hasChanges\` calculation and Apply/polling flow. Render the Apply footer with \`data-state={state.state}\` and \`data-dirty={hasChanges}\`.

Its leading message must represent only real state, in this priority order:

1. \`pending\`: \`保存成功，等待当前任务结束\`.
2. \`applying\`: \`正在替换 Runtime\`.
3. \`failed\`: existing server error plus \`旧 Runtime 仍可用。\`.
4. local difference: \`未保存的修改\`.
5. active baseline: \`已生效 revision：\` followed by the active revision.

Give each field error a stable id such as \`field-error-runtime-max-tool-rounds\`, and add it to the matching input's \`aria-describedby\` only while an error exists. Do not alter existing \`onApplied\`, error mapping, request-version protection, or cancelable polling.

In \`SettingsWorkspace.tsx\`, retain \`返回聊天\`, \`设置\`, and the single current location \`配置修改\`. Keep the right content column as the only editor surface. Do not add field-category navigation.

In \`ToolPermissionEditor.tsx\`, retain the separate final Tool approval section and catalog rules. Add semantic classes only where needed to style unavailable approvals as quiet removable rows, without a dialog or confirmation step.

Use larger gaps and heading rhythm in \`components.css\` to separate domains, not cards:

\`\`\`css
.settings-groups { gap: 32px; }
.settings-group { border: 0; box-shadow: none; background: transparent; padding: 0; }
.settings-field input,
.tool-permission-row,
.settings-apply-bar { border: 0; }
\`\`\`

Use \`--surface-raised\` for inputs and Tool rows. The sticky Apply bar is the sole elevated action surface, distinguished by background and soft shadow only. Use existing semantic colors for dirty, pending, applying, and failed states.

- [ ] **Step 4: Verify the complete Settings lifecycle**

Run:

\`\`\`powershell
pnpm exec playwright test tests/control_plane_mock.spec.ts -g "settings|configuration"
pnpm exec playwright test tests/workbench_visual.spec.ts -g "settings"
\`\`\`

Expected: the route remains separate from chat; local changes visibly remain unsaved until Apply; pending/active text reflects only polling outcomes; errors are accessible and local; configuration surfaces contain no visible border hierarchy.

### Task 4: Run frontend regression and prepare real-page handoff

**Files:**
- Test: \`web/frontend/tests/control_plane_mock.spec.ts\`
- Test: \`web/frontend/tests/workbench_visual.spec.ts\`

**Interfaces:**
- Consumes all three completed refinements.
- Produces test evidence only; no backend or persisted-state change.

- [ ] **Step 1: Run the full frontend quality gate**

Run:

\`\`\`powershell
pnpm run build
pnpm exec playwright test
git diff --check
\`\`\`

Expected: the Vite build succeeds, all frontend Playwright tests pass, and no whitespace error is reported.

- [ ] **Step 2: Perform real-page desktop review in light and dark themes**

Verify these flows after Mock tests pass:

1. Open inspector, a section, a record, and raw JSON. Confirm depth comes only from background and spacing.
2. Type \`/\`, navigate a direct command and a Skill/MCP entry with Arrow keys, Enter, and Escape. Confirm input focus and Composer controls remain stable.
3. Open Settings, edit a field, observe an inline field error, apply a valid change, and inspect pending then active status. Confirm the Apply bar stays visible inside the independent Settings scroll area.
4. Repeat in the other theme. Confirm all three surfaces remain low-saturation and free of fine border prompts.

- [ ] **Step 3: Hand off real end-to-end validation**

Report exact build and Mock Playwright results, then hand the unchanged backend contract to the primary session for real backend/end-to-end acceptance. Leave all Git changes uncommitted.

# Web Control Plane Frontend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add the approved standalone settings workspace, REST-only control-plane client, Tool approval editor, Composer Slash Command Catalog and inspector read models to the existing Phase 6 React workbench.

**Architecture:** The work starts from the already-present `SettingsWorkspace`, `#settings` Hash switch, sidebar entry, model-name editing prototype and two passing Mock Playwright cases. `App` keeps owning the chat/settings Hash view switch and preserves the existing WebSocket message flow. New REST DTOs stay in `api.ts`; local, unapplied control-plane state lives in focused components. Mock Playwright routes emulate the REST contract; no frontend path invents Runtime, Tool or command rules.

**Tech Stack:** React, TypeScript, Vite, existing Radix primitives, existing `lucide-react`, Playwright.

## Global Constraints

- Modify only `web/frontend` source and tests; do not alter Python backend code, REST behavior or WebSocket actions.
- Follow `DESIGN.md`: chat remains the default main work surface; settings is a separate full workspace; use surface hierarchy and spacing instead of card/border accumulation; all long content scrolls independently.
- Settings URL uses the browser Hash `#settings`; it must not require a FastAPI route addition.
- All unapplied fields, API-key entry and Tool approval diffs are React-local only and are discarded when SettingsWorkspace unmounts.
- `GET /api/control/tools` is only for SettingsWorkspace approval editing. `GET /api/control/commands` is only for Composer Slash actions.
- `/context` and `/tools` must never call `message.send`; selected Skill/MCP entries only insert server-provided text.
- When config is `pending` or `applying`, poll `GET /api/control/config` until `active` or `failed`; no control-plane WebSocket event exists.
- Composer retains `/api/settings/ui` and `patchUiSettings` for the immediate global “默认权限 / 完全访问” policy; SettingsWorkspace never reads or writes it.
- The existing `SettingsWorkspace` 250 ms recursive timer is transitional code: replace it with exactly one cancelable poller, monotonic request-version stale-result protection, and cleanup on unmount, leaving Settings, a new Apply, or a failed GET.
- The existing `control_plane_mock.spec.ts` two tests are a passing regression baseline. Extend and refactor that file; do not delete working coverage or claim the settings route/entry is absent.
- `Composer` retains `autoApprove`, `onAutoApproveChange`, `PermissionMenu`, `/api/settings/ui`, and `patchUiSettings`; verify that it remains independent of Config Apply.
- Do not commit changes; the user owns Git history.

---

### Task 1: Refactor the existing REST client to the complete control-plane contract

**Files:**
- Modify: `web/frontend/src/types.ts`
- Modify: `web/frontend/src/api.ts`
- Modify: `web/frontend/tests/control_plane_mock.spec.ts`

**Interfaces:**
- `ControlConfig`, `ConfigApplyRequest`, `ToolCatalog`, `CommandCatalog`, `SessionContextReadModel`, `ToolCallPage` and `McpServerSummary` exactly mirror the fields in `2026-07-29-web-control-plane-design.md`.
- `api.controlConfig()`, `api.applyControlConfig(payload)`, `api.testControlLlm(payload)`, `api.controlTools()`, `api.commands()`, `api.sessionContext(id)`, `api.toolCalls(id, cursor?)`, `api.mcpServers()` and `api.mcpServer(name)` only use documented REST paths.
- `ApiError` exposes optional `fieldErrors: Record<string, string>` after parsing HTTP 422 JSON.

- [ ] **Step 1: Extend the existing Mock contract test with failures the prototype cannot represent**

Keep the two current navigation/polling tests, replace their narrow `controlConfig` fixture with the full editable model, and add an Apply response that contains a top-level field error:

```ts
await page.route("**/api/control/config/apply", async (route) => {
  await route.fulfill({
    status: 422,
    contentType: "application/json",
    body: JSON.stringify({
      field_errors: { "runtime.max_tool_rounds": "必须大于 0" },
    }),
  });
});

await page.goto(`${baseUrl}/#settings`);
await page.getByLabel("最大工具轮数").fill("0");
await page.getByRole("button", { name: "应用配置" }).click();
await expect(page.getByText("必须大于 0")).toBeVisible();
```

- [ ] **Step 2: Run the focused test to verify the current contract gap**

Run: `pnpm exec playwright test tests/control_plane_mock.spec.ts`

Expected: the two existing tests remain PASS; the new field-error case FAILS because `ApiError` only preserves raw text, `applyControlConfig` accepts only `{ changes: { llm: { model } } }`, and SettingsWorkspace has no field-error mapping.

- [ ] **Step 3: Extend exact DTOs, error parsing and REST calls before changing components**

Extend `types.ts` with readonly API response types and write-only `ConfigApplyRequest`:

```ts
export type ConfigApplyRequest = {
  changes: {
    llm?: Partial<EditableControlConfig["llm"]> & {
      api_key?: string;
      clear_api_key?: boolean;
    };
    runtime?: Partial<EditableControlConfig["runtime"]>;
    memory?: Partial<EditableControlConfig["memory"]>;
    sessions?: Partial<EditableControlConfig["sessions"]>;
    skills?: Partial<EditableControlConfig["skills"]>;
  };
  approval_required_tools?: { add: string[]; remove: string[] };
};

export type ApiFieldErrors = Record<string, string>;
```

Extend `request` so a 422 JSON body with top-level `{ "field_errors": { "runtime.max_tool_rounds": "..." } }` creates `new ApiError(status, message, fieldErrors)`. For non-422 failures retain the textual/sanitized message and no `fieldErrors`. Add the documented route functions, URI-encoding session IDs and MCP names. Do not reintroduce `uiSettings` or `patchUiSettings` exports.

- [ ] **Step 4: Run DTO compilation and the expanded Mock test**

Run:

```powershell
pnpm run build
pnpm exec playwright test tests/control_plane_mock.spec.ts
```

Expected: build compiles the expanded DTOs; the original two settings tests still pass, and the new failure now reaches ConfigEditor rendering rather than failing at API parsing.

---

### Task 2: Refactor the existing settings workspace shell and preserve chat isolation

**Files:**
- Modify: `web/frontend/src/components/SettingsWorkspace.tsx`
- Modify: `web/frontend/src/App.tsx`
- Modify: `web/frontend/src/components/SessionSidebar.tsx`
- Modify: `web/frontend/src/components/Composer.tsx`
- Modify: `web/frontend/src/styles/layout.css`
- Modify: `web/frontend/src/styles/components.css`
- Modify: `web/frontend/tests/control_plane_mock.spec.ts`

**Interfaces:**
- `SettingsWorkspace({ onReturnToChat })` already renders only when `window.location.hash === "#settings"`; refactor its provisional `ConfigPreview` into a shell that will host ConfigEditor and preserves unmount-on-return behavior.
- `SessionSidebar` receives `onOpenSettings: () => void` and renders a left-bottom button with accessible name `打开设置`.
- `Composer` retains the immediate global approval menu; SettingsWorkspace does not receive that state or those callbacks.

- [ ] **Step 1: Extend the passing navigation test with final-shell assertions**

Keep the passing sidebar-to-Hash route test and add the following assertions:

```ts
await page.goto(baseUrl);
await page.getByRole("button", { name: "打开设置" }).click();
await expect(page).toHaveURL(/#settings$/);
await expect(page.getByRole("button", { name: "返回聊天" })).toBeVisible();
await expect(page.getByLabel("消息内容")).toHaveCount(0);
await expect(page.getByText("已生效 revision")).toBeVisible();
await expect(page.getByRole("button", { name: "测试连接" })).toBeVisible();
await page.getByRole("button", { name: "返回聊天" }).click();
await expect(page.getByLabel("消息内容")).toBeVisible();
```

- [ ] **Step 2: Run focused test to verify the shell needs refactoring**

Run: `pnpm exec playwright test tests/control_plane_mock.spec.ts -g "settings"`

Expected: the existing route/isolation assertions pass; the new revision and LLM-test action assertions fail because the current `ConfigPreview` is only a provisional model editor.

- [ ] **Step 3: Refactor the existing workspace and route integration**

Retain the existing Hash listener, sidebar bottom entry and chat tree unmount behavior. Preserve the last chat pathname before entering `#settings`; returning removes only the Hash and restores that pathname. Replace `ConfigPreview` with a SettingsWorkspace shell that owns initial config/catalog loading, unavailable/retry state, one navigation item `配置修改`, desired/active revision presentation and a slot for `ConfigEditor`. Give settings navigation and main content independent scroll containers. Do not add separate LLM/Runtime/Memory navigation and do not move any inspector content into this workspace.

- [ ] **Step 4: Apply the existing workbench design system to the refactored shell**

Use existing surface, radius and semantic token variables. The desktop settings shell uses a stable left navigation and 720-820px content column; main content never overlays chat because chat is unmounted in this view. On narrow viewports, collapse navigation to the return control and keep content single-column. Add focus-visible, reduced-motion and error/empty/loading styles without new dependencies.

- [ ] **Step 5: Verify the immediate-permission boundary and navigation test**

Run:

```powershell
pnpm exec playwright test tests/control_plane_mock.spec.ts -g "settings"
rg -n "autoApprove|onAutoApproveChange|PermissionMenu|/api/settings/ui|patchUiSettings" src/components/Composer.tsx src/App.tsx src/api.ts
```

Expected: settings test passes; `rg` shows the Composer/App/API immediate-policy path only, with no SettingsWorkspace or ConfigEditor match.

---

### Task 3: Implement local configuration editing, Tool approval diffs and reload polling

**Files:**
- Create: `web/frontend/src/components/ConfigEditor.tsx`
- Create: `web/frontend/src/components/ToolPermissionEditor.tsx`
- Modify: `web/frontend/src/components/SettingsWorkspace.tsx`
- Modify: `web/frontend/src/styles/components.css`
- Modify: `web/frontend/tests/control_plane_mock.spec.ts`

**Interfaces:**
- `ConfigEditor` owns a saved `ControlConfig` baseline and React-local edited scalar fields, API-key replacement intent, clear-key intent, `approval_required_tools` diff, field errors, a monotonically increasing request version and one polling cancellation handle.
- `ToolPermissionEditor({ catalog, selectedNames, onChange })` emits only the final add/remove-compatible selection; unavailable approval names are removal-only.
- `ConfigEditor` calls `onApplied(config)` after a successful Apply response and after poll reaches a terminal state.

- [ ] **Step 1: Add failing Apply, error, Tool-diff and cancellation tests**

Mock the Apply response as `pending`, then fulfill the next config GET as `active`:

```ts
await page.getByLabel("模型名称").fill("new-model");
await page.getByRole("button", { name: "应用配置" }).click();
await expect.poll(() => configGetCount).toBeGreaterThan(1);
await expect(page.getByText("配置已生效")).toBeVisible();

await page.getByLabel("最大工具轮数").fill("0");
await page.getByRole("button", { name: "应用配置" }).click();
await expect(page.getByText("必须大于 0")).toBeVisible();

await expect.poll(() => configGetCount).toBeGreaterThan(1);
await page.getByRole("button", { name: "返回聊天" }).click();
const readsAfterLeavingSettings = configGetCount;
await page.waitForTimeout(1_100);
expect(configGetCount).toBe(readsAfterLeavingSettings);
```

- [ ] **Step 2: Run focused test to verify failure**

Run: `pnpm exec playwright test tests/control_plane_mock.spec.ts -g "Apply"`

Expected: FAIL because the provisional `ConfigPreview` does not retain field-local diffs, cannot render errors or tool changes, and owns an uncancelled 250 ms recursive poll.

- [ ] **Step 3: Implement field-local state and Apply payload derivation**

Initialize a local baseline from `api.controlConfig()`. Compute changed scalar maps by comparing typed field paths to that baseline. Only include changed paths, `api_key` replacement, `clear_api_key`, and Tool add/remove in `ConfigApplyRequest`; global auto approval stays on the Composer immediate path. Do not use localStorage, session cache or URL parameters. On 422 map dotted `fieldErrors` to field help text. On 404 or request failure show a retryable control-plane-unavailable section. `POST /api/control/config/test-llm` receives only current LLM candidate fields; its 200, 422 and 502 results never change the saved baseline, Apply state or chat history.

- [ ] **Step 4: Implement polling and lifecycle cleanup**

Replace the 250 ms recursive timer with one scheduled `api.controlConfig()` loop, using a single timer ref and a monotonically increasing request version. Every Apply, retry and poll captures its version; a completion may update React state only if it still matches the latest version. Stop and clear the timer on terminal `active`, terminal `failed`, a failed GET, a new Apply, SettingsWorkspace unmount, or leaving `#settings`. Terminal `active` updates the baseline; terminal `failed` leaves editable values available and exposes `last_apply_error`. Do not poll while the initial state is active or after an LLM test. The retry action first performs a fresh GET and only schedules another read if that response is `pending` or `applying`.

- [ ] **Step 5: Implement Tool permission editor**

Fetch `api.controlTools()` only in SettingsWorkspace. Display active core and connected MCP tools with accessible switches that alter local selection. Render `unavailable_approval_required` below them with only a `移除` control. Never offer a raw Tool-name input. Submit the editor's final selection as set-difference add/remove arrays with Config Apply.

- [ ] **Step 6: Run mock interaction tests**

Run: `pnpm exec playwright test tests/control_plane_mock.spec.ts -g "Apply|Tool"`

Expected: PASS for pending-to-active polling, field error placement and removal-only unavailable MCP approval entries.

---

### Task 4: Add Composer Slash menu and inspector control read models

**Files:**
- Create: `web/frontend/src/components/SlashCommandMenu.tsx`
- Modify: `web/frontend/src/components/Composer.tsx`
- Modify: `web/frontend/src/App.tsx`
- Modify: `web/frontend/src/components/SessionInspector.tsx`
- Modify: `web/frontend/src/styles/components.css`
- Modify: `web/frontend/tests/control_plane_mock.spec.ts`

**Interfaces:**
- `SlashCommandMenu({ draft, onSelect })` fetches a new `CommandCatalog` only on a fresh `/` panel opening.
- `onSelect(entry)` returns either `{ kind: "inspect", section: "context" | "tools" }` or `{ kind: "insert", text: entry.insert_text }`; the component never sends a message.
- `SessionInspector` accepts optional control read-model data and requests only its opened Context, Tool Calls or MCP section.

- [ ] **Step 1: Add failing command behavior tests**

```ts
await page.getByLabel("消息内容").fill("/");
await expect(page.getByRole("listbox", { name: "Slash 命令" })).toBeVisible();
await page.getByRole("option", { name: "查看上下文" }).click();
await expect(page.getByRole("heading", { name: "会话检查器" })).toBeVisible();
expect(messageSendCount).toBe(0);

await page.getByLabel("消息内容").fill("/");
await page.getByRole("option", { name: "release-review" }).click();
await expect(page.getByLabel("消息内容")).toHaveValue("请优先参考 Skill「release-review」：");
```

- [ ] **Step 2: Run focused test to verify failure**

Run: `pnpm exec playwright test tests/control_plane_mock.spec.ts -g "Slash"`

Expected: FAIL because Composer has no Command Catalog overlay.

- [ ] **Step 3: Implement Slash Command Catalog overlay**

Open only for a `/`-prefixed draft. Fetch commands once per opening; close on empty draft, Escape, outside click or action selection. Anchor the listbox directly above Composer, constrain its height to the viewport and retain textarea focus after insert actions. Use server `kind`, `icon`, `label`, `description`, `action` and `insert_text`; never derive commands locally.

- [ ] **Step 4: Implement inspector read actions**

For `/context`, open inspector and fetch `api.sessionContext(currentSessionId)`. For `/tools`, fetch the first `api.toolCalls(currentSessionId)` page; use `next_cursor` only after explicit load-more. Add MCP list/detail as a read-only inspector section. Keep the existing observability summary intact, use independent section errors and render raw JSON only inside expanded record details.

- [ ] **Step 5: Run Slash and inspector mocks**

Run: `pnpm exec playwright test tests/control_plane_mock.spec.ts -g "Slash|inspector"`

Expected: PASS; inspect actions issue REST reads but no `message.send`, while Skill/MCP actions only alter the draft.

---

### Task 5: Verify visual integration and browser behavior

**Files:**
- Modify: `web/frontend/tests/workbench_visual.spec.ts`
- Test: `web/frontend/tests/control_plane_mock.spec.ts`

**Interfaces:**
- Existing workbench visual tests continue to cover chat; new tests cover settings deep surface, Composer anchoring and both themes.

- [ ] **Step 1: Add visual assertions**

Add tests that open `#settings` in dark and light themes, assert that the settings navigation surface differs from the main surface, and measure that `.slash-command-menu` ends at or above the Composer top edge.

```ts
const relation = await page.evaluate(() => {
  const menu = document.querySelector<HTMLElement>(".slash-command-menu")!;
  const composer = document.querySelector<HTMLElement>(".composer")!;
  return composer.getBoundingClientRect().top - menu.getBoundingClientRect().bottom;
});
expect(relation).toBeGreaterThanOrEqual(0);
```

- [ ] **Step 2: Run complete frontend validation**

Run:

```powershell
pnpm run build
pnpm exec playwright test
git diff --check
```

Expected: TypeScript build and all mock/visual Playwright tests pass; diff check reports no whitespace errors. Record unrelated failures without repairing them.

## Self-Review

### Spec coverage

- Standalone settings workspace, one configuration section, design-system rules and local draft disposal: Task 2.
- Exact config/Tool REST contracts, error handling, pending/applying polling and no legacy settings API: Tasks 1 and 3.
- Composer-upward Command Catalog and no-message inspect actions: Task 4.
- Context, paged Tool Calls and MCP inspection: Task 4.
- Mock-first and theme/layout validation: Tasks 1-5.

### Placeholder scan

This plan contains no unspecified implementation or validation behavior.

### Type consistency

`ControlConfig`, `ConfigApplyRequest`, `ConfigEditor`, `ToolPermissionEditor`, `SlashCommandMenu`, `SettingsWorkspace` and the REST functions use the same names across all tasks.

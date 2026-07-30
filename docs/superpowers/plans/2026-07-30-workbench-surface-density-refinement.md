# Workbench Surface Density Refinement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Slash catalog quieter and self-dismissing, make the Settings work surface readable in both themes, and preserve the existing continuous Inspector.

**Architecture:** This is a frontend-only visual and interaction refinement. `SlashCommandMenu` owns only the visibility of its local catalog overlay. `ConfigEditor` exposes semantic status markup while `components.css` owns all spacing, background depth, and sticky action treatment. A live Playwright spec exercises a running local API and browser, while the existing mocked regression suite remains compatibility coverage only.

**Tech Stack:** React, TypeScript, CSS, Vite, Playwright.

## Global Constraints

- Do not modify Python, REST contracts, Runtime behavior, WebSocket payloads, sidebar, empty state, topbar, or Inspector data models.
- Follow `DESIGN.md`: use only background surfaces, spacing, typography, and restrained shadows to distinguish regions. Do not add visible fine borders or outline focus states.
- Preserve the flat server-owned Slash Catalog, its icons, Arrow-key selection, Enter action, and Escape behavior that retains the slash draft.
- Keep Settings as an independent Hash-route work surface with a single editor column and a sticky Apply action.
- The new acceptance spec must make real requests to the local API and must not route, fulfill, or mock browser network traffic.
- Do not commit or push. Preserve unrelated user-owned changes.

---

### Task 1: Add real-page red tests for the confirmed behavior

**Files:**
- Create: `web/frontend/tests/workbench_real_page.spec.ts`
- Modify: `web/frontend/tests/control_plane_mock.spec.ts`

**Interfaces:**
- Consumes `TGA_REAL_PAGE=1`, `TGA_WEB_URL`, the live Session API, and the current browser UI.
- Produces opt-in real-page assertions for a non-archived session, with no request interception or persistent API mutation.

- [ ] **Step 1: Write the opt-in real Slash test**

```ts
test("real page hides Slash catalog when focus leaves Composer without clearing draft", async ({ page }) => {
  await page.goto(sessionUrl);
  await page.getByLabel("消息内容").fill("/");
  await expect(page.getByRole("listbox", { name: "Slash 命令" })).toBeVisible();
  await page.getByRole("button", { name: "打开会话检查器" }).click();
  await expect(page.getByRole("listbox", { name: "Slash 命令" })).toBeHidden();
  await expect(page.getByLabel("消息内容")).toHaveValue("/");
});
```

- [ ] **Step 2: Run the real Slash test before production changes**

Run: `TGA_REAL_PAGE=1 pnpm exec playwright test tests/workbench_real_page.spec.ts -g "Slash"`

Expected: FAIL because opening the Inspector leaves the Slash overlay visible.

- [ ] **Step 3: Write the real Settings geometry test**

```ts
await page.goto(`${baseUrl}/#settings`);
await expect(page.getByText("清除已配置 API Key")).toBeVisible();
const geometry = await page.evaluate(() => ({ clear: box(".settings-clear-api-key"), apply: box(".settings-apply-bar") }));
expect(geometry.clear.bottom).toBeLessThanOrEqual(geometry.apply.top);
```

- [ ] **Step 4: Run the real Settings test before production changes**

Run: `TGA_REAL_PAGE=1 pnpm exec playwright test tests/workbench_real_page.spec.ts -g "Settings"`

Expected: FAIL because the current sticky bar overlays the clear-key control at desktop height.

---

### Task 2: Refine Slash density and outside dismissal

**Files:**
- Modify: `web/frontend/src/components/SlashCommandMenu.tsx`
- Modify: `web/frontend/src/styles/components.css`

**Interfaces:**
- Consumes the existing `draft` and `onSelect(entry)` contract.
- Produces the existing listbox semantics while dismissing only the overlay when a pointer action occurs outside `.composer`.

- [ ] **Step 1: Add cleanup-safe outside-pointer dismissal**

```ts
const onPointerDown = (event: PointerEvent) => {
  if (!(event.target instanceof Element) || event.target.closest(".composer")) return;
  setDismissedDraft(draft);
};
document.addEventListener("pointerdown", onPointerDown, true);
return () => document.removeEventListener("pointerdown", onPointerDown, true);
```

- [ ] **Step 2: Compact the visual catalog without changing data**

```css
.slash-command-menu { max-height: min(264px, 42dvh); }
.slash-command-menu button { min-height: 44px; }
.slash-command-menu button small { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
```

- [ ] **Step 3: Verify the red test and existing keyboard tests**

Run: `TGA_REAL_PAGE=1 pnpm exec playwright test tests/workbench_real_page.spec.ts -g "Slash"`

Expected: PASS. The catalog hides on Inspector click, `/` remains in Composer, and no chat message is sent.

---

### Task 3: Repair Settings surface depth and persistent action priority

**Files:**
- Modify: `web/frontend/src/components/ConfigEditor.tsx`
- Modify: `web/frontend/src/styles/components.css`
- Modify: `web/frontend/tests/control_plane_mock.spec.ts`

**Interfaces:**
- Consumes the existing `ControlConfig` state, field errors, Apply action, and polling flow.
- Produces `settings-editor`, `settings-editor-scroll`, `settings-clear-api-key`, `settings-apply-status`, and `settings-apply-revision` semantic elements with unchanged API payloads.

- [ ] **Step 1: Make the clear-key control a horizontal semantic row**

```tsx
<label className="settings-clear-api-key">
  <input aria-label="清除 API Key" type="checkbox" ... />
  <span>清除已配置 API Key</span>
</label>
```

- [ ] **Step 2: Separate status from shortened revision metadata**

```tsx
<span className="settings-apply-status">已生效</span>
<span className="settings-apply-revision" title={state.active_revision}>revision {state.active_revision.slice(0, 14)}</span>
```

- [ ] **Step 3: Keep the Apply bar from covering the editor**

```css
.settings-editor { display: grid; grid-template-rows: minmax(0, 1fr) auto; min-height: 0; }
.settings-editor-scroll { min-height: 0; overflow: auto; }
.settings-apply-bar { position: static; }
.settings-clear-api-key { min-height: 36px; display: flex; align-items: center; gap: 8px; }
```

- [ ] **Step 4: Increase light-theme input separation through surface depth**

```css
:root[data-theme="light"] .settings-field input { background: color-mix(in srgb, var(--surface-raised) 84%, var(--page)); }
```

- [ ] **Step 5: Update only compatibility assertions affected by revision markup**

Run: `pnpm exec playwright test tests/control_plane_mock.spec.ts -g "settings"`

Expected: PASS. Existing contract tests still recognize active, pending, failure, and field-error state.

---

### Task 4: Run real visual acceptance

**Files:**
- Test only: `web/frontend/tests/workbench_real_page.spec.ts`

- [ ] **Step 1: Run build and static regression**

Run: `pnpm run build; pnpm exec playwright test; git diff --check`

Expected: Build and existing tests pass. Any real-page spec is skipped without `TGA_REAL_PAGE=1`.

- [ ] **Step 2: Run opt-in live browser acceptance**

Run: `TGA_REAL_PAGE=1 pnpm exec playwright test tests/workbench_real_page.spec.ts`

Expected: PASS against `127.0.0.1:8000`, with no intercepted network requests and no persisted configuration change.

- [ ] **Step 3: Inspect screenshots in dark and light themes**

Verify a populated session with Slash, Inspector, expanded Trace, Settings at the top, Settings at the Tool section, valid/invalid field state, and a long Composer draft. Confirm every hierarchy change is expressed by surfaces and spacing rather than visible fine borders.

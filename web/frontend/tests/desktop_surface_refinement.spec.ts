import { expect, test, type Page } from "@playwright/test";

const baseUrl = process.env.TGA_WEB_URL || "http://127.0.0.1:8000";

const session = {
  id: "desktop-surface-review",
  channel: "web",
  title: "桌面视觉验收",
  pinned: false,
  archived: false,
  created_at: "2026-07-30T00:00:00Z",
  updated_at: "2026-07-30T00:00:00Z",
};

const editable = {
  llm: { provider: "openai-compatible", api_key_configured: true, base_url: "https://api.example.test/v1", model: "example-model", timeout_seconds: 60, max_retries: 2, retry_delay_seconds: 0.5, streaming_enabled: true },
  runtime: { max_tool_rounds: 5, max_tool_calls_per_round: 8, parallel_tool_calls_enabled: true, max_parallel_tool_calls: 4, turn_timeout_seconds: 120, max_context_tokens: 300000, max_tool_result_tokens: 8000 },
  memory: { compact_token_threshold: 200000, recent_window_token_limit: 20000 },
  sessions: { retention_days: 7 },
  skills: { max_loaded_skills_per_turn: 3, max_skill_tokens: 8000, max_loaded_skill_tokens_per_turn: 16000 },
  tool_permissions: { auto_approve_tools: false, approval_required_tools: ["exec"] },
};

async function mockSession(page: Page) {
  await page.route(/\/api\/sessions\?archived=false$/, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify([session]) }));
  await page.route(/\/api\/sessions\?archived=true$/, (route) => route.fulfill({ contentType: "application/json", body: "[]" }));
  await page.route(`**/api/sessions/${session.id}/messages`, (route) => route.fulfill({ contentType: "application/json", body: "[]" }));
}

test("Settings loading mirrors the final parameter-row geometry", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.route("**/api/control/config", async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1_200));
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ desired_revision: "sha256:desired", active_revision: "sha256:active", state: "active", last_apply_error: null, desired: editable, active: editable }) });
  });
  await page.route("**/api/control/tools", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ active_revision: "sha256:active", tools: [], unavailable_approval_required: [] }) }));

  await page.goto(`${baseUrl}/#settings`);

  const skeleton = page.locator(".settings-loading-skeleton");
  await expect(skeleton).toBeVisible();
  await expect(skeleton.locator(".settings-skeleton-row")).toHaveCount(6);
  await expect(skeleton.locator(".settings-skeleton-row").first()).toHaveCSS("min-height", "54px");
});

test("Settings centers editor and Apply bar in the workspace after navigation", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(() => localStorage.setItem("tga-theme", "light"));
  await page.route("**/api/control/config", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ desired_revision: "sha256:desired", active_revision: "sha256:active", state: "active", last_apply_error: null, desired: editable, active: editable }) }));
  await page.route("**/api/control/tools", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ active_revision: "sha256:active", tools: [], unavailable_approval_required: [] }) }));

  await page.goto(`${baseUrl}/#settings`);
  await expect(page.getByLabel("模型名称")).toBeVisible();

  const [navigation, column, apply] = await Promise.all([
    page.locator(".settings-navigation").boundingBox(),
    page.locator(".settings-content-column").boundingBox(),
    page.locator(".settings-apply-bar").boundingBox(),
  ]);
  expect(navigation).not.toBeNull();
  expect(column).not.toBeNull();
  expect(apply).not.toBeNull();
  const expectedCenter = navigation!.x + navigation!.width + (1440 - navigation!.x - navigation!.width) / 2;
  const geometry = {
    expectedCenter,
    columnCenter: column!.x + column!.width / 2,
    columnLeft: column!.x,
    columnRight: column!.x + column!.width,
    applyLeft: apply!.x,
    applyRight: apply!.x + apply!.width,
  };
  expect(Math.abs(geometry.columnCenter - geometry.expectedCenter)).toBeLessThanOrEqual(1);
  expect(Math.abs(geometry.applyLeft - geometry.columnLeft)).toBeLessThanOrEqual(1);
  expect(Math.abs(geometry.applyRight - geometry.columnRight)).toBeLessThanOrEqual(1);
  await expect(page.getByLabel("模型名称")).toHaveCSS("background-color", "rgb(245, 245, 246)");
});

test("Dialogs use surfaces instead of thin input and neutral-button borders", async ({ page }) => {
  await mockSession(page);
  await page.goto(`${baseUrl}/sessions/${session.id}`);
  await page.getByRole("button", { name: `${session.title} 会话操作` }).click();
  await page.getByRole("menuitem", { name: "重命名" }).click();

  const input = page.getByLabel("会话名称");
  const cancel = page.getByRole("button", { name: "取消" });
  const save = page.getByRole("button", { name: "保存" });
  for (const control of [input, cancel, save]) {
    await expect(control).toHaveCSS("border-top-width", "0px");
  }
  await expect(page.locator(".rename-dialog")).toHaveCSS("border-top-width", "0px");
});

test("Inspector opens with stable summary and group skeletons, then uses continuous hierarchy", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockSession(page);
  await page.route(`**/api/sessions/${session.id}/observability`, async (route) => {
    await new Promise((resolve) => setTimeout(resolve, 1_200));
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ session, token_usage: [{ turn_id: "turn-1", input_tokens: 100, output_tokens: 20 }], traces: [{ turn_id: "turn-1", state: "RUN", duration_ms: 120 }], tool_calls: [] }) });
  });

  await page.goto(`${baseUrl}/sessions/${session.id}`);
  await page.getByRole("button", { name: "打开会话检查器" }).click();

  await expect(page.locator(".inspector-loading-skeleton")).toBeVisible();
  await expect(page.locator(".inspector-skeleton-summary > span")).toHaveCount(4);
  await expect(page.locator(".inspector-skeleton-groups > span")).toHaveCount(3);

  await expect(page.locator(".inspector-summary")).toBeVisible();
  const sectionSummary = page.locator(".inspector-section").first().locator(":scope > summary");
  await sectionSummary.click();
  await page.mouse.move(0, 0);
  const record = page.locator(".inspector-record").first();
  const recordSummary = record.locator(":scope > summary");
  await expect(recordSummary).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
  await recordSummary.click();
  await expect(recordSummary).not.toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
  const hierarchy = await page.evaluate(() => {
    const section = document.querySelector<HTMLElement>(".inspector-section")!;
    const record = document.querySelector<HTMLElement>(".inspector-record")!;
    const sectionStyle = getComputedStyle(section);
    const recordStyle = getComputedStyle(record);
    return { sectionBackground: sectionStyle.backgroundColor, sectionBorder: sectionStyle.borderTopWidth, recordBackground: recordStyle.backgroundColor };
  });
  expect(hierarchy).toEqual({ sectionBackground: "rgba(0, 0, 0, 0)", sectionBorder: "0px", recordBackground: "rgba(0, 0, 0, 0)" });
});

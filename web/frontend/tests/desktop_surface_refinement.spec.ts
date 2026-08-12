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
  multi_agent: { enabled: true, run_timeout_seconds: 60000, worker_timeout_seconds: 20000, max_workers_per_run: 4, max_concurrent_workers_per_run: 4, max_concurrent_workers_global: 8, worker_result_token_limit: 8000, parent_result_token_limit: 16000 },
  memory: { compact_token_threshold: 200000, recent_window_token_limit: 20000 },
  sessions: { retention_days: 7 },
  skills: { max_loaded_skills_per_turn: 3, max_skill_tokens: 8000, max_loaded_skill_tokens_per_turn: 16000 },
  proactive: {
    enabled: true,
    timezone: "Asia/Shanghai",
    review_provider: null,
    review_api_key_configured: false,
    review_base_url: null,
    review_model: null,
    background_max_concurrency: 4,
    breakbeat_refresh_minutes: 60,
    dream_refresh_hours: 24,
    review_window_token_limit: 100000,
    profile_total_token_limit: 16000,
    user_profile_token_limit: 12000,
    soul_profile_token_limit: 4000,
    skill_observation_turn_interval: 10,
    skill_observation_token_limit: 160,
    skill_evolution_batch_token_limit: 100000,
    skill_evolution_batches_per_kind: 3,
  },
  tool_permissions: { auto_approve_tools: false, approval_required_tools: ["exec"] },
};

async function mockSession(page: Page) {
  await page.route(/\/api\/sessions\?archived=false$/, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify([session]) }));
  await page.route(/\/api\/sessions\?archived=true$/, (route) => route.fulfill({ contentType: "application/json", body: "[]" }));
  await page.route(`**/api/sessions/${session.id}/messages`, (route) => route.fulfill({ contentType: "application/json", body: "[]" }));
}

async function installProactiveNoticeSocket(page: Page) {
  await page.addInitScript(() => {
    const sockets: Array<{ url: string; onopen: ((event: Event) => void) | null; onmessage: ((event: MessageEvent<string>) => void) | null; onclose: ((event: CloseEvent) => void) | null }> = [];
    class FakeWebSocket {
      static OPEN = 1;
      static CONNECTING = 0;
      static CLOSING = 2;
      static CLOSED = 3;
      readyState = FakeWebSocket.OPEN;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent<string>) => void) | null = null;
      onclose: ((event: CloseEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;

      constructor(readonly url: string) {
        sockets.push(this);
        queueMicrotask(() => this.onopen?.(new Event("open")));
      }

      send() {}

      close() {
        this.readyState = FakeWebSocket.CLOSED;
        this.onclose?.(new Event("close") as CloseEvent);
      }
    }
    Object.assign(window, {
      WebSocket: FakeWebSocket,
      __tgaEmitProactive: (payload: unknown) => sockets.filter((socket) => socket.url.includes("/ws/proactive")).forEach((socket) => socket.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>)),
    });
  });
}

async function mockEmptyAppShell(page: Page) {
  await page.route(/\/api\/sessions\?archived=false$/, (route) => route.fulfill({ contentType: "application/json", body: "[]" }));
  await page.route(/\/api\/sessions\?archived=true$/, (route) => route.fulfill({ contentType: "application/json", body: "[]" }));
  await page.route("**/api/settings/ui", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ auto_approve_tools: false }) }));
}

function proactiveNotice(id: string) {
  return {
    type: "notice",
    id,
    domain: "incident",
    entity_id: id,
    severity: "error",
    title: "需要处理的 Incident",
    message: "后台任务需要你的关注。",
    target: "#proactive/incidents",
    proactive_revision: 1,
    owner: { mode: "owner", writable: true, owner_id: "web-owner", owner_kind: "web", owner_pid: 42 },
  };
}

type Rectangle = { x: number; y: number; width: number; height: number };

function overlaps(left: Rectangle, right: Rectangle) {
  return left.x < right.x + right.width && right.x < left.x + left.width && left.y < right.y + right.height && right.y < left.y + left.height;
}

function contrastRatio(foreground: string, background: string) {
  const channels = (color: string) => {
    const values = color.match(/\d+/g)?.map(Number);
    if (!values || values.length < 3) throw new Error(`Unexpected CSS color: ${color}`);
    return values.slice(0, 3).map((value) => {
      const normalized = value / 255;
      return normalized <= 0.04045 ? normalized / 12.92 : ((normalized + 0.055) / 1.055) ** 2.4;
    });
  };
  const luminance = (color: string) => {
    const [red, green, blue] = channels(color);
    return red * 0.2126 + green * 0.7152 + blue * 0.0722;
  };
  const [lighter, darker] = [luminance(foreground), luminance(background)].sort((first, second) => second - first);
  return (lighter + 0.05) / (darker + 0.05);
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
  await expect(skeleton.locator(".settings-skeleton-row").nth(1)).toHaveCSS("border-top-width", "1px");
});

test("Settings centers editor and Apply bar in the workspace after navigation", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(() => localStorage.setItem("tga-theme", "light"));
  await page.route("**/api/control/config", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ desired_revision: "sha256:desired", active_revision: "sha256:active", state: "active", last_apply_error: null, desired: editable, active: editable }) }));
  await page.route("**/api/control/tools", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ active_revision: "sha256:active", tools: [], unavailable_approval_required: [] }) }));

  await page.goto(`${baseUrl}/#settings`);
  await expect(page.getByLabel("模型名称")).toBeVisible();

  const modelFields = page.locator(".settings-group").first().locator(":scope > .settings-field");
  await expect(modelFields.first()).toHaveCSS("border-top-width", "0px");
  await expect(modelFields.nth(1)).toHaveCSS("border-top-width", "1px");
  await expect(page.getByLabel("模型名称")).toHaveCSS("border-width", "0px");

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

for (const theme of ["dark", "light"] as const) {
  test(`Proactive notice stays top-centered and non-blocking in ${theme} theme`, async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await page.addInitScript((selectedTheme) => localStorage.setItem("tga-theme", selectedTheme), theme);
    await installProactiveNoticeSocket(page);
    await mockEmptyAppShell(page);
    await page.goto(baseUrl);

    const composer = page.getByRole("textbox", { name: "消息内容" });
    await composer.focus();
    await page.evaluate((payload) => (window as unknown as { __tgaEmitProactive: (value: unknown) => void }).__tgaEmitProactive(payload), proactiveNotice(`notice-${theme}`));

    const notice = page.locator(".notice");
    await expect(notice).toBeVisible();
    await expect(composer).toBeFocused();
    await expect(page.getByRole("dialog")).toHaveCount(0);
    await expect(notice).toHaveCSS("border-top-width", "0px");
    await expect(notice).toHaveCSS("pointer-events", "auto");
    await expect(page.locator(".notice-region")).toHaveCSS("pointer-events", "none");

    const [noticeBox, composerBox, conversationBox] = await Promise.all([notice.boundingBox(), composer.boundingBox(), page.locator(".conversation").boundingBox()]);
    expect(noticeBox).not.toBeNull();
    expect(composerBox).not.toBeNull();
    expect(conversationBox).not.toBeNull();
    expect(Math.abs(noticeBox!.x + noticeBox!.width / 2 - (conversationBox!.x + conversationBox!.width / 2))).toBeLessThanOrEqual(1);
    expect(noticeBox!.y).toBeGreaterThanOrEqual(68);
    expect(noticeBox!.y + noticeBox!.height).toBeLessThan(composerBox!.y);
    await expect(notice).not.toHaveCSS("background-color", "rgba(0, 0, 0, 0)");

    const colors = await notice.evaluate((node) => {
      const title = node.querySelector<HTMLElement>(".notice-content strong");
      const message = node.querySelector<HTMLElement>(".notice-content span");
      const icon = node.querySelector<HTMLElement>(".notice-severity-icon");
      return {
        background: getComputedStyle(node).backgroundColor,
        title: getComputedStyle(title!).color,
        message: getComputedStyle(message!).color,
        icon: getComputedStyle(icon!).color,
      };
    });
    expect(contrastRatio(colors.title, colors.background)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(colors.message, colors.background)).toBeGreaterThanOrEqual(4.5);
    expect(contrastRatio(colors.icon, colors.background)).toBeGreaterThanOrEqual(3);

    await page.setViewportSize({ width: 480, height: 900 });
    const narrowNotice = await notice.boundingBox();
    expect(narrowNotice).not.toBeNull();
    const topBarControls = await page.locator(".topbar button:not(:disabled)").evaluateAll((controls) => controls.map((control) => control.getBoundingClientRect().toJSON()));
    for (const control of topBarControls) expect(overlaps(narrowNotice!, control)).toBe(false);
  });
}

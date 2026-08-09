import { expect, test } from "@playwright/test";

const baseUrl = process.env.TGA_WEB_URL || "http://127.0.0.1:8000";

function controlConfig(state: "active" | "pending" | "applying" | "failed" = "active") {
  const editable = {
    llm: {
      provider: "openai-compatible",
      api_key_configured: true,
      base_url: "https://api.example.test/v1",
      model: "example-model",
      timeout_seconds: 60,
      max_retries: 2,
      retry_delay_seconds: 0.5,
      streaming_enabled: true,
    },
    runtime: {
      max_tool_rounds: 5,
      max_tool_calls_per_round: 8,
      parallel_tool_calls_enabled: true,
      max_parallel_tool_calls: 4,
      turn_timeout_seconds: 120,
      max_context_tokens: 300000,
      max_tool_result_tokens: 8000,
    },
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
  return {
    desired_revision: "sha256:desired",
    active_revision: "sha256:active",
    state,
    last_apply_error: null,
    desired: editable,
    active: editable,
  };
}

function toolCatalog() {
  return {
    active_revision: "sha256:active",
    tools: [{ name: "exec", description: "执行受控命令", source: { kind: "core" }, approval_required: true, effective_approval: "manual" }],
    unavailable_approval_required: ["mcp_offline_run"],
  };
}

function activeSession() {
  return {
    id: "session-control-read",
    channel: "web",
    title: "控制面读取测试",
    pinned: false,
    archived: false,
    created_at: "2026-07-30T00:00:00Z",
    updated_at: "2026-07-30T00:00:00Z",
  };
}

async function mockActiveSession(page: import("@playwright/test").Page) {
  const session = activeSession();
  await page.route(/\/api\/sessions\?archived=false$/, async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify([session]) });
  });
  await page.route(/\/api\/sessions\?archived=true$/, async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify([]) });
  });
  await page.route(`**/api/sessions/${session.id}/messages`, async (route) => {
    await route.fulfill({ contentType: "application/json", body: "[]" });
  });
  return session;
}

test("settings opens a separate configuration workspace from the sidebar", async ({ page }) => {
  await page.route("**/api/control/config", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) });
  });
  await page.route("**/api/control/tools", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(toolCatalog()) });
  });

  await page.goto(baseUrl);
  await page.getByRole("button", { name: "打开设置" }).click();

  await expect(page).toHaveURL(/#settings$/);
  await expect(page.getByRole("heading", { name: "配置修改" })).toBeVisible();
  await expect(page.getByRole("button", { name: "返回聊天" })).toBeVisible();
  await expect(page.getByLabel("模型名称")).toHaveValue("example-model");
  await expect(page.getByLabel("消息内容")).toHaveCount(0);
});

test("settings editor scrolls independently while the apply controls stay visible", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 720 });
  await page.route("**/api/control/config", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) });
  });
  await page.route("**/api/control/tools", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(toolCatalog()) });
  });

  await page.goto(`${baseUrl}/#settings`);
  const editor = page.locator(".settings-editor-scroll");
  const viewport = editor.locator(".scroll-area-viewport");
  await expect(viewport).toBeVisible();
  await viewport.evaluate((element) => { element.scrollTop = element.scrollHeight; });

  await expect.poll(() => viewport.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
  await expect(page.getByRole("button", { name: "应用配置" })).toBeInViewport();
});

test("settings keeps IM controls in the editor scroll area and clears sensitive failures", async ({ page }) => {
  const account = { id: "feishu-account", platform: "feishu", principal_id: "owner", status: "awaiting_owner_code", enabled: true, subscribed: false, credential_state: "configured", connected: false, app_id_masked: "cli••••mo" };
  await page.route("**/api/control/config", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) });
  });
  await page.route("**/api/control/tools", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(toolCatalog()) });
  });
  await page.route("**/api/control/channels", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ accounts: [account] }) });
  });
  await page.route("**/api/control/channels/feishu", async (route) => {
    await route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ detail: "super-secret-must-not-render" }) });
  });

  await page.goto(`${baseUrl}/#settings`);
  const scroll = page.locator(".settings-editor-scroll");
  await expect(scroll.getByRole("heading", { name: "IM Channels" })).toBeVisible();
  await expect(page.getByText("app_secret", { exact: false })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "订阅通知" })).toBeVisible();
  await expect(page.getByRole("button", { name: "停用并保留" })).toBeVisible();
  await expect(page.getByRole("button", { name: "轮换凭据" })).toBeVisible();
  await expect(page.getByRole("button", { name: "设置 Owner 验证码" })).toBeVisible();

  await page.getByLabel("飞书 App ID").fill("demo");
  await page.getByLabel("飞书 App Secret").fill("super-secret-must-not-render");
  await page.getByRole("button", { name: "登记飞书 Bot" }).click();

  await expect(page.getByLabel("飞书 App ID")).toHaveValue("");
  await expect(page.getByLabel("飞书 App Secret")).toHaveValue("");
  await expect(page.getByText("super-secret-must-not-render", { exact: false })).toHaveCount(0);
});

test("settings renders a local QR image when iLink returns a scan URL", async ({ page }) => {
  const account = { id: "weixin-binding", platform: "weixin", principal_id: "owner", status: "pending_qr", enabled: true, subscribed: false, credential_state: "pending", connected: false };
  await page.route("**/api/control/config", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) });
  });
  await page.route("**/api/control/tools", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(toolCatalog()) });
  });
  await page.route("**/api/control/channels", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ accounts: [account] }) });
  });
  await page.route("**/api/control/channels/weixin/weixin-binding/qr", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      headers: { "Cache-Control": "no-store" },
      body: JSON.stringify({ binding_id: account.id, status: "pending_qr", qr_content: "https://ilink.example/scan?token=opaque", expires_at: Math.floor(Date.now() / 1000) + 300 }),
    });
  });

  await page.goto(`${baseUrl}/#settings`);
  const image = page.getByAltText("微信登录二维码");
  await expect(image).toBeVisible();
  await expect.poll(() => image.getAttribute("src")).toMatch(/^data:image\/png;base64,/);
});

test("settings can restart an expired Weixin binding and show its new QR", async ({ page }) => {
  const expired = { id: "expired-binding", platform: "weixin", principal_id: "owner", status: "expired", enabled: false, subscribed: false, credential_state: "expired", connected: false };
  const pending = { ...expired, status: "pending_qr", enabled: true, credential_state: "pending" };
  let rescanCalled = false;
  await page.route("**/api/control/config", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) });
  });
  await page.route("**/api/control/tools", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(toolCatalog()) });
  });
  await page.route("**/api/control/channels", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ accounts: [expired] }) });
  });
  await page.route("**/api/control/channels/weixin/expired-binding/rescan", async (route) => {
    rescanCalled = true;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(pending) });
  });
  await page.route("**/api/control/channels/weixin/expired-binding/qr", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      headers: { "Cache-Control": "no-store" },
      body: JSON.stringify({ binding_id: pending.id, status: "pending_qr", qr_content: "https://ilink.example/scan?token=new", expires_at: Math.floor(Date.now() / 1000) + 300 }),
    });
  });

  await page.goto(`${baseUrl}/#settings`);
  await expect(page.getByRole("button", { name: "重新扫码" })).toBeVisible();
  await page.getByRole("button", { name: "重新扫码" }).click();

  await expect.poll(() => rescanCalled).toBe(true);
  await expect(page.getByAltText("微信登录二维码")).toBeVisible();
});

test("settings identifies IM roles and keeps a busy permanent-delete dialog open", async ({ page }) => {
  const busyMessage = "该 Binding 正在处理消息或等待回复投递，暂时不能删除。请等待本轮完成后再试";
  const integrityMessage = "独立主体数据关联异常，无法安全删除";
  const ownerWeixin = { id: "owner-weixin", platform: "weixin", principal_id: "opaque-owner", principal_kind: "owner", status: "active", enabled: true, subscribed: true, credential_state: "configured", connected: true };
  const independentWeixin = { id: "independent-weixin", platform: "weixin", principal_id: "opaque-independent", principal_kind: "independent", status: "active", enabled: true, subscribed: false, credential_state: "configured", connected: true };
  const ownerFeishu = { id: "owner-feishu", platform: "feishu", principal_id: "opaque-owner", principal_kind: "owner", status: "active", enabled: true, subscribed: false, credential_state: "configured", connected: true, app_id_masked: "cli••••mo" };
  let deleteAttempts = 0;
  let delayNextList = false;
  let staleListStarted = false;
  let releaseStaleList = () => {};
  const staleListGate = new Promise<void>((resolve) => { releaseStaleList = resolve; });
  await page.route("**/api/control/config", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) });
  });
  await page.route("**/api/control/tools", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(toolCatalog()) });
  });
  await page.route("**/api/control/channels", async (route) => {
    if (delayNextList) {
      delayNextList = false;
      staleListStarted = true;
      await staleListGate;
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ accounts: [ownerFeishu, ownerWeixin, independentWeixin] }) });
  });
  await page.route("**/api/control/channels/weixin/independent-weixin", async (route) => {
    if (route.request().method() !== "DELETE") return route.fallback();
    deleteAttempts += 1;
    if (deleteAttempts === 1) {
      await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: { code: "binding_busy", message: busyMessage } }) });
      return;
    }
    if (deleteAttempts === 2) {
      await route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: { code: "independent_principal_integrity_error", message: integrityMessage } }) });
      return;
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ deleted: true, account_id: independentWeixin.id, platform: independentWeixin.platform }) });
  });

  await page.goto(`${baseUrl}/#settings`);
  await expect(page.getByText("飞书 Bot · Owner", { exact: true })).toBeVisible();
  await expect(page.getByText("微信 Owner Binding · Owner", { exact: true })).toBeVisible();
  await expect(page.getByText("微信独立主体 Binding · 独立主体", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "微信 Owner Binding" })).toBeDisabled();
  await expect(page.getByText("已有 Owner Binding；请重新扫码或永久删除现有 Owner Binding 后再新建", { exact: true })).toBeVisible();

  const independentRow = page.locator(".channel-account-row").filter({ hasText: "微信独立主体 Binding · 独立主体" });
  delayNextList = true;
  await page.getByRole("button", { name: "刷新 IM 账号" }).click();
  await expect.poll(() => staleListStarted).toBe(true);
  await independentRow.getByRole("button", { name: "永久删除" }).click();
  const dialog = page.getByRole("alertdialog");
  await dialog.getByRole("button", { name: "确认永久删除" }).click();
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText(busyMessage, { exact: true })).toBeVisible();
  await expect(independentRow).toBeVisible();

  await dialog.getByRole("button", { name: "确认永久删除" }).click();
  await expect(dialog).toBeVisible();
  await expect(dialog.getByText(integrityMessage, { exact: true })).toBeVisible();
  await expect(dialog.getByText(busyMessage, { exact: true })).toHaveCount(0);

  await dialog.getByRole("button", { name: "确认永久删除" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(independentRow).toHaveCount(0);
  releaseStaleList();
  await page.waitForTimeout(250);
  await expect(independentRow).toHaveCount(0);
  await expect(page.getByText("微信 Owner Binding · Owner", { exact: true })).toBeVisible();
});

test("settings follows an active Weixin candidate from QR through first-DM ownership", async ({ page }) => {
  const pending = { id: "active-rebind", platform: "weixin", principal_id: "opaque-owner", principal_kind: "owner", status: "active", enabled: true, subscribed: true, credential_state: "configured", connected: true, rebind_state: "pending_qr" };
  const waiting = { ...pending, rebind_state: "waiting_for_idle" };
  const awaiting = { ...pending, status: "awaiting_first_dm", connected: false, rebind_state: undefined };
  const candidateStartedAt = Date.now();
  let firstQrAt = 0;
  let detailCalls = 0;
  await page.route("**/api/control/config", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) });
  });
  await page.route("**/api/control/tools", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(toolCatalog()) });
  });
  await page.route("**/api/control/channels", async (route) => {
    const candidateVisible = Date.now() - candidateStartedAt < 900;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ accounts: [candidateVisible ? pending : waiting] }) });
  });
  await page.route("**/api/control/channels/weixin/active-rebind/qr", async (route) => {
    if (!firstQrAt) firstQrAt = Date.now();
    const candidateVisible = Date.now() - firstQrAt < 900;
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ binding_id: pending.id, status: "active", qr_content: candidateVisible ? "https://ilink.example/scan?token=candidate" : null, expires_at: candidateVisible ? Math.floor(Date.now() / 1000) + 300 : null }),
    });
  });
  await page.route("**/api/control/channels/weixin/active-rebind", async (route) => {
    detailCalls += 1;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(detailCalls === 1 ? waiting : awaiting) });
  });

  await page.goto(`${baseUrl}/#settings`);
  await expect(page.getByAltText("微信登录二维码")).toBeVisible();
  await expect(page.getByRole("button", { name: "重新扫码并转移使用者" })).toBeVisible();
  await expect(page.getByText("候选凭据已就绪，正在等待当前回复结束", { exact: true })).toBeVisible({ timeout: 6_000 });
  await expect.poll(() => detailCalls).toBe(1);
  await page.waitForTimeout(700);
  expect(detailCalls).toBe(1);
  await expect(page.getByText("新使用者请发送第一条有效私聊，以完成 Binding 归属锁定。", { exact: true })).toBeVisible({ timeout: 6_000 });
  expect(detailCalls).toBe(2);
});

test("settings rejects stale list and late detail failure across a successful rescan", async ({ page }) => {
  const waiting = { id: "rebind-race", platform: "weixin", principal_id: "opaque-owner", principal_kind: "owner", status: "active", enabled: true, subscribed: true, credential_state: "configured", connected: true, rebind_state: "waiting_for_idle" };
  const pending = { ...waiting, rebind_state: "pending_qr" };
  let holdNextList = false;
  let heldListStarted = false;
  let releaseHeldList = () => {};
  const heldListGate = new Promise<void>((resolve) => { releaseHeldList = resolve; });
  await page.addInitScript(() => {
    const nativeFetch = window.fetch.bind(window);
    const rejectors: Array<(reason?: unknown) => void> = [];
    const controls = window as unknown as { __tgaOldDetailsStarted: number; __tgaRejectOldDetails: () => void };
    controls.__tgaOldDetailsStarted = 0;
    controls.__tgaRejectOldDetails = () => {
      rejectors.splice(0).forEach((reject) => reject(new Error("late old detail failure")));
    };
    window.fetch = (input: RequestInfo | URL, init?: RequestInit) => {
      const url = typeof input === "string" ? input : input instanceof Request ? input.url : input.toString();
      const method = (init?.method || (input instanceof Request ? input.method : "GET")).toUpperCase();
      if (method === "GET" && url.endsWith("/api/control/channels/weixin/rebind-race")) {
        controls.__tgaOldDetailsStarted += 1;
        return new Promise<Response>((_resolve, reject) => { rejectors.push(reject); });
      }
      return nativeFetch(input, init);
    };
  });
  await page.route("**/api/control/config", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) });
  });
  await page.route("**/api/control/tools", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(toolCatalog()) });
  });
  await page.route("**/api/control/channels", async (route) => {
    if (holdNextList) {
      holdNextList = false;
      heldListStarted = true;
      await heldListGate;
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ accounts: [waiting] }) });
  });
  await page.route("**/api/control/channels/weixin/rebind-race/rescan", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(pending) });
  });
  await page.route("**/api/control/channels/weixin/rebind-race/qr", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ binding_id: pending.id, status: "active", qr_content: "https://ilink.example/scan?token=new-candidate", expires_at: Math.floor(Date.now() / 1000) + 300 }) });
  });

  await page.goto(`${baseUrl}/#settings`);
  await expect(page.getByText("候选凭据已就绪，正在等待当前回复结束", { exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => (window as unknown as { __tgaOldDetailsStarted: number }).__tgaOldDetailsStarted)).toBeGreaterThan(0);
  holdNextList = true;
  await page.getByRole("button", { name: "刷新 IM 账号" }).click();
  await expect.poll(() => heldListStarted).toBe(true);
  await page.getByRole("button", { name: "重新扫码并转移使用者" }).click();
  await expect(page.getByAltText("微信登录二维码")).toBeVisible();

  releaseHeldList();
  await page.evaluate(() => (window as unknown as { __tgaRejectOldDetails: () => void }).__tgaRejectOldDetails());
  await page.waitForTimeout(250);
  await expect(page.getByAltText("微信登录二维码")).toBeVisible();
  await expect(page.getByText("候选凭据已就绪，正在等待当前回复结束", { exact: true })).toHaveCount(0);
  await expect(page.getByText("微信 Binding 状态暂不可用", { exact: true })).toHaveCount(0);
});

test("settings tests only editable LLM fields and uses text inputs with switches", async ({ page }) => {
  let testedChanges: Record<string, unknown> | null = null;
  await page.route("**/api/control/config", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) });
  });
  await page.route("**/api/control/tools", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(toolCatalog()) });
  });
  await page.route("**/api/control/config/test-llm", async (route) => {
    testedChanges = (route.request().postDataJSON() as { changes: Record<string, unknown> }).changes;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ ok: true, latency_ms: 42 }) });
  });

  await page.goto(`${baseUrl}/#settings`);
  await expect(page.getByText("编辑仅保留在当前浏览器页面，点击应用后将在空闲时生效。")).toHaveCount(0);
  await expect(page.getByLabel("请求超时（秒）")).toHaveAttribute("type", "text");

  const streaming = page.getByRole("switch", { name: "启用流式输出" });
  await expect(streaming).toHaveAttribute("aria-checked", "true");
  await streaming.click();
  await expect(streaming).toHaveAttribute("aria-checked", "false");

  await page.getByRole("button", { name: "测试连接" }).click();
  await expect.poll(() => testedChanges).not.toBeNull();
  expect(testedChanges).not.toHaveProperty("provider");
  expect(testedChanges).not.toHaveProperty("api_key_configured");
  expect(testedChanges).toMatchObject({ base_url: "https://api.example.test/v1", streaming_enabled: false });
  await expect(page.getByRole("status")).toContainText("连接成功，42 ms");
});

test("settings applies proactive fields and keeps the review API key redacted", async ({ page }) => {
  let proactiveChanges: Record<string, unknown> | null = null;
  await page.route("**/api/control/config", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) });
  });
  await page.route("**/api/control/tools", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(toolCatalog()) });
  });
  await page.route("**/api/control/config/apply", async (route) => {
    proactiveChanges = (route.request().postDataJSON() as { changes: { proactive: Record<string, unknown> } }).changes.proactive;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) });
  });

  await page.goto(`${baseUrl}/#settings`);
  await expect(page.getByLabel("启用主动能力")).toHaveAttribute("aria-checked", "true");
  await expect(page.getByLabel("替换审阅 API Key")).toHaveValue("");
  await page.getByLabel("主动能力时区").fill("America/New_York");
  await page.getByLabel("替换审阅 API Key").fill("review-secret");
  await page.getByRole("button", { name: "应用配置" }).click();

  await expect.poll(() => proactiveChanges).toMatchObject({
    timezone: "America/New_York",
    review_api_key: "review-secret",
  });
});

test("settings switches convey their state without visible on or off labels", async ({ page }) => {
  await page.route("**/api/control/config", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) });
  });
  await page.route("**/api/control/tools", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(toolCatalog()) });
  });

  await page.goto(`${baseUrl}/#settings`);

  const streaming = page.getByRole("switch", { name: "启用流式输出" });
  await expect(streaming).toHaveAttribute("aria-checked", "true");
  await expect(streaming).toHaveText("");
});

test("tool approval uses a switch and submits its changed membership", async ({ page }) => {
  let submittedApproval: { add: string[]; remove: string[] } | null = null;
  await page.route("**/api/control/config", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) });
  });
  await page.route("**/api/control/tools", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(toolCatalog()) });
  });
  await page.route("**/api/control/config/apply", async (route) => {
    submittedApproval = (route.request().postDataJSON() as { approval_required_tools: { add: string[]; remove: string[] } }).approval_required_tools;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) });
  });

  await page.goto(`${baseUrl}/#settings`);

  const approval = page.getByRole("switch", { name: "exec 需要审批" });
  await expect(approval).toHaveAttribute("aria-checked", "true");
  await approval.click();
  await expect(approval).toHaveAttribute("aria-checked", "false");
  await page.getByRole("button", { name: "应用配置" }).click();

  await expect.poll(() => submittedApproval).toEqual({ add: [], remove: ["exec"] });
});

test("settings uses compact parameter and capsule controls", async ({ page }) => {
  await page.route("**/api/control/config", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) });
  });
  await page.route("**/api/control/tools", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(toolCatalog()) });
  });

  await page.goto(`${baseUrl}/#settings`);

  const dimensions = await Promise.all([page.getByLabel("请求超时（秒）"), page.getByRole("switch", { name: "启用流式输出" })].map((control) => control.evaluate((element) => {
    const styles = getComputedStyle(element);
    return { width: styles.width, height: styles.height, radius: styles.borderRadius };
  })));
  expect(dimensions[0].height).toBe("34px");
  expect(dimensions[1]).toMatchObject({ width: "44px", height: "26px" });
});

test("tool permissions keep name, description, and approval switch on one compact row", async ({ page }) => {
  await page.route("**/api/control/config", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) });
  });
  await page.route("**/api/control/tools", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(toolCatalog()) });
  });

  await page.goto(`${baseUrl}/#settings`);

  const row = page.locator(".tool-permission-row").first();
  const copy = row.locator(".tool-permission-copy");
  await expect(copy).toHaveText("exec执行受控命令");
  const styles = await Promise.all([row, copy].map((element) => element.evaluate((node) => {
    const computed = getComputedStyle(node);
    return { display: computed.display, height: computed.height, whiteSpace: computed.whiteSpace, overflow: computed.overflow, textOverflow: computed.textOverflow };
  })));
  expect(styles[0]).toMatchObject({ display: "grid", height: "44px" });
  expect(styles[1]).toMatchObject({ whiteSpace: "nowrap", overflow: "hidden" });
  const description = copy.locator("span");
  const approvalSwitch = row.getByRole("switch", { name: "exec 需要审批" });
  await expect(description).toHaveCSS("text-align", "right");
  const [descriptionBox, switchBox] = await Promise.all([description.boundingBox(), approvalSwitch.boundingBox()]);
  expect(descriptionBox).not.toBeNull();
  expect(switchBox).not.toBeNull();
  expect(Math.abs(descriptionBox!.x + descriptionBox!.width + 16 - switchBox!.x)).toBeLessThanOrEqual(1);
  await expect(approvalSwitch).toBeVisible();
});

test("settings field labels and tool names share one secondary type scale", async ({ page }) => {
  await page.route("**/api/control/config", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) });
  });
  await page.route("**/api/control/tools", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(toolCatalog()) });
  });

  await page.goto(`${baseUrl}/#settings`);

  const sizes = await Promise.all([
    page.getByText("模型名称", { exact: true }),
    page.locator(".tool-permission-copy strong", { hasText: "exec" }),
  ].map((element) => element.evaluate((node) => getComputedStyle(node).fontSize)));
  expect(sizes).toEqual(["14px", "14px"]);
});

test("composer keeps the immediate global approval switch", async ({ page }) => {
  let updated = false;
  await page.route("**/api/settings/ui", async (route) => {
    if (route.request().method() === "PATCH") {
      expect(route.request().postDataJSON()).toEqual({ auto_approve_tools: true });
      updated = true;
      await route.fulfill({ contentType: "application/json", body: JSON.stringify({ auto_approve_tools: true }) });
      return;
    }
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ auto_approve_tools: false }) });
  });

  await page.goto(baseUrl);
  await expect(page.getByRole("button", { name: "工具权限：默认权限" })).toBeVisible();
  await page.getByRole("button", { name: "工具权限：默认权限" }).click();
  const permissionMenu = page.getByRole("menu");
  await expect(permissionMenu).toBeVisible();
  expect(await permissionMenu.evaluate((element) => getComputedStyle(element).borderTopWidth)).toBe("0px");
  await page.getByRole("menuitemradio", { name: "完全访问" }).click();

  await expect.poll(() => updated).toBe(true);
  await expect(page.getByRole("button", { name: "工具权限：完全访问" })).toBeVisible();
});

test("sidebar settings shortcut remains visible without scrolling", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto(baseUrl);

  await expect(page.getByRole("button", { name: "打开设置" })).toBeVisible();
  await expect(page.getByRole("button", { name: "打开设置" })).toBeInViewport();
});

test("settings polls control config until a pending apply becomes active", async ({ page }) => {
  let reads = 0;
  await page.route("**/api/control/config", async (route) => {
    reads += 1;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig(reads > 1 ? "active" : "active")) });
  });
  await page.route("**/api/control/config/apply", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig("pending")) });
  });
  await page.route("**/api/control/tools", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(toolCatalog()) });
  });

  await page.goto(`${baseUrl}/#settings`);
  await page.getByLabel("模型名称").fill("new-model");
  await page.getByRole("button", { name: "应用配置" }).click();

  await expect.poll(() => reads).toBeGreaterThan(1);
  await expect(page.getByText("已生效")).toBeVisible();
});

test("settings renders a top-level configuration field error from Apply", async ({ page }) => {
  await page.route("**/api/control/config", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) });
  });
  await page.route("**/api/control/tools", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(toolCatalog()) });
  });
  await page.route("**/api/control/config/apply", async (route) => {
    await route.fulfill({
      status: 422,
      contentType: "application/json",
      body: JSON.stringify({ field_errors: { "llm.model": "模型名称不能为空" } }),
    });
  });

  await page.goto(`${baseUrl}/#settings`);
  await page.getByLabel("模型名称").fill("invalid-model");
  await page.getByRole("button", { name: "应用配置" }).click();

  await expect(page.getByText("模型名称不能为空")).toBeVisible();
});

test("settings Apply bar distinguishes unsaved edits and links field errors", async ({ page }) => {
  await page.route("**/api/control/config", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) });
  });
  await page.route("**/api/control/tools", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(toolCatalog()) });
  });
  await page.route("**/api/control/config/apply", async (route) => {
    await route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ field_errors: { "llm.model": "模型名称不能为空" } }) });
  });

  await page.goto(`${baseUrl}/#settings`);
  const applyBar = page.locator(".settings-apply-bar");
  await expect(applyBar).toHaveAttribute("data-state", "active");
  await expect(applyBar).toHaveAttribute("data-dirty", "false");

  const groupSurface = await page.locator(".settings-group").first().evaluate((element) => ({
    background: getComputedStyle(element).backgroundColor,
    paddingTop: getComputedStyle(element).paddingTop,
  }));
  expect(groupSurface.background).toBe("rgba(0, 0, 0, 0)");
  expect(groupSurface.paddingTop).toBe("0px");

  const model = page.getByLabel("模型名称");
  await model.fill("invalid-model");
  await expect(applyBar).toHaveAttribute("data-dirty", "true");
  await expect(applyBar.getByText("未保存的修改")).toBeVisible();
  await page.getByRole("button", { name: "应用配置" }).click();

  const error = page.getByText("模型名称不能为空");
  await expect(error).toBeVisible();
  await expect(model).toHaveAttribute("aria-describedby", await error.getAttribute("id") || "");
});

test("configuration apply does not submit approval list as a scalar field", async ({ page }) => {
  await page.route("**/api/control/config", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) });
  });
  await page.route("**/api/control/tools", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(toolCatalog()) });
  });
  await page.route("**/api/control/config/apply", async (route) => {
    const body = route.request().postDataJSON() as { changes: Record<string, unknown> };
    const error = body.changes.tool_permissions
      ? { "tool_permissions.approval_required_tools": "不支持修改" }
      : { "runtime.max_tool_rounds": "必须大于 0" };
    await route.fulfill({ status: 422, contentType: "application/json", body: JSON.stringify({ field_errors: error }) });
  });

  await page.goto(`${baseUrl}/#settings`);
  await page.getByLabel("最大工具轮数").fill("0");
  await page.getByRole("button", { name: "应用配置" }).click();

  await expect(page.getByText("必须大于 0")).toBeVisible();
});

test("Skill guidance is an editable inline unit after the surrounding text", async ({ page }) => {
  await page.route("**/api/control/commands", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ entries: [{ id: "skill.release-review", kind: "skill", icon: "skill", slash: "/release-review", label: "release-review", description: "审阅发布内容", action: "insert_text", insert_text: "请优先参考 Skill「release-review」：" }] }) });
  });

  await page.goto(baseUrl);
  const editor = page.getByLabel("消息内容");
  await editor.fill("已有内容 /");
  await page.getByRole("option", { name: "release-review" }).click();

  await expect(editor).toHaveAttribute("contenteditable", "true");
  const tag = editor.locator('[data-guidance-id="skill.release-review"]');
  await expect(tag).toBeVisible();
  await expect(tag).toHaveAttribute("contenteditable", "false");
  await expect(tag).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
  await expect(tag).toHaveCSS("vertical-align", "baseline");
  await editor.press("End");
  await page.keyboard.type(" 后续文本");
  await expect.poll(() => editor.evaluate((node) => node.textContent?.replaceAll("\u200B", ""))).toContain("已有内容 release-review 后续文本");
});

test("Backspace removes a guidance tag as one editor unit", async ({ page }) => {
  await page.route("**/api/control/commands", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ entries: [{ id: "skill.release-review", kind: "skill", icon: "skill", slash: "/release-review", label: "release-review", description: "审阅发布内容", action: "insert_text", insert_text: "请优先参考 Skill「release-review」：" }] }) });
  });

  await page.goto(baseUrl);
  const editor = page.getByLabel("消息内容");
  await editor.fill("已有内容 /");
  await page.getByRole("option", { name: "release-review" }).click();
  await page.keyboard.press("Backspace");

  await expect(editor.locator('[data-guidance-id="skill.release-review"]')).toHaveCount(0);
  await expect(editor).toContainText("已有内容");
});

test("composer placeholder stays outside the editable content flow", async ({ page }) => {
  await page.goto(baseUrl);

  const editor = page.getByLabel("消息内容");
  await expect(page.locator(".composer-placeholder")).toHaveText("发送消息…");
  await expect(editor).not.toContainText("发送消息…");
});

test("composer reserves two text lines before growing upward", async ({ page }) => {
  await page.goto(baseUrl);

  const editor = page.getByLabel("消息内容");
  const emptyHeight = (await editor.boundingBox())!.height;
  await editor.fill("第一行\n第二行");
  const twoLineHeight = (await editor.boundingBox())!.height;
  await editor.fill("第一行\n第二行\n第三行");
  const threeLineHeight = (await editor.boundingBox())!.height;

  expect(Math.abs(twoLineHeight - emptyHeight)).toBeLessThanOrEqual(1);
  expect(threeLineHeight).toBeGreaterThan(twoLineHeight);
});

test("composer strips rich clipboard styling and keeps plain text", async ({ page, context }) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"], { origin: baseUrl });
  await page.goto(baseUrl);
  await page.evaluate(async () => {
    await navigator.clipboard.write([new ClipboardItem({
      "text/plain": new Blob(["粘贴的两行内容\n保持纯文本"], { type: "text/plain" }),
      "text/html": new Blob(["<span style=\"background:#fff;color:#000;border:2px solid #000\">粘贴的两行内容<br>保持纯文本</span>"], { type: "text/html" }),
    })]);
  });

  const editor = page.getByLabel("消息内容");
  await editor.focus();
  await page.keyboard.press("Control+V");

  await expect(editor).toContainText("粘贴的两行内容");
  await expect(editor).toContainText("保持纯文本");
  await expect(editor.locator("[style]")).toHaveCount(0);
  await expect(editor.locator("br")).toHaveCount(0);
});

test("sent guidance keeps its raw instruction between surrounding text", async ({ page }) => {
  await page.addInitScript(() => {
    const sent: Array<Record<string, unknown>> = [];
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

      constructor(_url: string) {
        queueMicrotask(() => this.onopen?.(new Event("open")));
      }

      send(payload: string) {
        const action = JSON.parse(payload) as Record<string, unknown>;
        sent.push(action);
        if (action.type === "message.send") {
          queueMicrotask(() => this.onmessage?.({ data: JSON.stringify({ type: "message.accepted", session_id: "session-control-read", request_id: "request-guidance", client_action_id: action.client_action_id }) } as MessageEvent<string>));
        }
      }

      close() {
        this.readyState = FakeWebSocket.CLOSED;
        this.onclose?.({} as CloseEvent);
      }
    }
    Object.assign(window, { WebSocket: FakeWebSocket, __tgaSentActions: sent });
  });
  await mockActiveSession(page);
  await page.route("**/api/control/commands", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ entries: [{ id: "skill.release-review", kind: "skill", icon: "skill", slash: "/release-review", label: "release-review", description: "审阅发布内容", action: "insert_text", insert_text: "请优先参考 Skill「release-review」：" }] }) });
  });

  await page.goto(`${baseUrl}/sessions/session-control-read`);
  const editor = page.getByLabel("消息内容");
  await editor.fill("前文 /");
  await page.getByRole("option", { name: "release-review" }).click();
  await editor.press("End");
  await page.keyboard.type("后文");
  await page.getByRole("button", { name: "发送消息" }).click();

  const expected = "前文 请优先参考 Skill「release-review」：后文";
  await expect.poll(() => page.evaluate(() => (window as unknown as { __tgaSentActions: Array<{ type: string; content?: string }> }).__tgaSentActions.find((action) => action.type === "message.send")?.content)).toBe(expected);
  await expect(page.locator(".message.user .markdown")).toContainText(expected);
});

test("Slash context read opens the shared inspector section hierarchy", async ({ page }) => {
  const session = await mockActiveSession(page);
  await page.route("**/api/control/commands", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ entries: [{ id: "context", kind: "command", icon: "command", slash: "/context", label: "context", description: "查看当前上下文", action: "open_context" }] }),
    });
  });
  await page.route(`**/api/control/sessions/${session.id}/context`, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        session_id: session.id,
        summary: "当前会话摘要",
        full_history_count: 2,
        uncompacted_history_count: 1,
        uncompacted_history_tokens: 12,
        token_breakdown: { max_context_tokens: 300000 },
        uncompacted_messages: [{ id: "message-1", role: "user", content: "保留在检查器内的上下文", token_count: 12, created_at: "2026-07-30T00:00:00Z" }],
      }),
    });
  });

  await page.goto(`${baseUrl}/sessions/${session.id}`);
  await page.getByLabel("消息内容").fill("保留文本 /");
  await page.getByRole("option", { name: "context" }).click();

  const section = page.locator("details.inspector-section[open]");
  await expect(section).toHaveCount(1);
  await expect(section.locator(":scope > summary")).toContainText("上下文");
  await expect(section.getByText("当前会话摘要")).toBeVisible();
  await expect(page.getByLabel("消息内容")).toContainText("保留文本");

  const sectionSurface = await page.locator(".inspector-sections").evaluate((element) => ({
    borderRadius: getComputedStyle(element).borderTopLeftRadius,
    background: getComputedStyle(element).backgroundColor,
  }));
  expect(sectionSurface.borderRadius).toBe("0px");
  expect(sectionSurface.background).toBe("rgba(0, 0, 0, 0)");
});

test("Slash Catalog selects the active Skill with ArrowDown and Enter", async ({ page }) => {
  const session = await mockActiveSession(page);
  await page.route("**/api/control/commands", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ entries: [
        { id: "context", kind: "command", icon: "command", slash: "/context", label: "context", description: "查看当前上下文", action: "open_context" },
        { id: "skill.release-review", kind: "skill", icon: "skill", slash: "/release-review", label: "release-review", description: "审阅发布内容", action: "insert_text", insert_text: "请优先参考 Skill「release-review」：" },
        { id: "mcp.connected-server", kind: "mcp", icon: "mcp", slash: "/connected-server", label: "connected-server", description: "已连接 MCP Server", action: "insert_text", insert_text: "请使用 MCP「connected-server」：" },
      ] }),
    });
  });

  await page.goto(`${baseUrl}/sessions/${session.id}`);
  await page.getByLabel("消息内容").fill("/");

  const menu = page.getByRole("listbox", { name: "Slash 命令" });
  const context = menu.getByRole("option", { name: "context" });
  const skill = menu.getByRole("option", { name: "release-review" });
  await expect(context).toHaveAttribute("aria-selected", "true");

  await page.keyboard.press("ArrowDown");
  await expect(skill).toHaveAttribute("aria-selected", "true");
  await expect.poll(() => skill.evaluate((element) => getComputedStyle(element).backgroundColor)).not.toBe("rgba(0, 0, 0, 0)");
  await page.keyboard.press("Enter");

  await expect(page.getByLabel("消息内容")).toContainText("release-review");
  const skillTag = page.getByLabel("消息内容").locator(".composer-command-tag.is-skill");
  await expect(skillTag).toHaveClass(/is-skill/);
  const skillColor = await skillTag.evaluate((element) => getComputedStyle(element).color);

  await page.getByLabel("消息内容").fill("/");
  await page.getByRole("option", { name: "connected-server" }).click();
  const mcpTag = page.getByLabel("消息内容").locator(".composer-command-tag.is-mcp");
  await expect(mcpTag).toHaveClass(/is-mcp/);
  const mcpColor = await mcpTag.evaluate((element) => getComputedStyle(element).color);
  expect(skillColor).not.toBe(mcpColor);
});

test("Slash Catalog renders each command as one compact row", async ({ page }) => {
  const session = await mockActiveSession(page);
  await page.route("**/api/control/commands", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({ entries: [
        { id: "context", kind: "command", icon: "context", slash: "/context", label: "查看上下文", description: "打开当前会话的结构化上下文", action: "open_context" },
        { id: "skill.long", kind: "skill", icon: "skill", slash: "/long-skill", label: "long-skill", description: "这是一个很长的说明，用于确认右侧说明会截断而不会覆盖左侧命令", action: "insert_text", insert_text: "请优先参考 Skill「long-skill」：" },
      ] }),
    });
  });

  await page.goto(`${baseUrl}/sessions/${session.id}`);
  await page.getByLabel("消息内容").fill("/");

  const menu = page.getByRole("listbox", { name: "Slash 命令" });
  const context = menu.getByRole("option", { name: "查看上下文" });
  await expect(context.getByText("context", { exact: true })).toBeVisible();
  await expect(context).not.toContainText("/context");

  const layout = await Promise.all([
    context,
    context.locator(".slash-command-summary"),
  ].map((element) => element.evaluate((node) => {
    const styles = getComputedStyle(node);
    return {
      display: styles.display,
      height: styles.height,
      whiteSpace: styles.whiteSpace,
      overflow: styles.overflow,
      textOverflow: styles.textOverflow,
    };
  })));
  expect(layout[0]).toMatchObject({ display: "grid", height: "36px" });
  expect(layout[1]).toMatchObject({ whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" });

  const edges = await context.evaluate((node) => {
    const summary = node.querySelector<HTMLElement>(".slash-command-summary");
    if (!summary) throw new Error("Slash command summary is missing");
    return { rowRight: node.getBoundingClientRect().right, summaryRight: summary.getBoundingClientRect().right };
  });
  expect(edges.rowRight - edges.summaryRight).toBeCloseTo(9, 0);
});

test("Slash Catalog Escape hides the menu without clearing the draft", async ({ page }) => {
  const session = await mockActiveSession(page);
  await page.route("**/api/control/commands", async (route) => {
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ entries: [{ id: "context", kind: "command", icon: "command", slash: "/context", label: "context", description: "查看当前上下文", action: "open_context" }] }) });
  });

  await page.goto(`${baseUrl}/sessions/${session.id}`);
  await page.getByLabel("消息内容").fill("/");
  await expect(page.getByRole("listbox", { name: "Slash 命令" })).toBeVisible();
  await page.keyboard.press("Escape");

  await expect(page.getByRole("listbox", { name: "Slash 命令" })).toHaveCount(0);
  await expect(page.getByLabel("消息内容")).toContainText("/");
});

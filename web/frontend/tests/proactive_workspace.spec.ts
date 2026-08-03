import { expect, test, type Page } from "@playwright/test";

const baseUrl = process.env.TGA_WEB_URL || "http://127.0.0.1:8000";

const owner = {
  mode: "owner",
  writable: true,
  owner_id: "web-owner",
  owner_kind: "web",
  owner_pid: 42,
};

function controlConfig() {
  const editable = {
    llm: { provider: "openai-compatible", api_key_configured: true, base_url: "https://api.example.test/v1", model: "example-model", timeout_seconds: 60, max_retries: 2, retry_delay_seconds: 0.5, streaming_enabled: true },
    runtime: { max_tool_rounds: 5, max_tool_calls_per_round: 8, parallel_tool_calls_enabled: true, max_parallel_tool_calls: 4, turn_timeout_seconds: 120, max_context_tokens: 300000, max_tool_result_tokens: 8000 },
    memory: { compact_token_threshold: 200000, recent_window_token_limit: 20000 },
    sessions: { retention_days: 7 },
    skills: { max_loaded_skills_per_turn: 3, max_skill_tokens: 8000, max_loaded_skill_tokens_per_turn: 16000 },
    proactive: { enabled: true, timezone: "Asia/Shanghai", review_provider: null, review_api_key_configured: false, review_base_url: null, review_model: null, background_max_concurrency: 4, breakbeat_refresh_minutes: 60, dream_refresh_hours: 24, review_window_token_limit: 100000, profile_total_token_limit: 16000, user_profile_token_limit: 12000, soul_profile_token_limit: 4000, skill_observation_turn_interval: 10, skill_observation_token_limit: 160, skill_evolution_batch_token_limit: 100000, skill_evolution_batches_per_kind: 3 },
    tool_permissions: { auto_approve_tools: false, approval_required_tools: [] },
  };
  return { desired_revision: "desired", active_revision: "active", state: "active", last_apply_error: null, desired: editable, active: editable };
}

function snapshot(
  domain: "cron" | "breakbeat" | "dream" | "skill" | "incident",
  revision = 1,
  overrides: Record<string, unknown> = {},
) {
  return {
    type: "snapshot",
    domain,
    data: {
      cron: { jobs: [], usage: { calls: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0 } },
      breakbeat: { items: [], cursors: {}, next_run_at: null, usage: { calls: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0 } },
      dream: { cursors: {}, next_run_at: null, usage: { calls: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0 }, memory: { user: "", soul: "" } },
      skill: { observations: [], drafts: [], next_run_at: null, usage: { calls: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0 } },
      incident: { incidents: [] },
    }[domain],
    runtime: { running: false, next_run_at: null, entity_states: {} },
    proactive_revision: revision,
    owner,
    ...overrides,
  };
}

async function mockAppShell(page: Page) {
  await page.route(/\/api\/sessions\?archived=false$/, (route) => route.fulfill({ contentType: "application/json", body: "[]" }));
  await page.route(/\/api\/sessions\?archived=true$/, (route) => route.fulfill({ contentType: "application/json", body: "[]" }));
  await page.route("**/api/settings/ui", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ auto_approve_tools: false }) }));
  await page.route("**/api/control/config", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) }));
  await page.route("**/api/control/tools", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ active_revision: "active", tools: [], unavailable_approval_required: [] }) }));
}

async function installProactiveSocket(page: Page) {
  await page.addInitScript((initialSnapshots) => {
    const sockets: Array<{ url: string; readyState: number; onopen: ((event: Event) => void) | null; onmessage: ((event: MessageEvent<string>) => void) | null; onclose: ((event: CloseEvent) => void) | null }> = [];
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
        queueMicrotask(() => {
          this.onopen?.(new Event("open"));
          if (url.includes("/ws/proactive")) {
            initialSnapshots.forEach((payload) => this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>));
          }
        });
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
      __tgaCloseProactive: () => sockets.filter((socket) => socket.url.includes("/ws/proactive")).forEach((socket) => socket.close()),
      __tgaProactiveConnections: () => sockets.filter((socket) => socket.url.includes("/ws/proactive") && socket.readyState !== FakeWebSocket.CLOSED).length,
    });
  }, [snapshot("cron"), snapshot("breakbeat"), snapshot("dream"), snapshot("skill"), snapshot("incident")]);
}

test("proactive workspace deep-links all five domains and retains the selected page after reload", async ({ page }) => {
  await installProactiveSocket(page);
  await mockAppShell(page);

  await page.goto(baseUrl);
  await page.getByRole("button", { name: "打开主动能力" }).click();
  await expect(page).toHaveURL(/#proactive\/cron$/);

  for (const [domain, label] of [["cron", "Cron"], ["breakbeat", "Breakbeat"], ["memory", "长期记忆与 Dream"], ["skills", "Skill 演进与 Draft"], ["incidents", "Incidents"]] as const) {
    await page.goto(`${baseUrl}/#proactive/${domain}`);
    await expect(page.getByRole("heading", { name: "主动能力", exact: true })).toBeVisible();
    await expect(page.getByRole("tab", { name: label })).toHaveAttribute("aria-selected", "true");
    await expect(page.locator("[data-proactive-domain]")).toHaveAttribute("data-proactive-domain", domain);
    await expect(page.locator(".message")).toHaveCount(0);
  }

  await page.reload();
  await expect(page.getByRole("tab", { name: "Incidents" })).toHaveAttribute("aria-selected", "true");
  await expect(page.locator("[data-proactive-domain]")).toHaveAttribute("data-proactive-domain", "incidents");
});

test("proactive socket receives complete snapshots, maps wire domains, and remains app-lifetime", async ({ page }) => {
  await installProactiveSocket(page);
  await mockAppShell(page);
  await page.goto(`${baseUrl}/#proactive/memory`);

  await expect(page.locator("[data-proactive-domain]")).toHaveAttribute("data-proactive-revision", "1");
  await page.evaluate((payload) => (window as unknown as { __tgaEmitProactive: (value: unknown) => void }).__tgaEmitProactive(payload), snapshot("dream", 2, { data: { cursors: {}, next_run_at: "2026-08-03T09:00:00+08:00", usage: { calls: 1, input_tokens: 2, output_tokens: 3, total_tokens: 5 }, memory: { user: "偏好简洁", soul: "先核对事实" } } }));
  await expect(page.locator("[data-proactive-domain]")).toHaveAttribute("data-proactive-revision", "2");
  await expect(page.getByText("偏好简洁")).toBeVisible();

  await page.evaluate(() => { window.location.hash = "settings"; });
  await expect(page.getByRole("button", { name: "返回聊天" })).toBeVisible();
  await expect.poll(() => page.evaluate(() => (window as unknown as { __tgaProactiveConnections: () => number }).__tgaProactiveConnections())).toBe(1);
});

test("sidebar health lamp reports idle, active, readonly, and reconnecting states in text", async ({ page }) => {
  await installProactiveSocket(page);
  await mockAppShell(page);
  await page.goto(baseUrl);

  const lamp = page.getByLabel("主动能力状态");
  await expect(lamp).toHaveAttribute("data-state", "idle");
  await expect(lamp).toContainText("空闲");

  await page.evaluate((payload) => (window as unknown as { __tgaEmitProactive: (value: unknown) => void }).__tgaEmitProactive(payload), snapshot("cron", 2, { runtime: { running: true, next_run_at: null, entity_states: { "cron-1": "running" } } }));
  await expect(lamp).toHaveAttribute("data-state", "active");
  await expect(lamp).toContainText("运行中");

  await page.evaluate((payload) => (window as unknown as { __tgaEmitProactive: (value: unknown) => void }).__tgaEmitProactive(payload), snapshot("incident", 3, { owner: { mode: "readonly", writable: false, owner_id: "cli-owner", owner_kind: "cli", owner_pid: 99 } }));
  await expect(lamp).toHaveAttribute("data-state", "readonly");
  await expect(lamp).toContainText("只读");

  await page.evaluate(() => (window as unknown as { __tgaCloseProactive: () => void }).__tgaCloseProactive());
  await expect(lamp).toHaveAttribute("data-state", "unavailable");
  await expect(lamp).toContainText("连接中");
});

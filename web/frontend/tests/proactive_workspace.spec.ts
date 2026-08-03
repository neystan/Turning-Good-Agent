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
      dream: { cursors: {}, next_run_at: null, usage: { calls: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0 }, memory: { user: "", soul: "" }, memory_tokens: { user_tokens: 0, soul_tokens: 0, total_tokens: 0 }, profile_limits: { user_profile_token_limit: 12000, soul_profile_token_limit: 4000, profile_total_token_limit: 16000 }, timezone: "Asia/Shanghai" },
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
  await page.route(/\/api\/sessions\/[^/]+\/messages$/, (route) => route.fulfill({ contentType: "application/json", body: "[]" }));
  await page.route(/\/api\/sessions\/[^/]+\/context-window$/, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ used_tokens: 0, max_tokens: 300000, remaining_tokens: 300000 }) }));
}

async function installProactiveSocket(page: Page, initialSnapshots = [snapshot("cron"), snapshot("breakbeat"), snapshot("dream"), snapshot("skill"), snapshot("incident")]) {
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
  }, initialSnapshots);
}

test("Cron cards show complete schedules and update only from the confirmed delete response", async ({ page }) => {
  const cron = snapshot("cron", 3, {
    data: {
      jobs: [
        { id: "cron-recurring", cron: "0 9 * * 1", created_at: "2026-08-01T08:00:00+08:00", prompt: "每周检查所有部署记录，并将异常汇总到 Incident。", recurring: true, delivery_channels: ["cli", "web"], updated_at: "2026-08-02T08:00:00+08:00", next_run_at: "2026-08-03T09:00:00+08:00" },
        { id: "cron-once", cron: null, created_at: "2026-08-01T09:00:00+08:00", prompt: "一次性检查发布状态。", recurring: false, delivery_channels: ["cli"], updated_at: "2026-08-01T09:00:00+08:00", next_run_at: "2026-08-04T15:30:00+08:00" },
      ],
      usage: { calls: 4, input_tokens: 21, output_tokens: 13, total_tokens: 34 },
    },
    runtime: { running: true, next_run_at: "2026-08-03T09:00:00+08:00", entity_states: { "cron-recurring": "running", "cron-once": "idle" } },
  });
  await installProactiveSocket(page, [cron, snapshot("breakbeat"), snapshot("dream"), snapshot("skill"), snapshot("incident")]);
  await mockAppShell(page);
  let actionRequests = 0;
  let domainGets = 0;
  await page.route("**/api/proactive/cron/**", async (route) => {
    if (route.request().method() === "DELETE") actionRequests += 1;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(snapshot("cron", 4, { data: { jobs: [cron.data.jobs[1]], usage: cron.data.usage } })) });
  });
  await page.route(/\/api\/proactive\/cron(?:\?.*)?$/, async (route) => {
    if (route.request().method() === "GET") domainGets += 1;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(cron) });
  });

  await page.goto(`${baseUrl}/#proactive/cron`);
  await expect(page.locator('[data-proactive-id="cron-recurring"]')).toContainText("每周检查所有部署记录，并将异常汇总到 Incident。");
  await expect(page.locator('[data-proactive-id="cron-recurring"]')).toContainText("0 9 * * 1");
  await expect(page.locator('[data-proactive-id="cron-once"]')).toContainText("2026-08-04T15:30:00+08:00");
  await expect(page.locator('[data-proactive-id="cron-recurring"]')).toHaveAttribute("data-proactive-state", "running");

  await page.getByRole("button", { name: "删除 Cron cron-recurring" }).click();
  await expect(page.getByRole("alertdialog")).toContainText("cron-recurring");
  await page.getByRole("button", { name: "确认删除 Cron" }).click();
  await expect(page.locator('[data-proactive-id="cron-recurring"]')).toHaveCount(0);
  expect(actionRequests).toBe(1);
  expect(domainGets).toBe(0);
});

test("Cron action failures preserve card data and expose status with readable detail", async ({ page }) => {
  const cron = snapshot("cron", 2, { data: { jobs: [{ id: "cron-error", cron: "*/5 * * * *", created_at: "2026-08-01T08:00:00+08:00", prompt: "保留失败前的完整 Prompt。", recurring: true, delivery_channels: ["cli"], updated_at: "2026-08-01T08:00:00+08:00", next_run_at: "2026-08-03T09:00:00+08:00" }], usage: { calls: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0 } } });
  await installProactiveSocket(page, [cron, snapshot("breakbeat"), snapshot("dream"), snapshot("skill"), snapshot("incident")]);
  await mockAppShell(page);
  await page.route("**/api/proactive/cron/cron-error", (route) => route.fulfill({ status: 409, contentType: "application/json", body: JSON.stringify({ detail: "该 Cron 由另一 Host 持有" }) }));

  await page.goto(`${baseUrl}/#proactive/cron`);
  await page.getByRole("button", { name: "删除 Cron cron-error" }).click();
  await page.getByRole("button", { name: "确认删除 Cron" }).click();

  await expect(page.getByRole("alert")).toContainText("409");
  await expect(page.getByRole("alert")).toContainText("该 Cron 由另一 Host 持有");
  await expect(page.locator('[data-proactive-id="cron-error"]')).toContainText("保留失败前的完整 Prompt。");
  await expect(page.locator('[data-proactive-id="cron-error"]')).toHaveAttribute("data-proactive-action-state", "error");
});

test("an open delete confirmation becomes non-mutating when ownership turns read-only", async ({ page }) => {
  const readonlyOwner = { mode: "readonly", writable: false, owner_id: "cli-owner", owner_kind: "cli", owner_pid: 77 };
  const cron = snapshot("cron", 2, { data: { jobs: [{ id: "ownership-cron", cron: "0 8 * * *", created_at: "2026-08-01T08:00:00+08:00", prompt: "所有权变化时不得删除。", recurring: true, delivery_channels: ["cli"], updated_at: "2026-08-01T08:00:00+08:00", next_run_at: "2026-08-04T08:00:00+08:00" }], usage: { calls: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0 } } });
  await installProactiveSocket(page, [cron, snapshot("breakbeat"), snapshot("dream"), snapshot("skill"), snapshot("incident")]);
  await mockAppShell(page);
  let deleteRequests = 0;
  await page.route("**/api/proactive/cron/ownership-cron", async (route) => {
    deleteRequests += 1;
    await route.fulfill({ contentType: "application/json", body: JSON.stringify(snapshot("cron", 4)) });
  });

  await page.goto(`${baseUrl}/#proactive/cron`);
  await page.getByRole("button", { name: "删除 Cron ownership-cron" }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.evaluate((payload) => (window as unknown as { __tgaEmitProactive: (value: unknown) => void }).__tgaEmitProactive(payload), snapshot("cron", 3, { data: cron.data, owner: readonlyOwner }));

  const confirm = page.getByRole("button", { name: "确认删除 Cron" });
  await expect(confirm).toBeDisabled();
  await confirm.click({ force: true });
  expect(deleteRequests).toBe(0);
  await expect(page.locator('[data-proactive-id="ownership-cron"]')).toBeVisible();
});

test("a stale removed delete target leaves a persistent accessible page error", async ({ page }) => {
  const cron = snapshot("cron", 2, { data: { jobs: [{ id: "stale-cron", cron: "0 8 * * *", created_at: "2026-08-01T08:00:00+08:00", prompt: "快照移除后保留错误。", recurring: true, delivery_channels: ["cli"], updated_at: "2026-08-01T08:00:00+08:00", next_run_at: "2026-08-04T08:00:00+08:00" }], usage: { calls: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0 } } });
  await installProactiveSocket(page, [cron, snapshot("breakbeat"), snapshot("dream"), snapshot("skill"), snapshot("incident")]);
  await mockAppShell(page);
  await page.route("**/api/proactive/cron/stale-cron", (route) => route.fulfill({ status: 404, contentType: "application/json", body: JSON.stringify({ detail: "Cron Job 不存在：stale-cron" }) }));

  await page.goto(`${baseUrl}/#proactive/cron`);
  await page.getByRole("button", { name: "删除 Cron stale-cron" }).click();
  await page.evaluate((payload) => (window as unknown as { __tgaEmitProactive: (value: unknown) => void }).__tgaEmitProactive(payload), snapshot("cron", 3));
  await expect(page.locator('[data-proactive-id="stale-cron"]')).toHaveCount(0);
  await page.getByRole("button", { name: "确认删除 Cron" }).click();

  const error = page.getByRole("alert");
  await expect(error).toContainText("404");
  await expect(error).toContainText("Cron Job 不存在：stale-cron");
  await page.waitForTimeout(100);
  await expect(error).toBeVisible();
});

test("Breakbeat cards sort active work first, complete directly, and navigate to the source session", async ({ page }) => {
  const breakbeat = snapshot("breakbeat", 5, {
    data: {
      items: [
        { id: "done-old", todo: "已完成事项", deadline: null, source_session_id: "session-done", status: "completed", created_at: "2026-08-01T08:00:00+08:00", updated_at: "2026-08-03T07:00:00+08:00" },
        { id: "active-new", todo: "继续整理接口证据", deadline: "2026-08-04 09:30 Asia/Shanghai", source_session_id: "session-source", status: "in_progress", created_at: "2026-08-02T08:00:00+08:00", updated_at: "2026-08-03T08:00:00+08:00" },
      ],
      cursors: { internal: { message_id: "hidden-cursor", created_at: "2026-08-03T08:00:00+08:00" } },
      next_run_at: "2026-08-03T10:00:00+08:00",
      usage: { calls: 2, input_tokens: 12, output_tokens: 8, total_tokens: 20 },
    },
  });
  await installProactiveSocket(page, [snapshot("cron"), breakbeat, snapshot("dream"), snapshot("skill"), snapshot("incident")]);
  await mockAppShell(page);
  await page.route("**/api/proactive/breakbeat/active-new/complete", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(snapshot("breakbeat", 6, { data: { ...breakbeat.data, items: [breakbeat.data.items[0], { ...breakbeat.data.items[1], status: "completed", updated_at: "2026-08-03T09:00:00+08:00" }] } })) }));

  await page.goto(`${baseUrl}/#proactive/breakbeat`);
  await expect(page.locator('[data-proactive-card="breakbeat-item"]').first()).toHaveAttribute("data-proactive-id", "active-new");
  await expect(page.locator('[data-proactive-id="active-new"]')).toContainText("2026-08-04 09:30 Asia/Shanghai");
  await expect(page.locator('[data-proactive-id="done-old"]')).toContainText("未提供截止时间");
  await expect(page.getByText("hidden-cursor")).toHaveCount(0);

  await page.getByRole("button", { name: "完成 Breakbeat active-new" }).click();
  await expect(page.locator('[data-proactive-id="active-new"]')).toHaveAttribute("data-proactive-state", "completed");
  await page.getByRole("button", { name: "查看来源会话 session-source" }).click();
  await expect(page).toHaveURL(/\/sessions\/session-source$/);
});

test("Memory shows exact server token budgets, configured timezone, and complete read-only documents", async ({ page }) => {
  const dream = snapshot("dream", 4, {
    data: {
      cursors: { internal: { message_id: "hidden-memory-message", created_at: "2026-08-03T08:00:00+08:00" } },
      next_run_at: "2026-08-04T09:00:00-04:00",
      usage: { calls: 7, input_tokens: 80, output_tokens: 34, total_tokens: 114 },
      memory: { user: "偏好直接给出证据。\n保留完整 USER 内容。", soul: "先核对事实。\n保留完整 SOUL 内容。" },
      memory_tokens: { user_tokens: 321, soul_tokens: 123, total_tokens: 444 },
      profile_limits: { user_profile_token_limit: 12000, soul_profile_token_limit: 4000, profile_total_token_limit: 16000 },
      timezone: "America/New_York",
    },
    runtime: { running: true, next_run_at: "2026-08-04T09:00:00-04:00", entity_states: { service: "running" } },
  });
  await installProactiveSocket(page, [snapshot("cron"), snapshot("breakbeat"), dream, snapshot("skill"), snapshot("incident")]);
  await mockAppShell(page);

  await page.goto(`${baseUrl}/#proactive/memory`);
  await expect(page.locator('[data-proactive-card="memory-user"]')).toContainText("保留完整 USER 内容。");
  await expect(page.locator('[data-proactive-card="memory-soul"]')).toContainText("保留完整 SOUL 内容。");
  await expect(page.getByText("321 / 12000 tokens")).toBeVisible();
  await expect(page.getByText("123 / 4000 tokens")).toBeVisible();
  await expect(page.getByText("444 / 16000 tokens")).toBeVisible();
  await expect(page.getByText("America/New_York")).toBeVisible();
  await expect(page.getByText("hidden-memory-message")).toHaveCount(0);
  await expect(page.locator('[data-proactive-page="memory"] button').filter({ hasText: /编辑|运行 Dream|删除/ })).toHaveCount(0);
});

test("Skill cards expose complete observation evidence and Markdown draft content before confirmed deletion", async ({ page }) => {
  const skill = snapshot("skill", 7, {
    data: {
      observations: [
        { id: "obs-1", created_at: "2026-08-03T08:00:00+08:00", kind: "failure_recovery", observation: "复现失败后先保存完整 traceback。", source_session_id: "session-observation", source_message_ids: ["message-a", "message-b"] },
        { id: "obs-2", created_at: "2026-08-03T08:01:00+08:00", kind: "workflow", observation: "按阶段保存证据。", source_session_id: "session-workflow", source_message_ids: ["message-c"] },
        { id: "obs-3", created_at: "2026-08-03T08:02:00+08:00", kind: "tool_procedure", observation: "先运行聚焦测试。", source_session_id: "session-tool", source_message_ids: ["message-d"] },
        { id: "obs-4", created_at: "2026-08-03T08:03:00+08:00", kind: "interaction_protocol", observation: "只提出一个决策问题。", source_session_id: "session-protocol", source_message_ids: ["message-e"] },
      ],
      drafts: [{ name: "focused-review", description: "执行聚焦代码评审", body: "# Run checks\n\n- Preserve evidence\n- Report scope\n\n```powershell\npytest -q\n```" }],
      next_run_at: null,
      usage: { calls: 3, input_tokens: 30, output_tokens: 18, total_tokens: 48 },
    },
  });
  await installProactiveSocket(page, [snapshot("cron"), snapshot("breakbeat"), snapshot("dream"), skill, snapshot("incident")]);
  await mockAppShell(page);
  await page.route("**/api/proactive/skills/drafts/focused-review", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(snapshot("skill", 8, { data: { ...skill.data, drafts: [] } })) }));

  await page.goto(`${baseUrl}/#proactive/skills`);
  await expect(page.locator('[data-proactive-id="obs-1"]')).toContainText("复现失败后先保存完整 traceback。");
  await expect(page.locator('[data-proactive-id="obs-1"]')).toContainText("message-a");
  await expect(page.locator('[data-proactive-id="obs-1"]')).toContainText("message-b");
  await expect(page.locator('[data-proactive-card="skill-observation"]')).toHaveCount(4);
  await expect(page.locator('[data-proactive-id="focused-review"]')).toContainText("执行聚焦代码评审");
  await expect(page.locator('[data-proactive-id="focused-review"]')).toContainText("pytest -q");

  await page.getByRole("button", { name: "删除 Draft focused-review" }).click();
  await expect(page.getByRole("alertdialog")).toContainText("focused-review");
  await page.getByRole("button", { name: "确认删除 Draft" }).click();
  await expect(page.locator('[data-proactive-id="focused-review"]')).toHaveCount(0);
  await page.getByRole("button", { name: "查看来源会话 session-observation" }).click();
  await expect(page).toHaveURL(/\/sessions\/session-observation$/);
});

test("Incident filters default open and action snapshots preserve complete resolution history", async ({ page }) => {
  const openIncident = { id: "incident-open", fingerprint: "cron:timeout", source: "cron", state: "open", first_detected_at: "2026-08-01T08:00:00+08:00", last_detected_at: "2026-08-03T08:00:00+08:00", occurrence_count: 3, message: "最近一次超时", history: [{ state: "open", occurred_at: "2026-08-01T08:00:00+08:00", message: "第一次超时" }, { state: "open", occurred_at: "2026-08-03T08:00:00+08:00", message: "最近一次超时" }] };
  const resolvedIncident = { id: "incident-resolved", fingerprint: "dream:parse", source: "dream", state: "resolved", first_detected_at: "2026-07-30T08:00:00+08:00", last_detected_at: "2026-07-31T08:00:00+08:00", occurrence_count: 1, message: "解析失败", history: [{ state: "open", occurred_at: "2026-07-30T08:00:00+08:00", message: "解析失败" }, { state: "resolved", occurred_at: "2026-07-31T08:00:00+08:00", message: "已处理" }] };
  const incidents = snapshot("incident", 9, { data: { incidents: [openIncident, resolvedIncident] } });
  await installProactiveSocket(page, [snapshot("cron"), snapshot("breakbeat"), snapshot("dream"), snapshot("skill"), incidents]);
  await mockAppShell(page);
  const resolved = { ...openIncident, state: "resolved", last_detected_at: "2026-08-03T09:00:00+08:00", history: [...openIncident.history, { state: "resolved", occurred_at: "2026-08-03T09:00:00+08:00", message: "用户在 Web 中标记已解决" }] };
  await page.route("**/api/proactive/incidents/cron%3Atimeout/resolve", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(snapshot("incident", 10, { data: { incidents: [resolved, resolvedIncident] } })) }));

  await page.goto(`${baseUrl}/#proactive/incidents`);
  await expect(page.locator('[data-proactive-id="incident-open"]')).toBeVisible();
  await expect(page.locator('[data-proactive-id="incident-resolved"]')).toHaveCount(0);
  await expect(page.locator('[data-proactive-id="incident-open"] [data-proactive-history-item]')).toHaveCount(2);

  await page.getByRole("button", { name: "全部 Incident" }).click();
  await expect(page.locator('[data-proactive-card="incident"]')).toHaveCount(2);
  await page.getByRole("button", { name: "open Incident" }).click();
  await page.getByRole("button", { name: "标记 Incident 已解决 cron:timeout" }).click();
  await expect(page.getByText("暂无 open Incident。")).toBeVisible();
  await page.getByRole("button", { name: "resolved Incident" }).click();
  await expect(page.locator('[data-proactive-id="incident-open"]')).toContainText("用户在 Web 中标记已解决");
  await expect(page.locator('[data-proactive-id="incident-open"] [data-proactive-history-item]')).toHaveCount(3);
});

test("read-only ownership disables mutations before any request", async ({ page }) => {
  const readonlyOwner = { mode: "readonly", writable: false, owner_id: "cli-owner", owner_kind: "cli", owner_pid: 77 };
  const cron = snapshot("cron", 2, { owner: readonlyOwner, data: { jobs: [{ id: "readonly-cron", cron: "0 8 * * *", created_at: "2026-08-01T08:00:00+08:00", prompt: "只读记录", recurring: true, delivery_channels: ["cli"], updated_at: "2026-08-01T08:00:00+08:00", next_run_at: "2026-08-04T08:00:00+08:00" }], usage: { calls: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0 } } });
  await installProactiveSocket(page, [cron, snapshot("breakbeat", 1, { owner: readonlyOwner }), snapshot("dream", 1, { owner: readonlyOwner }), snapshot("skill", 1, { owner: readonlyOwner }), snapshot("incident", 1, { owner: readonlyOwner })]);
  await mockAppShell(page);
  let requests = 0;
  await page.route("**/api/proactive/cron/**", (route) => { requests += 1; return route.abort(); });

  await page.goto(`${baseUrl}/#proactive/cron`);
  await expect(page.getByRole("button", { name: "删除 Cron readonly-cron" })).toBeDisabled();
  await page.getByRole("button", { name: "删除 Cron readonly-cron" }).click({ force: true });
  expect(requests).toBe(0);
});

test("Breakbeat and Incident hard deletes require confirmation and consume their response snapshots", async ({ page }) => {
  const breakbeat = snapshot("breakbeat", 2, { data: { items: [{ id: "delete-breakbeat", todo: "待删除事项", deadline: null, source_session_id: "session-delete", status: "in_progress", created_at: "2026-08-03T08:00:00+08:00", updated_at: "2026-08-03T08:00:00+08:00" }], cursors: {}, next_run_at: null, usage: { calls: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0 } } });
  const incident = snapshot("incident", 2, { data: { incidents: [{ id: "delete-incident", fingerprint: "cron:delete", source: "cron", state: "open", first_detected_at: "2026-08-03T08:00:00+08:00", last_detected_at: "2026-08-03T08:00:00+08:00", occurrence_count: 1, message: "待删除 Incident", history: [{ state: "open", occurred_at: "2026-08-03T08:00:00+08:00", message: "待删除 Incident" }] }] } });
  await installProactiveSocket(page, [snapshot("cron"), breakbeat, snapshot("dream"), snapshot("skill"), incident]);
  await mockAppShell(page);
  await page.route("**/api/proactive/breakbeat/delete-breakbeat", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(snapshot("breakbeat", 3)) }));
  await page.route("**/api/proactive/incidents/cron%3Adelete", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(snapshot("incident", 4)) }));

  await page.goto(`${baseUrl}/#proactive/breakbeat`);
  await page.getByRole("button", { name: "删除 Breakbeat delete-breakbeat" }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "确认删除 Breakbeat" }).click();
  await expect(page.locator('[data-proactive-id="delete-breakbeat"]')).toHaveCount(0);

  await page.evaluate(() => { window.location.hash = "proactive/incidents"; });
  await page.getByRole("button", { name: "删除 Incident cron:delete" }).click();
  await expect(page.getByRole("alertdialog")).toBeVisible();
  await page.getByRole("button", { name: "确认删除 Incident" }).click();
  await expect(page.locator('[data-proactive-id="delete-incident"]')).toHaveCount(0);
});

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

test("proactive tabs use roving focus, labeled panels, and Arrow navigation updates the hash", async ({ page }) => {
  await installProactiveSocket(page);
  await mockAppShell(page);
  await page.goto(`${baseUrl}/#proactive/cron`);

  const cronTab = page.getByRole("tab", { name: "Cron" });
  const breakbeatTab = page.getByRole("tab", { name: "Breakbeat" });
  await expect(cronTab).toHaveAttribute("id", "proactive-tab-cron");
  await expect(cronTab).toHaveAttribute("aria-controls", "proactive-panel-cron");
  await expect(cronTab).toHaveAttribute("tabindex", "0");
  await expect(breakbeatTab).toHaveAttribute("tabindex", "-1");
  const cronPanel = page.getByRole("tabpanel", { name: "Cron" });
  await expect(cronPanel).toHaveAttribute("id", "proactive-panel-cron");
  await expect(cronPanel).toHaveAttribute("aria-labelledby", "proactive-tab-cron");

  await cronTab.focus();
  await cronTab.press("ArrowRight");
  await expect(breakbeatTab).toBeFocused();
  await expect(breakbeatTab).toHaveAttribute("aria-selected", "true");
  await expect(page).toHaveURL(/#proactive\/breakbeat$/);
  await expect(page.getByRole("tabpanel", { name: "Breakbeat" })).toHaveAttribute("aria-labelledby", "proactive-tab-breakbeat");

  await breakbeatTab.press("ArrowLeft");
  await expect(cronTab).toBeFocused();
  await expect(page).toHaveURL(/#proactive\/cron$/);
});

test("proactive socket receives complete snapshots, maps wire domains, and remains app-lifetime", async ({ page }) => {
  await installProactiveSocket(page);
  await mockAppShell(page);
  await page.goto(`${baseUrl}/#proactive/memory`);

  await expect(page.locator("[data-proactive-domain]")).toHaveAttribute("data-proactive-revision", "1");
  await page.evaluate((payload) => (window as unknown as { __tgaEmitProactive: (value: unknown) => void }).__tgaEmitProactive(payload), snapshot("dream", 2, { data: { cursors: {}, next_run_at: "2026-08-03T09:00:00+08:00", usage: { calls: 1, input_tokens: 2, output_tokens: 3, total_tokens: 5 }, memory: { user: "偏好简洁", soul: "先核对事实" }, memory_tokens: { user_tokens: 4, soul_tokens: 4, total_tokens: 8 }, profile_limits: { user_profile_token_limit: 12000, soul_profile_token_limit: 4000, profile_total_token_limit: 16000 }, timezone: "Asia/Shanghai" } }));
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

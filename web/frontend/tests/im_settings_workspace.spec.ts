import { expect, test } from "@playwright/test";

const baseUrl = process.env.TGA_WEB_URL || "http://127.0.0.1:8000";

function controlConfig() {
  const editable = {
    llm: { provider: "openai-compatible", api_key_configured: true, base_url: "https://api.example.test/v1", model: "example-model", timeout_seconds: 60, max_retries: 2, retry_delay_seconds: 0.5, streaming_enabled: true },
    runtime: { max_tool_rounds: 5, max_tool_calls_per_round: 8, parallel_tool_calls_enabled: true, max_parallel_tool_calls: 4, turn_timeout_seconds: 120, max_context_tokens: 300000, max_tool_result_tokens: 8000 },
    memory: { compact_token_threshold: 200000, recent_window_token_limit: 20000 },
    sessions: { retention_days: 7 },
    skills: { max_loaded_skills_per_turn: 3, max_skill_tokens: 8000, max_loaded_skill_tokens_per_turn: 16000 },
    proactive: { enabled: true, timezone: "Asia/Shanghai", review_provider: null, review_api_key_configured: false, review_base_url: null, review_model: null, background_max_concurrency: 4, breakbeat_refresh_minutes: 60, dream_refresh_hours: 24, review_window_token_limit: 100000, profile_total_token_limit: 16000, user_profile_token_limit: 12000, soul_profile_token_limit: 4000, skill_observation_turn_interval: 10, skill_observation_token_limit: 160, skill_evolution_batch_token_limit: 100000 },
    tool_permissions: { auto_approve_tools: false, approval_required_tools: [] },
  };
  return { desired_revision: "sha256:desired", active_revision: "sha256:active", state: "active", last_apply_error: null, desired: editable, active: editable };
}

async function mockSettings(page: import("@playwright/test").Page, accounts: Array<Record<string, unknown>> = []) {
  await page.route("**/api/control/config", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(controlConfig()) }));
  await page.route("**/api/control/tools", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ active_revision: "sha256:active", tools: [], unavailable_approval_required: [] }) }));
  await page.route("**/api/control/channels", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ accounts }) }));
}

test("settings separates runtime configuration from message channel controls", async ({ page }) => {
  await mockSettings(page);
  await page.goto(`${baseUrl}/#settings`);

  await expect(page.getByRole("heading", { name: "运行配置" })).toBeVisible();
  await expect(page.getByRole("button", { name: "消息渠道" })).toBeVisible();
  await expect(page.getByLabel("模型名称")).toBeVisible();

  await page.getByRole("button", { name: "消息渠道" }).click();
  await expect(page.getByRole("heading", { name: "消息渠道" })).toBeVisible();
  await expect(page.getByLabel("飞书 App ID")).toBeVisible();
  await expect(page.getByLabel("模型名称")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "运行配置" })).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
});

test("Feishu inputs replace the browser outline with a surface focus state", async ({ page }) => {
  await mockSettings(page);
  await page.goto(`${baseUrl}/#settings`);
  await page.getByRole("button", { name: "消息渠道" }).click();

  const field = page.getByLabel("飞书 App ID");
  await field.focus();
  await expect(field).toBeFocused();
  await expect(field).toHaveCSS("outline-style", "none");
  await expect(field).toHaveCSS("box-shadow", "none");
});

test("message channels remains within the viewport on a narrow screen", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockSettings(page);
  await page.goto(`${baseUrl}/#settings`);
  await page.getByRole("button", { name: "消息渠道" }).click();

  const bounds = await page.locator(".settings-navigation, .settings-content, .channel-feishu-form").evaluateAll((elements) => elements.map((element) => {
    const rect = element.getBoundingClientRect();
    return { left: rect.left, right: rect.right };
  }));

  expect(bounds.every((box) => box.left >= 0 && box.right <= 390)).toBe(true);
  await expect(page.getByLabel("飞书 App ID")).toHaveJSProperty("offsetHeight", 34);
});

test("Owner verification input has the same borderless focus state as Feishu credentials", async ({ page }) => {
  await mockSettings(page, [{ id: "feishu-owner", platform: "feishu", principal_id: "owner", principal_kind: "owner", status: "active", enabled: true, subscribed: false, credential_state: "configured", connected: true, app_id_masked: "cli••••mo" }]);
  await page.goto(`${baseUrl}/#settings`);
  await page.getByRole("button", { name: "消息渠道" }).click();

  const field = page.getByLabel("Owner 验证码");
  await field.focus();
  await expect(field).toHaveCSS("outline-style", "none");
  await expect(field).toHaveCSS("box-shadow", "none");
});

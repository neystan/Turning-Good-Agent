import { expect, test, type Page } from "@playwright/test";

const baseUrl = process.env.TGA_WEB_URL || "http://127.0.0.1:8000";
const session = { id: "phase9-session", channel: "web", title: "协作验证", pinned: false, archived: false, created_at: "2026-08-11T00:00:00.000Z", updated_at: "2026-08-11T00:00:00.000Z" };

// 返回带完整最终结果的检查器读模型，避免把内容放入聊天流。
function observability() {
  const longContent = Array.from({ length: 36 }, (_, index) => `Worker 最终结论第 ${index + 1} 行`).join("\n");
  return {
    session,
    traces: [],
    token_usage: [],
    tool_calls: [],
    multi_agent_runs: [
      { run_id: "run-1", parent_request_id: "request-1", strategy: "fan_out_fan_in", status: "completed", nodes: [{ node_id: "worker-1", role: "资料核验", status: "completed", duration_ms: 820, content: longContent, error_code: null, error: null }], duration_ms: 920, usage: { total: { turn_total_tokens: 1280 } }, error_code: null, error: null },
      { run_id: "run-2", parent_request_id: "request-2", strategy: "pipeline", status: "completed", nodes: [{ node_id: "worker-2", role: "执行步骤", status: "completed", duration_ms: 720, content: "串行结果", error_code: null, error: null }, { node_id: "worker-2-summary", role: "汇总步骤", status: "completed", duration_ms: 180, content: "汇总结果", error_code: null, error: null }], duration_ms: 820, usage: null, error_code: null, error: null },
      { run_id: "run-3", parent_request_id: "request-3", strategy: "fan_out_fan_in", status: "completed", nodes: [{ node_id: "worker-3", role: "单项核验", status: "completed", duration_ms: 620, content: "单项结论", error_code: null, error: null }], duration_ms: 720, usage: null, error_code: null, error: null },
    ],
  };
}

// 为三次父消息模拟现有会话事件，不创建独立 Multi-Agent 通道。
async function installMultiAgentSocket(page: Page, keepFirstRunActive = false) {
  await page.addInitScript((firstRunStaysActive: boolean) => {
    let turn = 0;
    const sent: Array<Record<string, unknown>> = [];
    let activeSocket: { onmessage: ((event: MessageEvent<string>) => void) | null } | null = null;
    const strategies = ["fan_out_fan_in", "pipeline", "fan_out_fan_in"];
    class FakeWebSocket {
      static OPEN = 1;
      static CONNECTING = 0;
      readyState = FakeWebSocket.OPEN;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent<string>) => void) | null = null;
      onclose: ((event: CloseEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;

      constructor(readonly url: string) {
        if (url.includes("/ws/web")) activeSocket = this;
        queueMicrotask(() => this.onopen?.(new Event("open")));
      }

      send(raw: string) {
        const action = JSON.parse(raw) as Record<string, unknown>;
        sent.push(action);
        if (action.type !== "message.send") return;
        turn += 1;
        const currentTurn = turn;
        const requestId = `request-${currentTurn}`;
        const runId = `run-${currentTurn}`;
        const strategy = strategies[currentTurn - 1] || "fan_out_fan_in";
        const emit = (payload: Record<string, unknown>) => {
          this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>);
        };
        queueMicrotask(() => {
          emit({ type: "message.accepted", client_action_id: action.client_action_id, session_id: "phase9-session", request_id: requestId });
          emit({ event_id: currentTurn * 10 + 1, type: "task.queued", session_id: "phase9-session", request_id: requestId, payload: {} });
          emit({ event_id: currentTurn * 10 + 2, type: "multi_agent.run.started", session_id: "phase9-session", request_id: requestId, payload: { run_id: runId, node_id: null, task_label: "协作任务", strategy, status: "queued", duration_ms: null, usage: null, error_code: null, error: null } });
          if (!firstRunStaysActive || currentTurn !== 1) {
            emit({ event_id: currentTurn * 10 + 3, type: "multi_agent.node.updated", session_id: "phase9-session", request_id: requestId, payload: { run_id: runId, node_id: `worker-${currentTurn}`, task_label: "核验任务", strategy, status: "completed", duration_ms: 680, usage: null, error_code: null, error: null } });
            emit({ event_id: currentTurn * 10 + 4, type: "multi_agent.run.completed", session_id: "phase9-session", request_id: requestId, payload: { run_id: runId, node_id: null, task_label: "协作任务", strategy, status: "completed", duration_ms: 820, usage: null, error_code: null, error: null } });
            emit({ event_id: currentTurn * 10 + 5, type: "task.completed", session_id: "phase9-session", request_id: requestId, payload: {} });
            emit({ event_id: currentTurn * 10 + 6, type: "session.snapshot", session_id: "phase9-session", request_id: requestId, payload: { state: "idle", multi_agent_runs: [] } });
          }
        });
      }

      close() { this.onclose?.(new Event("close") as CloseEvent); }
    }
    Object.assign(window, {
      WebSocket: FakeWebSocket,
      __phase9Sent: () => sent,
      // 推进活跃 Run 状态以验证同一 Run 的 Stop 去重。
      __phase9AdvanceActiveRun: () => activeSocket?.onmessage?.({ data: JSON.stringify({ event_id: 13, type: "multi_agent.run.started", session_id: "phase9-session", request_id: "request-1", payload: { run_id: "run-1", node_id: null, task_label: "协作任务", strategy: "fan_out_fan_in", status: "running", duration_ms: 120, usage: null, error_code: null, error: null } }) } as MessageEvent<string>),
    });
  }, keepFirstRunActive);
}

// 安装普通会话与控制面 mock，保留真实组件渲染。
async function mockPhase9Api(page: Page) {
  const editable = {
    llm: { provider: "openai-compatible", api_key_configured: true, base_url: "https://api.example.test/v1", model: "example-model", timeout_seconds: 60, max_retries: 2, retry_delay_seconds: 0.5, streaming_enabled: true },
    runtime: { max_tool_rounds: 5, max_tool_calls_per_round: 8, parallel_tool_calls_enabled: true, max_parallel_tool_calls: 4, turn_timeout_seconds: 120, max_context_tokens: 300000, max_tool_result_tokens: 8000 },
    multi_agent: { enabled: true, run_timeout_seconds: 60000, worker_timeout_seconds: 20000, max_workers_per_run: 4, max_concurrent_workers_per_run: 4, max_concurrent_workers_global: 8, worker_result_token_limit: 8000, parent_result_token_limit: 16000 },
    memory: { compact_token_threshold: 200000, recent_window_token_limit: 20000 },
    sessions: { retention_days: 7 },
    skills: { max_loaded_skills_per_turn: 3, max_skill_tokens: 8000, max_loaded_skill_tokens_per_turn: 16000 },
    proactive: { enabled: true, timezone: "Asia/Shanghai", review_provider: null, review_api_key_configured: false, review_base_url: null, review_model: null, background_max_concurrency: 4, breakbeat_refresh_minutes: 60, dream_refresh_hours: 24, review_window_token_limit: 100000, profile_total_token_limit: 16000, user_profile_token_limit: 12000, soul_profile_token_limit: 4000, skill_observation_turn_interval: 10, skill_observation_token_limit: 160, skill_evolution_batch_token_limit: 100000, skill_evolution_batches_per_kind: 3 },
  };
  await page.route(/\/api\/sessions\?archived=false$/, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify([session]) }));
  await page.route(/\/api\/sessions\?archived=true$/, (route) => route.fulfill({ contentType: "application/json", body: "[]" }));
  await page.route(`**/api/sessions/${session.id}/messages`, (route) => route.fulfill({ contentType: "application/json", body: "[]" }));
  await page.route(`**/api/sessions/${session.id}/context-window`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ current_context_tokens: 0, max_context_tokens: 300000 }) }));
  await page.route(`**/api/sessions/${session.id}/observability`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify(observability()) }));
  await page.route("**/api/settings/ui", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ auto_approve_tools: false }) }));
  await page.route("**/api/control/config", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ desired_revision: "desired", active_revision: "active", state: "active", last_apply_error: null, desired: editable, active: editable }) }));
}

test("Multi-Agent uses the parent timeline and renders supported inspector topologies", async ({ page }) => {
  await installMultiAgentSocket(page);
  await mockPhase9Api(page);
  await page.goto(`${baseUrl}/sessions/${session.id}`);
  await expect.poll(() => page.evaluate(() => (window as unknown as { __phase9Sent: () => Array<Record<string, unknown>> }).__phase9Sent().some((item) => item.type === "session.subscribe" && item.session_id === "phase9-session"))).toBe(true);

  await expect(page.getByRole("radio", { name: "Auto" })).toHaveCount(1);
  await page.getByLabel("消息内容").fill("第一项复杂任务");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.getByLabel("消息内容")).toBeEditable();

  for (const [index, task] of ["第二项任务", "第三项任务"].entries()) {
    await page.getByLabel("消息内容").fill(task);
    await page.getByRole("button", { name: "发送消息" }).click();
    await expect(page.locator(".multi-agent-run-card")).toHaveCount(index + 2);
  }
  await expect(page.locator(".multi-agent-run-card")).toHaveCount(3);
  await expect(page.locator(".multi-agent-run-card")).toContainText(["并行汇总", "串行管线", "并行汇总"]);

  await page.locator(".multi-agent-run-card").first().click();
  await expect(page.locator(".multi-agent-inspector")).toBeVisible();
  await expect(page.locator(".multi-agent-topology.is-fan_out_fan_in")).toBeVisible();
  await expect(page.locator(".multi-agent-worker-result pre")).toContainText("Worker 最终结论第 36 行");
  await expect(page.locator(".multi-agent-worker-results-scroll .scroll-area-viewport")).toHaveCSS("max-height", "250px");
  await page.getByRole("button", { name: "关闭会话检查器" }).click();
  await page.locator(".multi-agent-run-card").nth(1).click();
  await expect(page.locator(".multi-agent-topology.is-pipeline")).toBeVisible();
  await expect(page.locator(".multi-agent-topology.is-pipeline .multi-agent-topology-arrow")).toHaveCount(3);
  await expect(page.locator(".multi-agent-topology.is-pipeline")).toContainText("父 Agent→执行步骤→汇总步骤→父 Agent");
  await page.getByRole("button", { name: "关闭会话检查器" }).click();
  await page.locator(".multi-agent-run-card").nth(2).click();
  await expect(page.locator(".multi-agent-topology.is-fan_out_fan_in")).toBeVisible();
});

// 验证活跃协作卡片的停止动作仍走父会话现有通道。
test("active Multi-Agent Run timeline card sends task.stop", async ({ page }) => {
  await installMultiAgentSocket(page, true);
  await mockPhase9Api(page);
  await page.goto(`${baseUrl}/sessions/${session.id}`);

  await page.getByLabel("消息内容").fill("保持运行的协作任务");
  await page.getByRole("button", { name: "发送消息" }).click();
  await expect(page.locator(".multi-agent-run-card.is-queued")).toBeVisible();
  const stopButton = page.getByRole("button", { name: "停止协作任务" });
  await stopButton.click();
  await page.evaluate(() => (window as unknown as { __phase9AdvanceActiveRun: () => void }).__phase9AdvanceActiveRun());
  await expect(page.locator(".multi-agent-run-card.is-running")).toBeVisible();
  await stopButton.click();
  await expect.poll(() => page.evaluate(() => (window as unknown as { __phase9Sent: () => Array<Record<string, unknown>> }).__phase9Sent().filter((item) => item.type === "task.stop" && item.session_id === "phase9-session").length)).toBe(1);
});

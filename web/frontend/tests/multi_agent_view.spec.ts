import { expect, test } from "vitest";

import { buildMultiAgentRunView } from "../src/state/multi_agent_view";
import { applySessionAction, createSessionState } from "../src/state/session_state";
import type { MultiAgentEvent, TaskEvent } from "../src/types";

const started: MultiAgentEvent = {
  event_id: 1,
  session_id: "session-1",
  request_id: "request-1",
  type: "multi_agent.run.started",
  created_at: "2026-08-11T08:00:00.000Z",
  payload: { run_id: "run-1", node_id: null, task_label: "协作任务", strategy: "fan_out_fan_in", status: "queued", duration_ms: null, usage: null, error_code: null, error: null },
};

const firstStarted: MultiAgentEvent = {
  ...started,
  event_id: 2,
  type: "multi_agent.node.updated",
  payload: { ...started.payload, node_id: "worker-1", task_label: "资料核验", status: "running" },
};

const firstCompleted: MultiAgentEvent = {
  ...firstStarted,
  event_id: 3,
  type: "multi_agent.node.updated",
  payload: { ...firstStarted.payload, status: "completed", duration_ms: 1250 },
};

const secondFailed: MultiAgentEvent = {
  ...firstStarted,
  event_id: 4,
  type: "multi_agent.node.updated",
  payload: { ...firstStarted.payload, node_id: "worker-2", task_label: "风险检查", status: "failed", duration_ms: 3200, error_code: "worker_failed", error: "Worker 未能完成核验" },
};

const runCompleted: MultiAgentEvent = {
  ...started,
  event_id: 5,
  type: "multi_agent.run.completed",
  payload: { ...started.payload, status: "completed", duration_ms: 4800 },
};

// 验证并行 Run 保留节点顺序和部分完成状态。
test("fan-out projection preserves node order and partial state", () => {
  const view = buildMultiAgentRunView([started, firstStarted, firstCompleted, secondFailed, runCompleted]);

  expect(view?.strategy).toBe("fan_out_fan_in");
  expect(view?.partial).toBe(true);
  expect(view?.nodes.map((node) => node.status)).toEqual(["completed", "failed"]);
});

// 验证投影拒绝已经删除的旧事件类型。
test("projection ignores removed event types", () => {
  const view = buildMultiAgentRunView([
    started,
    { ...firstStarted, event_id: 6, type: "multi_agent.node.started" as MultiAgentEvent["type"] },
  ]);

  expect(view?.nodes).toEqual([]);
});

// 验证空闲快照会清除断线前遗留的活动 Run，解除输入锁。
test("idle empty snapshot removes a stale active run after connection loss", () => {
  const queued: TaskEvent = { ...started, event_id: 0, type: "task.queued", payload: {} };
  const withTurn = applySessionAction(createSessionState(), { type: "event.received", event: queued });
  const withRun = applySessionAction(withTurn, { type: "event.received", event: started });
  const disconnected = applySessionAction(withRun, { type: "connection.lost" });
  const reconciled = applySessionAction(disconnected, { type: "event.received", event: { type: "session.snapshot", session_id: "session-1", request_id: "request-1", payload: { state: "idle", multi_agent_runs: [] } } });

  expect(withRun.turns["request-1"]?.multiAgentRuns?.[0]?.status).toBe("queued");
  expect(reconciled.turns["request-1"]?.multiAgentRuns?.some((run) => ["queued", "running", "waiting"].includes(run.status))).toBe(false);
});

// 验证权威快照可以补全实时终态 Run 未携带的持久化字段。
test("authoritative snapshot enriches a terminal realtime run", () => {
  const projected = buildMultiAgentRunView([started, firstCompleted, runCompleted]);
  const initial = applySessionAction(createSessionState(), { type: "session.reset", turns: {
    "request-1": { requestId: "request-1", status: "completed", events: [], guidanceCount: 0, startedAt: "2026-08-11T08:00:00.000Z", multiAgentRuns: projected ? [projected] : [] },
  } });
  const reconciled = applySessionAction(initial, { type: "event.received", event: {
    type: "session.snapshot",
    session_id: "session-1",
    request_id: "request-1",
    payload: { state: "idle", multi_agent_runs: [{ run_id: "run-1", parent_request_id: "request-1", strategy: "fan_out_fan_in", status: "completed", nodes: [{ node_id: "worker-1", role: "资料核验", status: "completed", duration_ms: 1250, content: "权威最终结果", error_code: null, error: null }], duration_ms: 4800, usage: { worker: { turn_total_tokens: 1200 }, total: { turn_total_tokens: 3210 } }, error_code: null, error: null }] },
  } });
  const run = reconciled.turns["request-1"]?.multiAgentRuns?.[0];

  expect(run).toMatchObject({ parent_request_id: "request-1", usage: { worker: { turn_total_tokens: 1200 }, total: { turn_total_tokens: 3210 } } });
  expect(run?.nodes[0]?.content).toBe("权威最终结果");
});

// 验证首次加载收到父请求标识时，Run 不会落到孤立的 synthetic turn。
test("cold snapshot anchors a persisted run to its parent request turn", () => {
  const state = applySessionAction(createSessionState(), { type: "event.received", event: {
    type: "session.snapshot",
    session_id: "session-1",
    request_id: "",
    payload: {
      state: "idle",
      multi_agent_runs: [{
        run_id: "run-1",
        parent_request_id: "request-1",
        strategy: "pipeline",
        status: "completed",
        nodes: [],
        duration_ms: 120,
        usage: null,
        error_code: null,
        error: null,
      }],
    },
  } });

  expect(state.turns["request-1"]?.multiAgentRuns?.[0]?.run_id).toBe("run-1");
  expect(state.turns["multi-agent:run-1"]).toBeUndefined();
});

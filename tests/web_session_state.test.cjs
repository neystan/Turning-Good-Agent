const assert = require("node:assert/strict");
const test = require("node:test");

const { applySessionAction, createSessionState } = require("/tmp/tga-web-state/state/session_state.js");

/** 创建用于断线回归验证的浏览器待发送动作。 */
function pendingAction(id, content) {
  return { id, kind: "message", content, sessionId: "session-1", createdAt: "2026-07-27T00:00:00+00:00" };
}

test("动作失败时保留失败消息且不恢复输入草稿", () => {
  let state = createSessionState();
  state = applySessionAction(state, { type: "message.optimistic", action: pendingAction("first", "第一条") });
  state = applySessionAction(state, { type: "message.optimistic", action: pendingAction("second", "第二条") });
  state = applySessionAction(state, { type: "action.failed", actionId: "first", message: "连接尚未建立" });

  assert.equal(state.pendingDraft, "");
  assert.deepEqual(state.messages.map((message) => message.content), ["第一条", "第二条"]);
  assert.equal(state.messages[0].delivery, "failed");
  assert.equal(state.pendingActions.first.status, "failed");
  assert.equal(state.pendingActions.second.status, "sending");
});

test("已受理动作不会被后续错误回包撤回", () => {
  let state = createSessionState();
  state = applySessionAction(state, { type: "message.optimistic", action: pendingAction("accepted", "已送达") });
  state = applySessionAction(state, { type: "action.accepted", actionId: "accepted", sessionId: "session-1", requestId: "turn-1" });
  state = applySessionAction(state, { type: "action.failed", actionId: "accepted", message: "请求失败" });

  assert.equal(state.messages[0].content, "已送达");
  assert.equal(state.messages[0].delivery, "sent");
});

test("恢复会话时保留本地缓存的消息与任务过程", () => {
  const turns = {
    "turn-1": {
      requestId: "turn-1",
      status: "completed",
      events: [{ type: "task.completed", request_id: "turn-1", session_id: "session-1", payload: {} }],
      guidanceCount: 0,
      startedAt: "2026-07-27T00:00:00+00:00",
      finishedAt: "2026-07-27T00:00:03+00:00",
    },
  };

  const messages = [{
    id: "client-turn-1",
    role: "user",
    content: "查询天气",
    created_at: "2026-07-27T00:00:00+00:00",
    metadata: {},
    client_action_id: "action-1",
    request_id: "turn-1",
  }];
  const state = applySessionAction(createSessionState(), { type: "session.reset", messages, turns });

  assert.deepEqual(state.turns, turns);
  assert.deepEqual(state.messages, messages);
});

test("空闲快照将未完成轮次收口为可重试的网络失败消息", () => {
  let state = createSessionState();
  state = applySessionAction(state, { type: "message.optimistic", action: pendingAction("network", "查询杭州天气") });
  state = applySessionAction(state, { type: "action.accepted", actionId: "network", sessionId: "session-1", requestId: "turn-network" });
  state = applySessionAction(state, { type: "event.received", event: { type: "task.running", request_id: "turn-network", session_id: "session-1", payload: {} } });
  state = applySessionAction(state, { type: "event.received", event: { type: "session.snapshot", request_id: "", session_id: "session-1", payload: { state: "idle" } } });

  assert.equal(state.turns["turn-network"].status, "interrupted");
  assert.equal(state.messages.at(-1).content, "网络连接失败");
  assert.equal(state.messages.at(-1).metadata.retry_action_id, "network");
  assert.equal(state.messages.at(-1).metadata.retry_content, "查询杭州天气");
});

test("重试失败消息时复用原消息而不复制聊天记录", () => {
  let state = createSessionState();
  state = applySessionAction(state, { type: "message.optimistic", action: pendingAction("retry", "重新发送") });
  state = applySessionAction(state, { type: "action.failed", actionId: "retry", message: "连接尚未建立" });
  state = applySessionAction(state, { type: "action.retry", actionId: "retry" });

  assert.equal(state.messages.length, 1);
  assert.equal(state.messages[0].delivery, "sending");
  assert.equal(state.pendingActions.retry.status, "sending");
  assert.equal(state.pendingActions.retry.error, undefined);
});

test("回放相同事件不会重复工具计数或追加回答内容", () => {
  let state = createSessionState();
  state = applySessionAction(state, { type: "message.optimistic", action: pendingAction("replay", "查询时间") });
  state = applySessionAction(state, { type: "action.accepted", actionId: "replay", sessionId: "session-1", requestId: "turn-replay" });
  const events = [
    { event_id: 11, type: "tool.started", request_id: "turn-replay", session_id: "session-1", payload: { tool_name: "now" } },
    { event_id: 12, type: "response.delta", request_id: "turn-replay", session_id: "session-1", payload: { content: "现在是 20:10。" } },
  ];
  for (const event of events) state = applySessionAction(state, { type: "event.received", event });
  for (const event of events) state = applySessionAction(state, { type: "event.received", event });

  assert.equal(state.turns["turn-replay"].events.length, 2);
  assert.equal(state.messages.at(-1).content, "现在是 20:10。");
});

test("点击重试会移除上一轮失败内容和任务过程", () => {
  let state = createSessionState();
  state = applySessionAction(state, { type: "message.optimistic", action: pendingAction("restart", "重新查询时间") });
  state = applySessionAction(state, { type: "action.accepted", actionId: "restart", sessionId: "session-1", requestId: "turn-restart" });
  state = applySessionAction(state, { type: "event.received", event: { event_id: 21, type: "task.running", request_id: "turn-restart", session_id: "session-1", payload: {} } });
  state = applySessionAction(state, { type: "connection.lost" });
  state = applySessionAction(state, { type: "action.retry", actionId: "restart" });

  assert.deepEqual(state.messages.map((message) => message.role), ["user"]);
  assert.equal(state.turns["turn-restart"], undefined);
  assert.equal(state.pendingActions.restart.status, "sending");
});

test("刷新后重试仍会恢复动作关联并清理旧内容", () => {
  const state = applySessionAction(createSessionState(), {
    type: "session.reset",
    messages: [
      { id: "user-1", role: "user", content: "删除文件", created_at: "2026-07-27T00:00:00+00:00", metadata: {}, request_id: "turn-cached" },
      { id: "assistant-1", role: "assistant", content: "网络连接失败", created_at: "2026-07-27T00:00:01+00:00", metadata: { network_failure: true, retry_action_id: "cached-action", retry_content: "删除文件" }, request_id: "turn-cached" },
    ],
    turns: { "turn-cached": { requestId: "turn-cached", status: "interrupted", events: [], guidanceCount: 0, startedAt: "2026-07-27T00:00:00+00:00" } },
  });
  const retried = applySessionAction(state, {
    type: "action.retry",
    actionId: "cached-action",
    retry: { id: "cached-action", kind: "message", content: "删除文件", sessionId: "session-1", createdAt: "2026-07-27T00:00:00+00:00", requestId: "turn-cached" },
  });

  assert.deepEqual(retried.messages.map((message) => message.role), ["user"]);
  assert.equal(retried.turns["turn-cached"], undefined);
  assert.equal(retried.pendingActions["cached-action"].requestId, "turn-cached");
});

test("重试后会隐藏旧轮并在历史重新加载时只保留新轮", () => {
  let state = applySessionAction(createSessionState(), {
    type: "session.reset",
    messages: [
      { id: "old-user", role: "user", content: "创建 hello", created_at: "2026-07-27T00:00:00+00:00", metadata: {}, request_id: "turn-old" },
      { id: "old-assistant", role: "assistant", content: "网络连接失败", created_at: "2026-07-27T00:00:01+00:00", metadata: { network_failure: true, retry_action_id: "retry-old", retry_content: "创建 hello" }, request_id: "turn-old" },
    ],
    turns: { "turn-old": { requestId: "turn-old", status: "interrupted", events: [], guidanceCount: 0, startedAt: "2026-07-27T00:00:00+00:00" } },
  });
  state = applySessionAction(state, { type: "action.retry", actionId: "retry-old", retry: { id: "retry-old", kind: "message", content: "创建 hello", sessionId: "session-1", createdAt: "2026-07-27T00:00:00+00:00", requestId: "turn-old" } });
  state = applySessionAction(state, { type: "action.accepted", actionId: "retry-old", sessionId: "session-1", requestId: "turn-new" });
  state = applySessionAction(state, { type: "history.loaded", messages: [
    { id: "old-user", role: "user", content: "创建 hello", created_at: "2026-07-27T00:00:00+00:00", metadata: {} },
    { id: "old-assistant", role: "assistant", content: "旧回复", created_at: "2026-07-27T00:00:01+00:00", metadata: { incomplete: true } },
    { id: "new-user", role: "user", content: "创建 hello", created_at: "2026-07-27T00:01:00+00:00", metadata: {} },
    { id: "new-assistant", role: "assistant", content: "新回复", created_at: "2026-07-27T00:01:02+00:00", metadata: {} },
  ] });

  assert.deepEqual(state.hiddenMessageIds.sort(), ["old-assistant", "old-user"]);
  assert.deepEqual(state.messages.map((message) => message.id), ["new-user", "new-assistant"]);
  assert.equal(state.messages[0].request_id, "turn-new");
});

test("重试时会删除同一旧轮未带请求标识的已停止消息", () => {
  let state = applySessionAction(createSessionState(), {
    type: "session.reset",
    messages: [
      { id: "old-user", role: "user", content: "删除它", created_at: "2026-07-27T00:00:00+00:00", metadata: {}, request_id: "turn-old" },
      { id: "stopped-assistant", role: "assistant", content: "", created_at: "2026-07-27T00:00:01+00:00", metadata: { incomplete: true } },
      { id: "later-user", role: "user", content: "下一件事", created_at: "2026-07-27T00:01:00+00:00", metadata: {} },
    ],
  });

  state = applySessionAction(state, {
    type: "action.retry",
    actionId: "retry-old",
    retry: { id: "retry-old", kind: "message", content: "删除它", sessionId: "session-1", createdAt: "2026-07-27T00:00:00+00:00", requestId: "turn-old" },
  });

  assert.deepEqual(state.messages.map((message) => message.id), ["old-user", "later-user"]);
  assert.ok(state.hiddenMessageIds.includes("stopped-assistant"));
});

import { applySessionAction, createSessionState } from "../web/frontend/src/state/session_state";

/** 断言失败消息只影响其对应的乐观消息。 */
function testActionErrorOnlyMarksMatchingMessage(): void {
  let state = createSessionState();
  state = applySessionAction(state, {
    type: "message.optimistic",
    action: {
      id: "first-action",
      kind: "message",
      content: "第一条",
      sessionId: "session-a",
      createdAt: "2026-07-26T00:00:00Z",
    },
  });
  state = applySessionAction(state, {
    type: "message.optimistic",
    action: {
      id: "second-action",
      kind: "message",
      content: "第二条",
      sessionId: "session-a",
      createdAt: "2026-07-26T00:00:01Z",
    },
  });
  state = applySessionAction(state, {
    type: "action.failed",
    actionId: "first-action",
    message: "网络异常",
  });

  if (state.pendingActions["first-action"]?.status !== "failed") throw new Error("首条消息未标记失败");
  if (state.pendingActions["second-action"]?.status !== "sending") throw new Error("不相关消息被错误影响");
}

/** 断言工具事件只归属对应 request_id 的 turn。 */
function testToolEventOnlyEntersMatchingTurn(): void {
  let state = createSessionState();
  state = applySessionAction(state, {
    type: "event.received",
    event: {
      session_id: "session-a",
      request_id: "turn-a",
      type: "task.running",
      payload: {},
    },
  });
  state = applySessionAction(state, {
    type: "event.received",
    event: {
      session_id: "session-a",
      request_id: "turn-b",
      type: "task.running",
      payload: {},
    },
  });
  state = applySessionAction(state, {
    type: "event.received",
    event: {
      session_id: "session-a",
      request_id: "turn-a",
      type: "tool.started",
      payload: { tool_name: "read_file" },
    },
  });

  if (state.turns["turn-a"]?.events.length !== 2) throw new Error("工具事件未进入所属 turn");
  if (state.turns["turn-b"]?.events.length !== 1) throw new Error("工具事件进入了错误 turn");
}

testActionErrorOnlyMarksMatchingMessage();
testToolEventOnlyEntersMatchingTurn();
console.log("web session state assertions passed");

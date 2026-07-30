import { readSessionCache, shouldWriteSessionCache, writeSessionCache } from "../src/state/session_cache";
import type { ChatMessage, TaskEvent, TurnState } from "../src/types";

const stored = new Map<string, string>();

Object.assign(globalThis, {
  sessionStorage: {
    getItem: (key: string) => stored.get(key) || null,
    setItem: (key: string, value: string) => stored.set(key, value),
  },
});

function expect(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

function event(eventId: number): TaskEvent {
  return { event_id: eventId, session_id: "session-1", request_id: "request-1", type: "task.running", payload: {} };
}

function turn(status: TurnState["status"], eventId: number): TurnState {
  return { requestId: `request-${eventId}`, status, events: [event(eventId)], guidanceCount: 0, startedAt: "2026-07-28T00:00:00.000Z" };
}

const completed: ChatMessage = {
  id: "persisted-answer",
  role: "assistant",
  content: "已经保存的回复",
  created_at: "2026-07-28T00:00:00.000Z",
  metadata: {},
  request_id: "request-9",
};
const pending: ChatMessage = {
  id: "client-pending",
  role: "user",
  content: "尚未确认的消息",
  created_at: "2026-07-28T00:00:00.000Z",
  metadata: {},
  delivery: "sending",
  client_action_id: "action-1",
};
const retryableFailure: ChatMessage = {
  id: "network-failure",
  role: "assistant",
  content: "网络连接失败",
  created_at: "2026-07-28T00:00:00.000Z",
  metadata: { network_failure: true, retry_action_id: "action-2" },
  request_id: "request-2",
};

writeSessionCache("session-1", {
  messages: [completed, pending, retryableFailure],
  turns: { completed: turn("completed", 9), running: turn("running", 10) },
  hiddenMessageIds: ["retry-hidden"],
  lastEventId: 10,
});

const cached = readSessionCache("session-1");
expect(cached.messages.length === 3, "completed messages must stay available to anchor their activity cluster in the timeline");
expect(cached.messages.some((message) => message.id === completed.id), "persisted assistant content must retain its request association after refresh");
expect(Object.keys(cached.turns).length === 2 && cached.turns.completed?.status === "completed", "terminal activity clusters must remain visible after refresh");
expect(cached.lastEventId === 10, "the WebSocket event cursor must survive a page refresh");
expect(!shouldWriteSessionCache("session-1", null), "the initial empty reducer state must not overwrite a cache before it is restored");
expect(shouldWriteSessionCache("session-1", "session-1"), "the restored session state must be persisted after hydration");

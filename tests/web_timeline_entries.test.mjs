import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const { buildTimelineEntries } = require("/tmp/tga-timeline/state/timeline_entries.js");

const turn = { requestId: "turn-1", status: "completed", events: [], guidanceCount: 0, startedAt: "2026-07-26T08:00:00Z" };

test("buildTimelineEntries 将同轮活动簇置于用户与助手消息之间", () => {
  const entries = buildTimelineEntries([
    { id: "u1", role: "user", content: "查询天气", created_at: "2026-07-26T08:00:00Z", metadata: {}, request_id: "turn-1" },
    { id: "a1", role: "assistant", content: "晴天", created_at: "2026-07-26T08:00:02Z", metadata: {}, request_id: "turn-1" },
  ], { "turn-1": turn });

  assert.deepEqual(entries.map((entry) => entry.kind), ["message", "turn", "message"]);
});

test("buildTimelineEntries 对尚未产生回答的运行中任务只插入一次", () => {
  const entries = buildTimelineEntries([
    { id: "u1", role: "user", content: "查询天气", created_at: "2026-07-26T08:00:00Z", metadata: {}, request_id: "turn-1" },
  ], { "turn-1": { ...turn, status: "running" } });

  assert.deepEqual(entries.map((entry) => entry.kind), ["message", "turn"]);
});

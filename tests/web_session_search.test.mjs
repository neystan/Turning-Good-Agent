import assert from "node:assert/strict";
import test from "node:test";

import { filterSessions } from "/tmp/tga-session-search.mjs";

const sessions = [
  { id: "older", title: "部署检查", updated_at: "2026-07-20T08:00:00Z" },
  { id: "newer", title: "部署回归", updated_at: "2026-07-24T08:00:00Z" },
  { id: "other", title: "整理文档", updated_at: "2026-07-25T08:00:00Z" },
];

test("filterSessions 按标题匹配并按更新时间倒序返回", () => {
  assert.deepEqual(
    filterSessions(sessions, "部署").map((session) => session.id),
    ["newer", "older"],
  );
});

test("filterSessions 空查询返回全部会话", () => {
  assert.deepEqual(
    filterSessions(sessions, "").map((session) => session.id),
    ["other", "newer", "older"],
  );
});

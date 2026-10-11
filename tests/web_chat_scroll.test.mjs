import assert from "node:assert/strict";
import test from "node:test";

import { shouldFollowLatest } from "/tmp/tga-chat-scroll.mjs";

test("阅读历史时助手新消息不自动跟随", () => {
  assert.equal(shouldFollowLatest({ nearBottom: false, latestRole: "assistant", forced: false }), false);
});

test("用户刚发送消息或显式请求时自动跟随", () => {
  assert.equal(shouldFollowLatest({ nearBottom: false, latestRole: "user", forced: false }), true);
  assert.equal(shouldFollowLatest({ nearBottom: false, latestRole: "assistant", forced: true }), true);
});

import assert from "node:assert/strict";
import test from "node:test";

import { buildContextWindowView } from "/tmp/tga-context-window.mjs";

test("buildContextWindowView 格式化上下文读数与剩余比例", () => {
  const view = buildContextWindowView({ current_context_tokens: 2617, max_context_tokens: 300000 });

  assert.equal(view.currentLabel, "2.6k");
  assert.equal(view.limitLabel, "300k");
  assert.equal(view.usedPercentLabel, "0.9%");
  assert.equal(view.remainingPercentLabel, "99.1%");
  assert.equal(view.tone, "normal");
});

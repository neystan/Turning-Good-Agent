import assert from "node:assert/strict";
import test from "node:test";

import { buildInspectorSections, formatTokenCount } from "/tmp/tga-observability-view.mjs";

test("formatTokenCount 使用中文区域千分位", () => {
  assert.equal(formatTokenCount(202828), "202,828");
});

test("buildInspectorSections 将 turn_id 作为可扫描记录标题", () => {
  const sections = buildInspectorSections({
    session: {},
    token_usage: [{ turn_id: "turn-42", input_tokens: 12, output_tokens: 8 }],
    tool_calls: [],
    traces: [],
  });

  assert.equal(sections[0].records[0].title, "turn-42");
});

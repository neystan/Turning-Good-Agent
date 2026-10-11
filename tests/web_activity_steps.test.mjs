import assert from "node:assert/strict";
import { createRequire } from "node:module";
import test from "node:test";

const require = createRequire(import.meta.url);
const { buildActivitySteps, latestActivityStep } = require("/tmp/tga-web-activity/state/activity_steps.js");

test("buildActivitySteps 仅映射允许展示的真实事件", () => {
  const steps = buildActivitySteps([
    { event_id: 1, type: "task.status", payload: { content: "已加入运行中引导" } },
    { event_id: 2, type: "tool.started", payload: { tool_name: "load_skill" } },
    { event_id: 3, type: "tool.started", payload: { tool_name: "server__fetch" } },
    { event_id: 4, type: "untrusted.reasoning", payload: { content: "不应展示" } },
  ]);

  assert.deepEqual(steps.map((step) => step.label), ["已引导", "正在加载 Skill", "正在调用 MCP"]);
});

test("buildActivitySteps 不展示工具调用完成", () => {
  const steps = buildActivitySteps([
    { event_id: 1, type: "tool.finished", payload: { failed: true } },
    { event_id: 2, type: "task.cancelled", payload: {} },
  ]);

  assert.deepEqual(steps.map((step) => step.label), ["任务已停止"]);
});

test("latestActivityStep 返回最后一个真实工具动作", () => {
  const step = latestActivityStep([
    { event_id: 1, type: "tool.started", payload: { tool_name: "read_file" } },
    { event_id: 2, type: "tool.started", payload: { tool_name: "weather" } },
  ]);

  assert.equal(step?.detail, "weather");
});

test("工具完成后不保留工具步骤作为运行摘要", () => {
  const step = latestActivityStep([
    { event_id: 1, type: "tool.started", payload: { tool_name: "read_file" } },
    { event_id: 2, type: "tool.finished", payload: { failed: false } },
  ]);

  assert.equal(step, null);
});

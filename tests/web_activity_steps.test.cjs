const assert = require("node:assert/strict");
const test = require("node:test");

const { buildDetailActivitySteps } = require("/tmp/tga-web-activity/state/activity_steps.js");

/** 创建用于任务过程展示的最小事件。 */
function event(type, payload = {}) {
  return { event_id: type, type, payload };
}

test("终态与待审批步骤不在展开过程重复展示", () => {
  const steps = buildDetailActivitySteps([
    event("tool.started", { tool_name: "read_file" }),
    event("approval.requested", { approval_id: "approval-1", tool_name: "write_file" }),
    event("task.stopping"),
    event("task.completed"),
  ], true);

  assert.deepEqual(steps.map((step) => step.label), ["正在调用工具", "已请求停止"]);
});

import { test } from "@playwright/test";

import { buildInspectorSections } from "../src/state/observability_view";
import { buildActivitySteps, latestActivityStep } from "../src/state/activity_steps";
import type { Observability, TaskEvent } from "../src/types";

function expect(condition: unknown, message: string): asserts condition {
  if (!condition) throw new Error(message);
}

test("observability and tool activity projections remain user-readable", () => {
const turnId = "58c1473e-2924-4cf0-b9c5-27bc535a5372";

const observability: Observability = {
  session: {
    id: "session-1",
    channel: "web",
    title: "测试会话",
    pinned: false,
    archived: false,
    created_at: "2026-07-28T00:00:00.000Z",
    updated_at: "2026-07-28T00:00:00.000Z",
  },
  token_usage: [{ turn_id: turnId, input_tokens: 4986, output_tokens: 129 }, { turn_id: "turn-2", input_tokens: 10, output_tokens: 5 }],
  traces: [
    { turn_id: turnId, state: "COMPACT", duration_ms: 1.5 },
    { turn_id: turnId, state: "SAVE", duration_ms: 6.2 },
    { turn_id: turnId, state: "RUN", duration_ms: 36058 },
    { turn_id: "turn-2", state: "RESPOND", duration_ms: 48 },
  ],
  tool_calls: [{ turn_id: turnId, tool_name: "web_fetch", duration_ms: 4331, error: null }],
};

const sections = buildInspectorSections(observability);

expect(sections[0].records[0].title === "第 1 轮 · 5,115 Token", "Token 账本应优先展示轮次与总 Token");
expect(sections[1].records[0].title === "第 1 轮 · Compaction check", "压缩记录应展示英文可读状态");
expect(sections[1].records[1].title === "第 1 轮 · Save context", "保存记录应展示英文可读状态");
expect(sections[2].records[0].title === "web_fetch · 4.3 秒", "工具记录应优先展示工具名与耗时");
expect(sections[3].records[2].title === "RUN · 36.1 s", "状态 Trace 应优先展示状态与耗时");
expect(sections[3].groups?.map((group) => `${group.title}:${group.count}`).join(",") === "第 1 轮:3,第 2 轮:1", "状态 Trace 应按轮次收拢");
expect(sections[2].records[0].fields.some((field) => field.label === "turn_id" && field.value === turnId), "UUID 应保留在展开详情中");

const toolEvents: Pick<TaskEvent, "event_id" | "type" | "payload">[] = [
  {
    event_id: 1,
    type: "tool.started",
    payload: { tool_call_id: "tool-1", tool_name: "run_breakbeat", args: { scope: "global" } },
  },
  {
    event_id: 2,
    type: "tool.finished",
    payload: { tool_call_id: "tool-1", tool_name: "run_breakbeat", failed: false },
  },
];
const toolSteps = buildActivitySteps(toolEvents);

expect(toolSteps.length === 1, "同一工具调用应维持为一个活动步骤");
expect(toolSteps[0].detail === "run_breakbeat" && toolSteps[0].tone === "done", "工具完成应更新同一名称的状态");
expect(latestActivityStep(toolEvents) === null, "工具完成后不应继续显示运行中的工具状态");
});

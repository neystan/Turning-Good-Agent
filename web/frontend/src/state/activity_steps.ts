import type { TaskEvent } from "../types";

export type ActivityStep = {
  key: string;
  label: string;
  detail?: string;
  tone: "running" | "waiting" | "done" | "failed" | "stopped";
};

/** 将可证明的任务事件转换为面向用户的活动步骤。 */
export function buildActivitySteps(events: Pick<TaskEvent, "event_id" | "type" | "payload">[]): ActivityStep[] {
  const steps = new Map<string, ActivityStep>();
  events.forEach((event, index) => {
    const step = toActivityStep(event, index);
    if (step) steps.set(step.key, step);
  });
  return [...steps.values()];
}

/** 构建展开区域需要的过程步骤，隐藏摘要和审批卡已表达的事件。 */
export function buildDetailActivitySteps(
  events: Pick<TaskEvent, "event_id" | "type" | "payload">[],
  hasPendingApproval: boolean,
): ActivityStep[] {
  return buildActivitySteps(events.filter((event) => !isTerminalEvent(event.type) && !(hasPendingApproval && event.type === "approval.requested")));
}

/** 返回仍在进行的最近动作；工具完成后回到中性思考状态。 */
export function latestActivityStep(events: Pick<TaskEvent, "event_id" | "type" | "payload">[]): ActivityStep | null {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.type === "tool.finished") return null;
    const step = toActivityStep(event, index);
    if (step) return step;
  }
  return null;
}

/** 映射单个白名单事件，未知事件一律不展示。 */
function toActivityStep(event: Pick<TaskEvent, "event_id" | "type" | "payload">, index: number): ActivityStep | null {
  const toolCallId = String(event.payload.tool_call_id || "");
  const key = event.type === "tool.started" || event.type === "tool.finished"
    ? `tool-${toolCallId || String(event.event_id ?? index)}`
    : `${event.event_id ?? index}-${event.type}`;
  if (event.type === "task.status" && event.payload.content === "已加入运行中引导") return { key, label: "已引导", tone: "done" };
  if (event.type === "task.status" && String(event.payload.content || "").startsWith("正在压缩")) return { key, label: "正在压缩上下文", tone: "running" };
  if (event.type === "task.status" && String(event.payload.content || "").startsWith("压缩完成")) return { key, label: "上下文压缩完成", tone: "done" };
  if (event.type === "tool.started") return toolStartedStep(key, String(event.payload.tool_name || "工具"));
  if (event.type === "tool.finished") return toolFinishedStep(key, String(event.payload.tool_name || "工具"), Boolean(event.payload.failed));
  if (event.type === "approval.requested") return { key, label: "等待你的批准", detail: String(event.payload.tool_name || "工具"), tone: "waiting" };
  if (event.type === "approval.resolved") return { key, label: event.payload.approved ? "已允许本次工具调用" : "已拒绝本次工具调用", tone: "done" };
  if (event.type === "task.stopping") return { key, label: "已请求停止", tone: "stopped" };
  if (event.type === "task.completed") return { key, label: "任务已完成", tone: "done" };
  if (event.type === "task.failed") return { key, label: "任务失败", tone: "failed" };
  if (event.type === "task.cancelled") return { key, label: "任务已停止", tone: "stopped" };
  return null;
}

/** 判断事件是否已由任务摘要表达终态。 */
function isTerminalEvent(type: string): boolean {
  return type === "task.completed" || type === "task.failed" || type === "task.cancelled";
}

/** 根据工具名称生成本地工具、MCP 或 Skill 的紧凑描述。 */
function toolStartedStep(key: string, toolName: string): ActivityStep {
  if (toolName === "load_skill") return { key, label: "正在加载 Skill", tone: "running" };
  if (toolName.includes("mcp") || toolName.includes("__")) return { key, label: "正在调用 MCP", tone: "running" };
  return { key, label: "正在调用工具", detail: toolName, tone: "running" };
}

/** 将同一工具调用的终态投影为完成或失败，而不暴露内部推理。 */
function toolFinishedStep(key: string, toolName: string, failed: boolean): ActivityStep {
  return {
    key,
    label: failed ? "工具调用失败" : "工具调用完成",
    detail: toolName,
    tone: failed ? "failed" : "done",
  };
}

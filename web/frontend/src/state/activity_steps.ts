import type { TaskEvent } from "../types";

export type ActivityStep = {
  key: string;
  label: string;
  detail?: string;
  tone: "running" | "waiting" | "done" | "failed" | "stopped";
};

/** 将可证明的任务事件转换为面向用户的活动步骤。 */
export function buildActivitySteps(events: Pick<TaskEvent, "event_id" | "type" | "payload">[]): ActivityStep[] {
  return events.flatMap((event, index) => {
    const step = toActivityStep(event, index);
    return step ? [step] : [];
  });
}

/** 映射单个白名单事件，未知事件一律不展示。 */
function toActivityStep(event: Pick<TaskEvent, "event_id" | "type" | "payload">, index: number): ActivityStep | null {
  const key = `${event.event_id ?? index}-${event.type}`;
  if (event.type === "task.status" && event.payload.content === "已加入运行中引导") return { key, label: "已引导", tone: "done" };
  if (event.type === "task.status" && String(event.payload.content || "").includes("压缩")) return { key, label: "正在整理上下文", tone: "running" };
  if (event.type === "tool.started") return toolStartedStep(key, String(event.payload.tool_name || "工具"));
  if (event.type === "tool.finished") return { key, label: event.payload.failed ? "工具调用失败" : "工具调用完成", tone: event.payload.failed ? "failed" : "done" };
  if (event.type === "approval.requested") return { key, label: "等待你的批准", detail: String(event.payload.tool_name || "工具"), tone: "waiting" };
  if (event.type === "approval.resolved") return { key, label: event.payload.approved ? "已允许本次工具调用" : "已拒绝本次工具调用", tone: "done" };
  if (event.type === "task.stopping") return { key, label: "正在停止任务", tone: "stopped" };
  if (event.type === "task.completed") return { key, label: "任务已完成", tone: "done" };
  if (event.type === "task.failed") return { key, label: "任务失败", tone: "failed" };
  if (event.type === "task.cancelled") return { key, label: "任务已停止", tone: "stopped" };
  return null;
}

/** 根据工具名称生成本地工具、MCP 或 Skill 的紧凑描述。 */
function toolStartedStep(key: string, toolName: string): ActivityStep {
  if (toolName === "load_skill") return { key, label: "正在加载 Skill", tone: "running" };
  if (toolName.includes("mcp") || toolName.includes("__")) return { key, label: "正在调用 MCP", tone: "running" };
  return { key, label: "正在调用工具", detail: toolName, tone: "running" };
}

import { useEffect, useMemo, useState } from "react";
import { Check, ChevronDown, CircleAlert, CircleStop, LoaderCircle, ShieldAlert } from "lucide-react";

import type { TaskEvent, TurnState } from "../types";

type ThinkingTrailProps = {
  turn: TurnState;
  onResolveApproval: (approvalId: string, approved: boolean) => void;
};

type TrailStep = { key: string; label: string; detail?: string; tone?: "waiting" | "failed" | "stopped" };

/** 渲染仅由真实任务事件组成的执行步骤，不展示模型内部推理。 */
export function ThinkingTrail({ turn, onResolveApproval }: ThinkingTrailProps) {
  const running = turn.status === "queued" || turn.status === "running" || turn.status === "stopping";
  const [open, setOpen] = useState(running);
  const steps = useMemo(() => turn.events.map(toTrailStep).filter((step): step is TrailStep => step !== null), [turn.events]);
  const approval = useMemo(() => pendingApproval(turn.events), [turn.events]);

  useEffect(() => {
    if (!running) setOpen(false);
  }, [running]);

  const toolCount = turn.events.filter((event) => event.type === "tool.started").length;
  const summary = running ? "思考中" : `${turnStatusLabel(turn.status)}${toolCount ? ` · ${toolCount} 个工具` : ""} · ${durationLabel(turn)}`;

  return <section className={`thinking-trail is-${turn.status}`} aria-label="任务执行过程">
    <button className="thinking-toggle" onClick={() => setOpen(!open)} aria-expanded={open}>
      {running ? <LoaderCircle className="thinking-spinner" size={16} /> : <TrailStatusIcon status={turn.status} />}
      <span>{summary}</span><ChevronDown size={15} className={open ? "" : "rotated"} />
    </button>
    {open && <ol className="thinking-steps">{steps.map((step) => <li className={step.tone ? `is-${step.tone}` : ""} key={step.key}><span className="step-marker" /><div><strong>{step.label}</strong>{step.detail && <small>{step.detail}</small>}</div></li>)}</ol>}
    {approval && <ApprovalBar approval={approval} onResolve={onResolveApproval} />}
  </section>;
}

/** 根据事件类型生成可见的真实执行步骤。 */
function toTrailStep(event: TaskEvent, index: number): TrailStep | null {
  const key = `${event.event_id || index}-${event.type}`;
  if (event.type === "task.status" && event.payload.content === "已加入运行中引导") return { key, label: "已引导" };
  if (event.type === "tool.started") return { key, label: toolLabel(String(event.payload.tool_name || "工具")) };
  if (event.type === "tool.finished") return { key, label: event.payload.failed ? "工具调用失败" : "工具调用完成", tone: event.payload.failed ? "failed" : undefined };
  if (event.type === "approval.requested") return { key, label: "等待你的批准", detail: String(event.payload.tool_name || "工具"), tone: "waiting" };
  if (event.type === "approval.resolved") return { key, label: event.payload.approved ? "已允许本次工具调用" : "已拒绝本次工具调用" };
  if (event.type === "task.stopping") return { key, label: "正在停止任务", tone: "stopped" };
  if (event.type === "task.completed") return { key, label: "任务已完成" };
  if (event.type === "task.failed") return { key, label: "任务失败", tone: "failed" };
  if (event.type === "task.cancelled") return { key, label: "任务已停止", tone: "stopped" };
  if (event.type === "task.status" && String(event.payload.content).includes("压缩")) return { key, label: "正在整理上下文" };
  return null;
}

/** 将工具名称转为 MCP、Skill 或本地工具的紧凑标签。 */
function toolLabel(toolName: string): string {
  if (toolName === "load_skill") return "正在加载 Skill";
  if (toolName.includes("mcp") || toolName.includes("__")) return `正在调用 MCP · ${toolName}`;
  return `正在调用工具 · ${toolName}`;
}

/** 返回终态对应的紧凑文字。 */
function turnStatusLabel(status: TurnState["status"]): string {
  return { completed: "已完成", failed: "失败", cancelled: "已停止", queued: "排队中", running: "思考中", stopping: "正在停止" }[status];
}

/** 计算任务从开始到当前或结束的显示时长。 */
function durationLabel(turn: TurnState): string {
  const started = Date.parse(turn.startedAt);
  const finished = Date.parse(turn.finishedAt || new Date().toISOString());
  const seconds = Number.isFinite(started) && Number.isFinite(finished) ? Math.max(0, Math.round((finished - started) / 1_000)) : 0;
  return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

/** 找出尚未被解决的最近一次工具审批。 */
function pendingApproval(events: TaskEvent[]): { approvalId: string; toolName: string; args: string } | null {
  const resolved = new Set(events.filter((event) => event.type === "approval.resolved").map((event) => String(event.payload.approval_id)));
  const request = [...events].reverse().find((event) => event.type === "approval.requested" && !resolved.has(String(event.payload.approval_id)));
  if (!request) return null;
  return { approvalId: String(request.payload.approval_id), toolName: String(request.payload.tool_name), args: String(request.payload.args || "{}") };
}

/** 渲染任务终态图标。 */
function TrailStatusIcon({ status }: { status: TurnState["status"] }) {
  if (status === "completed") return <Check size={16} />;
  if (status === "failed") return <CircleAlert size={16} />;
  return <CircleStop size={16} />;
}

/** 紧贴任务步骤渲染一次工具审批操作。 */
function ApprovalBar({ approval, onResolve }: { approval: { approvalId: string; toolName: string; args: string }; onResolve: (approvalId: string, approved: boolean) => void }) {
  return <div className="approval-bar"><div><ShieldAlert size={16} /><span>需要允许工具</span><strong>{approval.toolName}</strong></div><code title={approval.args}>{approval.args}</code><div className="approval-actions"><button onClick={() => onResolve(approval.approvalId, false)}>拒绝</button><button className="primary" onClick={() => onResolve(approval.approvalId, true)}><Check size={15} />允许一次</button></div></div>;
}

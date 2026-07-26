import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, CircleAlert, CircleStop, LoaderCircle, ShieldAlert } from "lucide-react";

import { buildActivitySteps, latestActivityStep } from "../state/activity_steps";
import type { TaskEvent, TurnState } from "../types";

type ActivityClusterProps = {
  turn: TurnState;
  onResolveApproval: (approvalId: string, approved: boolean) => void;
};

/** 渲染只包含真实任务事件的可折叠活动簇。 */
export function ActivityCluster({ turn, onResolveApproval }: ActivityClusterProps) {
  const running = turn.status === "queued" || turn.status === "running" || turn.status === "stopping";
  const [open, setOpen] = useState(running);
  const [now, setNow] = useState(Date.now());
  const steps = useMemo(() => buildActivitySteps(turn.events), [turn.events]);
  const latestStep = latestActivityStep(steps);
  const approval = useMemo(() => pendingApproval(turn.events), [turn.events]);
  const stepListRef = useRef<HTMLOListElement>(null);
  const atBottomRef = useRef(true);

  useEffect(() => {
    /** 运行时展开，终态保留短暂反馈后自动收拢。 */
    if (running) {
      setOpen(true);
      return;
    }
    setOpen(true);
    const timer = window.setTimeout(() => setOpen(false), 900);
    return () => window.clearTimeout(timer);
  }, [running, turn.status]);

  useEffect(() => {
    /** 运行时刷新已用时间，减少动态模式下由 CSS 接管静止状态。 */
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, [running]);

  useEffect(() => {
    /** 用户停留在步骤区底部时才跟随后续真实事件。 */
    const list = stepListRef.current;
    if (open && list && atBottomRef.current) list.scrollTop = list.scrollHeight;
  }, [open, steps.length]);

  if (!steps.length && !approval && !running) return null;
  const toolCount = turn.events.filter((event) => event.type === "tool.started").length;
  const summary = running ? runningSummary(latestStep, turn.startedAt, now) : completedSummary(turn.status, toolCount, turn.startedAt, turn.finishedAt);
  return <section className={`activity-cluster is-${turn.status}`} aria-label="任务执行过程"><button className="activity-toggle" onClick={() => setOpen((value) => !value)} aria-expanded={open}>{running ? <LoaderCircle className="activity-spinner" size={16} aria-hidden="true" /> : <StatusIcon status={turn.status} />}<span>{summary}</span><ChevronDown size={15} className={open ? "" : "rotated"} aria-hidden="true" /></button>{open && <ol ref={stepListRef} className="activity-steps" onScroll={() => { const list = stepListRef.current; if (list) atBottomRef.current = list.scrollHeight - list.scrollTop - list.clientHeight < 20; }}>{steps.map((step) => <li className={`is-${step.tone}`} key={step.key}><span className="activity-marker" /><div><strong>{step.label}</strong>{step.detail && <small>{step.detail}</small>}</div></li>)}</ol>}{approval && <ApprovalBar approval={approval} onResolve={onResolveApproval} />}</section>;
}

/** 优先显示最近真实动作，空事件阶段才显示中性运行状态。 */
function runningSummary(step: ReturnType<typeof latestActivityStep>, startedAt: string, now: number): string {
  if (step) return `${step.label}${step.detail ? ` ${step.detail}` : ""}`;
  return `思考中，已用 ${durationLabel(startedAt, now)}`;
}

/** 使用紧凑中文摘要表达已结束的真实任务。 */
function completedSummary(status: TurnState["status"], toolCount: number, startedAt: string, finishedAt?: string): string {
  const parts = [turnStatusLabel(status), durationLabel(startedAt, Date.parse(finishedAt || new Date().toISOString()))];
  if (toolCount) parts.push(`调用 ${toolCount} 个工具`);
  return parts.join("，");
}

/** 计算开始时间到当前或结束时间的紧凑耗时。 */
function durationLabel(startedAt: string, finishedAt: number): string {
  const started = Date.parse(startedAt);
  const seconds = Number.isFinite(started) ? Math.max(0, Math.round((finishedAt - started) / 1_000)) : 0;
  return seconds < 60 ? `${seconds} 秒` : `${Math.floor(seconds / 60)} 分 ${seconds % 60} 秒`;
}

/** 返回终态对应的紧凑文字。 */
function turnStatusLabel(status: TurnState["status"]): string {
  return { completed: "已完成", failed: "失败", cancelled: "已停止", queued: "排队中", running: "思考中", stopping: "正在停止" }[status];
}

/** 渲染任务终态图标。 */
function StatusIcon({ status }: { status: TurnState["status"] }) {
  if (status === "completed") return <Check size={16} aria-hidden="true" />;
  if (status === "failed") return <CircleAlert size={16} aria-hidden="true" />;
  return <CircleStop size={16} aria-hidden="true" />;
}

/** 找出当前未处理的工具审批请求。 */
function pendingApproval(events: TaskEvent[]): { approvalId: string; toolName: string; args: string } | null {
  const resolved = new Set(events.filter((event) => event.type === "approval.resolved").map((event) => String(event.payload.approval_id)));
  const request = [...events].reverse().find((event) => event.type === "approval.requested" && !resolved.has(String(event.payload.approval_id)));
  if (!request) return null;
  return { approvalId: String(request.payload.approval_id), toolName: String(request.payload.tool_name || "工具"), args: String(request.payload.args || "{}") };
}

/** 渲染当前审批的最小操作界面。 */
function ApprovalBar({ approval, onResolve }: { approval: { approvalId: string; toolName: string; args: string }; onResolve: (approvalId: string, approved: boolean) => void }) {
  return <div className="activity-approval"><div><ShieldAlert size={16} aria-hidden="true" /><span>需要允许工具</span><strong>{approval.toolName}</strong></div><code title={approval.args}>{approval.args}</code><div className="activity-approval-actions"><button onClick={() => onResolve(approval.approvalId, false)}>拒绝</button><button className="primary" onClick={() => onResolve(approval.approvalId, true)}><Check size={15} aria-hidden="true" />允许一次</button></div></div>;
}

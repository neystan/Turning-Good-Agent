import { useEffect, useMemo, useRef, useState } from "react";
import { Check, ChevronDown, CircleAlert, CircleStop, LoaderCircle, ShieldAlert } from "lucide-react";

import { ScrollArea } from "./ScrollArea";
import { buildActivitySteps, buildDetailActivitySteps, latestActivityStep } from "../state/activity_steps";
import type { TaskEvent, TurnState } from "../types";

type ActivityClusterProps = {
  turn: TurnState;
  onResolveApproval: (approvalId: string, approved: boolean) => void;
  actionsEnabled?: boolean;
};

/** 渲染只包含真实任务事件的可折叠活动簇。 */
export function ActivityCluster({ turn, onResolveApproval, actionsEnabled = true }: ActivityClusterProps) {
  const running = turn.status === "queued" || turn.status === "running" || turn.status === "stopping";
  const [open, setOpen] = useState(false);
  const [now, setNow] = useState(Date.now());
  const steps = useMemo(() => buildActivitySteps(turn.events), [turn.events]);
  const approval = useMemo(() => pendingApproval(turn.events), [turn.events]);
  const detailSteps = useMemo(() => buildDetailActivitySteps(turn.events, Boolean(approval)), [approval, turn.events]);
  const latestStep = latestActivityStep(turn.events);
  const stepViewportRef = useRef<HTMLDivElement>(null);
  const atBottomRef = useRef(true);

  useEffect(() => {
    /** 运行时刷新已用时间，减少动态模式下由 CSS 接管静止状态。 */
    if (!running) return;
    const timer = window.setInterval(() => setNow(Date.now()), 500);
    return () => window.clearInterval(timer);
  }, [running]);

  useEffect(() => {
    /** 用户停留在步骤区底部时才跟随后续真实事件。 */
    const list = stepViewportRef.current;
    if (open && list && atBottomRef.current) list.scrollTop = list.scrollHeight;
  }, [open, steps.length]);

  if (!steps.length && !approval && !running) return null;
  const toolCount = turn.events.filter((event) => event.type === "tool.started").length;
  const summary = approval ? waitingApprovalSummary(approval.toolName) : running ? runningSummary(turn.status, latestStep, turn.startedAt, now) : completedSummary(turn.status, toolCount, turn.startedAt, turn.finishedAt);
  const expandable = detailSteps.length > 0;
  return <section className={`activity-cluster is-${turn.status}`} aria-label="任务执行过程"><button className="activity-toggle" type="button" disabled={!expandable} onClick={() => setOpen((value) => !value)} aria-expanded={expandable && open}>{running ? <LoaderCircle className="activity-spinner" size={13} aria-hidden="true" /> : <StatusIcon status={turn.status} />}<span>{summary}</span>{expandable && <ChevronDown size={12} className={open ? "" : "rotated"} aria-hidden="true" />}</button>{open && <ScrollArea viewportRef={stepViewportRef} className="activity-steps" onViewportScroll={() => { const list = stepViewportRef.current; if (list) atBottomRef.current = list.scrollHeight - list.scrollTop - list.clientHeight < 20; }}><ol className="activity-steps-list">{detailSteps.map((step) => <li className={`is-${step.tone}`} key={step.key}><span className="activity-marker" /><div><strong>{step.label}</strong>{step.detail && <small>{step.detail}</small>}</div></li>)}</ol></ScrollArea>}{approval && <ApprovalBar approval={approval} enabled={actionsEnabled} onResolve={onResolveApproval} />}</section>;
}

/** 优先显示最近真实动作，空事件阶段才显示中性运行状态。 */
function runningSummary(status: TurnState["status"], step: ReturnType<typeof latestActivityStep>, startedAt: string, now: number): string {
  if (status === "stopping") return `正在停止，已用 ${durationLabel(startedAt, now)}`;
  if (step) return `思考中 · ${step.label}${step.detail ? ` ${step.detail}` : ""}`;
  return `思考中，已用 ${durationLabel(startedAt, now)}`;
}

/** 生成等待审批时优先展示的当前动作。 */
function waitingApprovalSummary(toolName: string): string {
  return `等待你的批准 · ${toolName}`;
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
  return { completed: "已完成", failed: "失败", cancelled: "已停止", interrupted: "网络连接失败", queued: "排队中", running: "思考中", stopping: "正在停止" }[status];
}

/** 渲染任务终态图标。 */
function StatusIcon({ status }: { status: TurnState["status"] }) {
  if (status === "completed") return <Check size={13} aria-hidden="true" />;
  if (status === "failed" || status === "interrupted") return <CircleAlert size={13} aria-hidden="true" />;
  return <CircleStop size={13} aria-hidden="true" />;
}

/** 找出当前未处理的工具审批请求。 */
function pendingApproval(events: TaskEvent[]): { approvalId: string; toolName: string; args: string } | null {
  const resolved = new Set(events.filter((event) => event.type === "approval.resolved").map((event) => String(event.payload.approval_id)));
  const request = [...events].reverse().find((event) => event.type === "approval.requested" && !resolved.has(String(event.payload.approval_id)));
  if (!request) return null;
  return { approvalId: String(request.payload.approval_id), toolName: String(request.payload.tool_name || "工具"), args: String(request.payload.args || "{}") };
}

/** 渲染当前审批的最小操作界面。 */
function ApprovalBar({ approval, enabled, onResolve }: { approval: { approvalId: string; toolName: string; args: string }; enabled: boolean; onResolve: (approvalId: string, approved: boolean) => void }) {
  const hint = enabled ? undefined : "正在重连，恢复后可审批";
  return <section className="activity-approval" aria-label={`等待允许 ${approval.toolName}`}><div className="activity-approval-summary"><ShieldAlert size={15} aria-hidden="true" /><strong>{approval.toolName}</strong><code>{approval.args}</code></div><div className="activity-approval-actions"><button className="secondary" type="button" disabled={!enabled} title={hint} onClick={() => onResolve(approval.approvalId, false)}>拒绝</button><button className="primary" type="button" disabled={!enabled} title={hint} onClick={() => onResolve(approval.approvalId, true)}><Check size={13} aria-hidden="true" />允许一次</button></div></section>;
}

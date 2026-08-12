import { useRef } from "react";
import { Check, CircleAlert, CircleStop, LoaderCircle, Network, Square } from "lucide-react";

import type { MultiAgentRunSummary } from "../types";

type MultiAgentRunCardProps = {
  run: MultiAgentRunSummary;
  onOpen: (run: MultiAgentRunSummary) => void;
  onStop: () => void;
};

// 将父会话中的安全 Run 摘要渲染为可打开的时间线卡片。
export function MultiAgentRunCard({ run, onOpen, onStop }: MultiAgentRunCardProps) {
  const stopRequestedRunId = useRef<string | null>(null);
  const completed = run.nodes.filter((node) => node.status === "completed").length;
  const failed = run.nodes.filter((node) => isFailure(node.status)).length;
  const active = isActive(run.status);
  const Icon = active ? LoaderCircle : run.status === "completed" ? Check : run.status === "cancelled" ? CircleStop : CircleAlert;
  const status = runStatusLabel(run.status);
  const summary = run.partial ? `部分完成 · ${failed} 个节点未完成` : failed ? `${failed} 个节点未完成` : `${completed}/${run.nodes.length} 个节点完成`;
  /** 对同一 Run 的连续 Stop 点击只转发一次。 */
  const requestStop = () => {
    if (stopRequestedRunId.current === run.run_id) return;
    stopRequestedRunId.current = run.run_id;
    onStop();
  };
  return <div className={`multi-agent-run-card is-${run.status}`}>
    <button type="button" className="multi-agent-run-open" onClick={() => onOpen(run)} aria-label={`打开 Multi-Agent Run，${status}`}>
      <span className="multi-agent-run-icon" aria-hidden="true">{active ? <Icon size={15} className="multi-agent-run-spinner" /> : <Icon size={15} />}</span>
      <span className="multi-agent-run-main"><span className="multi-agent-run-heading"><strong>Multi-Agent</strong><span>{strategyLabel(run.strategy)}</span></span><span className="multi-agent-run-summary">{status} · {summary}</span></span>
      <span className="multi-agent-run-metrics"><span>{formatDuration(run.duration_ms)}</span><span>{formatUsage(run)}</span><Network size={14} aria-hidden="true" /></span>
    </button>
    {active && <button type="button" className="multi-agent-run-stop" aria-label="停止协作任务" title="停止协作任务" onClick={requestStop}><Square size={14} aria-hidden="true" /></button>}
  </div>;
}

// 判断 Run 是否仍占用当前父会话的执行槽位。
function isActive(status: MultiAgentRunSummary["status"]): boolean {
  return status === "queued" || status === "running" || status === "waiting";
}

// 判断节点状态是否代表有待解释的失败或中断。
function isFailure(status: string): boolean {
  return status === "failed" || status === "timed_out" || status === "cancelled" || status === "interrupted";
}

// 映射受控 Run 状态到紧凑可扫描文本。
function runStatusLabel(status: MultiAgentRunSummary["status"]): string {
  return { queued: "排队中", running: "运行中", waiting: "等待审批", completed: "已完成", failed: "失败", timed_out: "超时", cancelled: "已停止", interrupted: "已中断" }[status];
}

// 映射固定拓扑而不暴露可编辑图结构。
function strategyLabel(strategy: MultiAgentRunSummary["strategy"]): string {
  return { fan_out_fan_in: "并行汇总", pipeline: "串行管线" }[strategy];
}

// 格式化 Run 已持久化或实时汇总的受控时长。
function formatDuration(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "-";
  return value >= 1000 ? `${(value / 1000).toFixed(1)} 秒` : `${Math.round(value)} ms`;
}

// 提取总账本 token，缺失时保持紧凑占位。
function formatUsage(run: MultiAgentRunSummary): string {
  const usage = run.usage && "total" in run.usage ? run.usage.total : run.usage;
  const total = usage && typeof usage === "object" ? (usage as { turn_total_tokens?: number; total_tokens?: number }).turn_total_tokens ?? (usage as { total_tokens?: number }).total_tokens : undefined;
  const summary = run.usage && "worker" in run.usage ? run.usage.worker : null;
  const worker = summary && typeof summary === "object" ? summary.turn_total_tokens ?? summary.total_tokens : undefined;
  if (typeof total !== "number" || !Number.isFinite(total)) return "Run Token - · Worker Token -";
  return `Run ${new Intl.NumberFormat("zh-CN").format(total)} · Worker ${typeof worker === "number" && Number.isFinite(worker) ? new Intl.NumberFormat("zh-CN").format(worker) : "-"} Token`;
}

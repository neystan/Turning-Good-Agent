import { ChevronDown, X } from "lucide-react";

import { ScrollArea } from "./ScrollArea";
import { buildInspectorSections, buildInspectorSummary, formatTokenCount, type InspectorRecordGroup, type InspectorRecordView } from "../state/observability_view";
import type { MultiAgentNodeView, MultiAgentRunSummary, Observability, SessionContextReadModel, ToolCallPage } from "../types";

/** 渲染先摘要、后结构化明细的会话观测抽屉。 */
export function SessionInspector({ data, multiAgentRunId, control, onClose }: { data: Observability | null; multiAgentRunId?: string | null; control?: { section: "context" | "tools"; context?: SessionContextReadModel; toolCalls?: ToolCallPage; error?: string } | null; onClose: () => void }) {
  if (control) return <section className="inspector" aria-label="会话检查器"><InspectorHeader onClose={onClose} /><ScrollArea className="inspector-body"><ControlReadSection control={control} /></ScrollArea></section>;
  if (!data) return <section className="inspector" aria-label="会话检查器"><InspectorHeader onClose={onClose} /><InspectorLoadingSkeleton /></section>;
  const summary = buildInspectorSummary(data);
  const sections = buildInspectorSections(data);
  const selectedRun = multiAgentRunId ? data.multi_agent_runs.find((run) => run.run_id === multiAgentRunId) || null : null;
  return <section className="inspector" aria-label="会话检查器"><InspectorHeader onClose={onClose} /><ScrollArea className="inspector-body">{selectedRun && <MultiAgentRunInspector run={selectedRun} />}<dl className="inspector-summary"><Metric label="累计输入" value={formatTokenCount(summary.inputTokens)} /><Metric label="累计输出" value={formatTokenCount(summary.outputTokens)} /><Metric label="当前上下文" value={formatTokenCount(summary.contextTokens)} /><Metric label="压缩次数" value={formatTokenCount(summary.compactions)} /><Metric label="工具失败" value={formatTokenCount(summary.toolFailures)} /></dl><div className="inspector-sections">{sections.map((section) => <InspectorSection key={section.title} title={section.title} count={section.count} records={section.records} groups={section.groups} />)}</div></ScrollArea></section>;
}

// 用固定拓扑展示一个父会话内的协作 Run，而非可编辑流程图。
function MultiAgentRunInspector({ run }: { run: MultiAgentRunSummary }) {
  const completed = run.nodes.filter((node) => node.status === "completed").length;
  return <section className="multi-agent-inspector" aria-label="Multi-Agent 运行详情"><header><div><strong>Multi-Agent</strong></div><span className={`multi-agent-status is-${run.status}`}>{multiAgentStatus(run.status)}</span></header><dl className="inspector-summary"><Metric label="拓扑" value={multiAgentStrategy(run.strategy)} /><Metric label="Worker" value={`${completed}/${run.nodes.length}`} /><Metric label="耗时" value={multiAgentDuration(run.duration_ms)} /><Metric label="Run Token" value={formatTokenCount(multiAgentTokens(run))} /><Metric label="Worker Token" value={formatTokenCount(multiAgentWorkerTokens(run))} /></dl><MultiAgentTopology run={run} /><WorkerFinalResults nodes={run.nodes} /></section>;
}

// 按协议支持的两种布局呈现固定的父子关系。
function MultiAgentTopology({ run }: { run: MultiAgentRunSummary }) {
  if (run.strategy === "pipeline") return <div className="multi-agent-topology is-pipeline" aria-label={`${multiAgentStrategy(run.strategy)}拓扑`}><span className="multi-agent-topology-parent">父 Agent</span>{run.nodes.length ? run.nodes.flatMap((node) => [<span key={`${node.node_id}-edge`} className="multi-agent-topology-arrow" aria-hidden="true">→</span>, <span key={node.node_id} className={`multi-agent-topology-worker is-${node.status}`}>{node.role}</span>]) : <><span className="multi-agent-topology-arrow" aria-hidden="true">→</span><span className="multi-agent-topology-worker">等待 Worker</span></>}<span className="multi-agent-topology-arrow" aria-hidden="true">→</span><span className="multi-agent-topology-parent">父 Agent</span></div>;
  return <div className={`multi-agent-topology is-${run.strategy}`} aria-label={`${multiAgentStrategy(run.strategy)}拓扑`}><span className="multi-agent-topology-parent">父 Agent</span><span className="multi-agent-topology-arrow" aria-hidden="true">→</span><div className="multi-agent-topology-workers">{run.nodes.length ? run.nodes.map((node) => <span key={node.node_id} className={`multi-agent-topology-worker is-${node.status}`}>{node.role}</span>) : <span className="multi-agent-topology-worker">等待 Worker</span>}</div><span className="multi-agent-topology-arrow" aria-hidden="true">→</span><span className="multi-agent-topology-parent">父 Agent</span></div>;
}

// 在独立滚动区呈现 Worker 有界最终结果和受控错误。
function WorkerFinalResults({ nodes }: { nodes: MultiAgentNodeView[] }) {
  return <section className="multi-agent-worker-results"><h3>Worker 结果</h3><ScrollArea className="multi-agent-worker-results-scroll">{nodes.length ? nodes.map((node) => <article key={node.node_id} className={`multi-agent-worker-result is-${node.status}`}><header><strong>{node.role}</strong><span>{multiAgentStatus(node.status)}</span></header>{node.content ? <pre>{node.content}</pre> : node.error ? <p>{node.error}</p> : <p>尚无最终结果</p>}</article>) : <p>尚未创建 Worker</p>}</ScrollArea></section>;
}

// 映射受控状态到紧凑、可扫描的中文文案。
function multiAgentStatus(status: string): string {
  return { queued: "排队中", running: "运行中", waiting: "等待", completed: "已完成", failed: "失败", timed_out: "超时", cancelled: "已停止", interrupted: "已中断" }[status] || "未知";
}

// 映射协议固定的协作策略名称。
function multiAgentStrategy(strategy: MultiAgentRunSummary["strategy"]): string {
  return { fan_out_fan_in: "并行汇总", pipeline: "串行管线" }[strategy];
}

// 格式化持久化 Run 的受控时长字段。
function multiAgentDuration(value: number | null | undefined): string {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) return "-";
  return value >= 1000 ? `${(value / 1000).toFixed(1)} 秒` : `${Math.round(value)} ms`;
}

// 只读取 Run 汇总中明确保存的总 Token。
function multiAgentTokens(run: MultiAgentRunSummary): number {
  const usage = run.usage && "total" in run.usage ? run.usage.total : run.usage;
  const value = usage && typeof usage === "object" ? (usage as { turn_total_tokens?: number; total_tokens?: number }).turn_total_tokens ?? (usage as { total_tokens?: number }).total_tokens : undefined;
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
}

// 读取 Run 摘要中保留的 Worker 总 Token。
function multiAgentWorkerTokens(run: MultiAgentRunSummary): number {
  const usage = run.usage && "worker" in run.usage ? run.usage.worker : null;
  const value = usage && typeof usage === "object" ? usage.turn_total_tokens ?? usage.total_tokens : undefined;
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : 0;
}

function InspectorLoadingSkeleton() {
  return <div className="inspector-loading-skeleton" aria-label="正在读取观测数据" aria-busy="true"><div className="inspector-skeleton-summary">{Array.from({ length: 4 }, (_, index) => <span key={index} />)}</div><div className="inspector-skeleton-groups">{Array.from({ length: 3 }, (_, index) => <span key={index} />)}</div></div>;
}

function ControlReadSection({ control }: { control: { section: "context" | "tools"; context?: SessionContextReadModel; toolCalls?: ToolCallPage; error?: string } }) {
  if (control.error) return <p>{control.error}</p>;
  if (control.section === "context") {
    if (!control.context) return <p>正在读取上下文…</p>;
    const context = control.context;
    return <div className="inspector-sections"><ControlInspectorSection title="上下文" count={context.uncompacted_messages.length}><p>{context.summary || "暂无摘要"}</p><dl className="inspector-summary"><Metric label="完整历史" value={String(context.full_history_count)} /><Metric label="未压缩消息" value={String(context.uncompacted_history_count)} /><Metric label="未压缩 Token" value={formatTokenCount(context.uncompacted_history_tokens)} /><Metric label="上下文上限" value={formatTokenCount(context.token_breakdown.max_context_tokens || 0)} /></dl><div className="inspector-records">{context.uncompacted_messages.map((message) => <details className="inspector-record" key={message.id}><summary>{message.role}</summary><p>{message.content}</p></details>)}</div></ControlInspectorSection></div>;
  }
  if (!control.toolCalls) return <p>正在读取工具调用…</p>;
  return <div className="inspector-sections"><ControlInspectorSection title="工具调用" count={control.toolCalls.items.length}><InspectorRecords records={control.toolCalls.items.map((item) => ({ id: item.tool_call_id, title: item.tool_name, raw: item, fields: [{ label: "状态", value: item.error || "完成" }, { label: "耗时", value: item.duration_ms === null ? "-" : `${item.duration_ms} ms` }] }))} /></ControlInspectorSection></div>;
}

function ControlInspectorSection({ title, count, children }: { title: string; count: number; children: React.ReactNode }) {
  return <details className="inspector-section" open><summary><span>{title}</span><span>{count}</span><ChevronDown size={15} /></summary><div className="inspector-control-content">{children}</div></details>;
}

/** 渲染检查器标题与关闭控制。 */
function InspectorHeader({ onClose }: { onClose: () => void }) {
  return <header><div><h2>会话检查器</h2></div><button className="icon-button" aria-label="关闭会话检查器" onClick={onClose}><X /></button></header>;
}

/** 渲染一个观测摘要指标。 */
function Metric({ label, value }: { label: string; value: string }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

/** 渲染可逐条展开的观测记录分组。 */
function InspectorSection({ title, count, records, groups }: { title: string; count: number; records: InspectorRecordView[]; groups?: InspectorRecordGroup[] }) {
  return <details className="inspector-section"><summary><span>{title}</span><span>{count}</span><ChevronDown size={15} /></summary><div className="inspector-records">{groups?.length ? groups.map((group) => <InspectorRecordGroup key={group.id} group={group} />) : <InspectorRecords records={records} />}</div></details>;
}

function InspectorRecordGroup({ group }: { group: InspectorRecordGroup }) {
  return <details className="inspector-record-group"><summary><span>{group.title}</span><span>{group.count}</span><ChevronDown size={14} /></summary><div className="inspector-records"><InspectorRecords records={group.records} /></div></details>;
}

function InspectorRecords({ records }: { records: InspectorRecordView[] }) {
  return records.length ? records.map((record) => <details className="inspector-record" key={record.id}><summary>{record.title}</summary><dl>{record.fields.filter((field) => field.label !== "turn_id" || field.value !== record.title).map((field) => <div key={field.label}><dt>{field.label}</dt><dd>{field.value}</dd></div>)}</dl><details className="inspector-raw"><summary>查看原始记录</summary><pre>{JSON.stringify(record.raw, null, 2)}</pre></details></details>) : <p>暂无记录</p>;
}

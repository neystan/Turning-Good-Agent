import { ChevronDown, X } from "lucide-react";

import { ScrollArea } from "./ScrollArea";
import { buildInspectorSections, buildInspectorSummary, formatTokenCount, type InspectorRecordGroup, type InspectorRecordView } from "../state/observability_view";
import type { Observability, SessionContextReadModel, ToolCallPage } from "../types";

/** 渲染先摘要、后结构化明细的会话观测抽屉。 */
export function SessionInspector({ data, control, onClose }: { data: Observability | null; control?: { section: "context" | "tools"; context?: SessionContextReadModel; toolCalls?: ToolCallPage; error?: string } | null; onClose: () => void }) {
  if (control) return <section className="inspector" aria-label="会话检查器"><InspectorHeader onClose={onClose} /><ScrollArea className="inspector-body"><ControlReadSection control={control} /></ScrollArea></section>;
  if (!data) return <section className="inspector" aria-label="会话检查器"><InspectorHeader onClose={onClose} /><InspectorLoadingSkeleton /></section>;
  const summary = buildInspectorSummary(data);
  const sections = buildInspectorSections(data);
  return <section className="inspector" aria-label="会话检查器"><InspectorHeader onClose={onClose} /><ScrollArea className="inspector-body"><dl className="inspector-summary"><Metric label="累计输入" value={formatTokenCount(summary.inputTokens)} /><Metric label="累计输出" value={formatTokenCount(summary.outputTokens)} /><Metric label="当前上下文" value={formatTokenCount(summary.contextTokens)} /><Metric label="压缩次数" value={formatTokenCount(summary.compactions)} /><Metric label="工具失败" value={formatTokenCount(summary.toolFailures)} /></dl><div className="inspector-sections">{sections.map((section) => <InspectorSection key={section.title} title={section.title} count={section.count} records={section.records} groups={section.groups} />)}</div></ScrollArea></section>;
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

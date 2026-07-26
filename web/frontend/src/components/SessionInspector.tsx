import { ChevronDown, X } from "lucide-react";

import { IconTooltip } from "./IconTooltip";
import { buildInspectorSections, buildInspectorSummary, formatTokenCount, type InspectorRecordView } from "../state/observability_view";
import type { Observability } from "../types";

/** 渲染先摘要、后结构化明细的会话观测抽屉。 */
export function SessionInspector({ data, onClose }: { data: Observability | null; onClose: () => void }) {
  if (!data) return <aside className="inspector" aria-label="会话检查器"><InspectorHeader onClose={onClose} /><p>正在读取观测数据…</p></aside>;
  const summary = buildInspectorSummary(data);
  const sections = buildInspectorSections(data);
  return <aside className="inspector" aria-label="会话检查器"><InspectorHeader onClose={onClose} /><div className="inspector-body"><dl className="inspector-summary"><Metric label="累计输入" value={formatTokenCount(summary.inputTokens)} /><Metric label="累计输出" value={formatTokenCount(summary.outputTokens)} /><Metric label="当前上下文" value={formatTokenCount(summary.contextTokens)} /><Metric label="压缩次数" value={formatTokenCount(summary.compactions)} /><Metric label="工具失败" value={formatTokenCount(summary.toolFailures)} /></dl>{sections.map((section) => <InspectorSection key={section.title} title={section.title} count={section.count} records={section.records} />)}</div></aside>;
}

/** 渲染检查器标题与关闭控制。 */
function InspectorHeader({ onClose }: { onClose: () => void }) {
  return <header><div><h2>会话检查器</h2><span>当前会话的已持久化记录</span></div><IconTooltip label="关闭检查器"><button className="icon-button" aria-label="关闭会话检查器" onClick={onClose}><X /></button></IconTooltip></header>;
}

/** 渲染一个观测摘要指标。 */
function Metric({ label, value }: { label: string; value: string }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

/** 渲染可逐条展开的观测记录分组。 */
function InspectorSection({ title, count, records }: { title: string; count: number; records: InspectorRecordView[] }) {
  return <details className="inspector-section"><summary><span>{title}</span><span>{count}</span><ChevronDown size={15} /></summary><div className="inspector-records">{records.length ? records.map((record) => <details className="inspector-record" key={record.id}><summary>{record.title}</summary><dl>{record.fields.map((field) => <div key={field.label}><dt>{field.label}</dt><dd>{field.value}</dd></div>)}</dl><details className="inspector-raw"><summary>查看原始记录</summary><pre>{JSON.stringify(record.raw, null, 2)}</pre></details></details>) : <p>暂无记录</p>}</div></details>;
}

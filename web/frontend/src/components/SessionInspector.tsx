import { ChevronDown, X } from "lucide-react";

import { IconTooltip } from "./IconTooltip";
import type { Observability } from "../types";

type InspectorSummary = { inputTokens: number; outputTokens: number; compactions: number; contextTokens: number; toolFailures: number };

/** 渲染先摘要、后明细的会话观测抽屉。 */
export function SessionInspector({ data, onClose }: { data: Observability | null; onClose: () => void }) {
  if (!data) return <aside className="inspector" aria-label="会话检查器"><header><h2>会话检查器</h2><IconTooltip label="关闭检查器"><button className="icon-button" aria-label="关闭会话检查器" onClick={onClose}><X /></button></IconTooltip></header><p>正在读取观测数据…</p></aside>;
  const summary = buildSummary(data);
  return <aside className="inspector" aria-label="会话检查器"><header><div><span className="inspector-kicker">SESSION INSPECTOR</span><h2>会话检查器</h2></div><IconTooltip label="关闭检查器"><button className="icon-button" aria-label="关闭会话检查器" onClick={onClose}><X /></button></IconTooltip></header><div className="inspector-body"><dl className="inspector-summary"><Metric label="累计输入" value={`${summary.inputTokens} tokens`} /><Metric label="累计输出" value={`${summary.outputTokens} tokens`} /><Metric label="当前上下文" value={`${summary.contextTokens} tokens`} /><Metric label="压缩次数" value={String(summary.compactions)} /><Metric label="工具失败" value={String(summary.toolFailures)} /></dl><InspectorSection title="Token 账本" rows={data.token_usage} /><InspectorSection title="压缩与上下文" rows={data.traces.filter((trace) => trace.state === "COMPACT" || trace.state === "SAVE")} /><InspectorSection title="工具调用" rows={data.tool_calls} /><InspectorSection title="状态 Trace" rows={data.traces} /></div></aside>;
}

/** 渲染一个观测摘要指标。 */
function Metric({ label, value }: { label: string; value: string }) {
  return <div><dt>{label}</dt><dd>{value}</dd></div>;
}

/** 从既有 trace 与 token 账本提取摘要指标。 */
function buildSummary(data: Observability): InspectorSummary {
  const totals = data.token_usage.reduce<{ inputTokens: number; outputTokens: number; compactions: number }>((current, row) => ({ inputTokens: current.inputTokens + numberValue(row.input_tokens), outputTokens: current.outputTokens + numberValue(row.output_tokens), compactions: current.compactions + numberValue(row.compacted) }), { inputTokens: 0, outputTokens: 0, compactions: 0 });
  const saveTrace = [...data.traces].reverse().find((trace) => trace.state === "SAVE");
  const respondTrace = [...data.traces].reverse().find((trace) => trace.state === "RESPOND");
  const saveMetadata = (saveTrace?.metadata || {}) as Record<string, unknown>;
  const respondMetadata = (respondTrace?.metadata || {}) as Record<string, unknown>;
  return { ...totals, contextTokens: numberValue(saveMetadata.current_context_tokens), toolFailures: numberValue(respondMetadata.tool_failure_count) };
}

/** 将观测记录中的未知数值安全转换为数字。 */
function numberValue(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

/** 渲染可按需展开的原始观测记录。 */
function InspectorSection({ title, rows }: { title: string; rows: Record<string, unknown>[] }) {
  return <details className="inspector-section"><summary><span>{title}</span><span>{rows.length}</span><ChevronDown size={15} /></summary><pre>{rows.length ? JSON.stringify(rows, null, 2) : "暂无记录"}</pre></details>;
}

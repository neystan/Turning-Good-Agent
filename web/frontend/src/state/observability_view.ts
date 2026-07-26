import type { Observability } from "../types";

export type InspectorRecordView = { id: string; title: string; fields: { label: string; value: string }[]; raw: Record<string, unknown> };
export type InspectorSectionView = { title: string; count: number; records: InspectorRecordView[] };
export type InspectorSummary = { inputTokens: number; outputTokens: number; compactions: number; contextTokens: number; toolFailures: number };

/** 使用中文区域格式化可读的 Token 数字。 */
export function formatTokenCount(value: number): string {
  return new Intl.NumberFormat("zh-CN").format(Math.max(0, Math.round(value)));
}

/** 从既有 trace 和 token 账本提取检查器摘要。 */
export function buildInspectorSummary(data: Observability): InspectorSummary {
  const totals = data.token_usage.reduce<Pick<InspectorSummary, "inputTokens" | "outputTokens" | "compactions">>((current, row) => ({ inputTokens: current.inputTokens + numberValue(row.input_tokens), outputTokens: current.outputTokens + numberValue(row.output_tokens), compactions: current.compactions + numberValue(row.compacted) }), { inputTokens: 0, outputTokens: 0, compactions: 0 });
  const saveMetadata = latestMetadata(data.traces, "SAVE");
  const respondMetadata = latestMetadata(data.traces, "RESPOND");
  return { ...totals, contextTokens: numberValue(saveMetadata.current_context_tokens), toolFailures: numberValue(respondMetadata.tool_failure_count) };
}

/** 将已有观测记录转换为可折叠的结构化阅读模型。 */
export function buildInspectorSections(data: Observability): InspectorSectionView[] {
  return [
    section("Token 账本", data.token_usage, (row, index) => record(row, index, ["turn_id", "input_tokens", "output_tokens", "created_at"])),
    section("压缩与上下文", data.traces.filter((row) => row.state === "COMPACT" || row.state === "SAVE"), (row, index) => record(row, index, ["turn_id", "state", "created_at"])),
    section("工具调用", data.tool_calls, (row, index) => record(row, index, ["tool_name", "turn_id", "duration_ms", "error", "created_at"])),
    section("状态 Trace", data.traces, (row, index) => record(row, index, ["state", "turn_id", "created_at"])),
  ];
}

/** 生成单个分组及其结构化记录。 */
function section(title: string, rows: Record<string, unknown>[], build: (row: Record<string, unknown>, index: number) => InspectorRecordView): InspectorSectionView {
  return { title, count: rows.length, records: rows.map(build) };
}

/** 使用稳定身份字段构造可扫描的观测条目。 */
function record(raw: Record<string, unknown>, index: number, keys: string[]): InspectorRecordView {
  const title = stringValue(raw.turn_id) || stringValue(raw.tool_call_id) || stringValue(raw.tool_name) || `${index + 1} 条记录`;
  return { id: `${title}-${index}`, title, fields: keys.filter((key) => raw[key] !== undefined && raw[key] !== "").map((key) => ({ label: key, value: displayValue(raw[key]) })), raw };
}

/** 读取指定状态最近一次的 metadata。 */
function latestMetadata(rows: Record<string, unknown>[], state: string): Record<string, unknown> {
  const row = [...rows].reverse().find((item) => item.state === state);
  return row && typeof row.metadata === "object" && row.metadata ? row.metadata as Record<string, unknown> : {};
}

/** 将未知数值安全转换为数字。 */
function numberValue(value: unknown): number {
  return typeof value === "number" && Number.isFinite(value) ? value : 0;
}

/** 将未知字段转换为紧凑字符串。 */
function displayValue(value: unknown): string {
  return typeof value === "number" ? formatTokenCount(value) : typeof value === "string" ? value : JSON.stringify(value);
}

/** 将未知字段安全转换为非空字符串。 */
function stringValue(value: unknown): string {
  return typeof value === "string" && value ? value : "";
}

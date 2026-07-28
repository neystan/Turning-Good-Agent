import type { Observability } from "../types";

export type InspectorRecordView = { id: string; title: string; fields: { label: string; value: string }[]; raw: Record<string, unknown> };
export type InspectorRecordGroup = { id: string; title: string; count: number; records: InspectorRecordView[] };
export type InspectorSectionView = { title: string; count: number; records: InspectorRecordView[]; groups?: InspectorRecordGroup[] };
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
  const turnNumbers = buildTurnNumbers(data);
  return [
    section("Token 账本", data.token_usage, (row, index) => record(row, index, ["turn_id", "input_tokens", "output_tokens", "created_at"], `第 ${index + 1} 轮 · ${formatTokenCount(numberValue(row.input_tokens) + numberValue(row.output_tokens))} Token`)),
    section("压缩与上下文", data.traces.filter((row) => row.state === "COMPACT" || row.state === "SAVE"), (row, index) => record(row, index, ["turn_id", "state", "created_at"], `${turnLabel(row, turnNumbers, index)} · ${contextStateLabel(stringValue(row.state))}`)),
    section("工具调用", data.tool_calls, (row, index) => record(row, index, ["tool_name", "turn_id", "duration_ms", "error", "created_at"], `${stringValue(row.tool_name) || "工具调用"} · ${formatDuration(numberValue(row.duration_ms))}`)),
    section("状态 Trace", data.traces, (row, index) => record(row, index, ["state", "turn_id", "created_at"], `${stringValue(row.state) || "未知状态"} · ${formatDuration(numberValue(row.duration_ms), "s")}`), turnNumbers),
  ];
}

/** 生成单个分组及其结构化记录。 */
function section(title: string, rows: Record<string, unknown>[], build: (row: Record<string, unknown>, index: number) => InspectorRecordView, turnNumbers?: Map<string, number>): InspectorSectionView {
  const records = rows.map(build);
  return { title, count: records.length, records, groups: turnNumbers ? groupTraceRecords(records, turnNumbers) : undefined };
}

function groupTraceRecords(records: InspectorRecordView[], turnNumbers: Map<string, number>): InspectorRecordGroup[] {
  const groups = new Map<string, InspectorRecordGroup>();
  for (const record of records) {
    const turnId = stringValue(record.raw.turn_id);
    const groupId = turnId || `system-${groups.size + 1}`;
    const current = groups.get(groupId);
    if (current) {
      current.records.push(record);
      current.count += 1;
      continue;
    }
    const turnNumber = turnNumbers.get(turnId) || groups.size + 1;
    groups.set(groupId, { id: groupId, title: turnId ? `第 ${turnNumber} 轮` : "系统事件", count: 1, records: [record] });
  }
  return [...groups.values()];
}

/** 使用稳定身份字段构造可扫描的观测条目。 */
function record(raw: Record<string, unknown>, index: number, keys: string[], title: string): InspectorRecordView {
  return { id: `${title}-${index}`, title, fields: keys.filter((key) => raw[key] !== undefined && raw[key] !== "").map((key) => ({ label: key, value: displayValue(raw[key]) })), raw };
}

/** 为跨分组的同一 turn 提供稳定、可读的轮次编号。 */
function buildTurnNumbers(data: Observability): Map<string, number> {
  return new Map(data.token_usage.map((row, index): [string, number] => [stringValue(row.turn_id), index + 1]).filter(([turnId]) => turnId));
}

/** 优先复用 token 账本中的轮次，缺失时回退为当前分组序号。 */
function turnLabel(row: Record<string, unknown>, turnNumbers: Map<string, number>, index: number): string {
  return `第 ${turnNumbers.get(stringValue(row.turn_id)) || index + 1} 轮`;
}

/** 将上下文阶段转换为用户可读的中文描述。 */
function contextStateLabel(state: string): string {
  return state === "SAVE" ? "Save context" : state === "COMPACT" ? "Compaction check" : state || "Context event";
}

/** 以秒为主、毫秒为辅呈现已存在的耗时数据。 */
function formatDuration(durationMs: number, secondsUnit = "秒"): string {
  return durationMs >= 1000 ? `${(durationMs / 1000).toFixed(1)} ${secondsUnit}` : `${Math.round(durationMs)} ms`;
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

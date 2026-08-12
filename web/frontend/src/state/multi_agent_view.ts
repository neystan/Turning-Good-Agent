import { MULTI_AGENT_EVENT_TYPES, type MultiAgentEvent, type MultiAgentEventPayload, type MultiAgentNodeStatus, type MultiAgentNodeView, type MultiAgentRunStatus, type MultiAgentRunSummary, type MultiAgentStrategy } from "../types";

const runStatuses = new Set<MultiAgentRunStatus>(["queued", "running", "waiting", "completed", "failed", "timed_out", "cancelled", "interrupted"]);
const nodeStatuses = new Set<MultiAgentNodeStatus>(["queued", "running", "completed", "failed", "timed_out", "cancelled", "interrupted"]);
const strategies = new Set<MultiAgentStrategy>(["fan_out_fan_in", "pipeline"]);
const terminalStatuses = new Set<MultiAgentRunStatus>(["completed", "failed", "timed_out", "cancelled", "interrupted"]);

// 将运行时事件收敛为不会泄露原始输入的安全 Run 视图。
export function buildMultiAgentRunView(events: MultiAgentEvent[]): MultiAgentRunSummary | null {
  let run: MultiAgentRunSummary | null = null;
  const terminalNodes = new Set<string>();
  let runTerminal = false;

  for (const event of events) {
    if (!MULTI_AGENT_EVENT_TYPES.has(event.type)) continue;
    const payload = event.payload;
    if (!isSafePayload(payload)) continue;
    if (!run) run = createRun(payload);
    if (payload.run_id !== run.run_id) continue;
    if (event.type === "multi_agent.node.updated") {
      updateNode(run, payload, terminalNodes);
      continue;
    }
    if (runTerminal || !runStatuses.has(payload.status)) continue;
    run.status = payload.status;
    run.duration_ms = numberOrNull(payload.duration_ms);
    run.usage = payload.usage ?? null;
    run.error_code = textOrNull(payload.error_code);
    run.error = textOrNull(payload.error);
    run.partial = isFanoutPartial(run);
    if (terminalStatuses.has(run.status)) runTerminal = true;
  }
  if (run) run.partial = isFanoutPartial(run);
  return run;
}

// 合并快照和实时视图，终态 Run 不接受晚到覆盖。
export function mergeMultiAgentRunView(current: MultiAgentRunSummary[], next: MultiAgentRunSummary): MultiAgentRunSummary[] {
  const index = current.findIndex((run) => run.run_id === next.run_id);
  if (index < 0) return [...current, next];
  const previous = current[index];
  if (terminalStatuses.has(previous.status)) return current;
  const merged = [...current];
  merged[index] = { ...next, parent_request_id: next.parent_request_id || previous.parent_request_id };
  return merged;
}

// 仅在权威快照到达时替换同一 Run 的实时投影。
export function mergeAuthoritativeMultiAgentRunView(current: MultiAgentRunSummary[], next: MultiAgentRunSummary): MultiAgentRunSummary[] {
  const index = current.findIndex((run) => run.run_id === next.run_id);
  if (index < 0) return [...current, next];
  const merged = [...current];
  merged[index] = next;
  return merged;
}

// 规整服务端快照，拒绝缺少固定运行身份的外部数据。
export function normalizeMultiAgentRunSummary(value: unknown): MultiAgentRunSummary | null {
  if (!isRecord(value) || typeof value.run_id !== "string" || !value.run_id || !strategies.has(value.strategy as MultiAgentStrategy) || !runStatuses.has(value.status as MultiAgentRunStatus)) return null;
  const nodes = Array.isArray(value.nodes) ? value.nodes.map(normalizeNode).filter((node): node is MultiAgentNodeView => node !== null) : [];
  const run: MultiAgentRunSummary = {
    run_id: value.run_id,
    parent_request_id: textOrNull(value.parent_request_id),
    strategy: value.strategy as MultiAgentStrategy,
    status: value.status as MultiAgentRunStatus,
    partial: false,
    nodes,
    duration_ms: numberOrNull(value.duration_ms),
    usage: normalizeUsage(value.usage),
    error_code: textOrNull(value.error_code),
    error: textOrNull(value.error),
  };
  run.partial = isFanoutPartial(run);
  return run;
}

// 校验事件具备最小公开投影字段。
function isSafePayload(payload: MultiAgentEventPayload): boolean {
  return Boolean(payload.run_id) && strategies.has(payload.strategy);
}

// 根据首个事件创建默认安全 Run。
function createRun(payload: MultiAgentEventPayload): MultiAgentRunSummary {
  return {
    run_id: payload.run_id,
    strategy: payload.strategy,
    status: runStatuses.has(payload.status) ? payload.status : "queued",
    partial: false,
    nodes: [],
    duration_ms: numberOrNull(payload.duration_ms),
    usage: payload.usage ?? null,
    error_code: textOrNull(payload.error_code),
    error: textOrNull(payload.error),
  };
}

// 读取快照中的单个 Worker，保留安全的最终内容和受控错误。
function normalizeNode(value: unknown): MultiAgentNodeView | null {
  if (!isRecord(value) || typeof value.node_id !== "string" || !value.node_id || !nodeStatuses.has(value.status as MultiAgentNodeStatus)) return null;
  return {
    node_id: value.node_id,
    role: textOrNull(value.role) || value.node_id,
    status: value.status as MultiAgentNodeStatus,
    duration_ms: numberOrNull(value.duration_ms),
    content: textOrNull(value.content),
    error_code: textOrNull(value.error_code),
    error: textOrNull(value.error),
  };
}

// 按首次出现顺序更新 Worker，同时冻结一次终态。
function updateNode(run: MultiAgentRunSummary, payload: MultiAgentEventPayload, terminalNodes: Set<string>): void {
  if (!payload.node_id || !nodeStatuses.has(payload.status as MultiAgentNodeStatus) || terminalNodes.has(payload.node_id)) return;
  const index = run.nodes.findIndex((node) => node.node_id === payload.node_id);
  const node: MultiAgentNodeView = {
    node_id: payload.node_id,
    role: textOrNull(payload.task_label) || payload.node_id,
    status: payload.status as MultiAgentNodeStatus,
    duration_ms: numberOrNull(payload.duration_ms),
    content: null,
    error_code: textOrNull(payload.error_code),
    error: textOrNull(payload.error),
  };
  if (index < 0) run.nodes.push(node);
  else run.nodes[index] = { ...run.nodes[index], ...node, content: run.nodes[index].content };
  if (terminalStatuses.has(payload.status)) terminalNodes.add(payload.node_id);
}

// 判断并行拓扑是否已存在成功与失败的混合结果。
function isFanoutPartial(run: MultiAgentRunSummary): boolean {
  return run.strategy === "fan_out_fan_in" && run.nodes.some((node) => node.status === "completed") && run.nodes.some((node) => terminalStatuses.has(node.status) && node.status !== "completed");
}

// 仅接收非负有限时长，其他值统一归空。
function numberOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 ? value : null;
}

// 收紧外部文本字段，拒绝空字符串和非字符串值。
function textOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

// 确认未知值为可安全读取的对象。
function isRecord(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === "object" && !Array.isArray(value);
}

// 保留服务端已经汇总过的 token 数值结构。
function normalizeUsage(value: unknown): MultiAgentRunSummary["usage"] {
  return isRecord(value) ? value as MultiAgentRunSummary["usage"] : null;
}

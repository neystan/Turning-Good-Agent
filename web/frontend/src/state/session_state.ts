import type { ChatMessage, PendingAction, PendingActionInput, TaskEvent, TurnState, TurnStatus } from "../types";

/** 保存单个浏览器会话页的即时消息、turn 和待确认动作。 */
export type SessionState = {
  messages: ChatMessage[];
  turns: Record<string, TurnState>;
  pendingActions: Record<string, PendingAction>;
  pendingDraft: string;
  running: boolean;
};

/** 描述前端会话状态的最小状态变更。 */
export type SessionAction =
  | { type: "session.reset" }
  | { type: "history.loaded"; messages: ChatMessage[] }
  | { type: "message.optimistic"; action: PendingActionInput }
  | { type: "action.accepted"; actionId: string; sessionId: string; requestId: string }
  | { type: "action.failed"; actionId: string; message: string }
  | { type: "draft.pending"; content: string }
  | { type: "event.received"; event: TaskEvent };

/** 创建空的会话页状态。 */
export function createSessionState(): SessionState {
  return { messages: [], turns: {}, pendingActions: {}, pendingDraft: "", running: false };
}

/** 将 WebSocket 和 REST 数据规整为可直接渲染的会话状态。 */
export function applySessionAction(state: SessionState, action: SessionAction): SessionState {
  if (action.type === "session.reset") return createSessionState();
  if (action.type === "history.loaded") return { ...state, messages: mergeHistory(state.messages, action.messages) };
  if (action.type === "draft.pending") return { ...state, pendingDraft: action.content };
  if (action.type === "message.optimistic") return addOptimisticMessage(state, action.action);
  if (action.type === "action.accepted") return acceptAction(state, action);
  if (action.type === "action.failed") return failAction(state, action.actionId, action.message);
  return applyTaskEvent(state, action.event);
}

/** 合并已落盘历史与当前浏览器尚未消失的乐观消息。 */
function mergeHistory(current: ChatMessage[], loaded: ChatMessage[]): ChatMessage[] {
  const remaining = [...loaded];
  const optimistic = current.filter((message) => message.client_action_id).filter((message) => {
    const index = remaining.findIndex((item) => item.role === message.role && item.content === message.content);
    if (index < 0) return true;
    remaining.splice(index, 1);
    return false;
  });
  return [...remaining, ...optimistic];
}

/** 添加尚未获服务端确认的用户消息。 */
function addOptimisticMessage(state: SessionState, input: PendingActionInput): SessionState {
  const action: PendingAction = { ...input, status: "sending" };
  const message: ChatMessage = {
    id: `client-${input.id}`,
    role: "user",
    content: input.content,
    created_at: input.createdAt,
    metadata: {},
    delivery: "sending",
    client_action_id: input.id,
  };
  return {
    ...state,
    messages: [...state.messages, message],
    pendingActions: { ...state.pendingActions, [action.id]: action },
  };
}

/** 记录服务端已受理的动作并绑定生成的请求轮次。 */
function acceptAction(
  state: SessionState,
  action: Extract<SessionAction, { type: "action.accepted" }>,
): SessionState {
  const pending = state.pendingActions[action.actionId];
  if (!pending) return state;
  return {
    ...state,
    messages: state.messages.map((item) => item.client_action_id === action.actionId
      ? { ...item, delivery: "sent", request_id: action.requestId }
      : item),
    pendingActions: {
      ...state.pendingActions,
      [action.actionId]: { ...pending, status: "sent", sessionId: action.sessionId, requestId: action.requestId },
    },
  };
}

/** 仅标记与错误回包关联的乐观消息。 */
function failAction(state: SessionState, actionId: string, message: string): SessionState {
  const pending = state.pendingActions[actionId];
  if (!pending) return state;
  return {
    ...state,
    messages: state.messages.map((item) => item.client_action_id === actionId
      ? { ...item, delivery: "failed", metadata: { ...item.metadata, error: message } }
      : item),
    pendingActions: { ...state.pendingActions, [actionId]: { ...pending, status: "failed", error: message } },
  };
}

/** 将真实 WebSocket 事件归入唯一 request_id 的 turn。 */
function applyTaskEvent(state: SessionState, event: TaskEvent): SessionState {
  if (event.type === "session.snapshot") {
    return { ...state, running: event.payload.state === "running" || event.payload.state === "stopping" };
  }
  if (!event.request_id) return state;
  const turn = appendTurnEvent(state.turns[event.request_id], event);
  const turns = { ...state.turns, [event.request_id]: turn };
  let messages = state.messages;
  if (event.type === "response.delta") messages = appendAssistantDelta(messages, event.request_id, String(event.payload.content || ""));
  if (event.type === "response.completed") messages = completeAssistant(messages, event.request_id, String(event.payload.content || ""));
  if (event.type === "response.error") messages = completeAssistant(messages, event.request_id, String(event.payload.content || "请求失败"));
  if (event.type === "guidance.pending") {
    return { ...state, messages, turns, pendingDraft: String((event.payload.items as string[]).join("\n")), running: false };
  }
  return { ...state, messages, turns, running: isTurnRunning(turn.status) };
}

/** 追加事件并推导当前 turn 的可见状态。 */
function appendTurnEvent(current: TurnState | undefined, event: TaskEvent): TurnState {
  const status = eventStatus(event.type, current?.status || "queued");
  const terminal = !isTurnRunning(status);
  return {
    requestId: event.request_id,
    status,
    events: [...(current?.events || []), event],
    guidanceCount: (current?.guidanceCount || 0) + (isGuidanceStatus(event) ? 1 : 0),
    startedAt: current?.startedAt || event.created_at || new Date().toISOString(),
    finishedAt: terminal ? event.created_at || new Date().toISOString() : undefined,
  };
}

/** 由事件类型更新 turn 状态，普通事件保持原状态。 */
function eventStatus(type: string, current: TurnStatus): TurnStatus {
  const statuses: Record<string, TurnStatus> = {
    "task.queued": "queued",
    "task.running": "running",
    "task.stopping": "stopping",
    "task.completed": "completed",
    "task.failed": "failed",
    "task.cancelled": "cancelled",
  };
  return statuses[type] || current;
}

/** 判断状态事件是否表示 guidance 已进入当前任务队列。 */
function isGuidanceStatus(event: TaskEvent): boolean {
  return event.type === "task.status" && event.payload.content === "已加入运行中引导";
}

/** 判断 turn 是否仍在执行或等待执行。 */
function isTurnRunning(status: TurnStatus): boolean {
  return status === "queued" || status === "running" || status === "stopping";
}

/** 向所属 turn 的 assistant 临时消息追加流式内容。 */
function appendAssistantDelta(messages: ChatMessage[], requestId: string, delta: string): ChatMessage[] {
  const index = latestAssistantIndex(messages, requestId);
  if (index >= 0) return messages.map((item, itemIndex) => itemIndex === index ? { ...item, content: item.content + delta } : item);
  return [...messages, temporaryAssistant(requestId, delta)];
}

/** 用终态内容完成所属 turn 的 assistant 临时消息。 */
function completeAssistant(messages: ChatMessage[], requestId: string, content: string): ChatMessage[] {
  const index = latestAssistantIndex(messages, requestId);
  if (index >= 0) {
    return messages.map((item, itemIndex) => itemIndex === index ? { ...item, content: item.content || content } : item);
  }
  return content ? [...messages, temporaryAssistant(requestId, content)] : messages;
}

/** 从末尾查找指定 turn 的 assistant 临时消息。 */
function latestAssistantIndex(messages: ChatMessage[], requestId: string): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role === "assistant" && message.request_id === requestId) return index;
  }
  return -1;
}

/** 创建仅供当前浏览器渲染的 assistant 消息。 */
function temporaryAssistant(requestId: string, content: string): ChatMessage {
  return {
    id: `assistant-${requestId}`,
    role: "assistant",
    content,
    created_at: new Date().toISOString(),
    metadata: {},
    request_id: requestId,
  };
}

import type { ChatMessage, PendingAction, PendingActionInput, TaskEvent, TurnState, TurnStatus } from "../types";

/** 保存单个浏览器会话页的即时消息、turn 和待确认动作。 */
export type SessionState = {
  messages: ChatMessage[];
  turns: Record<string, TurnState>;
  hiddenMessageIds: string[];
  pendingActions: Record<string, PendingAction>;
  pendingDraft: string;
  running: boolean;
};

/** 描述前端会话状态的最小状态变更。 */
export type SessionAction =
  | { type: "session.reset"; messages?: ChatMessage[]; turns?: Record<string, TurnState>; hiddenMessageIds?: string[] }
  | { type: "history.loaded"; messages: ChatMessage[] }
  | { type: "message.optimistic"; action: PendingActionInput }
  | { type: "action.retry"; actionId: string; retry?: PendingActionInput }
  | { type: "action.accepted"; actionId: string; sessionId: string; requestId: string }
  | { type: "action.failed"; actionId: string; message: string }
  | { type: "connection.lost" }
  | { type: "draft.pending"; content: string }
  | { type: "event.received"; event: TaskEvent };

/** 创建空的会话页状态。 */
export function createSessionState(): SessionState {
  return { messages: [], turns: {}, hiddenMessageIds: [], pendingActions: {}, pendingDraft: "", running: false };
}

/** 将 WebSocket 和 REST 数据规整为可直接渲染的会话状态。 */
export function applySessionAction(state: SessionState, action: SessionAction): SessionState {
  if (action.type === "session.reset") return {
    ...createSessionState(),
    messages: action.messages || [],
    turns: action.turns || {},
    hiddenMessageIds: action.hiddenMessageIds || [],
  };
  if (action.type === "history.loaded") return { ...state, messages: mergeHistory(state.messages, hideRetriedHistory(action.messages, state.hiddenMessageIds)) };
  if (action.type === "draft.pending") return { ...state, pendingDraft: action.content };
  if (action.type === "message.optimistic") return addOptimisticMessage(state, action.action);
  if (action.type === "action.retry") return retryAction(state, action);
  if (action.type === "action.accepted") return acceptAction(state, action);
  if (action.type === "action.failed") return failAction(state, action.actionId, action.message);
  if (action.type === "connection.lost") return interruptRunningTurns(state);
  return applyTaskEvent(state, action.event);
}

/** 隐藏已被 Web 重试替代的旧轮，运行时和会话文件保持原样。 */
function hideRetriedHistory(messages: ChatMessage[], hiddenMessageIds: string[]): ChatMessage[] {
  const hidden = new Set(hiddenMessageIds);
  let hideAssistant = false;
  return messages.filter((message) => {
    if (message.role === "user") {
      hideAssistant = hidden.has(message.id);
      return !hideAssistant;
    }
    if (hidden.has(message.id) || hideAssistant) return false;
    return true;
  });
}

/** 合并已落盘历史与当前浏览器尚未消失的乐观消息。 */
function mergeHistory(current: ChatMessage[], loaded: ChatMessage[]): ChatMessage[] {
  const remaining = [...loaded];
  const localOnly = current.filter((message) => {
    const index = remaining.findIndex((item) => item.role === message.role && item.content === message.content);
    if (index < 0) return Boolean(message.client_action_id || message.metadata.network_failure);
    remaining[index] = mergePersistedMessage(remaining[index], message);
    return false;
  });
  return [...remaining, ...localOnly];
}

/** 保留浏览器消息的动作关联，避免任务过程脱离所属用户消息。 */
function mergePersistedMessage(persisted: ChatMessage, cached: ChatMessage): ChatMessage {
  return {
    ...persisted,
    request_id: cached.request_id || persisted.request_id,
    client_action_id: cached.client_action_id || persisted.client_action_id,
  };
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
    messages: state.messages.map((item) => {
      if (item.client_action_id === action.actionId) return { ...item, delivery: "sent", request_id: action.requestId };
      if (item.metadata.retry_action_id === action.actionId) return clearNetworkFailure(item, action.requestId);
      return item;
    }),
    pendingActions: {
      ...state.pendingActions,
      [action.actionId]: { ...pending, status: "sent", sessionId: action.sessionId, requestId: action.requestId },
    },
  };
}

/** 标记未送达消息，供用户在聊天流中重试。 */
function failAction(state: SessionState, actionId: string, message: string): SessionState {
  const pending = state.pendingActions[actionId];
  if (!pending || pending.status !== "sending") return state;
  return {
    ...state,
    messages: state.messages.map((item) => item.client_action_id === actionId
      ? { ...item, delivery: "failed", metadata: { ...item.metadata, error: message } }
      : item),
    pendingActions: { ...state.pendingActions, [actionId]: { ...pending, status: "failed", error: message } },
  };
}

/** 复用原动作标识重发失败消息，并在刷新后恢复必要关联。 */
function retryAction(state: SessionState, action: Extract<SessionAction, { type: "action.retry" }>): SessionState {
  const pending = state.pendingActions[action.actionId] || (action.retry ? { ...action.retry, status: "sent" as const } : undefined);
  if (!pending || (pending.status !== "failed" && pending.status !== "sent")) return state;
  const { error: _error, ...retriedAction } = pending;
  const retryHiddenIds = retryHiddenMessageIds(state.messages, pending, action.actionId);
  const messages = state.messages.filter((item) => item.role !== "assistant" || !retryHiddenIds.has(item.id));
  const turns = { ...state.turns };
  if (pending.requestId) delete turns[pending.requestId];
  return {
    ...state,
    messages: messages.map((item) => item.client_action_id === action.actionId || (item.role === "user" && retryHiddenIds.has(item.id))
      ? { ...item, client_action_id: action.actionId, delivery: "sending", metadata: omitDeliveryError(item.metadata) }
      : item),
    turns,
    hiddenMessageIds: [...new Set([...state.hiddenMessageIds, ...retryHiddenIds])],
    pendingActions: { ...state.pendingActions, [action.actionId]: { ...retriedAction, status: "sending" } },
  };
}

/** 找到重试前同一轮的用户与 assistant 消息，用于浏览器内隐藏。 */
function retryHiddenMessageIds(messages: ChatMessage[], pending: PendingAction, actionId: string): Set<string> {
  const hidden = new Set<string>();
  const assistantIndex = findRetryAssistantIndex(messages, pending.requestId, actionId);
  if (assistantIndex >= 0) {
    hidden.add(messages[assistantIndex].id);
    for (let index = assistantIndex - 1; index >= 0; index -= 1) {
      if (messages[index].role !== "user") continue;
      hidden.add(messages[index].id);
      break;
    }
  }
  for (const message of messages) {
    if (message.client_action_id === actionId || (
      message.role === "user" && Boolean(pending.requestId) && message.request_id === pending.requestId
    )) hidden.add(message.id);
  }
  for (let index = 0; index < messages.length; index += 1) {
    if (messages[index].role !== "user" || !hidden.has(messages[index].id)) continue;
    for (let next = index + 1; next < messages.length && messages[next].role !== "user"; next += 1) {
      if (messages[next].role === "assistant") hidden.add(messages[next].id);
    }
  }
  return hidden;
}

/** 从末尾定位失败轮的 assistant，避免误命中更早的同名消息。 */
function findRetryAssistantIndex(messages: ChatMessage[], requestId: string | undefined, actionId: string): number {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index];
    if (message.role !== "assistant") continue;
    if (message.request_id === requestId || message.metadata.retry_action_id === actionId) return index;
  }
  return -1;
}

/** 删除仅用于浏览器展示的失败说明。 */
function omitDeliveryError(metadata: Record<string, unknown>): Record<string, unknown> {
  const { error: _error, ...remaining } = metadata;
  return remaining;
}

/** 连接中断时将未完成轮次标记为浏览器内可重试失败。 */
function interruptRunningTurns(state: SessionState): SessionState {
  const requestIds = Object.values(state.turns).filter((turn) => isTurnRunning(turn.status)).map((turn) => turn.requestId);
  if (!requestIds.length) return { ...state, running: false };
  const interruptedAt = new Date().toISOString();
  const turns = Object.fromEntries(Object.entries(state.turns).map(([requestId, turn]) => [requestId, requestIds.includes(requestId)
    ? { ...turn, status: "interrupted" as const, finishedAt: interruptedAt }
    : turn]));
  return { ...state, turns, messages: requestIds.reduce(interruptAssistantMessage, state.messages), running: false };
}

/** 将当前轮的临时回答替换成可重试的网络失败消息。 */
function interruptAssistantMessage(messages: ChatMessage[], requestId: string): ChatMessage[] {
  const user = [...messages].reverse().find((message) => message.role === "user" && message.request_id === requestId);
  if (!user?.client_action_id) return messages;
  const index = latestAssistantIndex(messages, requestId);
  const failure = networkFailureMessage(requestId, user, index >= 0 ? messages[index] : undefined);
  if (index < 0) return [...messages, failure];
  return messages.map((item, itemIndex) => itemIndex === index ? failure : item);
}

/** 构造仅保存在浏览器内的网络失败 assistant 消息。 */
function networkFailureMessage(requestId: string, user: ChatMessage, previous?: ChatMessage): ChatMessage {
  return {
    id: previous?.id || `assistant-${requestId}`,
    role: "assistant",
    content: "网络连接失败",
    created_at: previous?.created_at || new Date().toISOString(),
    metadata: { network_failure: true, retry_action_id: user.client_action_id, retry_content: user.content },
    request_id: requestId,
  };
}

/** 将真实 WebSocket 事件归入唯一 request_id 的 turn。 */
function applyTaskEvent(state: SessionState, event: TaskEvent): SessionState {
  if (event.type === "session.snapshot") {
    if (event.payload.state === "idle") return interruptRunningTurns(state);
    return { ...state, running: event.payload.state === "running" || event.payload.state === "stopping" };
  }
  if (!event.request_id) return state;
  if (isDuplicateEvent(state.turns[event.request_id], event)) return state;
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

/** 忽略同一 turn 已处理过的 EventHub 回放事件。 */
function isDuplicateEvent(turn: TurnState | undefined, event: TaskEvent): boolean {
  return event.event_id !== undefined && Boolean(turn?.events.some((item) => item.event_id === event.event_id));
}

/** 追加事件并推导当前 turn 的可见状态。 */
function appendTurnEvent(current: TurnState | undefined, event: TaskEvent): TurnState {
  const status = eventStatus(event.type, current?.status || "queued");
  const terminal = !isTurnRunning(status);
  return {
    requestId: event.request_id,
    kind: event.type === "task.queued" && event.payload.kind === "catalog" ? "catalog" : current?.kind || "chat",
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
  if (statuses[type]) return statuses[type];
  return current === "interrupted" ? "running" : current;
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
  if (index >= 0) return messages.map((item, itemIndex) => itemIndex === index
    ? { ...clearNetworkFailure(item, requestId), content: item.metadata.network_failure ? delta : item.content + delta }
    : item);
  return [...messages, temporaryAssistant(requestId, delta)];
}

/** 用终态内容完成所属 turn 的 assistant 临时消息。 */
function completeAssistant(messages: ChatMessage[], requestId: string, content: string): ChatMessage[] {
  const index = latestAssistantIndex(messages, requestId);
  if (index >= 0) {
    return messages.map((item, itemIndex) => itemIndex === index
      ? { ...clearNetworkFailure(item, requestId), content: item.metadata.network_failure ? content : item.content || content }
      : item);
  }
  return content ? [...messages, temporaryAssistant(requestId, content)] : messages;
}

/** 清理网络失败提示并绑定重试后实际使用的 request_id。 */
function clearNetworkFailure(message: ChatMessage, requestId: string): ChatMessage {
  const { network_failure: _failure, retry_action_id: _action, retry_content: _content, ...metadata } = message.metadata;
  return { ...message, metadata, request_id: requestId };
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

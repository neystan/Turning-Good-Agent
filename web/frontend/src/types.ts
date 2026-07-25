export type Session = {
  id: string;
  channel: string;
  title: string;
  pinned: boolean;
  archived: boolean;
  created_at: string;
  updated_at: string;
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  metadata: Record<string, unknown>;
  delivery?: "sending" | "sent" | "failed";
  client_action_id?: string;
  request_id?: string;
};

export type TaskEvent = {
  event_id?: number;
  session_id: string;
  request_id: string;
  type: string;
  payload: Record<string, unknown>;
  created_at?: string;
};

export type Observability = {
  session: Session;
  traces: Record<string, unknown>[];
  token_usage: Record<string, unknown>[];
  tool_calls: Record<string, unknown>[];
};

export type ConnectionState = "connecting" | "connected" | "reconnecting" | "disconnected";

export type PendingActionKind = "message" | "guidance" | "stop" | "approval";

export type PendingAction = {
  id: string;
  kind: PendingActionKind;
  content: string;
  sessionId: string | null;
  createdAt: string;
  status: "sending" | "sent" | "failed";
  requestId?: string;
  error?: string;
};

export type PendingActionInput = Omit<PendingAction, "status">;

export type TurnStatus = "queued" | "running" | "stopping" | "completed" | "failed" | "cancelled";

export type TurnState = {
  requestId: string;
  status: TurnStatus;
  events: TaskEvent[];
  guidanceCount: number;
  startedAt: string;
  finishedAt?: string;
};

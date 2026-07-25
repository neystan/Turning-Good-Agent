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

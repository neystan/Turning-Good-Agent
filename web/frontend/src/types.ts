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

export type ContextWindow = {
  current_context_tokens: number;
  max_context_tokens: number;
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

export type TurnStatus = "queued" | "running" | "stopping" | "completed" | "failed" | "cancelled" | "interrupted";

export type TurnState = {
  requestId: string;
  kind?: "chat" | "catalog";
  status: TurnStatus;
  events: TaskEvent[];
  guidanceCount: number;
  startedAt: string;
  finishedAt?: string;
};

export type ControlConfigState = "active" | "pending" | "applying" | "failed";

export type EditableControlConfig = {
  llm: {
    provider: "openai-compatible";
    api_key_configured: boolean;
    base_url: string;
    model: string | null;
    timeout_seconds: number;
    max_retries: number;
    retry_delay_seconds: number;
    streaming_enabled: boolean;
  };
  runtime: {
    max_tool_rounds: number;
    max_tool_calls_per_round: number;
    parallel_tool_calls_enabled: boolean;
    max_parallel_tool_calls: number;
    turn_timeout_seconds: number;
    max_context_tokens: number;
    max_tool_result_tokens: number;
  };
  memory: { compact_token_threshold: number; recent_window_token_limit: number };
  sessions: { retention_days: number };
  skills: { max_loaded_skills_per_turn: number; max_skill_tokens: number; max_loaded_skill_tokens_per_turn: number };
  proactive: {
    enabled: boolean;
    timezone: string;
    review_provider: string | null;
    review_api_key_configured: boolean;
    review_base_url: string | null;
    review_model: string | null;
    background_max_concurrency: number;
    breakbeat_refresh_minutes: number;
    dream_refresh_hours: number;
    review_window_token_limit: number;
    profile_total_token_limit: number;
    user_profile_token_limit: number;
    soul_profile_token_limit: number;
    skill_observation_turn_interval: number;
    skill_observation_token_limit: number;
    skill_evolution_batch_token_limit: number;
    skill_evolution_batches_per_kind: number;
  };
};

export type ConfigApplyRequest = {
  changes: {
    llm?: Partial<EditableControlConfig["llm"]> & { api_key?: string; clear_api_key?: boolean };
    runtime?: Partial<EditableControlConfig["runtime"]>;
    memory?: Partial<EditableControlConfig["memory"]>;
    sessions?: Partial<EditableControlConfig["sessions"]>;
    skills?: Partial<EditableControlConfig["skills"]>;
    proactive?: Omit<Partial<EditableControlConfig["proactive"]>, "review_api_key_configured"> & { review_api_key?: string; clear_review_api_key?: boolean };
  };
  approval_required_tools?: { add: string[]; remove: string[] };
};

export type ControlConfig = {
  desired_revision: string;
  active_revision: string;
  state: ControlConfigState;
  last_apply_error: string | null;
  desired: EditableControlConfig;
  active: EditableControlConfig;
};

export type LlmTestResult = { ok: true; latency_ms: number };

export type ToolCatalogEntry = {
  name: string;
  description: string;
  source: { kind: "core" } | { kind: "mcp"; server_name: string };
  approval_required: boolean;
  effective_approval: "not_required" | "manual" | "automatic";
};

export type ToolCatalog = {
  active_revision: string;
  tools: ToolCatalogEntry[];
  unavailable_approval_required: string[];
};

export type CommandEntry = {
  id: string;
  kind: "inspect" | "skill" | "mcp" | "action";
  icon: "context" | "tools" | "skill" | "mcp" | "compress" | "skill_deposit" | "dream" | "breakbeat";
  slash: string;
  label: string;
  description: string;
  action: "open_context" | "open_tools" | "insert_text" | "execute_catalog";
  insert_text?: string;
  catalog_action?: "compact" | "run_skill_evolution" | "run_dream:session" | "run_dream:global" | "run_breakbeat:session" | "run_breakbeat:global";
};

export type ComposerTextSegment = { type: "text"; id: string; text: string };

export type ComposerGuidanceSegment = {
  type: "guidance";
  id: string;
  entry: CommandEntry & { kind: "skill" | "mcp"; insert_text: string };
};

export type ComposerSegment = ComposerTextSegment | ComposerGuidanceSegment;

export type CommandCatalog = { entries: CommandEntry[] };

export type SessionContextReadModel = {
  session_id: string;
  summary: string | null;
  full_history_count: number;
  uncompacted_history_count: number;
  uncompacted_history_tokens: number;
  uncompacted_messages: Array<{ id: string; role: string; content: string; token_count: number; created_at: string }>;
  token_breakdown: Record<string, number>;
  active_revision: string;
};

export type PersistedToolCall = {
  turn_id: string;
  tool_call_id: string;
  tool_name: string;
  args: Record<string, unknown>;
  content: string | null;
  error: string | null;
  duration_ms: number | null;
  created_at: string;
};

export type ToolCallPage = { items: PersistedToolCall[]; next_cursor: string | null; snapshot: string | null };

export type McpServerSummary = {
  name: string;
  state: string;
  connected: boolean;
  error: string | null;
  transport: string | null;
  catalog_counts: Record<string, number>;
  enabled_tools: string[];
};

export type McpServerDetail = McpServerSummary & {
  catalog?: Array<{ kind: string; name: string; description: string }>;
};

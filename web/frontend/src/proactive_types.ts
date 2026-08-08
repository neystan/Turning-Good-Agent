export type ProactiveRouteDomain = "cron" | "breakbeat" | "memory" | "skills" | "incidents";
export type ProactiveWireDomain = "cron" | "breakbeat" | "dream" | "skill" | "incident";
export type ProactiveRoute = ProactiveRouteDomain;

export type ProactiveDomain = ProactiveRouteDomain;

export const proactiveRouteDomains: readonly ProactiveRouteDomain[] = [
  "cron",
  "breakbeat",
  "memory",
  "skills",
  "incidents",
];

const routeToWire: Record<ProactiveRouteDomain, ProactiveWireDomain> = {
  cron: "cron",
  breakbeat: "breakbeat",
  memory: "dream",
  skills: "skill",
  incidents: "incident",
};

const wireToRoute: Record<ProactiveWireDomain, ProactiveRouteDomain> = {
  cron: "cron",
  breakbeat: "breakbeat",
  dream: "memory",
  skill: "skills",
  incident: "incidents",
};

export type ProactiveOwner = {
  mode: "owner" | "readonly";
  writable: boolean;
  owner_id: string | null;
  owner_kind: string | null;
  owner_pid: number | null;
};

export type ProactiveRuntime = {
  running: boolean;
  next_run_at: string | null;
  entity_states: Record<string, string>;
};

export type ProactiveUsage = {
  calls: number;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
};

export type CronJob = {
  id: string;
  cron: string | null;
  created_at: string;
  prompt: string;
  recurring: boolean;
  delivery_channels: string[];
  updated_at: string;
  next_run_at: string | null;
};

export type CronSnapshotData = { jobs: CronJob[]; usage: ProactiveUsage };

export type BreakbeatItem = {
  id: string;
  todo: string;
  deadline: string | null;
  source_session_id: string;
  status: "in_progress" | "completed";
  created_at: string;
  updated_at: string;
};

export type BreakbeatSnapshotData = {
  items: BreakbeatItem[];
  next_run_at: string | null;
  usage: ProactiveUsage;
};

export type DreamSnapshotData = {
  next_run_at: string | null;
  usage: ProactiveUsage;
  memory: { user: string; soul: string };
  memory_tokens: { user_tokens: number; soul_tokens: number; total_tokens: number };
  profile_limits: {
    user_profile_token_limit: number;
    soul_profile_token_limit: number;
    profile_total_token_limit: number;
  };
  timezone: string;
};

export type SkillObservation = {
  id: string;
  created_at: string;
  kind: "workflow" | "tool_procedure" | "failure_recovery" | "interaction_protocol";
  observation: string;
  source_session_id: string;
  source_message_ids: string[];
};

export type SkillDraft = { name: string; description: string; body: string };
export type SkillSnapshotData = {
  observations: SkillObservation[];
  drafts: SkillDraft[];
  next_run_at: string | null;
  usage: ProactiveUsage;
};

export type IncidentHistoryItem = {
  state: "open" | "resolved";
  occurred_at: string;
  message: string;
};

export type ProactiveIncident = {
  id: string;
  fingerprint: string;
  source: string;
  state: "open" | "resolved";
  first_detected_at: string;
  last_detected_at: string;
  occurrence_count: number;
  message: string;
  history: IncidentHistoryItem[];
};

export type IncidentSnapshotData = { incidents: ProactiveIncident[] };

export type ProactiveSnapshot = {
  type?: "snapshot";
  domain: ProactiveWireDomain;
  data: Record<string, unknown>;
  runtime: ProactiveRuntime;
  proactive_revision: number;
  owner: ProactiveOwner;
};

export type ProactiveNotice = {
  type: "notice";
  id: string;
  domain: ProactiveWireDomain;
  entity_id: string;
  severity: "info" | "warning" | "error";
  title: string;
  message: string;
  target: string;
  proactive_revision: number;
  owner: ProactiveOwner;
};

export type ProactiveState = {
  snapshots: Partial<Record<ProactiveRouteDomain, ProactiveSnapshot>>;
  owner: ProactiveOwner | null;
  connection: "connecting" | "connected" | "reconnecting" | "disconnected";
};

export function routeDomain(domain: ProactiveRoute): string {
  return `#proactive/${domain}`;
}

export function wireDomainForRoute(domain: ProactiveRouteDomain): ProactiveWireDomain {
  return routeToWire[domain];
}

export function routeDomainForWire(domain: ProactiveWireDomain): ProactiveRouteDomain {
  return wireToRoute[domain];
}

export function proactiveRouteFromHash(hash = window.location.hash): ProactiveRoute | null {
  if (hash === "#proactive" || hash === "#proactive/") return "cron";
  const match = hash.match(/^#proactive\/(cron|breakbeat|memory|skills|incidents)$/);
  return match ? match[1] as ProactiveRoute : null;
}

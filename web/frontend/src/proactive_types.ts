export type ProactiveRouteDomain = "cron" | "breakbeat" | "memory" | "skills" | "incidents";
export type ProactiveWireDomain = "cron" | "breakbeat" | "dream" | "skill" | "incident";

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

export function routeDomain(domain: ProactiveRouteDomain): string {
  return `#proactive/${domain}`;
}

export function wireDomainForRoute(domain: ProactiveRouteDomain): ProactiveWireDomain {
  return routeToWire[domain];
}

export function routeDomainForWire(domain: ProactiveWireDomain): ProactiveRouteDomain {
  return wireToRoute[domain];
}

export function proactiveRouteFromHash(hash = window.location.hash): ProactiveRouteDomain | null {
  if (hash === "#proactive" || hash === "#proactive/") return "cron";
  const match = hash.match(/^#proactive\/(cron|breakbeat|memory|skills|incidents)$/);
  return match ? match[1] as ProactiveRouteDomain : null;
}

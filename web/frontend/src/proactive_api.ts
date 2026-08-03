import type { ProactiveDomain, ProactiveOwner, ProactiveSnapshot } from "./proactive_types";

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!response.ok) throw new Error((await response.text()) || "请求失败 (" + response.status + ")");
  return response.json() as Promise<T>;
}

const paths: Record<ProactiveDomain, string> = { cron: "cron", breakbeat: "breakbeat", memory: "memory", skills: "skills", incidents: "incidents" };

export type ProactiveSnapshotCollection = {
  snapshots: ProactiveSnapshot[];
  owner: ProactiveOwner;
  proactive_revision: number;
};

export const proactiveApi = {
  allSnapshots: () => request<ProactiveSnapshotCollection>("/api/proactive"),
  snapshot: (domain: ProactiveDomain) => request<ProactiveSnapshot>("/api/proactive/" + paths[domain]),
  deleteCron: (id: string) => request<ProactiveSnapshot>("/api/proactive/cron/" + encodeURIComponent(id), { method: "DELETE" }),
  completeBreakbeat: (id: string) => request<ProactiveSnapshot>("/api/proactive/breakbeat/" + encodeURIComponent(id) + "/complete", { method: "POST" }),
  deleteBreakbeat: (id: string) => request<ProactiveSnapshot>("/api/proactive/breakbeat/" + encodeURIComponent(id), { method: "DELETE" }),
  deleteDraft: (name: string) => request<ProactiveSnapshot>("/api/proactive/skills/drafts/" + encodeURIComponent(name), { method: "DELETE" }),
  resolveIncident: (fingerprint: string) => request<ProactiveSnapshot>("/api/proactive/incidents/" + encodeURIComponent(fingerprint) + "/resolve", { method: "POST" }),
  deleteIncident: (fingerprint: string) => request<ProactiveSnapshot>("/api/proactive/incidents/" + encodeURIComponent(fingerprint), { method: "DELETE" }),
};

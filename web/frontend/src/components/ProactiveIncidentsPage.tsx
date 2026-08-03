import { useState } from "react";

import { proactiveApi } from "../proactive_api";
import type { IncidentSnapshotData, ProactiveSnapshot } from "../proactive_types";
import { ProactiveCard } from "./ProactiveCard";
import { DomainSummary, EmptyRecords, ProactiveActionError, ProactiveDeleteDialog, useProactiveActions } from "./ProactivePageSupport";

type IncidentFilter = "all" | "open" | "resolved";

export function ProactiveIncidentsPage({ snapshot, writable, onSnapshot }: {
  snapshot: ProactiveSnapshot;
  writable: boolean;
  onSnapshot: (snapshot: ProactiveSnapshot) => void;
}) {
  const data = snapshot.data as IncidentSnapshotData;
  const [filter, setFilter] = useState<IncidentFilter>("open");
  const [deleting, setDeleting] = useState<string | null>(null);
  const actions = useProactiveActions(onSnapshot);
  const deletingEntry = deleting ? actions.entry(`delete:${deleting}`) : actions.entry("");
  const incidents = data.incidents.filter((incident) => filter === "all" || incident.state === filter);

  const confirmDelete = async () => {
    if (!deleting) return;
    await actions.run(`delete:${deleting}`, () => proactiveApi.deleteIncident(deleting));
    setDeleting(null);
  };

  return <div className="proactive-domain-page" data-proactive-page="incidents">
    <DomainSummary runtimeNextRunAt={snapshot.runtime.next_run_at} running={snapshot.runtime.running} />
    <div className="proactive-filter" role="group" aria-label="Incident 状态筛选">
      <button type="button" aria-label="全部 Incident" aria-pressed={filter === "all"} onClick={() => setFilter("all")}>全部</button>
      <button type="button" aria-label="open Incident" aria-pressed={filter === "open"} onClick={() => setFilter("open")}>open</button>
      <button type="button" aria-label="resolved Incident" aria-pressed={filter === "resolved"} onClick={() => setFilter("resolved")}>resolved</button>
    </div>
    {incidents.length === 0 ? <EmptyRecords>{filter === "all" ? "暂无 Incident。" : `暂无 ${filter} Incident。`}</EmptyRecords> : <div className="proactive-card-grid">
      {incidents.map((incident) => {
        const resolveEntry = actions.entry(`resolve:${incident.fingerprint}`);
        const deleteEntry = actions.entry(`delete:${incident.fingerprint}`);
        const failure = resolveEntry.error || deleteEntry.error;
        return <ProactiveCard key={incident.id} card="incident" id={incident.id} state={incident.state} className={incident.state === "resolved" ? "is-resolved" : undefined} actionState={resolveEntry.pending || deleteEntry.pending ? "pending" : failure ? "error" : "idle"} title={`${incident.source} Incident`} subtitle={incident.id} actions={<>
          {incident.state === "open" && <button type="button" aria-label={`标记 Incident 已解决 ${incident.fingerprint}`} disabled={!writable || resolveEntry.pending} onClick={() => void actions.run(`resolve:${incident.fingerprint}`, () => proactiveApi.resolveIncident(incident.fingerprint))}>标记已解决</button>}
          <button className="proactive-danger-action" type="button" aria-label={`删除 Incident ${incident.fingerprint}`} disabled={!writable || deleteEntry.pending} onClick={() => setDeleting(incident.fingerprint)}>删除</button>
        </>}>
          <p className="proactive-primary-content">{incident.message}</p>
          <dl className="proactive-facts">
            <div><dt>状态</dt><dd>{incident.state}</dd></div>
            <div><dt>来源</dt><dd>{incident.source}</dd></div>
            <div><dt>Fingerprint</dt><dd>{incident.fingerprint}</dd></div>
            <div><dt>首次发现</dt><dd>{incident.first_detected_at}</dd></div>
            <div><dt>最近发现</dt><dd>{incident.last_detected_at}</dd></div>
            <div><dt>发生次数</dt><dd>{incident.occurrence_count}</dd></div>
          </dl>
          <section className="proactive-history" aria-label="完整 Incident 历史">
            <h4>完整历史</h4>
            <ol>{incident.history.map((item, index) => <li key={`${item.occurred_at}:${index}`} data-proactive-history-item>
              <div><strong>{item.state}</strong><time>{item.occurred_at}</time></div>
              <p>{item.message}</p>
            </li>)}</ol>
          </section>
          <ProactiveActionError failure={failure} />
        </ProactiveCard>;
      })}
    </div>}
    <ProactiveDeleteDialog open={Boolean(deleting)} title="删除 Incident？" description={`将永久删除 Incident“${deleting || ""}”及其历史，无法恢复。`} confirmLabel="确认删除 Incident" pending={deletingEntry.pending} onOpenChange={(open) => { if (!open) setDeleting(null); }} onConfirm={() => void confirmDelete()} />
  </div>;
}

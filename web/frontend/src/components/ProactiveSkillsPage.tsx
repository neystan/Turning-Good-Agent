import { useState } from "react";
import ReactMarkdown from "react-markdown";

import { proactiveApi } from "../proactive_api";
import type { ProactiveSnapshot, SkillSnapshotData } from "../proactive_types";
import { ProactiveCard } from "./ProactiveCard";
import { DomainSummary, EmptyRecords, ProactiveActionError, ProactiveDeleteDialog, useProactiveActions } from "./ProactivePageSupport";

export function ProactiveSkillsPage({ snapshot, writable, onSnapshot, onOpenSession }: {
  snapshot: ProactiveSnapshot;
  writable: boolean;
  onSnapshot: (snapshot: ProactiveSnapshot) => void;
  onOpenSession: (sessionId: string) => void;
}) {
  const data = snapshot.data as SkillSnapshotData;
  const [deleting, setDeleting] = useState<string | null>(null);
  const actions = useProactiveActions(onSnapshot);
  const deletingEntry = deleting ? actions.entry(`delete:${deleting}`) : actions.entry("");

  const confirmDelete = async () => {
    if (!deleting || !writable) return;
    await actions.run(`delete:${deleting}`, () => proactiveApi.deleteDraft(deleting));
    setDeleting(null);
  };

  return <div className="proactive-domain-page" data-proactive-page="skills">
    <DomainSummary nextRunAt={data.next_run_at} runtimeNextRunAt={snapshot.runtime.next_run_at} running={snapshot.runtime.running} usage={data.usage} />
    <ProactiveActionError failure={actions.failure} />
    {data.observations.length === 0 && data.drafts.length === 0 ? <EmptyRecords>暂无 Observation 或 Draft。</EmptyRecords> : <div className="proactive-card-grid">
      {data.observations.map((observation) => <ProactiveCard key={observation.id} card="skill-observation" id={observation.id} state={observation.kind} title="Observation" subtitle={observation.id}>
        <p className="proactive-primary-content">{observation.observation}</p>
        <dl className="proactive-facts">
          <div><dt>类型</dt><dd>{observation.kind}</dd></div>
          <div><dt>创建时间</dt><dd>{observation.created_at}</dd></div>
          <div><dt>来源消息</dt><dd className="proactive-id-list">{observation.source_message_ids.map((id) => <code key={id}>{id}</code>)}</dd></div>
        </dl>
        <button className="proactive-source-link" type="button" aria-label={`查看来源会话 ${observation.source_session_id}`} onClick={() => onOpenSession(observation.source_session_id)}>来源会话：{observation.source_session_id}</button>
      </ProactiveCard>)}
      {data.drafts.map((draft) => {
        const entry = actions.entry(`delete:${draft.name}`);
        return <ProactiveCard key={draft.name} card="skill-draft" id={draft.name} state="draft" actionState={entry.pending ? "pending" : entry.error ? "error" : "idle"} title={draft.name} subtitle={draft.description} actions={<button className="proactive-danger-action" type="button" aria-label={`删除 Draft ${draft.name}`} disabled={!writable || entry.pending} onClick={() => setDeleting(draft.name)}>删除 Draft</button>}>
          <div className="proactive-markdown"><ReactMarkdown>{draft.body}</ReactMarkdown></div>
        </ProactiveCard>;
      })}
    </div>}
    <ProactiveDeleteDialog open={Boolean(deleting)} title="删除 Draft？" description={`将永久删除 Draft“${deleting || ""}”，无法恢复。`} confirmLabel="确认删除 Draft" pending={deletingEntry.pending} disabled={!writable} onOpenChange={(open) => { if (!open) setDeleting(null); }} onConfirm={() => void confirmDelete()} />
  </div>;
}

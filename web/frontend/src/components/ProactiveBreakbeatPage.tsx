import { useState } from "react";

import { proactiveApi } from "../proactive_api";
import type { BreakbeatItem, BreakbeatSnapshotData, ProactiveSnapshot } from "../proactive_types";
import { ProactiveCard } from "./ProactiveCard";
import { DomainSummary, EmptyRecords, ProactiveActionError, ProactiveDeleteDialog, useProactiveActions } from "./ProactivePageSupport";

export function ProactiveBreakbeatPage({ snapshot, writable, onSnapshot, onOpenSession }: {
  snapshot: ProactiveSnapshot;
  writable: boolean;
  onSnapshot: (snapshot: ProactiveSnapshot) => void;
  onOpenSession: (sessionId: string) => void;
}) {
  const data = snapshot.data as BreakbeatSnapshotData;
  const [deleting, setDeleting] = useState<string | null>(null);
  const actions = useProactiveActions(onSnapshot);
  const deletingEntry = deleting ? actions.entry(`delete:${deleting}`) : actions.entry("");
  const items = [...data.items].sort(compareItems);

  const confirmDelete = async () => {
    if (!deleting || !writable) return;
    await actions.run(`delete:${deleting}`, () => proactiveApi.deleteBreakbeat(deleting));
    setDeleting(null);
  };

  return <div className="proactive-domain-page" data-proactive-page="breakbeat">
    <DomainSummary nextRunAt={data.next_run_at} runtimeNextRunAt={snapshot.runtime.next_run_at} running={snapshot.runtime.running} usage={data.usage} />
    <ProactiveActionError failure={actions.failure} />
    {items.length === 0 ? <EmptyRecords>暂无 Breakbeat。</EmptyRecords> : <div className="proactive-card-grid">
      {items.map((item) => {
        const completeEntry = actions.entry(`complete:${item.id}`);
        const deleteEntry = actions.entry(`delete:${item.id}`);
        const failure = completeEntry.error || deleteEntry.error;
        return <ProactiveCard key={item.id} card="breakbeat-item" id={item.id} state={item.status} className={item.status === "completed" ? "is-completed" : undefined} actionState={completeEntry.pending || deleteEntry.pending ? "pending" : failure ? "error" : "idle"} title={item.status === "completed" ? "已完成 Breakbeat" : "进行中 Breakbeat"} subtitle={item.id} actions={<>
          {item.status === "in_progress" && <button type="button" aria-label={`完成 Breakbeat ${item.id}`} disabled={!writable || completeEntry.pending} onClick={() => void actions.run(`complete:${item.id}`, () => proactiveApi.completeBreakbeat(item.id))}>标记完成</button>}
          <button className="proactive-danger-action" type="button" aria-label={`删除 Breakbeat ${item.id}`} disabled={!writable || deleteEntry.pending} onClick={() => setDeleting(item.id)}>删除</button>
        </>}>
          <p className="proactive-primary-content">{item.todo}</p>
          <dl className="proactive-facts">
            <div><dt>状态</dt><dd>{item.status}</dd></div>
            <div><dt>原始截止时间</dt><dd>{item.deadline || "未提供截止时间"}</dd></div>
            <div><dt>创建时间</dt><dd>{item.created_at}</dd></div>
            <div><dt>更新时间</dt><dd>{item.updated_at}</dd></div>
          </dl>
          <button className="proactive-source-link" type="button" aria-label={`查看来源会话 ${item.source_session_id}`} onClick={() => onOpenSession(item.source_session_id)}>来源会话：{item.source_session_id}</button>
        </ProactiveCard>;
      })}
    </div>}
    <ProactiveDeleteDialog open={Boolean(deleting)} title="删除 Breakbeat？" description={`将永久删除 Breakbeat“${deleting || ""}”，无法恢复。`} confirmLabel="确认删除 Breakbeat" pending={deletingEntry.pending} disabled={!writable} onOpenChange={(open) => { if (!open) setDeleting(null); }} onConfirm={() => void confirmDelete()} />
  </div>;
}

function compareItems(left: BreakbeatItem, right: BreakbeatItem): number {
  if (left.status !== right.status) return left.status === "in_progress" ? -1 : 1;
  return right.updated_at.localeCompare(left.updated_at);
}

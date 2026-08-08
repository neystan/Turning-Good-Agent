import { useState } from "react";

import { proactiveApi } from "../proactive_api";
import type { CronSnapshotData, ProactiveSnapshot } from "../proactive_types";
import { ProactiveCard } from "./ProactiveCard";
import { DomainSummary, EmptyRecords, formatLocalDateTime, formatNextRun, ProactiveActionError, ProactiveDeleteDialog, useProactiveActions } from "./ProactivePageSupport";

export function ProactiveCronPage({ snapshot, writable, timezone, onSnapshot }: {
  snapshot: ProactiveSnapshot;
  writable: boolean;
  timezone: string | null;
  onSnapshot: (snapshot: ProactiveSnapshot) => void;
}) {
  const data = snapshot.data as CronSnapshotData;
  const [deleting, setDeleting] = useState<string | null>(null);
  const actions = useProactiveActions(onSnapshot);
  const deletingEntry = deleting ? actions.entry(`delete:${deleting}`) : actions.entry("");

  const confirmDelete = async () => {
    if (!deleting || !writable) return;
    await actions.run(`delete:${deleting}`, () => proactiveApi.deleteCron(deleting));
    setDeleting(null);
  };

  return <div className="proactive-domain-page" data-proactive-page="cron">
    <DomainSummary nextRunAt={nextRunAt(data.jobs)} usage={data.usage} timezone={timezone} />
    <ProactiveActionError failure={actions.failure} />
    {data.jobs.length === 0 ? <EmptyRecords>暂无 Cron。</EmptyRecords> : <div className="proactive-card-grid">
      {data.jobs.map((job) => {
        const state = snapshot.runtime.entity_states[job.id] || "idle";
        const entry = actions.entry(`delete:${job.id}`);
        return <ProactiveCard key={job.id} card="cron-job" id={job.id} state={state} actionState={entry.pending ? "pending" : entry.error ? "error" : "idle"} title={job.recurring ? "周期 Cron" : "一次性 Cron"} actions={<button className="proactive-danger-action" type="button" aria-label={`删除 Cron ${job.id}`} disabled={!writable || entry.pending} onClick={() => setDeleting(job.id)}>删除 Cron</button>}>
          <p className="proactive-primary-content">{job.prompt}</p>
          <dl className="proactive-facts">
            <div><dt>Cron 表达式</dt><dd>{job.cron || "一次性任务"}</dd></div>
            <div><dt>下次执行</dt><dd>{formatNextRun(job.next_run_at, timezone)}</dd></div>
            <div><dt>创建时间</dt><dd>{formatLocalDateTime(job.created_at, timezone)}</dd></div>
            <div><dt>更新时间</dt><dd>{formatLocalDateTime(job.updated_at, timezone)}</dd></div>
            <div><dt>投递渠道</dt><dd>{job.delivery_channels.length ? job.delivery_channels.join("、") : "无"}</dd></div>
          </dl>
        </ProactiveCard>;
      })}
    </div>}
    <ProactiveDeleteDialog open={Boolean(deleting)} title="删除 Cron？" description={`将永久删除 Cron“${deleting || ""}”，无法恢复。`} confirmLabel="确认删除 Cron" pending={deletingEntry.pending} disabled={!writable} onOpenChange={(open) => { if (!open) setDeleting(null); }} onConfirm={() => void confirmDelete()} />
  </div>;
}

function nextRunAt(jobs: CronSnapshotData["jobs"]): string | null {
  return jobs.reduce<string | null>((nearest, job) => {
    if (!job.next_run_at) return nearest;
    if (!nearest || Date.parse(job.next_run_at) < Date.parse(nearest)) return job.next_run_at;
    return nearest;
  }, null);
}

import { useState } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";

import { ApiError } from "../api";
import type { ProactiveSnapshot, ProactiveUsage } from "../proactive_types";

type ActionFailure = { status: number | null; detail: string };
type ActionEntry = { pending: boolean; error: ActionFailure | null };

export function useProactiveActions(onSnapshot: (snapshot: ProactiveSnapshot) => void) {
  const [entries, setEntries] = useState<Record<string, ActionEntry>>({});
  const [failure, setFailure] = useState<ActionFailure | null>(null);

  const run = async (key: string, operation: () => Promise<ProactiveSnapshot>) => {
    setFailure(null);
    setEntries((current) => ({ ...current, [key]: { pending: true, error: null } }));
    try {
      onSnapshot(await operation());
      setEntries((current) => ({ ...current, [key]: { pending: false, error: null } }));
      return true;
    } catch (error) {
      const nextFailure = failureFrom(error);
      setFailure(nextFailure);
      setEntries((current) => ({ ...current, [key]: { pending: false, error: nextFailure } }));
      return false;
    }
  };

  const entry = (key: string): ActionEntry => entries[key] || { pending: false, error: null };
  return { entry, failure, run };
}

export function ProactiveActionError({ failure }: { failure: ActionFailure | null }) {
  if (!failure) return null;
  return <p className="proactive-action-error" role="alert">
    {failure.status ? `请求失败（${failure.status}）：${failure.detail}` : `请求失败：${failure.detail}`}
  </p>;
}

export function ProactiveDeleteDialog({ open, title, description, confirmLabel, pending, disabled, onOpenChange, onConfirm }: {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  pending: boolean;
  disabled: boolean;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}) {
  return <AlertDialog.Root open={open} onOpenChange={(nextOpen) => { if (!pending) onOpenChange(nextOpen); }}>
    <AlertDialog.Portal>
      <AlertDialog.Overlay className="dialog-overlay" />
      <AlertDialog.Content className="confirm-dialog">
        <AlertDialog.Title>{title}</AlertDialog.Title>
        <AlertDialog.Description>{description}</AlertDialog.Description>
        <div className="dialog-actions">
          <AlertDialog.Cancel asChild><button disabled={pending}>取消</button></AlertDialog.Cancel>
          <button className="danger" type="button" disabled={pending || disabled} onClick={onConfirm}>{pending ? "正在删除" : confirmLabel}</button>
        </div>
      </AlertDialog.Content>
    </AlertDialog.Portal>
  </AlertDialog.Root>;
}

export function UsageFacts({ usage }: { usage: ProactiveUsage }) {
  return <dl className="proactive-usage" aria-label="累计用量">
    <div><dt>调用</dt><dd>{usage.calls}</dd></div>
    <div><dt>输入 tokens</dt><dd>{usage.input_tokens}</dd></div>
    <div><dt>输出 tokens</dt><dd>{usage.output_tokens}</dd></div>
    <div><dt>总 tokens</dt><dd>{usage.total_tokens}</dd></div>
  </dl>;
}

export function DomainSummary({ nextRunAt, usage, timezone }: {
  nextRunAt?: string | null;
  usage?: ProactiveUsage;
  timezone?: string | null;
}) {
  if (nextRunAt === undefined && !usage) return null;
  return <section className="proactive-domain-summary" aria-label="领域运行摘要">
    <dl className="proactive-summary-facts">
      {nextRunAt !== undefined && <div><dt>下次执行</dt><dd>{formatNextRun(nextRunAt, timezone)}</dd></div>}
    </dl>
    {usage && <UsageFacts usage={usage} />}
  </section>;
}

export function formatNextRun(value: string | null, timezone?: string | null): string {
  if (!value) return "暂无计划";
  return formatLocalDateTime(value, timezone);
}

export function formatProactiveState(value: string): string {
  return {
    queued: "等待执行",
    running: "运行中",
    in_progress: "进行中",
    completed: "已完成",
    partial: "部分完成",
    failed: "失败",
    open: "未解决",
    resolved: "已解决",
    idle: "空闲",
    readonly: "只读",
  }[value] || value;
}

export function formatLocalDateTime(value: string | null | undefined, timezone?: string | null): string {
  if (!value) return "时间待确认";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "时间待确认";
  const options: Intl.DateTimeFormatOptions = { dateStyle: "medium", timeStyle: "short", ...(timezone ? { timeZone: timezone } : {}) };
  try {
    return new Intl.DateTimeFormat("zh-CN", options).format(date);
  } catch {
    return new Intl.DateTimeFormat("zh-CN", { dateStyle: "medium", timeStyle: "short" }).format(date);
  }
}

export function EmptyRecords({ children }: { children: string }) {
  return <p className="proactive-empty">{children}</p>;
}

function failureFrom(error: unknown): ActionFailure {
  if (error instanceof ApiError) return { status: error.status, detail: error.message };
  return { status: null, detail: error instanceof Error ? error.message : "未知错误" };
}

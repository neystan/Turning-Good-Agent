import { useState } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";

import { ApiError } from "../api";
import type { ProactiveSnapshot, ProactiveUsage } from "../proactive_types";

type ActionFailure = { status: number | null; detail: string };
type ActionEntry = { pending: boolean; error: ActionFailure | null };

export function useProactiveActions(onSnapshot: (snapshot: ProactiveSnapshot) => void) {
  const [entries, setEntries] = useState<Record<string, ActionEntry>>({});

  const run = async (key: string, operation: () => Promise<ProactiveSnapshot>) => {
    setEntries((current) => ({ ...current, [key]: { pending: true, error: null } }));
    try {
      onSnapshot(await operation());
      setEntries((current) => ({ ...current, [key]: { pending: false, error: null } }));
      return true;
    } catch (error) {
      setEntries((current) => ({ ...current, [key]: { pending: false, error: failureFrom(error) } }));
      return false;
    }
  };

  const entry = (key: string): ActionEntry => entries[key] || { pending: false, error: null };
  return { entry, run };
}

export function ProactiveActionError({ failure }: { failure: ActionFailure | null }) {
  if (!failure) return null;
  return <p className="proactive-action-error" role="alert">
    {failure.status ? `请求失败（${failure.status}）：${failure.detail}` : `请求失败：${failure.detail}`}
  </p>;
}

export function ProactiveDeleteDialog({ open, title, description, confirmLabel, pending, onOpenChange, onConfirm }: {
  open: boolean;
  title: string;
  description: string;
  confirmLabel: string;
  pending: boolean;
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
          <button className="danger" type="button" disabled={pending} onClick={onConfirm}>{pending ? "正在删除" : confirmLabel}</button>
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

export function DomainSummary({ nextRunAt, runtimeNextRunAt, running, usage, timezone }: {
  nextRunAt?: string | null;
  runtimeNextRunAt: string | null;
  running: boolean;
  usage?: ProactiveUsage;
  timezone?: string | null;
}) {
  return <section className="proactive-domain-summary" aria-label="领域运行摘要">
    <dl className="proactive-summary-facts">
      <div><dt>服务</dt><dd>{running ? "运行中" : "空闲"}</dd></div>
      <div><dt>数据计划</dt><dd>{nextRunAt || "暂无计划"}</dd></div>
      <div><dt>运行投影</dt><dd>{runtimeNextRunAt || "暂无计划"}</dd></div>
      {timezone && <div><dt>配置时区</dt><dd>{timezone}</dd></div>}
    </dl>
    {usage && <UsageFacts usage={usage} />}
  </section>;
}

export function EmptyRecords({ children }: { children: string }) {
  return <p className="proactive-empty">{children}</p>;
}

function failureFrom(error: unknown): ActionFailure {
  if (error instanceof ApiError) return { status: error.status, detail: error.message };
  return { status: null, detail: error instanceof Error ? error.message : "未知错误" };
}

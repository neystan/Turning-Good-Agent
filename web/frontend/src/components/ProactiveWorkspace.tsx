import { ArrowLeft, BellRing, BrainCircuit, CalendarClock, CircleAlert, Gauge, WandSparkles } from "lucide-react";

import { ScrollArea } from "./ScrollArea";
import { ProactiveCard } from "./ProactiveCard";
import type { ProactiveDomain, ProactiveOwner, ProactiveSnapshot, ProactiveState } from "../proactive_types";

type ProactiveWorkspaceProps = {
  domain: ProactiveDomain;
  snapshots: ProactiveState["snapshots"];
  owner: ProactiveOwner | null;
  connection: ProactiveState["connection"];
  onSelectDomain: (domain: ProactiveDomain) => void;
  onReturnToChat: () => void;
};

const panels: Array<{ domain: ProactiveDomain; label: string; description: string; icon: typeof CalendarClock }> = [
  { domain: "cron", label: "Cron", description: "计划提醒与下次执行", icon: CalendarClock },
  { domain: "breakbeat", label: "Breakbeat", description: "会话中的未完成事项", icon: BellRing },
  { domain: "memory", label: "长期记忆与 Dream", description: "USER、SOUL 与画像更新", icon: BrainCircuit },
  { domain: "skills", label: "Skill 演进与 Draft", description: "观察与待审批草稿", icon: WandSparkles },
  { domain: "incidents", label: "Incidents", description: "主动能力健康状态", icon: CircleAlert },
];

/** 渲染独立于聊天历史的主动能力工作面外壳。 */
export function ProactiveWorkspace({ domain, snapshots, owner, connection, onSelectDomain, onReturnToChat }: ProactiveWorkspaceProps) {
  const panel = panels.find((item) => item.domain === domain) || panels[0];
  const snapshot = snapshots[domain];
  const ownerLabel = ownershipLabel(owner, connection);

  return <main className="proactive-workspace" id="main-content">
    <header className="proactive-header">
      <button className="proactive-return" type="button" onClick={onReturnToChat}><ArrowLeft size={16} />返回聊天</button>
      <div>
        <span className="proactive-kicker">部署级工作面</span>
        <h1>主动能力</h1>
        <p>后台结果仅在此处更新，不会写入聊天会话。</p>
      </div>
      <span className={`proactive-owner ${owner?.writable ? "is-owner" : ""}`}>{ownerLabel}</span>
    </header>
    <nav className="proactive-tabs" aria-label="主动能力页面" role="tablist">
      {panels.map((item) => {
        const Icon = item.icon;
        return <button key={item.domain} type="button" role="tab" aria-selected={item.domain === domain} onClick={() => onSelectDomain(item.domain)}><Icon size={16} /><span>{item.label}</span></button>;
      })}
    </nav>
    <ScrollArea className="proactive-scroll" viewportClassName="proactive-scroll-viewport">
      <section className="proactive-panel" data-proactive-domain={domain} data-proactive-revision={snapshot?.proactive_revision ?? 0}>
        <header className="proactive-panel-heading">
          <div>
            <span className="proactive-kicker">{panel.label}</span>
            <h2>{panel.description}</h2>
          </div>
          <span className={`proactive-runtime ${snapshot?.runtime.running ? "is-running" : ""}`}><Gauge size={15} />{runtimeLabel(snapshot)}</span>
        </header>
        {snapshot ? <SnapshotPreview domain={domain} snapshot={snapshot} /> : <ProactiveCard title="正在同步主动状态"><p className="proactive-muted">连接建立后会收到该领域的完整最新快照。</p></ProactiveCard>}
      </section>
    </ScrollArea>
  </main>;
}

/** Task 7 只呈现快照入口；完整领域记录与操作由后续卡片任务负责。 */
function SnapshotPreview({ domain, snapshot }: { domain: ProactiveDomain; snapshot: ProactiveSnapshot }) {
  const memory = snapshot.data.memory;
  const memoryView = memory && typeof memory === "object" ? memory as { user?: unknown; soul?: unknown } : null;

  return <div className="proactive-card-grid">
    <ProactiveCard title="当前快照" subtitle={`Revision ${snapshot.proactive_revision}`}>
      <dl className="proactive-facts">
        <div><dt>实时状态</dt><dd>{runtimeLabel(snapshot)}</dd></div>
        <div><dt>下次计划</dt><dd>{snapshot.runtime.next_run_at || value(snapshot.data.next_run_at) || "暂无计划"}</dd></div>
      </dl>
    </ProactiveCard>
    {domain === "memory" && <>
      <ProactiveCard title="USER.md" subtitle="长期用户画像"><p className="proactive-document">{value(memoryView?.user) || "暂无稳定用户画像。"}</p></ProactiveCard>
      <ProactiveCard title="SOUL.md" subtitle="长期协作原则"><p className="proactive-document">{value(memoryView?.soul) || "暂无稳定协作原则。"}</p></ProactiveCard>
    </>}
  </div>;
}

function runtimeLabel(snapshot: ProactiveSnapshot | undefined): string {
  if (!snapshot) return "等待快照";
  const states = Object.values(snapshot.runtime.entity_states);
  if (snapshot.runtime.running || states.some((state) => state === "running" || state === "queued")) return "运行中";
  return "空闲";
}

function ownershipLabel(owner: ProactiveOwner | null, connection: ProactiveState["connection"]): string {
  if (connection !== "connected") return "正在连接";
  if (!owner?.writable) return owner?.owner_id ? "只读：由其他 Host 持有" : "已停用";
  return "本机所有者";
}

function value(input: unknown): string | null {
  return typeof input === "string" && input.trim() ? input : null;
}

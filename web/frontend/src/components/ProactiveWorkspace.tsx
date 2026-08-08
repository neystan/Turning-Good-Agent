import { ArrowLeft, Gauge } from "lucide-react";

import { ScrollArea } from "./ScrollArea";
import { ProactiveCard } from "./ProactiveCard";
import { ProactiveBreakbeatPage } from "./ProactiveBreakbeatPage";
import { ProactiveCronPage } from "./ProactiveCronPage";
import { ProactiveIncidentsPage } from "./ProactiveIncidentsPage";
import { ProactiveMemoryPage } from "./ProactiveMemoryPage";
import { ProactiveSkillsPage } from "./ProactiveSkillsPage";
import type { ProactiveDomain, ProactiveOwner, ProactiveRoute, ProactiveSnapshot, ProactiveState } from "../proactive_types";

type ProactiveWorkspaceProps = {
  route: ProactiveRoute;
  state: ProactiveState;
  onReturnToChat: () => void;
  onSnapshot: (snapshot: ProactiveSnapshot) => void;
  onOpenSession: (sessionId: string) => void;
};

const panels: Record<ProactiveDomain, { label: string; description: string }> = {
  cron: { label: "Cron", description: "计划提醒与下次执行" },
  breakbeat: { label: "Breakbeat", description: "会话中的未完成事项" },
  memory: { label: "长期记忆与 Dream", description: "USER、SOUL 与画像更新" },
  skills: { label: "Skill 自进化", description: "观察与待审批草稿" },
  incidents: { label: "Incidents", description: "主动能力健康状态" },
};

/** 渲染独立于聊天历史的主动能力工作面外壳。 */
export function ProactiveWorkspace({ route, state, onReturnToChat, onSnapshot, onOpenSession }: ProactiveWorkspaceProps) {
  const snapshot = state.snapshots[route];
  const ownerLabel = ownershipLabel(state.owner, state.connection);
  const timezone = typeof state.snapshots.memory?.data.timezone === "string" ? state.snapshots.memory.data.timezone : null;

  return <main className="proactive-workspace" id="main-content">
    <header className="proactive-header">
      <div className="proactive-header-inner">
        <button className="proactive-return" type="button" onClick={onReturnToChat}><ArrowLeft size={16} />返回聊天</button>
        <h1>主动能力</h1>
        <span className={`proactive-owner ${state.owner?.writable ? "is-owner" : ""}`}>{ownerLabel}</span>
      </div>
    </header>
    <ScrollArea className="proactive-scroll" viewportClassName="proactive-scroll-viewport">
      <section className="proactive-panel" data-proactive-domain={route} data-proactive-revision={snapshot?.proactive_revision ?? 0}>
        <header className="proactive-panel-heading">
          <div>
            <span className="proactive-kicker">{panels[route].label}</span>
            <h2>{panels[route].description}</h2>
          </div>
          <div className="proactive-panel-meta">
            {timezone && <span className="proactive-timezone">时区：{timezone}</span>}
            <span className={`proactive-runtime ${snapshot?.runtime.running ? "is-running" : ""}`}><Gauge size={15} />{runtimeLabel(snapshot)}</span>
          </div>
        </header>
        {snapshot ? <DomainPage domain={route} snapshot={snapshot} writable={Boolean(state.owner?.writable)} timezone={timezone} onSnapshot={onSnapshot} onOpenSession={onOpenSession} /> : <ProactiveCard card="loading" title="正在同步主动状态"><p className="proactive-muted">连接建立后会收到该领域的完整最新快照。</p></ProactiveCard>}
      </section>
    </ScrollArea>
  </main>;
}

function DomainPage({ domain, snapshot, writable, timezone, onSnapshot, onOpenSession }: {
  domain: ProactiveDomain;
  snapshot: ProactiveSnapshot;
  writable: boolean;
  timezone: string | null;
  onSnapshot: (snapshot: ProactiveSnapshot) => void;
  onOpenSession: (sessionId: string) => void;
}) {
  if (domain === "cron") return <ProactiveCronPage snapshot={snapshot} writable={writable} timezone={timezone} onSnapshot={onSnapshot} />;
  if (domain === "breakbeat") return <ProactiveBreakbeatPage snapshot={snapshot} writable={writable} timezone={timezone} onSnapshot={onSnapshot} onOpenSession={onOpenSession} />;
  if (domain === "memory") return <ProactiveMemoryPage snapshot={snapshot} />;
  if (domain === "skills") return <ProactiveSkillsPage snapshot={snapshot} writable={writable} timezone={timezone} onSnapshot={onSnapshot} onOpenSession={onOpenSession} />;
  return <ProactiveIncidentsPage snapshot={snapshot} writable={writable} timezone={timezone} onSnapshot={onSnapshot} />;
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

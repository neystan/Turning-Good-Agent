import type { DreamSnapshotData, ProactiveSnapshot } from "../proactive_types";
import { ProactiveCard } from "./ProactiveCard";
import { DomainSummary } from "./ProactivePageSupport";

export function ProactiveMemoryPage({ snapshot }: { snapshot: ProactiveSnapshot }) {
  const data = snapshot.data as DreamSnapshotData;
  const serviceState = snapshot.runtime.entity_states.service || (snapshot.runtime.running ? "running" : "idle");

  return <div className="proactive-domain-page" data-proactive-page="memory">
    <DomainSummary nextRunAt={data.next_run_at} usage={data.usage} timezone={data.timezone} />
    <div className="proactive-card-grid proactive-card-grid-single">
      <ProactiveCard card="memory-user" state="readonly" title="USER.md" subtitle="长期用户画像，只读">
        <p className="proactive-document">{data.memory.user || "暂无稳定用户画像。"}</p>
        <p className="proactive-token-readout">{data.memory_tokens.user_tokens} / {data.profile_limits.user_profile_token_limit} tokens</p>
      </ProactiveCard>
      <ProactiveCard card="memory-soul" state="readonly" title="SOUL.md" subtitle="长期协作原则，只读">
        <p className="proactive-document">{data.memory.soul || "暂无稳定协作原则。"}</p>
        <p className="proactive-token-readout">{data.memory_tokens.soul_tokens} / {data.profile_limits.soul_profile_token_limit} tokens</p>
      </ProactiveCard>
      <ProactiveCard card="dream-runtime" state={serviceState} title="Dream 画像预算" subtitle="长期记忆容量">
        <dl className="proactive-facts">
          <div><dt>USER 配额</dt><dd>{data.profile_limits.user_profile_token_limit} tokens</dd></div>
          <div><dt>SOUL 配额</dt><dd>{data.profile_limits.soul_profile_token_limit} tokens</dd></div>
          <div><dt>画像总计</dt><dd>{data.memory_tokens.total_tokens} / {data.profile_limits.profile_total_token_limit} tokens</dd></div>
        </dl>
      </ProactiveCard>
    </div>
  </div>;
}

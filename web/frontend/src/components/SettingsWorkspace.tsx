import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, MessageCircle, Settings2 } from "lucide-react";

import { api } from "../api";
import type { ControlConfig, ToolCatalog } from "../types";
import { ChannelAccountSettings } from "./ChannelAccountSettings";
import { ConfigEditor } from "./ConfigEditor";
import { ScrollArea } from "./ScrollArea";

export function SettingsWorkspace({ onReturnToChat }: { onReturnToChat: () => void }) {
  const [activePane, setActivePane] = useState<"runtime" | "channels">("runtime");
  const [config, setConfig] = useState<ControlConfig | null>(null);
  const [catalog, setCatalog] = useState<ToolCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const requestVersion = useRef(0);

  const load = useCallback(() => {
    const version = ++requestVersion.current;
    setError(null);
    void Promise.all([api.controlConfig(), api.controlTools()]).then(([nextConfig, nextCatalog]) => {
      if (version !== requestVersion.current) return;
      setConfig(nextConfig);
      setCatalog(nextCatalog);
    }).catch((reason: unknown) => {
      if (version === requestVersion.current) setError(reason instanceof Error ? reason.message : "控制面暂不可用");
    });
  }, []);

  useEffect(() => {
    load();
    return () => { requestVersion.current += 1; };
  }, [load]);

  const showRuntime = activePane === "runtime";

  return <main className="settings-workspace" aria-label="设置">
    <aside className="settings-navigation"><button className="settings-return" type="button" onClick={onReturnToChat}><ArrowLeft size={16} aria-hidden="true" />返回聊天</button><h1>设置</h1><nav className="settings-nav-list" aria-label="设置工作面"><button className="settings-nav-item" type="button" aria-pressed={showRuntime} onClick={() => setActivePane("runtime")}><Settings2 size={16} aria-hidden="true" />运行配置</button><button className="settings-nav-item" type="button" aria-pressed={!showRuntime} onClick={() => setActivePane("channels")}><MessageCircle size={16} aria-hidden="true" />消息渠道</button></nav></aside>
    <section className="settings-content"><div className="settings-content-column"><header className="settings-heading"><h2>{showRuntime ? "运行配置" : "消息渠道"}</h2></header>{showRuntime ? <div className="settings-editor-host">{error && <section className="settings-unavailable"><strong>控制面暂不可用</strong><p>{error}</p><button type="button" onClick={load}>重试</button></section>}{!config && !error && <SettingsLoadingSkeleton />}{config && <ConfigEditor key={config.desired_revision} config={config} catalog={catalog} onApplied={setConfig} onUnavailable={setError} />}</div> : <ScrollArea className="settings-channel-scroll"><ChannelAccountSettings /></ScrollArea>}</div></section>
  </main>;
}

function SettingsLoadingSkeleton() {
  return <div className="settings-loading-skeleton" aria-label="正在读取配置" aria-busy="true"><div className="settings-skeleton-heading" />{Array.from({ length: 6 }, (_, index) => <div className="settings-skeleton-row" key={index}><span /><span /></div>)}</div>;
}

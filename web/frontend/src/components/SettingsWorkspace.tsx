import { useCallback, useEffect, useRef, useState } from "react";
import { ArrowLeft, Settings2 } from "lucide-react";

import { api } from "../api";
import type { ControlConfig, ToolCatalog } from "../types";
import { ConfigEditor } from "./ConfigEditor";

export function SettingsWorkspace({ onReturnToChat }: { onReturnToChat: () => void }) {
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

  return <main className="settings-workspace" aria-label="设置">
    <aside className="settings-navigation"><button className="settings-return" type="button" onClick={onReturnToChat}><ArrowLeft size={16} aria-hidden="true" />返回聊天</button><h1>设置</h1><div className="settings-nav-current"><Settings2 size={16} aria-hidden="true" />配置修改</div></aside>
    <section className="settings-content"><div className="settings-content-column"><header className="settings-heading"><h2>配置修改</h2></header><div className="settings-editor-host">{error && <section className="settings-unavailable"><strong>控制面暂不可用</strong><p>{error}</p><button type="button" onClick={load}>重试</button></section>}{!config && !error && <SettingsLoadingSkeleton />}{config && <ConfigEditor key={config.desired_revision} config={config} catalog={catalog} onApplied={setConfig} onUnavailable={setError} />}</div></div></section>
  </main>;
}

function SettingsLoadingSkeleton() {
  return <div className="settings-loading-skeleton" aria-label="正在读取配置" aria-busy="true"><div className="settings-skeleton-heading" />{Array.from({ length: 6 }, (_, index) => <div className="settings-skeleton-row" key={index}><span /><span /></div>)}</div>;
}

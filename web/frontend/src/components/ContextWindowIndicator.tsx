import { useId, type CSSProperties } from "react";

import { buildContextWindowView } from "../state/context_window";
import type { ContextWindow } from "../types";

/** 渲染 Composer 内只读的上下文窗口占用环。 */
export function ContextWindowIndicator({ context }: { context: ContextWindow | null }) {
  const tooltipId = useId();
  const view = buildContextWindowView(context);
  const label = view.available
    ? `上下文窗口：${view.currentLabel} / ${view.limitLabel} Token，已用 ${view.usedPercentLabel}，剩余 ${view.remainingPercentLabel}`
    : "上下文窗口：暂无已保存上下文";
  const style = { "--context-window-progress": `${view.usedPercent}%` } as CSSProperties & Record<"--context-window-progress", string>;
  return <span className={`context-window-indicator is-${view.tone}`} tabIndex={0} aria-label={label} aria-describedby={tooltipId}>
    <span className="context-window-ring" style={style} aria-hidden="true" />
    <span className="context-window-tooltip" id={tooltipId} role="tooltip">
      <span>上下文窗口：</span>
      {view.available ? <><strong>{view.currentLabel} / {view.limitLabel} Token</strong><small>已用 {view.usedPercentLabel}，剩余 {view.remainingPercentLabel}</small></> : <small>暂无已保存上下文</small>}
    </span>
  </span>;
}

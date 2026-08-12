import type { MultiAgentMode } from "../types";

type MultiAgentModeSelectorProps = {
  value: MultiAgentMode;
  enabled: boolean;
  disabledReason: string | null;
  locked: boolean;
  onChange: (mode: MultiAgentMode) => void;
};

// 渲染一次性 Multi-Agent 模式选择。
export function MultiAgentModeSelector({ value, enabled, disabledReason, locked, onChange }: MultiAgentModeSelectorProps) {
  const disabled = !enabled || locked;
  const reason = disabledReason || (locked ? "协作运行中，完成后可调整" : null);
  return <div className="multi-agent-mode" aria-label="Multi-Agent 模式">
    <div className="multi-agent-mode-options" role="radiogroup" aria-label="Multi-Agent 模式选择">
      <ModeButton value="auto" label="Auto" selected={value === "auto"} disabled={disabled} onChange={onChange} />
      <ModeButton value="off" label="Off" selected={value === "off"} disabled={disabled} onChange={onChange} />
    </div>
    {reason && <span className="multi-agent-mode-reason">{reason}</span>}
  </div>;
}

// 渲染模式分段中的单个稳定选择项。
function ModeButton({ value, label, selected, disabled, onChange }: { value: MultiAgentMode; label: string; selected: boolean; disabled: boolean; onChange: (mode: MultiAgentMode) => void }) {
  return <button type="button" role="radio" aria-checked={selected} className={selected ? "is-selected" : ""} disabled={disabled} onClick={() => onChange(value)}>{label}</button>;
}

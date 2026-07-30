import type { ToolCatalog } from "../types";
import { ToggleSwitch } from "./ToggleSwitch";

type ToolPermissionEditorProps = {
  catalog: ToolCatalog | null;
  selectedNames: Set<string>;
  onChange: (names: Set<string>) => void;
};

export function ToolPermissionEditor({ catalog, selectedNames, onChange }: ToolPermissionEditorProps) {
  if (!catalog) return <p className="settings-loading">正在读取 Tool Catalog…</p>;

  const setRequired = (name: string, required: boolean) => {
    const next = new Set(selectedNames);
    if (required) next.add(name);
    else next.delete(name);
    onChange(next);
  };

  return <section className="settings-group" aria-labelledby="tool-permissions-heading">
    <h3 id="tool-permissions-heading">工具权限</h3>
    <div className="tool-permission-list">
      {catalog.tools.map((tool) => <div className="tool-permission-row" key={tool.name}>
        <div className="tool-permission-copy"><strong title={tool.name}>{tool.name}</strong><span title={tool.description || "未提供说明"}>{tool.description || "未提供说明"}</span></div>
        <ToggleSwitch label={`${tool.name} 需要审批`} checked={selectedNames.has(tool.name)} onCheckedChange={(required) => setRequired(tool.name, required)} />
      </div>)}
    </div>
    {catalog.unavailable_approval_required.length > 0 && <div className="tool-permission-unavailable">
      <strong>不可用，仅可移除</strong>
      {catalog.unavailable_approval_required.map((name) => <div key={name}><span>{name}</span><button type="button" onClick={() => setRequired(name, false)}>移除</button></div>)}
    </div>}
  </section>;
}

import type { ToolCatalog, ToolCatalogEntry } from "../types";
import { ToggleSwitch } from "./ToggleSwitch";

type ToolPermissionEditorProps = {
  catalog: ToolCatalog | null;
  selectedNames: Set<string>;
  onChange: (names: Set<string>) => void;
};

type ToolCategoryId = "basic" | "search" | "cron" | "breakbeat" | "dream" | "incidents" | "skills" | "mcp" | "other";

type ToolCategory = {
  id: ToolCategoryId;
  label: string;
};

const toolCategories: ToolCategory[] = [
  { id: "basic", label: "基础 Tools" },
  { id: "search", label: "搜索 Tools" },
  { id: "cron", label: "Cron Tools" },
  { id: "breakbeat", label: "Breakbeat Tools" },
  { id: "dream", label: "Dream Tools" },
  { id: "incidents", label: "Incidents Tools" },
  { id: "skills", label: "Skill Tools" },
  { id: "mcp", label: "MCP Tools" },
  { id: "other", label: "其它 Tools" },
];

const basicToolNames = new Set([
  "echo",
  "exec",
  "write_stdin",
  "read_file",
  "write_file",
  "edit_file",
  "find_file",
  "grep",
  "list_dir",
  "now",
]);

const mcpToolNames = new Set([
  "search_mcp_capabilities",
  "apply_mcp_prompt",
  "attach_mcp_resource",
]);

function categoryFor(tool: ToolCatalogEntry): ToolCategoryId {
  if (tool.source.kind === "mcp" || mcpToolNames.has(tool.name)) return "mcp";
  if (basicToolNames.has(tool.name)) return "basic";
  if (tool.name.startsWith("web_") || tool.name === "weather") return "search";
  if (tool.name.includes("cron")) return "cron";
  if (tool.name.includes("breakbeat")) return "breakbeat";
  if (tool.name.includes("dream") || tool.name === "read_profile_memory") return "dream";
  if (tool.name.includes("incident")) return "incidents";
  if (tool.name.includes("skill")) return "skills";
  return "other";
}

export function ToolPermissionEditor({ catalog, selectedNames, onChange }: ToolPermissionEditorProps) {
  if (!catalog) return <p className="settings-loading">正在读取 Tool Catalog…</p>;

  const setRequired = (name: string, required: boolean) => {
    const next = new Set(selectedNames);
    if (required) next.add(name);
    else next.delete(name);
    onChange(next);
  };
  const groups = toolCategories
    .map((category) => ({
      ...category,
      tools: catalog.tools.filter((tool) => categoryFor(tool) === category.id).sort((left, right) => left.name.localeCompare(right.name)),
    }))
    .filter((category) => category.tools.length > 0);

  return <section className="settings-group" aria-labelledby="tool-permissions-heading">
    <h3 id="tool-permissions-heading">工具权限</h3>
    <div className="tool-permission-list">
      {groups.map((category) => <section className="tool-permission-category" data-category={category.id} key={category.id} aria-labelledby={`tool-category-${category.id}`}>
        <div className="tool-permission-category-heading"><h4 id={`tool-category-${category.id}`}>{category.label}</h4></div>
        {category.tools.map((tool) => <div className="tool-permission-row" key={tool.name}>
          <div className="tool-permission-copy"><strong title={tool.name}>{tool.name}</strong><span title={tool.description || "未提供说明"}>{tool.description || "未提供说明"}</span></div>
          <ToggleSwitch label={`${tool.name} 需要审批`} checked={selectedNames.has(tool.name)} onCheckedChange={(required) => setRequired(tool.name, required)} />
        </div>)}
      </section>)}
    </div>
    {catalog.unavailable_approval_required.length > 0 && <div className="tool-permission-unavailable">
      <strong>不可用，仅可移除</strong>
      {catalog.unavailable_approval_required.map((name) => <div key={name}><span>{name}</span><button type="button" onClick={() => setRequired(name, false)}>移除</button></div>)}
    </div>}
  </section>;
}

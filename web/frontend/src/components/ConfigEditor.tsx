import { useEffect, useRef, useState } from "react";

import { ApiError, api } from "../api";
import type { ConfigApplyRequest, ControlConfig, EditableControlConfig, ToolCatalog } from "../types";
import { ScrollArea } from "./ScrollArea";
import { ChannelAccountSettings } from "./ChannelAccountSettings";
import { ToolPermissionEditor } from "./ToolPermissionEditor";
import { ToggleSwitch } from "./ToggleSwitch";

type ConfigEditorProps = {
  config: ControlConfig;
  catalog: ToolCatalog | null;
  onApplied: (config: ControlConfig) => void;
  onUnavailable: (message: string) => void;
};

type FieldGroup = Exclude<keyof EditableControlConfig, "tool_permissions">;
type FieldDefinition = { group: FieldGroup; field: string; label: string; type: "text" | "number" | "switch" };

const fields: FieldDefinition[] = [
  { group: "llm", field: "base_url", label: "服务地址", type: "text" },
  { group: "llm", field: "model", label: "模型名称", type: "text" },
  { group: "llm", field: "timeout_seconds", label: "请求超时（秒）", type: "number" },
  { group: "llm", field: "max_retries", label: "最大重试次数", type: "number" },
  { group: "llm", field: "retry_delay_seconds", label: "重试间隔（秒）", type: "number" },
  { group: "llm", field: "streaming_enabled", label: "启用流式输出", type: "switch" },
  { group: "runtime", field: "max_tool_rounds", label: "最大工具轮数", type: "number" },
  { group: "runtime", field: "max_tool_calls_per_round", label: "每轮最大工具调用数", type: "number" },
  { group: "runtime", field: "parallel_tool_calls_enabled", label: "启用并行工具调用", type: "switch" },
  { group: "runtime", field: "max_parallel_tool_calls", label: "最大并行工具调用数", type: "number" },
  { group: "runtime", field: "turn_timeout_seconds", label: "单轮超时（秒）", type: "number" },
  { group: "runtime", field: "max_context_tokens", label: "最大上下文 Token", type: "number" },
  { group: "runtime", field: "max_tool_result_tokens", label: "最大工具结果 Token", type: "number" },
  { group: "memory", field: "compact_token_threshold", label: "压缩阈值 Token", type: "number" },
  { group: "memory", field: "recent_window_token_limit", label: "最近窗口 Token", type: "number" },
  { group: "sessions", field: "retention_days", label: "会话保留天数", type: "number" },
  { group: "skills", field: "max_loaded_skills_per_turn", label: "每轮最大加载 Skill 数", type: "number" },
  { group: "skills", field: "max_skill_tokens", label: "单个 Skill 最大 Token", type: "number" },
  { group: "skills", field: "max_loaded_skill_tokens_per_turn", label: "每轮 Skill 最大 Token", type: "number" },
  { group: "proactive", field: "enabled", label: "启用主动能力", type: "switch" },
  { group: "proactive", field: "timezone", label: "主动能力时区", type: "text" },
  { group: "proactive", field: "review_provider", label: "审阅模型 Provider", type: "text" },
  { group: "proactive", field: "review_base_url", label: "审阅模型服务地址", type: "text" },
  { group: "proactive", field: "review_model", label: "审阅模型标识", type: "text" },
  { group: "proactive", field: "background_max_concurrency", label: "后台最大并发数", type: "number" },
  { group: "proactive", field: "breakbeat_refresh_minutes", label: "Breakbeat 刷新间隔（分钟）", type: "number" },
  { group: "proactive", field: "dream_refresh_hours", label: "Dream 刷新间隔（小时）", type: "number" },
  { group: "proactive", field: "review_window_token_limit", label: "审阅窗口 Token 上限", type: "number" },
  { group: "proactive", field: "profile_total_token_limit", label: "长期画像总 Token 上限", type: "number" },
  { group: "proactive", field: "user_profile_token_limit", label: "USER 画像 Token 上限", type: "number" },
  { group: "proactive", field: "soul_profile_token_limit", label: "SOUL 画像 Token 上限", type: "number" },
  { group: "proactive", field: "skill_observation_turn_interval", label: "Skill 观察轮次间隔", type: "number" },
  { group: "proactive", field: "skill_observation_token_limit", label: "Skill 观察 Token 上限", type: "number" },
  { group: "proactive", field: "skill_evolution_batch_token_limit", label: "Skill 演进批次 Token 上限", type: "number" },
  { group: "proactive", field: "skill_evolution_batches_per_kind", label: "每类 Skill 演进批次数", type: "number" },
];

const groups: Array<{ title: string; key: FieldGroup }> = [
  { title: "模型连接", key: "llm" },
  { title: "Runtime 限制", key: "runtime" },
  { title: "记忆、会话与 Skill", key: "memory" },
  { title: "记忆、会话与 Skill", key: "sessions" },
  { title: "记忆、会话与 Skill", key: "skills" },
  { title: "主动能力", key: "proactive" },
];

function cloneConfig(config: EditableControlConfig): EditableControlConfig {
  return structuredClone(config);
}

function changesFor(baseline: EditableControlConfig, draft: EditableControlConfig): ConfigApplyRequest["changes"] {
  const changes: ConfigApplyRequest["changes"] = {};
  for (const group of ["llm", "runtime", "memory", "sessions", "skills", "proactive"] as const) {
    const changed = Object.fromEntries(Object.entries(draft[group]).filter(([key, value]) => baseline[group][key as never] !== value));
    if (Object.keys(changed).length) Object.assign(changes, { [group]: changed });
  }
  return changes;
}

export function ConfigEditor({ config, catalog, onApplied, onUnavailable }: ConfigEditorProps) {
  const [baseline, setBaseline] = useState(() => config.desired);
  const [draft, setDraft] = useState(() => cloneConfig(config.desired));
  const [apiKey, setApiKey] = useState("");
  const [clearApiKey, setClearApiKey] = useState(false);
  const [reviewApiKey, setReviewApiKey] = useState("");
  const [clearReviewApiKey, setClearReviewApiKey] = useState(false);
  const [selectedTools, setSelectedTools] = useState(() => new Set([...(catalog?.tools.filter((tool) => tool.approval_required).map((tool) => tool.name) || []), ...(catalog?.unavailable_approval_required || [])]));
  const [baselineTools, setBaselineTools] = useState(() => new Set([...(catalog?.tools.filter((tool) => tool.approval_required).map((tool) => tool.name) || []), ...(catalog?.unavailable_approval_required || [])]));
  const [fieldErrors, setFieldErrors] = useState<Record<string, string>>({});
  const [state, setState] = useState(config);
  const [isApplying, setIsApplying] = useState(false);
  const [testResult, setTestResult] = useState<string | null>(null);
  const requestVersion = useRef(0);
  const pollTimer = useRef<number | null>(null);

  const hasChanges = Object.keys(changesFor(baseline, draft)).length > 0 || Boolean(apiKey) || clearApiKey || Boolean(reviewApiKey) || clearReviewApiKey || !sameSet(baselineTools, selectedTools);

  useEffect(() => {
    const timer = pollTimer.current;
    return () => {
      requestVersion.current += 1;
      if (timer !== null) window.clearTimeout(timer);
      if (pollTimer.current !== null) window.clearTimeout(pollTimer.current);
      pollTimer.current = null;
    };
  }, []);

  const stopPolling = () => {
    if (pollTimer.current !== null) window.clearTimeout(pollTimer.current);
    pollTimer.current = null;
  };

  const adopt = (next: ControlConfig) => {
    setState(next);
    if (next.state === "active") {
      setBaseline(next.desired);
      setDraft(cloneConfig(next.desired));
      setApiKey("");
      setClearApiKey(false);
      setReviewApiKey("");
      setClearReviewApiKey(false);
      setBaselineTools(new Set(selectedTools));
      onApplied(next);
    }
  };

  const startPolling = (version: number) => {
    stopPolling();
    const poll = async () => {
      try {
        const next = await api.controlConfig();
        if (version !== requestVersion.current) return;
        adopt(next);
        if (next.state === "pending" || next.state === "applying") pollTimer.current = window.setTimeout(() => void poll(), 750);
        else stopPolling();
      } catch (error) {
        if (version === requestVersion.current) onUnavailable(error instanceof Error ? error.message : "控制面暂不可用");
        stopPolling();
      }
    };
    pollTimer.current = window.setTimeout(() => void poll(), 750);
  };

  const updateField = (definition: FieldDefinition, value: string | boolean) => {
    const isOptionalReviewField = definition.group === "proactive" && ["review_provider", "review_base_url", "review_model"].includes(definition.field);
    const nextValue = definition.type === "number" ? Number(value) : isOptionalReviewField && value === "" ? null : value;
    setDraft((previous) => ({ ...previous, [definition.group]: { ...previous[definition.group], [definition.field]: nextValue } }));
  };

  const apply = async () => {
    const version = ++requestVersion.current;
    stopPolling();
    setIsApplying(true);
    setFieldErrors({});
    try {
      const changes = changesFor(baseline, draft);
      if (apiKey || clearApiKey) changes.llm = { ...(changes.llm || {}), ...(apiKey ? { api_key: apiKey } : {}), ...(clearApiKey ? { clear_api_key: true } : {}) };
      if (reviewApiKey || clearReviewApiKey) changes.proactive = { ...(changes.proactive || {}), ...(reviewApiKey ? { review_api_key: reviewApiKey } : {}), ...(clearReviewApiKey ? { clear_review_api_key: true } : {}) };
      const add = [...selectedTools].filter((name) => !baselineTools.has(name));
      const remove = [...baselineTools].filter((name) => !selectedTools.has(name));
      const response = await api.applyControlConfig({ changes, approval_required_tools: { add, remove } });
      if (version !== requestVersion.current) return;
      adopt(response);
      if (response.state === "pending" || response.state === "applying") startPolling(version);
    } catch (error) {
      if (version !== requestVersion.current) return;
      if (error instanceof ApiError && error.fieldErrors) setFieldErrors(error.fieldErrors);
      else onUnavailable(error instanceof Error ? error.message : "应用配置失败");
    } finally {
      if (version === requestVersion.current) setIsApplying(false);
    }
  };

  const testLlm = async () => {
    setTestResult(null);
    try {
      const { provider: _provider, api_key_configured: _apiKeyConfigured, ...changes } = draft.llm;
      const result = await api.testControlLlm({ ...changes, ...(apiKey ? { api_key: apiKey } : {}) });
      setTestResult(`连接成功，${result.latency_ms} ms`);
    } catch (error) {
      setTestResult(error instanceof Error ? error.message : "连接测试失败");
    }
  };

  const fieldsFor = (group: FieldGroup) => fields.filter((field) => field.group === group).map((field) => {
    const fieldError = fieldErrors[`${field.group}.${field.field}`];
    const fieldErrorId = `settings-field-error-${field.group}-${field.field}`;
    const checked = Boolean(draft[field.group][field.field as never]);
    return <div className="settings-field" key={`${field.group}.${field.field}`}>
      <span>{field.label}</span>
      {field.type === "switch"
        ? <ToggleSwitch label={field.label} checked={checked} onCheckedChange={(next) => updateField(field, next)} />
        : <input aria-label={field.label} aria-describedby={fieldError ? fieldErrorId : undefined} type="text" inputMode={field.type === "number" ? "decimal" : "text"} value={String(draft[field.group][field.field as never] ?? "")} onChange={(event) => updateField(field, event.target.value)} />}
      {fieldError && <small id={fieldErrorId} className="settings-field-error">{fieldError}</small>}
    </div>;
  });

  const applyStatus = state.state === "pending"
    ? "保存成功，等待当前任务结束"
    : state.state === "applying"
      ? "正在替换 Runtime"
      : state.state === "failed"
        ? `${state.last_apply_error || "配置应用失败"}，旧 Runtime 仍可用。`
        : hasChanges
          ? "未保存的修改"
          : "已生效";
  const shortRevision = state.active_revision.replace(/^sha256:/, "").slice(0, 12);

  return <div className="settings-editor">
    <ScrollArea className="settings-editor-scroll"><div className="settings-groups">
      {groups.map((group, index) => (index === 0 || groups[index - 1].title !== group.title) && <section className="settings-group" key={group.key}><h3>{group.title}</h3>{groups.filter((item) => item.title === group.title).flatMap((item) => fieldsFor(item.key))}{group.key === "llm" && <><div className="settings-field"><span>替换 API Key</span><input aria-label="替换 API Key" type="password" value={apiKey} onChange={(event) => { setApiKey(event.target.value); setClearApiKey(false); }} /></div><div className="settings-field settings-clear-api-key"><span>清除已配置 API Key</span><ToggleSwitch label="清除 API Key" checked={clearApiKey} onCheckedChange={(next) => { setClearApiKey(next); setApiKey(""); }} /></div></>}{group.key === "proactive" && <><div className="settings-field"><span>替换审阅 API Key</span><input aria-label="替换审阅 API Key" type="password" value={reviewApiKey} onChange={(event) => { setReviewApiKey(event.target.value); setClearReviewApiKey(false); }} /></div><div className="settings-field settings-clear-api-key"><span>清除已配置审阅 API Key</span><ToggleSwitch label="清除审阅 API Key" checked={clearReviewApiKey} onCheckedChange={(next) => { setClearReviewApiKey(next); setReviewApiKey(""); }} /></div></>}</section>)}
      <ToolPermissionEditor catalog={catalog} selectedNames={selectedTools} onChange={setSelectedTools} />
      <ChannelAccountSettings />
    </div></ScrollArea>
    <footer className="settings-apply-bar" data-state={state.state} data-dirty={hasChanges}><span className="settings-apply-status">{applyStatus}</span>{state.state === "active" && !hasChanges && <span className="settings-apply-revision" title={state.active_revision}>revision {shortRevision}</span>}{testResult && <span className="settings-test-result" role="status">{testResult}</span>}<button type="button" onClick={() => void testLlm()} disabled={isApplying}>测试连接</button><button type="button" onClick={() => void apply()} disabled={!hasChanges || isApplying}>{isApplying ? "正在应用…" : "应用配置"}</button></footer>
  </div>;
}

function sameSet(left: Set<string>, right: Set<string>): boolean {
  return left.size === right.size && [...left].every((value) => right.has(value));
}

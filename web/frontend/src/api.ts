import type { AttachmentMetadata, ChatMessage, CommandCatalog, ConfigApplyRequest, ContextWindow, ControlConfig, LlmTestResult, McpServerDetail, McpServerSummary, Observability, Session, SessionContextReadModel, ToolCallPage, ToolCatalog } from "./types";

/** 保存 REST 调用失败时的状态码与后端错误内容。 */
export class ApiError extends Error {
  /** 创建可供通知区显示的 REST 错误。 */
  constructor(readonly status: number, message: string, readonly fieldErrors?: Record<string, string>) {
    super(message);
    this.name = "ApiError";
  }
}

/** 调用本机 Web Host 的 REST 接口并统一转换错误。 */
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const headers = options?.body instanceof FormData ? undefined : { "Content-Type": "application/json" };
  const response = await fetch(path, { headers, ...options });
  if (!response.ok) {
    const body = await response.text();
    let message = body || "请求失败";
    let fieldErrors: Record<string, string> | undefined;
    try {
      const payload = JSON.parse(body) as { detail?: unknown; field_errors?: unknown; message?: unknown };
      if (typeof payload.detail === "string") message = payload.detail;
      if (typeof payload.message === "string") message = payload.message;
      if (response.status === 422 && payload.field_errors && typeof payload.field_errors === "object" && !Array.isArray(payload.field_errors)) {
        const entries = Object.entries(payload.field_errors).filter((entry): entry is [string, string] => typeof entry[1] === "string");
        if (entries.length) {
          fieldErrors = Object.fromEntries(entries);
          message = entries.map(([field, error]) => `${field}: ${error}`).join("；");
        }
      }
    } catch {}
    throw new ApiError(response.status, message, fieldErrors);
  }
  return response.status === 204 ? (undefined as T) : response.json() as Promise<T>;
}

export const api = {
  listSessions: (archived = false) => request<Session[]>(`/api/sessions?archived=${archived}`),
  createSession: () => request<Session>("/api/sessions", { method: "POST" }),
  messages: (id: string, signal?: AbortSignal) =>
    request<ChatMessage[]>(`/api/sessions/${encodeURIComponent(id)}/messages`, { signal }),
  observability: (id: string) => request<Observability>(`/api/sessions/${encodeURIComponent(id)}/observability`),
  contextWindow: (id: string) => request<ContextWindow>(`/api/sessions/${encodeURIComponent(id)}/context-window`),
  patchSession: (id: string, payload: Partial<Pick<Session, "title" | "pinned" | "archived">>) =>
    request<Session>(`/api/sessions/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteSession: (id: string) => request<void>(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }),
  uiSettings: () => request<{ auto_approve_tools: boolean }>("/api/settings/ui"),
  patchUiSettings: (auto_approve_tools: boolean) =>
    request<{ auto_approve_tools: boolean }>("/api/settings/ui", { method: "PATCH", body: JSON.stringify({ auto_approve_tools }) }),
  controlConfig: () => request<ControlConfig>("/api/control/config"),
  applyControlConfig: (payload: ConfigApplyRequest) => request<ControlConfig>("/api/control/config/apply", { method: "POST", body: JSON.stringify(payload) }),
  testControlLlm: (changes: ConfigApplyRequest["changes"]["llm"]) => request<LlmTestResult>("/api/control/config/test-llm", { method: "POST", body: JSON.stringify({ changes }) }),
  controlTools: () => request<ToolCatalog>("/api/control/tools"),
  commands: () => request<CommandCatalog>("/api/control/commands"),
  sessionContext: (id: string) => request<SessionContextReadModel>(`/api/control/sessions/${encodeURIComponent(id)}/context`),
  toolCalls: (id: string, cursor?: string) => request<ToolCallPage>(`/api/control/sessions/${encodeURIComponent(id)}/tool-calls${cursor ? `?cursor=${encodeURIComponent(cursor)}` : ""}`),
  mcpServers: () => request<{ servers: McpServerSummary[] }>("/api/control/mcp/servers"),
  mcpServer: (name: string) => request<McpServerDetail>(`/api/control/mcp/servers/${encodeURIComponent(name)}`)
  ,uploadAttachments: (sessionId: string, files: File[]) => {
    const body = new FormData();
    files.forEach((file) => body.append("files", file, file.name));
    return request<{ attachments: AttachmentMetadata[] }>(`/api/control/sessions/${encodeURIComponent(sessionId)}/attachments`, { method: "POST", body });
  },
  modelCapabilities: () => request<{ supports_vision: boolean }>("/api/control/model-capabilities")
};

import type { ChatMessage, Observability, Session } from "./types";

/** 保存 REST 调用失败时的状态码与后端错误内容。 */
export class ApiError extends Error {
  /** 创建可供通知区显示的 REST 错误。 */
  constructor(readonly status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
}

/** 调用本机 Web Host 的 REST 接口并统一转换错误。 */
async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!response.ok) throw new ApiError(response.status, await response.text() || "请求失败");
  return response.status === 204 ? (undefined as T) : response.json() as Promise<T>;
}

export const api = {
  listSessions: (archived = false) => request<Session[]>(`/api/sessions?archived=${archived}`),
  messages: (id: string) => request<ChatMessage[]>(`/api/sessions/${encodeURIComponent(id)}/messages`),
  observability: (id: string) => request<Observability>(`/api/sessions/${encodeURIComponent(id)}/observability`),
  patchSession: (id: string, payload: Partial<Pick<Session, "title" | "pinned" | "archived">>) =>
    request<Session>(`/api/sessions/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify(payload) }),
  deleteSession: (id: string) => request<void>(`/api/sessions/${encodeURIComponent(id)}`, { method: "DELETE" }),
  uiSettings: () => request<{ auto_approve_tools: boolean }>("/api/settings/ui"),
  patchUiSettings: (auto_approve_tools: boolean) =>
    request<{ auto_approve_tools: boolean }>("/api/settings/ui", { method: "PATCH", body: JSON.stringify({ auto_approve_tools }) })
};

import { ApiError as BaseApiError } from "./api";

export type ChannelAccountView = {
  id: string;
  platform: "feishu" | "weixin";
  principal_id: string;
  principal_kind: "owner" | "independent";
  status: string;
  enabled: boolean;
  subscribed: boolean;
  credential_state: string;
  connected: boolean;
  app_id_masked?: string | null;
};

export type WeixinQrView = {
  binding_id: string;
  status: string;
  qr_content: string | null;
  expires_at: number | null;
};

export type ChannelDeletionView = {
  deleted: true;
  account_id: string;
  platform: "feishu" | "weixin";
};

export class ApiError extends BaseApiError {
  constructor(status: number, message: string, readonly code?: string) {
    super(status, message);
    this.name = "ChannelControlApiError";
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!response.ok) {
    const body = await response.text();
    let message = body || "请求失败";
    let code: string | undefined;
    try {
      const payload = JSON.parse(body) as { detail?: unknown; message?: unknown; code?: unknown };
      const detail = payload.detail;
      if (typeof detail === "string") message = detail;
      if (detail && typeof detail === "object" && !Array.isArray(detail)) {
        const structured = detail as { code?: unknown; message?: unknown };
        if (typeof structured.message === "string") message = structured.message;
        if (typeof structured.code === "string") code = structured.code;
      }
      if (typeof payload.message === "string") message = payload.message;
      if (typeof payload.code === "string") code = payload.code;
    } catch {}
    throw new ApiError(response.status, message, code);
  }
  return response.status === 204 ? (undefined as T) : response.json() as Promise<T>;
}

export const channelControlApi = {
  list: () => request<{ accounts: ChannelAccountView[] }>("/api/control/channels"),
  inviteWeixin: (principal: "owner" | "new") => request<ChannelAccountView>("/api/control/channels/weixin/invitations", { method: "POST", body: JSON.stringify({ principal }) }),
  rescanWeixin: (id: string) => request<ChannelAccountView>(`/api/control/channels/weixin/${encodeURIComponent(id)}/rescan`, { method: "POST" }),
  getWeixinQr: (id: string) => request<WeixinQrView>(`/api/control/channels/weixin/${encodeURIComponent(id)}/qr`),
  registerFeishu: (payload: { app_id: string; app_secret: string; domain: string }) => request<ChannelAccountView>("/api/control/channels/feishu", { method: "POST", body: JSON.stringify(payload) }),
  enable: (platform: string, id: string) => request<ChannelAccountView>(`/api/control/channels/${platform}/${encodeURIComponent(id)}/enable`, { method: "POST" }),
  disable: (platform: string, id: string) => request<ChannelAccountView>(`/api/control/channels/${platform}/${encodeURIComponent(id)}/disable`, { method: "POST" }),
  revoke: (platform: string, id: string) => request<ChannelAccountView>(`/api/control/channels/${platform}/${encodeURIComponent(id)}/revoke`, { method: "POST" }),
  subscribe: (platform: string, id: string) => request<ChannelAccountView>(`/api/control/channels/${platform}/${encodeURIComponent(id)}/subscribe`, { method: "POST" }),
  unsubscribe: (platform: string, id: string) => request<ChannelAccountView>(`/api/control/channels/${platform}/${encodeURIComponent(id)}/unsubscribe`, { method: "POST" }),
  delete: (platform: "feishu" | "weixin", id: string) => request<ChannelDeletionView>(`/api/control/channels/${platform}/${encodeURIComponent(id)}`, { method: "DELETE" }),
  setFeishuOwnerCode: (id: string, code: string) => request<ChannelAccountView>(`/api/control/channels/feishu/${encodeURIComponent(id)}/owner-code`, { method: "POST", body: JSON.stringify({ code }) }),
  rotateFeishuCredentials: (id: string, payload: { app_id: string; app_secret: string; domain: string }) => request<ChannelAccountView>(`/api/control/channels/feishu/${encodeURIComponent(id)}/credentials`, { method: "POST", body: JSON.stringify(payload) }),
};

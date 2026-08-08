export type ChannelAccountView = {
  id: string;
  platform: "feishu" | "weixin";
  principal_id: string;
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

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
  if (!response.ok) throw new Error("请求失败");
  return response.json() as Promise<T>;
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
  setFeishuOwnerCode: (id: string, code: string) => request<ChannelAccountView>(`/api/control/channels/feishu/${encodeURIComponent(id)}/owner-code`, { method: "POST", body: JSON.stringify({ code }) }),
  rotateFeishuCredentials: (id: string, payload: { app_id: string; app_secret: string; domain: string }) => request<ChannelAccountView>(`/api/control/channels/feishu/${encodeURIComponent(id)}/credentials`, { method: "POST", body: JSON.stringify(payload) }),
};

import { useCallback, useEffect, useRef, useState } from "react";
import { MessageCircle, Plus, RefreshCw, ShieldCheck } from "lucide-react";
import QRCode from "qrcode";

import { channelControlApi, type ChannelAccountView } from "../channel_control_api";

const WEIXIN_QR_POLL_INTERVAL_MS = 2_000;

type WeixinQrDisplay = {
  source: string;
  imageSrc: string;
  expiresAt: number | null;
};

function isPendingWeixin(account: ChannelAccountView): boolean {
  return account.platform === "weixin" && account.status === "pending_qr";
}

function toImageSource(content: string): Promise<string> {
  const trimmed = content.trim();
  if (/^data:image\//i.test(trimmed)) return Promise.resolve(trimmed);
  const base64 = trimmed.replace(/\s/g, "");
  if (base64.startsWith("iVBORw0KGgo")) return Promise.resolve(`data:image/png;base64,${base64}`);
  if (base64.startsWith("R0lGOD")) return Promise.resolve(`data:image/gif;base64,${base64}`);
  return QRCode.toDataURL(trimmed, { errorCorrectionLevel: "M", margin: 1, width: 176 });
}

export function ChannelAccountSettings() {
  const [accounts, setAccounts] = useState<ChannelAccountView[]>([]);
  const [weixinQrs, setWeixinQrs] = useState<Record<string, WeixinQrDisplay>>({});
  const [error, setError] = useState<string | null>(null);
  const [appId, setAppId] = useState("");
  const [domain, setDomain] = useState("open.feishu.cn");
  const [rotationId, setRotationId] = useState<string | null>(null);
  const [rotationAppId, setRotationAppId] = useState("");
  const [rotationDomain, setRotationDomain] = useState("open.feishu.cn");
  const [busy, setBusy] = useState(false);
  const accountsRef = useRef<ChannelAccountView[]>([]);
  const weixinQrsRef = useRef<Record<string, WeixinQrDisplay>>({});
  const qrRequestsRef = useRef(new Set<string>());
  const appSecretRef = useRef<HTMLInputElement>(null);
  const ownerCodeRefs = useRef<Record<string, HTMLInputElement | null>>({});
  const rotationSecretRef = useRef<HTMLInputElement>(null);

  const commitWeixinQrs = useCallback((next: Record<string, WeixinQrDisplay>) => {
    weixinQrsRef.current = next;
    setWeixinQrs(next);
  }, []);

  const clearWeixinQr = useCallback((bindingId: string, expectedSource?: string) => {
    const current = weixinQrsRef.current;
    const display = current[bindingId];
    if (!display || (expectedSource && display.source !== expectedSource)) return;
    const next = { ...current };
    delete next[bindingId];
    commitWeixinQrs(next);
  }, [commitWeixinQrs]);

  const commitAccounts = useCallback((next: ChannelAccountView[]) => {
    accountsRef.current = next;
    setAccounts(next);
    const pendingIds = new Set(next.filter(isPendingWeixin).map((account) => account.id));
    const currentQrs = weixinQrsRef.current;
    const nextQrs = Object.fromEntries(Object.entries(currentQrs).filter(([bindingId]) => pendingIds.has(bindingId)));
    if (Object.keys(nextQrs).length !== Object.keys(currentQrs).length) commitWeixinQrs(nextQrs);
  }, [commitWeixinQrs]);

  const load = useCallback(async () => {
    try {
      const result = await channelControlApi.list();
      commitAccounts(result.accounts);
    } catch {
      setError("账号状态暂不可用");
    }
  }, [commitAccounts]);

  const replaceAccount = useCallback((next: ChannelAccountView) => {
    const current = accountsRef.current;
    const index = current.findIndex((item) => item.id === next.id && item.platform === next.platform);
    commitAccounts(index >= 0 ? current.map((item) => item.id === next.id && item.platform === next.platform ? next : item) : [...current, next]);
  }, [commitAccounts]);

  const refreshWeixinQr = useCallback(async (bindingId: string) => {
    const account = accountsRef.current.find((item) => item.id === bindingId && item.platform === "weixin");
    if (!account || !isPendingWeixin(account) || qrRequestsRef.current.has(bindingId)) {
      if (!account || !isPendingWeixin(account)) clearWeixinQr(bindingId);
      return;
    }
    qrRequestsRef.current.add(bindingId);
    try {
      const qr = await channelControlApi.getWeixinQr(bindingId);
      const current = accountsRef.current.find((item) => item.id === bindingId && item.platform === "weixin");
      if (!current || !isPendingWeixin(current) || qr.binding_id !== bindingId) return;
      if (qr.status !== "pending_qr") {
        clearWeixinQr(bindingId);
        void load();
        return;
      }
      if (!qr.qr_content) {
        clearWeixinQr(bindingId);
        return;
      }
      const previous = weixinQrsRef.current[bindingId];
      if (previous?.source === qr.qr_content && previous.expiresAt === qr.expires_at) return;
      const imageSrc = await toImageSource(qr.qr_content);
      const active = accountsRef.current.find((item) => item.id === bindingId && item.platform === "weixin");
      if (!active || !isPendingWeixin(active)) return;
      commitWeixinQrs({
        ...weixinQrsRef.current,
        [bindingId]: { source: qr.qr_content, imageSrc, expiresAt: qr.expires_at },
      });
    } catch {
      clearWeixinQr(bindingId);
    } finally {
      qrRequestsRef.current.delete(bindingId);
    }
  }, [clearWeixinQr, commitWeixinQrs, load]);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    const pendingBindings = accounts.filter(isPendingWeixin).map((account) => account.id);
    if (pendingBindings.length === 0) return;
    let disposed = false;
    const refresh = () => {
      if (disposed) return;
      pendingBindings.forEach((bindingId) => { void refreshWeixinQr(bindingId); });
    };
    refresh();
    const intervalId = window.setInterval(refresh, WEIXIN_QR_POLL_INTERVAL_MS);
    return () => { disposed = true; window.clearInterval(intervalId); };
  }, [accounts, refreshWeixinQr]);

  useEffect(() => {
    const timeoutIds = Object.entries(weixinQrs).flatMap(([bindingId, display]) => {
      if (display.expiresAt === null) return [];
      const delay = Math.max(0, display.expiresAt * 1_000 - Date.now());
      return [window.setTimeout(() => clearWeixinQr(bindingId, display.source), delay)];
    });
    return () => { timeoutIds.forEach((timeoutId) => window.clearTimeout(timeoutId)); };
  }, [clearWeixinQr, weixinQrs]);

  const clearRegistration = () => {
    setAppId("");
    setDomain("");
    if (appSecretRef.current) appSecretRef.current.value = "";
  };

  const clearRotation = () => {
    setRotationId(null);
    setRotationAppId("");
    setRotationDomain("");
    if (rotationSecretRef.current) rotationSecretRef.current.value = "";
  };

  const invite = async (principal: "owner" | "new") => {
    setBusy(true); setError(null);
    try {
      const account = await channelControlApi.inviteWeixin(principal);
      replaceAccount(account);
      void refreshWeixinQr(account.id);
    }
    catch { setError("创建微信 Binding 失败"); }
    finally { setBusy(false); }
  };

  const register = async () => {
    const secret = appSecretRef.current?.value || "";
    setBusy(true); setError(null);
    try {
      const account = await channelControlApi.registerFeishu({ app_id: appId.trim(), app_secret: secret, domain: domain.trim() });
      replaceAccount(account);
    } catch { setError("登记飞书 Bot 失败"); }
    finally { clearRegistration(); setBusy(false); }
  };

  const toggle = async (account: ChannelAccountView) => {
    setBusy(true); setError(null);
    try {
      const next = account.enabled ? await channelControlApi.disable(account.platform, account.id) : await channelControlApi.enable(account.platform, account.id);
      replaceAccount(next);
      if (isPendingWeixin(next)) void refreshWeixinQr(next.id);
      else if (next.platform === "weixin") clearWeixinQr(next.id);
    } catch { setError("更新账号状态失败"); }
    finally { setBusy(false); }
  };

  const rescan = async (account: ChannelAccountView) => {
    setBusy(true); setError(null); clearWeixinQr(account.id);
    try {
      const next = await channelControlApi.rescanWeixin(account.id);
      replaceAccount(next);
      void refreshWeixinQr(next.id);
    } catch { setError("重新扫码失败"); }
    finally { setBusy(false); }
  };

  const setSubscription = async (account: ChannelAccountView) => {
    setBusy(true); setError(null);
    try { replaceAccount(account.subscribed ? await channelControlApi.unsubscribe(account.platform, account.id) : await channelControlApi.subscribe(account.platform, account.id)); }
    catch { setError("更新通知订阅失败"); }
    finally { setBusy(false); }
  };

  const revoke = async (account: ChannelAccountView) => {
    setBusy(true); setError(null);
    try { replaceAccount(await channelControlApi.revoke(account.platform, account.id)); }
    catch { setError("撤销账号失败"); }
    finally { setBusy(false); }
  };

  const setOwnerCode = async (account: ChannelAccountView) => {
    const code = ownerCodeRefs.current[account.id]?.value || "";
    setBusy(true); setError(null);
    try { replaceAccount(await channelControlApi.setFeishuOwnerCode(account.id, code)); }
    catch { setError("设置 Owner 验证码失败"); }
    finally { if (ownerCodeRefs.current[account.id]) ownerCodeRefs.current[account.id]!.value = ""; setBusy(false); }
  };

  const rotate = async (account: ChannelAccountView) => {
    const secret = rotationSecretRef.current?.value || "";
    setBusy(true); setError(null);
    try { replaceAccount(await channelControlApi.rotateFeishuCredentials(account.id, { app_id: rotationAppId.trim(), app_secret: secret, domain: rotationDomain.trim() })); }
    catch { setError("轮换凭据失败，旧连接仍保留"); }
    finally { clearRotation(); setBusy(false); }
  };

  return <section className="settings-group channel-account-settings" aria-label="IM Channels">
    <div className="channel-account-heading"><div><h3>IM Channels</h3><p>二维码仅在本机控制台短时显示；凭据和平台用户标识不会进入浏览器。</p></div><button className="settings-icon-button" type="button" onClick={() => void load()} aria-label="刷新 IM 账号"><RefreshCw size={15} aria-hidden="true" /></button></div>
    {error && <p className="settings-field-error" role="alert">{error}</p>}
    <div className="channel-account-actions"><button type="button" disabled={busy} onClick={() => void invite("owner")}><Plus size={14} aria-hidden="true" />微信 Owner Binding</button><button type="button" disabled={busy} onClick={() => void invite("new")}><Plus size={14} aria-hidden="true" />微信新主体</button></div>
    <div className="channel-feishu-form"><label>飞书 App ID<input aria-label="飞书 App ID" value={appId} onChange={(event) => setAppId(event.target.value)} autoComplete="off" /></label><label>飞书 App Secret<input aria-label="飞书 App Secret" ref={appSecretRef} type="password" autoComplete="new-password" /></label><label>域<input value={domain} onChange={(event) => setDomain(event.target.value)} /></label><button type="button" disabled={busy || !appId.trim()} onClick={() => void register()}><ShieldCheck size={14} aria-hidden="true" />登记飞书 Bot</button></div>
    <div className="channel-account-list">{accounts.length === 0 ? <p className="settings-loading">尚未登记 IM 账号</p> : accounts.map((account) => <div className="channel-account-row" key={`${account.platform}:${account.id}`}><div className="channel-account-copy"><MessageCircle size={15} aria-hidden="true" /><strong>{account.platform === "feishu" ? "飞书 Bot" : "微信 Binding"}</strong><span>{account.id.slice(0, 8)} · {account.status} · {account.connected ? "已连接" : "未连接"}{account.platform === "feishu" && <> · App {account.app_id_masked || "已登记"}</>}</span></div><div className="channel-account-controls">{account.platform === "weixin" && ["expired", "revoked"].includes(account.status) && <button type="button" disabled={busy} onClick={() => void rescan(account)}>重新扫码</button>}<button type="button" disabled={busy || account.status === "revoked" || (account.platform === "weixin" && account.status === "expired")} onClick={() => void toggle(account)}>{account.enabled ? "停用" : "启用"}</button><button type="button" disabled={busy || account.status === "revoked"} onClick={() => void setSubscription(account)}>{account.subscribed ? "取消订阅" : "订阅通知"}</button><button type="button" disabled={busy || account.status === "revoked"} onClick={() => void revoke(account)}>撤销账号</button>{account.platform === "feishu" && <><button type="button" disabled={busy || account.status === "revoked"} onClick={() => { setRotationId(account.id); setRotationAppId(""); setRotationDomain("open.feishu.cn"); }}>轮换凭据</button><input aria-label="Owner 验证码" ref={(element) => { ownerCodeRefs.current[account.id] = element; }} inputMode="numeric" maxLength={6} autoComplete="one-time-code" /><button type="button" disabled={busy || account.status === "revoked"} onClick={() => void setOwnerCode(account)}>设置 Owner 验证码</button></>}</div>{isPendingWeixin(account) && <WeixinQr account={account} qr={weixinQrs[account.id]} />}{rotationId === account.id && <div className="channel-feishu-form channel-rotation-form"><label>新 App ID<input aria-label="新 App ID" value={rotationAppId} onChange={(event) => setRotationAppId(event.target.value)} autoComplete="off" /></label><label>新 App Secret<input aria-label="新 App Secret" ref={rotationSecretRef} type="password" autoComplete="new-password" /></label><label>新域<input aria-label="新域" value={rotationDomain} onChange={(event) => setRotationDomain(event.target.value)} /></label><button type="button" disabled={busy || !rotationAppId.trim()} onClick={() => void rotate(account)}>提交轮换</button></div>}</div>)}</div>
  </section>;
}

function WeixinQr({ account, qr }: { account: ChannelAccountView; qr?: WeixinQrDisplay }) {
  return <div className="channel-weixin-qr" aria-live="polite">
    {qr ? <img className="channel-weixin-qr-image" src={qr.imageSrc} alt="微信登录二维码" /> : <div className="channel-weixin-qr-placeholder">正在生成二维码…</div>}
    <div className="channel-weixin-qr-copy"><strong>请使用微信扫码</strong><span>扫码完成或二维码过期后会自动清除。</span><span className="channel-weixin-qr-binding">Binding {account.id.slice(0, 8)}</span></div>
  </div>;
}

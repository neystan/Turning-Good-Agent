import { useCallback, useEffect, useRef, useState } from "react";
import { MessageCircle, Plus, RefreshCw, ShieldCheck } from "lucide-react";

import { channelControlApi, type ChannelAccountView } from "../channel_control_api";

export function ChannelAccountSettings() {
  const [accounts, setAccounts] = useState<ChannelAccountView[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [appId, setAppId] = useState("");
  const [domain, setDomain] = useState("open.feishu.cn");
  const [rotationId, setRotationId] = useState<string | null>(null);
  const [rotationAppId, setRotationAppId] = useState("");
  const [rotationDomain, setRotationDomain] = useState("open.feishu.cn");
  const [busy, setBusy] = useState(false);
  const appSecretRef = useRef<HTMLInputElement>(null);
  const ownerCodeRefs = useRef<Record<string, HTMLInputElement | null>>({});
  const rotationSecretRef = useRef<HTMLInputElement>(null);

  const load = useCallback(() => {
    void channelControlApi.list().then((result) => setAccounts(result.accounts)).catch(() => setError("账号状态暂不可用"));
  }, []);

  useEffect(() => { load(); }, [load]);

  const replaceAccount = (next: ChannelAccountView) => {
    setAccounts((current) => current.map((item) => item.id === next.id && item.platform === next.platform ? next : item));
  };

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
    try { const account = await channelControlApi.inviteWeixin(principal); setAccounts((current) => [...current, account]); }
    catch { setError("创建微信 Binding 失败"); }
    finally { setBusy(false); }
  };

  const register = async () => {
    const secret = appSecretRef.current?.value || "";
    setBusy(true); setError(null);
    try {
      const account = await channelControlApi.registerFeishu({ app_id: appId.trim(), app_secret: secret, domain: domain.trim() });
      setAccounts((current) => [...current, account]);
    } catch { setError("登记飞书 Bot 失败"); }
    finally { clearRegistration(); setBusy(false); }
  };

  const toggle = async (account: ChannelAccountView) => {
    setBusy(true); setError(null);
    try {
      const next = account.enabled ? await channelControlApi.disable(account.platform, account.id) : await channelControlApi.enable(account.platform, account.id);
      replaceAccount(next);
    } catch { setError("更新账号状态失败"); }
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
    <div className="channel-account-heading"><div><h3>IM Channels</h3><p>仅显示脱敏状态；二维码、凭据和平台用户标识不会进入浏览器。</p></div><button className="settings-icon-button" type="button" onClick={load} aria-label="刷新 IM 账号"><RefreshCw size={15} aria-hidden="true" /></button></div>
    {error && <p className="settings-field-error" role="alert">{error}</p>}
    <div className="channel-account-actions"><button type="button" disabled={busy} onClick={() => void invite("owner")}><Plus size={14} aria-hidden="true" />微信 Owner Binding</button><button type="button" disabled={busy} onClick={() => void invite("new")}><Plus size={14} aria-hidden="true" />微信新主体</button></div>
    <div className="channel-feishu-form"><label>飞书 App ID<input aria-label="飞书 App ID" value={appId} onChange={(event) => setAppId(event.target.value)} autoComplete="off" /></label><label>飞书 App Secret<input aria-label="飞书 App Secret" ref={appSecretRef} type="password" autoComplete="new-password" /></label><label>域<input value={domain} onChange={(event) => setDomain(event.target.value)} /></label><button type="button" disabled={busy || !appId.trim()} onClick={() => void register()}><ShieldCheck size={14} aria-hidden="true" />登记飞书 Bot</button></div>
    <div className="channel-account-list">{accounts.length === 0 ? <p className="settings-loading">尚未登记 IM 账号</p> : accounts.map((account) => <div className="channel-account-row" key={`${account.platform}:${account.id}`}><div className="channel-account-copy"><MessageCircle size={15} aria-hidden="true" /><strong>{account.platform === "feishu" ? "飞书 Bot" : "微信 Binding"}</strong><span>{account.id.slice(0, 8)} · {account.status} · {account.connected ? "已连接" : "未连接"}{account.platform === "feishu" && <> · App {account.app_id_masked || "已登记"} · CardKit {account.cardkit_enabled ? "已启用" : "未启用"}</>}</span></div><div className="channel-account-controls"><button type="button" disabled={busy || account.status === "revoked"} onClick={() => void toggle(account)}>{account.enabled ? "停用" : "启用"}</button><button type="button" disabled={busy || account.status === "revoked"} onClick={() => void setSubscription(account)}>{account.subscribed ? "取消订阅" : "订阅通知"}</button><button type="button" disabled={busy || account.status === "revoked"} onClick={() => void revoke(account)}>撤销账号</button>{account.platform === "feishu" && <><button type="button" disabled={busy || account.status === "revoked"} onClick={() => { setRotationId(account.id); setRotationAppId(""); setRotationDomain("open.feishu.cn"); }}>轮换凭据</button><input aria-label="Owner 验证码" ref={(element) => { ownerCodeRefs.current[account.id] = element; }} inputMode="numeric" maxLength={6} autoComplete="one-time-code" /><button type="button" disabled={busy || account.status === "revoked"} onClick={() => void setOwnerCode(account)}>设置 Owner 验证码</button></>}</div>{rotationId === account.id && <div className="channel-feishu-form channel-rotation-form"><label>新 App ID<input aria-label="新 App ID" value={rotationAppId} onChange={(event) => setRotationAppId(event.target.value)} autoComplete="off" /></label><label>新 App Secret<input aria-label="新 App Secret" ref={rotationSecretRef} type="password" autoComplete="new-password" /></label><label>新域<input aria-label="新域" value={rotationDomain} onChange={(event) => setRotationDomain(event.target.value)} /></label><button type="button" disabled={busy || !rotationAppId.trim()} onClick={() => void rotate(account)}>提交轮换</button></div>}</div>)}</div>
  </section>;
}

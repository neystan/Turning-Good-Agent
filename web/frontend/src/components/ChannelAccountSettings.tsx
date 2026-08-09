import { useCallback, useEffect, useRef, useState } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import { MessageCircle, Plus, RefreshCw, ShieldCheck } from "lucide-react";
import QRCode from "qrcode";

import { ApiError, channelControlApi, type ChannelAccountView } from "../channel_control_api";

const WEIXIN_QR_POLL_INTERVAL_MS = 2_000;
const BUSY_DELETE_MESSAGE = "该 Binding 正在处理消息或等待回复投递，暂时不能删除。请等待本轮完成后再试";

type WeixinQrDisplay = {
  source: string;
  imageSrc: string;
  expiresAt: number | null;
};

function isInitialWeixinQr(account: ChannelAccountView): boolean {
  return account.platform === "weixin" && account.status === "pending_qr";
}

function accountRoleLabel(account: ChannelAccountView): string {
  if (account.platform === "feishu") return "飞书 Bot";
  return account.principal_kind === "owner" ? "我的微信" : "微信独立用户";
}

function accountStatusLabel(account: ChannelAccountView): string {
  if (account.status === "active") return account.connected ? "已连接" : "未连接";
  if (account.status === "awaiting_first_dm") return "待首条私聊";
  if (account.status === "disabled") return "已停用";
  if (account.status === "pending_qr") return "等待扫码";
  if (account.status === "expired") return "凭据过期";
  if (account.status === "revoked") return "已撤销";
  if (account.status === "failed") return "连接失败";
  return account.status;
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
  const [scanningBindings, setScanningBindings] = useState<Set<string>>(() => new Set());
  const [error, setError] = useState<string | null>(null);
  const [appId, setAppId] = useState("");
  const [domain, setDomain] = useState("open.feishu.cn");
  const [rotationId, setRotationId] = useState<string | null>(null);
  const [rotationAppId, setRotationAppId] = useState("");
  const [rotationDomain, setRotationDomain] = useState("open.feishu.cn");
  const [busy, setBusy] = useState(false);
  const [deleteTarget, setDeleteTarget] = useState<ChannelAccountView | null>(null);
  const [deletePending, setDeletePending] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const accountsRef = useRef<ChannelAccountView[]>([]);
  const scanningRef = useRef<Set<string>>(new Set());
  const qrRequestsRef = useRef(new Set<string>());
  const appSecretRef = useRef<HTMLInputElement>(null);
  const ownerCodeRefs = useRef<Record<string, HTMLInputElement | null>>({});
  const rotationSecretRef = useRef<HTMLInputElement>(null);

  const commitAccounts = useCallback((next: ChannelAccountView[]) => {
    accountsRef.current = next;
    setAccounts(next);
    const ids = new Set(next.filter((account) => account.platform === "weixin").map((account) => account.id));
    const initial = next.filter(isInitialWeixinQr).map((account) => account.id);
    const scanning = new Set([...scanningRef.current].filter((bindingId) => ids.has(bindingId)));
    initial.forEach((bindingId) => scanning.add(bindingId));
    scanningRef.current = scanning;
    setScanningBindings(new Set(scanning));
    setWeixinQrs((current) => Object.fromEntries(Object.entries(current).filter(([bindingId]) => ids.has(bindingId))));
  }, []);

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
    commitAccounts(index < 0 ? [...current, next] : current.map((item) => item.id === next.id && item.platform === next.platform ? next : item));
  }, [commitAccounts]);

  const beginQrPolling = useCallback((bindingId: string) => {
    const next = new Set(scanningRef.current);
    next.add(bindingId);
    scanningRef.current = next;
    setScanningBindings(new Set(next));
  }, []);

  const stopQrPolling = useCallback((bindingId: string) => {
    const next = new Set(scanningRef.current);
    next.delete(bindingId);
    scanningRef.current = next;
    setScanningBindings(new Set(next));
    setWeixinQrs((current) => {
      if (!(bindingId in current)) return current;
      const copy = { ...current };
      delete copy[bindingId];
      return copy;
    });
  }, []);

  const clearWeixinQr = useCallback((bindingId: string) => {
    setWeixinQrs((current) => {
      if (!(bindingId in current)) return current;
      const copy = { ...current };
      delete copy[bindingId];
      return copy;
    });
  }, []);

  const refreshWeixinQr = useCallback(async (bindingId: string) => {
    if (!scanningRef.current.has(bindingId) || qrRequestsRef.current.has(bindingId)) return;
    qrRequestsRef.current.add(bindingId);
    try {
      const qr = await channelControlApi.getWeixinQr(bindingId);
      if (!scanningRef.current.has(bindingId) || qr.binding_id !== bindingId) return;
      if (qr.status !== "pending_qr" || !qr.qr_content) {
        if (qr.status !== "pending_qr") {
          stopQrPolling(bindingId);
          void load();
        }
        return;
      }
      const imageSrc = await toImageSource(qr.qr_content);
      if (!scanningRef.current.has(bindingId)) return;
      setWeixinQrs((current) => ({
        ...current,
        [bindingId]: { source: qr.qr_content!, imageSrc, expiresAt: qr.expires_at },
      }));
    } catch {
      // 短时二维码服务不可用时保留扫码面板，下一次轮询恢复即可。
    } finally {
      qrRequestsRef.current.delete(bindingId);
    }
  }, [load, stopQrPolling]);

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    if (scanningBindings.size === 0) return;
    const refresh = () => scanningRef.current.forEach((bindingId) => { void refreshWeixinQr(bindingId); });
    refresh();
    const intervalId = window.setInterval(refresh, WEIXIN_QR_POLL_INTERVAL_MS);
    return () => window.clearInterval(intervalId);
  }, [refreshWeixinQr, scanningBindings]);

  useEffect(() => {
    const timeoutIds = Object.entries(weixinQrs).flatMap(([bindingId, display]) => {
      if (display.expiresAt === null) return [];
      // 二维码过期不代表候选登录终止；Transport 会获取新二维码，继续轮询即可。
      return [window.setTimeout(() => clearWeixinQr(bindingId), Math.max(0, display.expiresAt * 1_000 - Date.now()))];
    });
    return () => timeoutIds.forEach((timeoutId) => window.clearTimeout(timeoutId));
  }, [clearWeixinQr, weixinQrs]);

  const clearRegistration = () => {
    setAppId("");
    setDomain("open.feishu.cn");
    if (appSecretRef.current) appSecretRef.current.value = "";
  };

  const clearRotation = () => {
    setRotationId(null);
    setRotationAppId("");
    setRotationDomain("open.feishu.cn");
    if (rotationSecretRef.current) rotationSecretRef.current.value = "";
  };

  const invite = async (principal: "owner" | "new") => {
    setBusy(true); setError(null);
    try {
      const account = await channelControlApi.inviteWeixin(principal);
      replaceAccount(account);
      beginQrPolling(account.id);
    } catch { setError("创建微信 Binding 失败"); }
    finally { setBusy(false); }
  };

  const register = async () => {
    const secret = appSecretRef.current?.value || "";
    setBusy(true); setError(null);
    try { replaceAccount(await channelControlApi.registerFeishu({ app_id: appId.trim(), app_secret: secret, domain: domain.trim() })); }
    catch { setError("登记飞书 Bot 失败"); }
    finally { clearRegistration(); setBusy(false); }
  };

  const toggle = async (account: ChannelAccountView) => {
    setBusy(true); setError(null);
    try { replaceAccount(account.enabled ? await channelControlApi.disable(account.platform, account.id) : await channelControlApi.enable(account.platform, account.id)); }
    catch { setError("更新账号状态失败"); }
    finally { setBusy(false); }
  };

  const rescan = async (account: ChannelAccountView) => {
    setBusy(true); setError(null); beginQrPolling(account.id);
    try { replaceAccount(await channelControlApi.rescanWeixin(account.id)); }
    catch { stopQrPolling(account.id); setError("重新扫码失败"); }
    finally { setBusy(false); }
  };

  const setSubscription = async (account: ChannelAccountView) => {
    setBusy(true); setError(null);
    try { replaceAccount(account.subscribed ? await channelControlApi.unsubscribe(account.platform, account.id) : await channelControlApi.subscribe(account.platform, account.id)); }
    catch { setError("更新通知订阅失败"); }
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

  const deleteBinding = async () => {
    const account = deleteTarget;
    if (!account || deletePending) return;
    setDeletePending(true); setDeleteError(null);
    try {
      await channelControlApi.delete(account.platform, account.id);
      commitAccounts(accountsRef.current.filter((item) => !(item.platform === account.platform && item.id === account.id)));
      stopQrPolling(account.id);
      setDeleteTarget(null);
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 409) {
        setDeleteError(requestError.message || BUSY_DELETE_MESSAGE);
      } else {
        setDeleteError("永久删除失败");
      }
    } finally { setDeletePending(false); }
  };

  const ownerWeixinExists = accounts.some((account) => account.platform === "weixin" && account.principal_kind === "owner");

  return <>
    <section className="settings-group channel-account-settings" aria-label="IM Channels">
      <div className="channel-account-heading"><div><h3>IM Channels</h3><p>二维码仅在本机控制台短时显示；凭据和平台用户标识不会进入浏览器。</p></div><button className="settings-icon-button" type="button" onClick={() => void load()} aria-label="刷新 IM 账号"><RefreshCw size={15} aria-hidden="true" /></button></div>
      {error && <p className="settings-field-error" role="alert">{error}</p>}
      <div className="channel-account-actions"><button type="button" disabled={busy || ownerWeixinExists} onClick={() => void invite("owner")}><Plus size={14} aria-hidden="true" />我的微信</button><button type="button" disabled={busy} onClick={() => void invite("new")}><Plus size={14} aria-hidden="true" />邀请独立用户</button></div>
      <p className="channel-account-action-help">我的微信共享 Owner 的画像和主动状态（聊天仍按 Channel 隔离）；独立用户与单一 Binding 一一对应，拥有私有聊天、画像和主动状态。</p>
      {ownerWeixinExists && <p className="channel-account-action-help">已有我的微信 Binding；请重新扫码或永久删除现有 Binding 后再创建。</p>}
      <div className="channel-feishu-form"><label>飞书 App ID<input aria-label="飞书 App ID" value={appId} onChange={(event) => setAppId(event.target.value)} autoComplete="off" /></label><label>飞书 App Secret<input aria-label="飞书 App Secret" ref={appSecretRef} type="password" autoComplete="new-password" /></label><label>域<input value={domain} onChange={(event) => setDomain(event.target.value)} /></label><button type="button" disabled={busy || !appId.trim()} onClick={() => void register()}><ShieldCheck size={14} aria-hidden="true" />登记飞书 Bot</button></div>
      <div className="channel-account-list">{accounts.length === 0 ? <p className="settings-loading">尚未登记 IM 账号</p> : accounts.map((account) => <div className="channel-account-row" key={`${account.platform}:${account.id}`}><div className="channel-account-copy"><MessageCircle size={15} aria-hidden="true" /><strong>{accountRoleLabel(account)}</strong><span>{account.id.slice(0, 8)} · {accountStatusLabel(account)}{account.platform === "feishu" && <> · App {account.app_id_masked || "已登记"}</>}</span></div><div className="channel-account-controls">{account.platform === "weixin" && ["active", "expired", "revoked"].includes(account.status) && <button type="button" disabled={busy || scanningBindings.has(account.id)} onClick={() => void rescan(account)}>重新扫码</button>}<button type="button" disabled={busy || account.status === "revoked" || (account.platform === "weixin" && account.status === "expired")} onClick={() => void toggle(account)}>{account.enabled ? "停用并保留" : "启用"}</button><button type="button" disabled={busy || account.status === "revoked"} onClick={() => void setSubscription(account)}>{account.subscribed ? "取消订阅" : "订阅通知"}</button><button className="channel-delete-trigger" type="button" disabled={busy || deletePending} onClick={() => { setDeleteError(null); setDeleteTarget(account); }}>永久删除</button>{account.platform === "feishu" && <><button type="button" disabled={busy || account.status === "revoked"} onClick={() => { setRotationId(account.id); setRotationAppId(""); setRotationDomain("open.feishu.cn"); }}>轮换凭据</button><input aria-label="Owner 验证码" ref={(element) => { ownerCodeRefs.current[account.id] = element; }} inputMode="numeric" maxLength={6} autoComplete="one-time-code" /><button type="button" disabled={busy || account.status === "revoked"} onClick={() => void setOwnerCode(account)}>设置 Owner 验证码</button></>}</div>{scanningBindings.has(account.id) && <WeixinQr account={account} qr={weixinQrs[account.id]} />}{account.platform === "weixin" && account.status === "awaiting_first_dm" && <p className="channel-rebind-state" role="status">新使用者请发送第一条有效私聊，以完成 Binding 归属锁定。</p>}{rotationId === account.id && <div className="channel-feishu-form channel-rotation-form"><label>新 App ID<input aria-label="新 App ID" value={rotationAppId} onChange={(event) => setRotationAppId(event.target.value)} autoComplete="off" /></label><label>新 App Secret<input aria-label="新 App Secret" ref={rotationSecretRef} type="password" autoComplete="new-password" /></label><label>新域<input aria-label="新域" value={rotationDomain} onChange={(event) => setRotationDomain(event.target.value)} /></label><button type="button" disabled={busy || !rotationAppId.trim()} onClick={() => void rotate(account)}>提交轮换</button></div>}</div>)}</div>
    </section>
    <BindingDeleteDialog account={deleteTarget} pending={deletePending} error={deleteError} onOpenChange={(open) => { if (!open && !deletePending) setDeleteTarget(null); }} onConfirm={() => void deleteBinding()} />
  </>;
}

function BindingDeleteDialog({ account, pending, error, onOpenChange, onConfirm }: { account: ChannelAccountView | null; pending: boolean; error: string | null; onOpenChange: (open: boolean) => void; onConfirm: () => void }) {
  const description = account?.principal_kind === "independent"
    ? "将永久删除此独立主体 Binding，以及它的聊天、画像、主动状态和 Draft Skills。此操作无法恢复。"
    : "将永久删除此 Binding 的账号凭据和聊天记录；Owner 的共享画像与其他 Channel 数据会保留。此操作无法恢复。";
  return <AlertDialog.Root open={Boolean(account)} onOpenChange={(open) => { if (!pending) onOpenChange(open); }}><AlertDialog.Portal><AlertDialog.Overlay className="dialog-overlay" /><AlertDialog.Content className="confirm-dialog channel-delete-dialog"><AlertDialog.Title>永久删除 Binding？</AlertDialog.Title><AlertDialog.Description>{description}</AlertDialog.Description>{account && <p className="channel-delete-binding">{accountRoleLabel(account)} · {account.id.slice(0, 8)}</p>}{error && <p className="channel-delete-error" role="alert">{error}</p>}<div className="confirm-dialog-actions"><AlertDialog.Cancel asChild><button disabled={pending}>取消</button></AlertDialog.Cancel><button className="danger" type="button" disabled={pending} onClick={onConfirm}>{pending ? "正在删除" : "确认永久删除"}</button></div></AlertDialog.Content></AlertDialog.Portal></AlertDialog.Root>;
}

function WeixinQr({ account, qr }: { account: ChannelAccountView; qr?: WeixinQrDisplay }) {
  return <div className="channel-weixin-qr" aria-live="polite"><div>{qr ? <img className="channel-weixin-qr-image" src={qr.imageSrc} alt="微信登录二维码" /> : <div className="channel-weixin-qr-placeholder">正在生成二维码…</div>}</div><div className="channel-weixin-qr-copy"><strong>请使用微信扫码</strong><span>扫码失败或过期不会影响旧 Binding。</span><span className="channel-weixin-qr-binding">Binding {account.id.slice(0, 8)}</span></div></div>;
}

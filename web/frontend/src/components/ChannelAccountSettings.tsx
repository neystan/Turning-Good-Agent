import { useCallback, useEffect, useRef, useState } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import { MessageCircle, Plus, RefreshCw, ShieldCheck } from "lucide-react";
import QRCode from "qrcode";

import { ApiError, channelControlApi, type ChannelAccountView } from "../channel_control_api";

const WEIXIN_QR_POLL_INTERVAL_MS = 2_000;
const WEIXIN_REBIND_POLL_INTERVAL_MS = 1_500;
const WEIXIN_REBIND_MAX_POLLS = 60;
const BUSY_DELETE_MESSAGE = "该 Binding 正在处理消息或等待回复投递，暂时不能删除。请等待本轮完成后再试";

type WeixinQrDisplay = {
  source: string;
  imageSrc: string;
  expiresAt: number | null;
};

function isPendingWeixin(account: ChannelAccountView): boolean {
  return account.platform === "weixin" && (account.status === "pending_qr" || account.rebind_state === "pending_qr");
}

function isWaitingWeixin(account: ChannelAccountView): boolean {
  return account.platform === "weixin" && account.rebind_state === "waiting_for_idle";
}

function accountRoleLabel(account: ChannelAccountView): string {
  if (account.platform === "feishu") return `飞书 Bot · ${account.principal_kind === "owner" ? "Owner" : "独立主体"}`;
  return account.principal_kind === "owner"
    ? "微信 Owner Binding · Owner"
    : "微信独立主体 Binding · 独立主体";
}

function accountCacheKey(platform: ChannelAccountView["platform"], accountId: string): string {
  return `${platform}:${accountId}`;
}

function toImageSource(content: string): Promise<string> {
  const trimmed = content.trim();
  if (/^data:image\//i.test(trimmed)) return Promise.resolve(trimmed);
  const base64 = trimmed.replace(/\s/g, "");
  if (base64.startsWith("iVBORw0KGgo")) return Promise.resolve(`data:image/png;base64,${base64}`);
  if (base64.startsWith("R0lGOD")) return Promise.resolve(`data:image/gif;base64,${base64}`);
  return QRCode.toDataURL(trimmed, { errorCorrectionLevel: "M", margin: 1, width: 176 });
}

function isAbortError(error: unknown): boolean {
  return error instanceof Error && error.name === "AbortError";
}

export function ChannelAccountSettings() {
  const [accounts, setAccounts] = useState<ChannelAccountView[]>([]);
  const [accountsLoaded, setAccountsLoaded] = useState(false);
  const [weixinQrs, setWeixinQrs] = useState<Record<string, WeixinQrDisplay>>({});
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
  const [stoppedPollingIds, setStoppedPollingIds] = useState<Set<string>>(() => new Set());
  const accountsRef = useRef<ChannelAccountView[]>([]);
  const weixinQrsRef = useRef<Record<string, WeixinQrDisplay>>({});
  const lifecycleGenerationsRef = useRef<Record<string, number>>({});
  const qrControllersRef = useRef(new Map<string, AbortController>());
  const detailControllersRef = useRef(new Map<string, AbortController>());
  const detailTimersRef = useRef(new Map<string, number>());
  const detailPollCountsRef = useRef<Record<string, number>>({});
  const refreshWeixinDetailRef = useRef<(bindingId: string) => Promise<void>>(async () => {});
  const listControllerRef = useRef<AbortController | null>(null);
  const listGenerationRef = useRef(0);
  const deletedAccountKeysRef = useRef(new Set<string>());
  const mountedRef = useRef(true);
  const appSecretRef = useRef<HTMLInputElement>(null);
  const ownerCodeRefs = useRef<Record<string, HTMLInputElement | null>>({});
  const rotationSecretRef = useRef<HTMLInputElement>(null);

  const invalidateAccountList = useCallback(() => {
    listGenerationRef.current += 1;
    const controller = listControllerRef.current;
    listControllerRef.current = null;
    controller?.abort();
  }, []);

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

  const resetWeixinLifecycle = useCallback((bindingId: string) => {
    lifecycleGenerationsRef.current[bindingId] = (lifecycleGenerationsRef.current[bindingId] || 0) + 1;
    qrControllersRef.current.get(bindingId)?.abort();
    detailControllersRef.current.get(bindingId)?.abort();
    const detailTimer = detailTimersRef.current.get(bindingId);
    if (detailTimer !== undefined) window.clearTimeout(detailTimer);
    qrControllersRef.current.delete(bindingId);
    detailControllersRef.current.delete(bindingId);
    detailTimersRef.current.delete(bindingId);
    delete detailPollCountsRef.current[bindingId];
    setStoppedPollingIds((current) => {
      if (!current.has(bindingId)) return current;
      const next = new Set(current);
      next.delete(bindingId);
      return next;
    });
    clearWeixinQr(bindingId);
  }, [clearWeixinQr]);

  const stopWeixinPolling = useCallback((bindingId: string) => {
    lifecycleGenerationsRef.current[bindingId] = (lifecycleGenerationsRef.current[bindingId] || 0) + 1;
    qrControllersRef.current.get(bindingId)?.abort();
    detailControllersRef.current.get(bindingId)?.abort();
    const detailTimer = detailTimersRef.current.get(bindingId);
    if (detailTimer !== undefined) window.clearTimeout(detailTimer);
    qrControllersRef.current.delete(bindingId);
    detailControllersRef.current.delete(bindingId);
    detailTimersRef.current.delete(bindingId);
    delete detailPollCountsRef.current[bindingId];
    setStoppedPollingIds((current) => current.has(bindingId) ? current : new Set(current).add(bindingId));
    clearWeixinQr(bindingId);
  }, [clearWeixinQr]);

  const commitAccounts = useCallback((next: ChannelAccountView[]) => {
    const visible = next.filter((account) => !deletedAccountKeysRef.current.has(accountCacheKey(account.platform, account.id)));
    accountsRef.current = visible;
    setAccounts(visible);
    const pendingIds = new Set(visible.filter(isPendingWeixin).map((account) => account.id));
    const currentQrs = weixinQrsRef.current;
    const nextQrs = Object.fromEntries(Object.entries(currentQrs).filter(([bindingId]) => pendingIds.has(bindingId)));
    if (Object.keys(nextQrs).length !== Object.keys(currentQrs).length) commitWeixinQrs(nextQrs);
  }, [commitWeixinQrs]);

  const load = useCallback(async () => {
    const generation = ++listGenerationRef.current;
    listControllerRef.current?.abort();
    const controller = new AbortController();
    listControllerRef.current = controller;
    try {
      const result = await channelControlApi.list(controller.signal);
      if (!mountedRef.current || generation !== listGenerationRef.current) return;
      commitAccounts(result.accounts);
      setAccountsLoaded(true);
    } catch (requestError) {
      if (isAbortError(requestError) || generation !== listGenerationRef.current) return;
      setError("账号状态暂不可用");
    } finally {
      if (listControllerRef.current === controller) listControllerRef.current = null;
    }
  }, [commitAccounts]);

  const replaceAccount = useCallback((next: ChannelAccountView) => {
    const current = accountsRef.current;
    const index = current.findIndex((item) => item.id === next.id && item.platform === next.platform);
    commitAccounts(index >= 0 ? current.map((item) => item.id === next.id && item.platform === next.platform ? next : item) : [...current, next]);
  }, [commitAccounts]);

  const refreshWeixinQr = useCallback(async (bindingId: string) => {
    const account = accountsRef.current.find((item) => item.id === bindingId && item.platform === "weixin");
    if (!account || !isPendingWeixin(account) || stoppedPollingIds.has(bindingId) || qrControllersRef.current.has(bindingId)) {
      if (!account || !isPendingWeixin(account)) clearWeixinQr(bindingId);
      return;
    }
    const generation = lifecycleGenerationsRef.current[bindingId] || 0;
    const controller = new AbortController();
    qrControllersRef.current.set(bindingId, controller);
    try {
      const qr = await channelControlApi.getWeixinQr(bindingId, controller.signal);
      if (!mountedRef.current || generation !== (lifecycleGenerationsRef.current[bindingId] || 0)) return;
      const current = accountsRef.current.find((item) => item.id === bindingId && item.platform === "weixin");
      if (!current || !isPendingWeixin(current) || qr.binding_id !== bindingId) return;
      if (qr.status !== "pending_qr" && current.rebind_state !== "pending_qr") {
        clearWeixinQr(bindingId);
        void load();
        return;
      }
      if (!qr.qr_content) {
        clearWeixinQr(bindingId);
        void load();
        return;
      }
      const previous = weixinQrsRef.current[bindingId];
      if (previous?.source === qr.qr_content && previous.expiresAt === qr.expires_at) return;
      const imageSrc = await toImageSource(qr.qr_content);
      if (!mountedRef.current || generation !== (lifecycleGenerationsRef.current[bindingId] || 0)) return;
      const active = accountsRef.current.find((item) => item.id === bindingId && item.platform === "weixin");
      if (!active || !isPendingWeixin(active)) return;
      commitWeixinQrs({
        ...weixinQrsRef.current,
        [bindingId]: { source: qr.qr_content, imageSrc, expiresAt: qr.expires_at },
      });
    } catch (requestError) {
      if (isAbortError(requestError)) return;
      if (!mountedRef.current || generation !== (lifecycleGenerationsRef.current[bindingId] || 0)) return;
      const current = accountsRef.current.find((item) => item.id === bindingId && item.platform === "weixin");
      if (!current || !isPendingWeixin(current)) return;
      stopWeixinPolling(bindingId);
      setError("微信二维码状态暂不可用");
    } finally {
      if (qrControllersRef.current.get(bindingId) === controller) qrControllersRef.current.delete(bindingId);
    }
  }, [clearWeixinQr, commitWeixinQrs, load, stopWeixinPolling, stoppedPollingIds]);

  const refreshWeixinDetail = useCallback(async (bindingId: string) => {
    const account = accountsRef.current.find((item) => item.id === bindingId && item.platform === "weixin");
    if (!account || !isWaitingWeixin(account) || stoppedPollingIds.has(bindingId) || detailControllersRef.current.has(bindingId)) return;
    const count = detailPollCountsRef.current[bindingId] || 0;
    if (count >= WEIXIN_REBIND_MAX_POLLS) {
      stopWeixinPolling(bindingId);
      setError("候选凭据仍在等待空闲，请稍后手动刷新账号状态");
      return;
    }
    detailPollCountsRef.current[bindingId] = count + 1;
    const generation = lifecycleGenerationsRef.current[bindingId] || 0;
    const controller = new AbortController();
    detailControllersRef.current.set(bindingId, controller);
    try {
      const next = await channelControlApi.get("weixin", bindingId, controller.signal);
      if (!mountedRef.current || generation !== (lifecycleGenerationsRef.current[bindingId] || 0)) return;
      const current = accountsRef.current.find((item) => item.id === bindingId && item.platform === "weixin");
      if (!current || !isWaitingWeixin(current)) return;
      replaceAccount(next);
      if (!isWaitingWeixin(next)) {
        delete detailPollCountsRef.current[bindingId];
        clearWeixinQr(bindingId);
      }
    } catch (requestError) {
      if (isAbortError(requestError)) return;
      if (!mountedRef.current || generation !== (lifecycleGenerationsRef.current[bindingId] || 0)) return;
      const current = accountsRef.current.find((item) => item.id === bindingId && item.platform === "weixin");
      if (!current || !isWaitingWeixin(current)) return;
      stopWeixinPolling(bindingId);
      setError("微信 Binding 状态暂不可用");
    } finally {
      if (detailControllersRef.current.get(bindingId) === controller) detailControllersRef.current.delete(bindingId);
      const current = accountsRef.current.find((item) => item.id === bindingId && item.platform === "weixin");
      const shouldContinue = mountedRef.current
        && generation === (lifecycleGenerationsRef.current[bindingId] || 0)
        && current !== undefined
        && isWaitingWeixin(current);
      if (shouldContinue) {
        if ((detailPollCountsRef.current[bindingId] || 0) >= WEIXIN_REBIND_MAX_POLLS) {
          stopWeixinPolling(bindingId);
          setError("候选凭据仍在等待空闲，请稍后手动刷新账号状态");
        } else {
          const timerId = window.setTimeout(() => {
            if (detailTimersRef.current.get(bindingId) !== timerId) return;
            detailTimersRef.current.delete(bindingId);
            void refreshWeixinDetailRef.current(bindingId);
          }, WEIXIN_REBIND_POLL_INTERVAL_MS);
          detailTimersRef.current.set(bindingId, timerId);
        }
      }
    }
  }, [clearWeixinQr, replaceAccount, stopWeixinPolling, stoppedPollingIds]);
  refreshWeixinDetailRef.current = refreshWeixinDetail;

  useEffect(() => { void load(); }, [load]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      qrControllersRef.current.forEach((controller) => controller.abort());
      detailControllersRef.current.forEach((controller) => controller.abort());
      detailTimersRef.current.forEach((timerId) => window.clearTimeout(timerId));
      invalidateAccountList();
      qrControllersRef.current.clear();
      detailControllersRef.current.clear();
      detailTimersRef.current.clear();
      detailPollCountsRef.current = {};
      weixinQrsRef.current = {};
    };
  }, [invalidateAccountList]);

  useEffect(() => {
    const pendingBindings = accounts.filter((account) => isPendingWeixin(account) && !stoppedPollingIds.has(account.id)).map((account) => account.id);
    if (pendingBindings.length === 0) return;
    let disposed = false;
    const refresh = () => {
      if (disposed) return;
      pendingBindings.forEach((bindingId) => { void refreshWeixinQr(bindingId); });
    };
    refresh();
    const intervalId = window.setInterval(refresh, WEIXIN_QR_POLL_INTERVAL_MS);
    return () => { disposed = true; window.clearInterval(intervalId); };
  }, [accounts, refreshWeixinQr, stoppedPollingIds]);

  const waitingBindingKey = accounts
    .filter((account) => isWaitingWeixin(account) && !stoppedPollingIds.has(account.id))
    .map((account) => account.id)
    .sort()
    .join("\u0000");

  useEffect(() => {
    const waitingBindings = waitingBindingKey ? waitingBindingKey.split("\u0000") : [];
    const waitingSet = new Set(waitingBindings);
    detailTimersRef.current.forEach((timerId, bindingId) => {
      if (waitingSet.has(bindingId)) return;
      window.clearTimeout(timerId);
      detailTimersRef.current.delete(bindingId);
      delete detailPollCountsRef.current[bindingId];
    });
    detailControllersRef.current.forEach((controller, bindingId) => {
      if (waitingSet.has(bindingId)) return;
      controller.abort();
      detailControllersRef.current.delete(bindingId);
      delete detailPollCountsRef.current[bindingId];
    });
    waitingBindings.forEach((bindingId) => {
      if (!detailTimersRef.current.has(bindingId) && !detailControllersRef.current.has(bindingId)) void refreshWeixinDetail(bindingId);
    });
  }, [refreshWeixinDetail, waitingBindingKey]);

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
    setBusy(true); setError(null);
    invalidateAccountList();
    stopWeixinPolling(account.id);
    try {
      const next = await channelControlApi.rescanWeixin(account.id);
      invalidateAccountList();
      replaceAccount(next);
      resetWeixinLifecycle(account.id);
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
    catch { setError("停用并保留失败"); }
    finally { setBusy(false); }
  };

  const openDeleteDialog = (account: ChannelAccountView) => {
    setDeleteError(null);
    setDeleteTarget(account);
  };

  const deleteBinding = async () => {
    const account = deleteTarget;
    if (!account || deletePending) return;
    setDeletePending(true);
    setDeleteError(null);
    try {
      await channelControlApi.delete(account.platform, account.id);
      deletedAccountKeysRef.current.add(accountCacheKey(account.platform, account.id));
      invalidateAccountList();
      if (account.platform === "weixin") resetWeixinLifecycle(account.id);
      commitAccounts(accountsRef.current.filter((item) => item.id !== account.id || item.platform !== account.platform));
      if (rotationId === account.id) clearRotation();
      setDeleteTarget(null);
    } catch (requestError) {
      if (requestError instanceof ApiError && requestError.status === 409 && requestError.code === "binding_busy") {
        setDeleteError(BUSY_DELETE_MESSAGE);
      } else {
        setDeleteError(requestError instanceof Error ? requestError.message : "永久删除失败");
      }
    } finally {
      setDeletePending(false);
    }
  };

  const refreshAccounts = () => {
    accountsRef.current.filter((account) => account.platform === "weixin").forEach((account) => resetWeixinLifecycle(account.id));
    setError(null);
    void load();
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

  const ownerWeixinExists = accounts.some((account) => account.platform === "weixin" && account.principal_kind === "owner");

  return <>
    <section className="settings-group channel-account-settings" aria-label="IM Channels">
      <div className="channel-account-heading">
        <div><h3>IM Channels</h3><p>二维码仅在本机控制台短时显示；凭据和平台用户标识不会进入浏览器。</p></div>
        <button className="settings-icon-button" type="button" onClick={refreshAccounts} aria-label="刷新 IM 账号"><RefreshCw size={15} aria-hidden="true" /></button>
      </div>
      {error && <p className="settings-field-error" role="alert">{error}</p>}
      <div className="channel-account-actions">
        <button type="button" disabled={busy || !accountsLoaded || ownerWeixinExists} aria-describedby={ownerWeixinExists ? "owner-weixin-binding-help" : undefined} onClick={() => void invite("owner")}><Plus size={14} aria-hidden="true" />微信 Owner Binding</button>
        <button type="button" disabled={busy} onClick={() => void invite("new")}><Plus size={14} aria-hidden="true" />微信独立主体 Binding</button>
        {ownerWeixinExists && <span id="owner-weixin-binding-help" className="channel-account-action-help">已有 Owner Binding；请重新扫码或永久删除现有 Owner Binding 后再新建</span>}
      </div>
      <div className="channel-feishu-form">
        <label>飞书 App ID<input aria-label="飞书 App ID" value={appId} onChange={(event) => setAppId(event.target.value)} autoComplete="off" /></label>
        <label>飞书 App Secret<input aria-label="飞书 App Secret" ref={appSecretRef} type="password" autoComplete="new-password" /></label>
        <label>域<input value={domain} onChange={(event) => setDomain(event.target.value)} /></label>
        <button type="button" disabled={busy || !appId.trim()} onClick={() => void register()}><ShieldCheck size={14} aria-hidden="true" />登记飞书 Bot</button>
        <span className="channel-feishu-owner-help">飞书 Bot 始终属于 Owner</span>
      </div>
      <div className="channel-account-list">
        {accounts.length === 0 ? <p className="settings-loading">尚未登记 IM 账号</p> : accounts.map((account) => {
          const canRescan = account.platform === "weixin" && ["active", "expired", "revoked"].includes(account.status);
          return <div className="channel-account-row" key={`${account.platform}:${account.id}`}>
            <div className="channel-account-copy">
              <MessageCircle size={15} aria-hidden="true" />
              <strong>{accountRoleLabel(account)}</strong>
              <span>{account.id.slice(0, 8)} · {account.status} · {account.connected ? "已连接" : "未连接"}{account.platform === "feishu" && <> · App {account.app_id_masked || "已登记"}</>}</span>
            </div>
            <div className="channel-account-controls">
              {canRescan && <button type="button" disabled={busy} onClick={() => void rescan(account)}>{account.status === "active" ? "重新扫码并转移使用者" : "重新扫码"}</button>}
              <button type="button" disabled={busy || account.status === "revoked" || (account.platform === "weixin" && account.status === "expired")} onClick={() => void toggle(account)}>{account.enabled ? "停用" : "启用"}</button>
              <button type="button" disabled={busy || account.status === "revoked"} onClick={() => void setSubscription(account)}>{account.subscribed ? "取消订阅" : "订阅通知"}</button>
              <button type="button" disabled={busy || account.status === "revoked"} onClick={() => void revoke(account)}>停用并保留</button>
              <button className="channel-delete-trigger" type="button" disabled={busy || deletePending} onClick={() => openDeleteDialog(account)}>永久删除</button>
              {account.platform === "feishu" && <>
                <button type="button" disabled={busy || account.status === "revoked"} onClick={() => { setRotationId(account.id); setRotationAppId(""); setRotationDomain("open.feishu.cn"); }}>轮换凭据</button>
                <input aria-label="Owner 验证码" ref={(element) => { ownerCodeRefs.current[account.id] = element; }} inputMode="numeric" maxLength={6} autoComplete="one-time-code" />
                <button type="button" disabled={busy || account.status === "revoked"} onClick={() => void setOwnerCode(account)}>设置 Owner 验证码</button>
              </>}
            </div>
            {isPendingWeixin(account) && <WeixinQr account={account} qr={weixinQrs[account.id]} />}
            {isWaitingWeixin(account) && <p className="channel-rebind-state" role="status">候选凭据已就绪，正在等待当前回复结束</p>}
            {account.platform === "weixin" && account.status === "awaiting_first_dm" && <p className="channel-rebind-state" role="status">新使用者请发送第一条有效私聊，以完成 Binding 归属锁定。</p>}
            {rotationId === account.id && <div className="channel-feishu-form channel-rotation-form">
              <label>新 App ID<input aria-label="新 App ID" value={rotationAppId} onChange={(event) => setRotationAppId(event.target.value)} autoComplete="off" /></label>
              <label>新 App Secret<input aria-label="新 App Secret" ref={rotationSecretRef} type="password" autoComplete="new-password" /></label>
              <label>新域<input aria-label="新域" value={rotationDomain} onChange={(event) => setRotationDomain(event.target.value)} /></label>
              <button type="button" disabled={busy || !rotationAppId.trim()} onClick={() => void rotate(account)}>提交轮换</button>
            </div>}
          </div>;
        })}
      </div>
    </section>
    <BindingDeleteDialog account={deleteTarget} pending={deletePending} error={deleteError} onOpenChange={(open) => {
      if (!open && !deletePending) {
        setDeleteTarget(null);
        setDeleteError(null);
      }
    }} onConfirm={() => void deleteBinding()} />
  </>;
}

function BindingDeleteDialog({ account, pending, error, onOpenChange, onConfirm }: {
  account: ChannelAccountView | null;
  pending: boolean;
  error: string | null;
  onOpenChange: (open: boolean) => void;
  onConfirm: () => void;
}) {
  const description = account?.principal_kind === "independent"
    ? "将永久删除此独立主体 Binding，以及它的聊天、画像、主动状态和 Draft Skills。此操作无法恢复。"
    : "将永久删除此 Binding 的账号凭据和聊天记录；Owner 的共享画像与其他 Channel 数据会保留。此操作无法恢复。";
  return <AlertDialog.Root open={Boolean(account)} onOpenChange={(open) => { if (!pending) onOpenChange(open); }}>
    <AlertDialog.Portal>
      <AlertDialog.Overlay className="dialog-overlay" />
      <AlertDialog.Content className="confirm-dialog channel-delete-dialog">
        <AlertDialog.Title>永久删除 Binding？</AlertDialog.Title>
        <AlertDialog.Description>{description}</AlertDialog.Description>
        {account && <p className="channel-delete-binding">{accountRoleLabel(account)} · {account.id.slice(0, 8)}</p>}
        {error && <p className="channel-delete-error" role="alert">{error}</p>}
        <div className="dialog-actions">
          <AlertDialog.Cancel asChild><button disabled={pending}>取消</button></AlertDialog.Cancel>
          <button className="danger" type="button" disabled={pending} onClick={onConfirm}>{pending ? "正在删除" : "确认永久删除"}</button>
        </div>
      </AlertDialog.Content>
    </AlertDialog.Portal>
  </AlertDialog.Root>;
}

function WeixinQr({ account, qr }: { account: ChannelAccountView; qr?: WeixinQrDisplay }) {
  return <div className="channel-weixin-qr" aria-live="polite">
    {qr ? <img className="channel-weixin-qr-image" src={qr.imageSrc} alt="微信登录二维码" /> : <div className="channel-weixin-qr-placeholder">正在生成二维码…</div>}
    <div className="channel-weixin-qr-copy"><strong>请使用微信扫码</strong><span>扫码完成或二维码过期后会自动清除。</span><span className="channel-weixin-qr-binding">Binding {account.id.slice(0, 8)}</span></div>
  </div>;
}

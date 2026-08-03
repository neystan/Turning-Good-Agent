import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { Menu, Moon, PanelRight, Sun } from "lucide-react";

import { api } from "./api";
import { ChatTimeline } from "./components/ChatTimeline";
import { Composer } from "./components/Composer";
import { NoticeRegion } from "./components/NoticeRegion";
import { ProactiveWorkspace } from "./components/ProactiveWorkspace";
import { SessionInspector } from "./components/SessionInspector";
import { SessionSearchDialog } from "./components/SessionSearchDialog";
import { SessionSidebar } from "./components/SessionSidebar";
import { SettingsWorkspace } from "./components/SettingsWorkspace";
import { ActivityCluster } from "./components/ActivityCluster";
import { SessionHistoryLoader } from "./state/history_loader";
import { applySessionAction, createSessionState } from "./state/session_state";
import { SessionSocketClient } from "./state/socket_client";
import { ProactiveSocket } from "./state/proactive_socket";
import { readSessionCache, shouldWriteSessionCache, writeSessionCache } from "./state/session_cache";
import { createTextSegment, serializeComposerContent } from "./state/composer_segments";
import { proactiveRouteFromHash, routeDomain, routeDomainForWire } from "./proactive_types";
import type { ChatMessage, CommandEntry, ComposerSegment, ConnectionState, ContextWindow, Observability, Session, SessionContextReadModel, TaskEvent, ToolCallPage } from "./types";
import type { Notice } from "./components/NoticeRegion";
import type { ProactiveDomain, ProactiveNotice, ProactiveSnapshot, ProactiveState } from "./proactive_types";

type SocketMessage = Partial<TaskEvent> & { type: string; client_action_id?: string; message?: string; session_id?: string; request_id?: string };
type ProactiveHealth = { state: "idle" | "active" | "incident" | "readonly" | "unavailable"; label: string };

const emptyStateStarters = [
  "整理需求，生成可执行的任务清单",
  "分析一份文件，提取关键结论",
  "对现有方案进行风险检查",
];

/** 从浏览器路由读取当前已持久化会话。 */
function activeSessionId(): string | null {
  const match = window.location.pathname.match(/^\/sessions\/([^/]+)$/);
  return match ? decodeURIComponent(match[1]) : null;
}

/** 渲染重构后的本机 Web 工作台根组件。 */
export function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [archived, setArchived] = useState<Session[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(activeSessionId);
  const [hydratedSessionId, setHydratedSessionId] = useState<string | null>(null);
  const [sessionState, dispatch] = useReducer(applySessionAction, undefined, createSessionState);
  const [composerSegments, setComposerSegments] = useState<ComposerSegment[]>(() => [createTextSegment()]);
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [inspectorClosing, setInspectorClosing] = useState(false);
  const [inspector, setInspector] = useState<Observability | null>(null);
  const [controlInspection, setControlInspection] = useState<{ section: "context" | "tools"; context?: SessionContextReadModel; toolCalls?: ToolCallPage; error?: string } | null>(null);
  const [contextWindow, setContextWindow] = useState<ContextWindow | null>(null);
  const [autoApprove, setAutoApprove] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(() => window.location.hash === "#settings");
  const [proactiveDomain, setProactiveDomain] = useState<ProactiveDomain | null>(() => proactiveRouteFromHash());
  const [theme, setTheme] = useState<"dark" | "light">(() => localStorage.getItem("tga-theme") === "light" ? "light" : "dark");
  const [mobileMenu, setMobileMenu] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [notices, setNotices] = useState<Notice[]>([]);
  const [proactiveState, setProactiveState] = useState<ProactiveState>({ snapshots: {}, owner: null, connection: "connecting" });
  const [restoreFocusVersion, setRestoreFocusVersion] = useState(0);
  const socketRef = useRef<SessionSocketClient | null>(null);
  const proactiveSocketRef = useRef<ProactiveSocket | null>(null);
  const sessionIdRef = useRef<string | null>(sessionId);
  const historyLoader = useRef(new SessionHistoryLoader());
  const preserveNextSession = useRef(false);
  const eventHandler = useRef<(event: SocketMessage) => void>(() => undefined);
  const composerRef = useRef<HTMLElement>(null);
  const inspectorCloseTimer = useRef<number | null>(null);
  const messageActionIds = useRef(new Set<string>());
  const stateSessionId = useRef<string | null>(sessionId);
  const retryRequest = useRef<{ actionId: string; content: string } | null>(null);
  const contextRequestVersion = useRef(0);
  const controlInspectionRequestVersion = useRef(0);
  const eventCursors = useRef<Record<string, number>>({});

  /** 清除仅供本次查看的 Slash 读取结果，并使未完成请求失效。 */
  const clearControlInspection = useCallback(() => {
    controlInspectionRequestVersion.current += 1;
    setControlInspection(null);
  }, []);

  /** 重新读取侧栏会话分组。 */
  const refreshSessions = useCallback(async () => {
    const [active, archivedItems] = await Promise.all([api.listSessions(false), api.listSessions(true)]);
    setSessions(active);
    setArchived(archivedItems);
  }, []);

  /** 读取当前会话最近一次 SAVE 后的上下文窗口摘要。 */
  const refreshContextWindow = useCallback((id: string | null) => {
    const requestVersion = ++contextRequestVersion.current;
    if (!id) {
      setContextWindow(null);
      return;
    }
    void api.contextWindow(id).then((context) => {
      if (requestVersion === contextRequestVersion.current && sessionIdRef.current === id) setContextWindow(context);
    }).catch(() => {
      if (requestVersion === contextRequestVersion.current && sessionIdRef.current === id) setContextWindow(null);
    });
  }, []);

  /** 添加短暂可关闭的操作错误提示。 */
  const addNotice = useCallback((message: string) => {
    const notice = `${Date.now()}-${message}`;
    setNotices((items) => items.some((item) => item.message === message) ? items : [...items, { id: notice, message }]);
  }, []);

  /** 接受某个领域的完整快照；较旧领域 revision 不得覆盖当前页面。 */
  const receiveProactiveSnapshot = useCallback((snapshot: ProactiveSnapshot) => {
    const domain = routeDomainForWire(snapshot.domain);
    if (!domain) return;
    setProactiveState((state) => {
      const current = state.snapshots[domain];
      if (current && current.proactive_revision > snapshot.proactive_revision) return state;
      return {
        ...state,
        owner: snapshot.owner,
        snapshots: { ...state.snapshots, [domain]: snapshot },
      };
    });
  }, []);

  /** Web 主动通知仅保留在当前 App 内存中，不影响聊天消息。 */
  const receiveProactiveNotice = useCallback((notice: ProactiveNotice) => {
    setProactiveState((state) => ({ ...state, owner: notice.owner }));
    setNotices((items) => items.some((item) => item.id === notice.id) ? items : [...items, {
      id: notice.id,
      title: notice.title,
      message: notice.message,
      target: notice.target,
    }]);
  }, []);

  /** 删除指定提示。 */
  const dismissNotice = useCallback((noticeId: string) => {
    setNotices((items) => items.filter((item) => item.id !== noticeId));
  }, []);

  /** 切换路由与当前会话，不重建 WebSocket。 */
  const navigate = useCallback((id: string | null, preserve = false) => {
    preserveNextSession.current = preserve;
    window.history.pushState({}, "", id ? `/sessions/${encodeURIComponent(id)}` : "/");
    setSessionId(id);
    setSettingsOpen(false);
    setProactiveDomain(null);
    setMobileMenu(false);
  }, []);

  /** 打开可深链接的主动领域页面，不复用聊天会话连接。 */
  const openProactive = useCallback((domain: ProactiveDomain = "cron") => {
    const target = routeDomain(domain);
    if (window.location.hash !== target) window.location.hash = target.slice(1);
    setSettingsOpen(false);
    setProactiveDomain(domain);
    setMobileMenu(false);
  }, []);

  /** 从主动工作面回到当前会话路径。 */
  const returnToChat = useCallback(() => {
    window.history.replaceState({}, "", window.location.pathname);
    setSettingsOpen(false);
    setProactiveDomain(null);
  }, []);

  /** 处理 WebSocket 事件与动作确认。 */
  eventHandler.current = (event) => {
    if (event.session_id && typeof event.event_id === "number") {
      eventCursors.current[event.session_id] = Math.max(eventCursors.current[event.session_id] || 0, event.event_id);
    }
    if (event.type === "error") {
      const isMessageFailure = Boolean(event.client_action_id && messageActionIds.current.delete(event.client_action_id));
      if (event.client_action_id) dispatch({ type: "action.failed", actionId: event.client_action_id, message: event.message || "请求失败" });
      if (!isMessageFailure) addNotice(event.message || "请求失败");
      return;
    }
    if (event.type === "message.accepted" && event.client_action_id && event.session_id && event.request_id) {
      messageActionIds.current.delete(event.client_action_id);
      dispatch({ type: "action.accepted", actionId: event.client_action_id, sessionId: event.session_id, requestId: event.request_id });
      if (!sessionIdRef.current) navigate(event.session_id, true);
      void refreshSessions().catch((error: unknown) => addNotice(error instanceof Error ? error.message : "刷新会话失败"));
      return;
    }
    if (!event.session_id || event.session_id !== sessionIdRef.current) {
      void refreshSessions().catch((error: unknown) => addNotice(error instanceof Error ? error.message : "刷新会话失败"));
      return;
    }
    dispatch({ type: "event.received", event: event as TaskEvent });
    if (["task.completed", "task.failed", "task.cancelled"].includes(event.type)) {
      void refreshSessions().catch((error: unknown) => addNotice(error instanceof Error ? error.message : "刷新会话失败"));
      refreshContextWindow(event.session_id);
    }
  };

  useEffect(() => {
    void refreshSessions().catch((error: unknown) => addNotice(error instanceof Error ? error.message : "读取会话失败"));
    void api.uiSettings().then((item) => setAutoApprove(item.auto_approve_tools)).catch((error: unknown) => addNotice(error instanceof Error ? error.message : "读取权限设置失败"));
  }, [addNotice, refreshSessions]);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#121417" : "#f6f7f9");
    localStorage.setItem("tga-theme", theme);
  }, [theme]);

  useEffect(() => {
    const previousSessionId = stateSessionId.current;
    if (previousSessionId && previousSessionId !== sessionId) {
      writeSessionCache(previousSessionId, {
        messages: sessionState.messages,
        turns: sessionState.turns,
        hiddenMessageIds: sessionState.hiddenMessageIds,
        lastEventId: eventCursors.current[previousSessionId] || socketRef.current?.lastEventId(previousSessionId) || 0,
      });
    }
    stateSessionId.current = sessionId;
    sessionIdRef.current = sessionId;
    setHydratedSessionId(null);
    const preserve = preserveNextSession.current;
    preserveNextSession.current = false;
    if (!preserve) {
      const cached = sessionId ? readSessionCache(sessionId) : { messages: [], turns: {}, hiddenMessageIds: [], lastEventId: 0 };
      if (sessionId) {
        eventCursors.current[sessionId] = Math.max(eventCursors.current[sessionId] || 0, cached.lastEventId);
        socketRef.current?.setLastEventId(sessionId, eventCursors.current[sessionId]);
      }
      dispatch({
        type: "session.reset",
        messages: cached.messages,
        turns: cached.turns,
        hiddenMessageIds: cached.hiddenMessageIds,
      });
    }
    setHydratedSessionId(sessionId);
    socketRef.current?.setActiveSession(sessionId);
    if (inspectorCloseTimer.current) window.clearTimeout(inspectorCloseTimer.current);
    setInspectorOpen(false);
    setInspectorClosing(false);
    setInspector(null);
    clearControlInspection();
    setComposerSegments([createTextSegment()]);
    refreshContextWindow(sessionId);
    if (!sessionId) return undefined;
    void historyLoader.current.load(sessionId, (signal) => api.messages(sessionId, signal)).then((messages) => {
      if (messages) dispatch({ type: "history.loaded", messages });
    }).catch((error: unknown) => addNotice(error instanceof Error ? error.message : "读取会话历史失败"));
    return () => historyLoader.current.cancel();
  }, [addNotice, clearControlInspection, refreshContextWindow, sessionId]);

  useEffect(() => {
    /** 将消息关联、任务过程和已消费事件游标保留到浏览器标签页缓存。 */
    const currentSessionId = stateSessionId.current;
    if (currentSessionId && shouldWriteSessionCache(currentSessionId, hydratedSessionId)) {
      writeSessionCache(currentSessionId, {
        messages: sessionState.messages,
        turns: sessionState.turns,
        hiddenMessageIds: sessionState.hiddenMessageIds,
        lastEventId: eventCursors.current[currentSessionId] || socketRef.current?.lastEventId(currentSessionId) || 0,
      });
    }
  }, [hydratedSessionId, sessionState.hiddenMessageIds, sessionState.messages, sessionState.turns]);

  useEffect(() => {
    /** 恢复浏览器前进后退对应的会话路由。 */
    const restoreRoute = () => setSessionId(activeSessionId());
    window.addEventListener("popstate", restoreRoute);
    return () => window.removeEventListener("popstate", restoreRoute);
  }, []);

  useEffect(() => {
    const syncWorkspaceView = () => {
      setSettingsOpen(window.location.hash === "#settings");
      setProactiveDomain(proactiveRouteFromHash());
    };
    window.addEventListener("hashchange", syncWorkspaceView);
    return () => window.removeEventListener("hashchange", syncWorkspaceView);
  }, []);

  useEffect(() => {
    const socket = new SessionSocketClient({
      onEvent: (event) => eventHandler.current(event),
      onConnectionChange: (state) => {
        setConnection(state);
        if (state === "reconnecting") dispatch({ type: "connection.lost" });
      },
    });
    socketRef.current = socket;
    for (const [id, eventId] of Object.entries(eventCursors.current)) socket.setLastEventId(id, eventId);
    socket.setActiveSession(sessionIdRef.current);
    socket.connect();
    /** 在浏览器网络恢复时主动淘汰半失效连接。 */
    const reconnect = () => socket.reconnect();
    /** 断网时立即禁用依赖 WebSocket 的操作。 */
    const markOffline = () => setConnection("reconnecting");
    window.addEventListener("online", reconnect);
    window.addEventListener("offline", markOffline);
    return () => {
      window.removeEventListener("online", reconnect);
      window.removeEventListener("offline", markOffline);
      socket.close();
    };
  }, []);

  /** 主动状态使用独立 App 生命周期连接，与会话 WebSocket 完全分离。 */
  useEffect(() => {
    const socket = new ProactiveSocket({
      onSnapshot: receiveProactiveSnapshot,
      onNotice: receiveProactiveNotice,
      onConnection: (nextConnection) => setProactiveState((state) => ({ ...state, connection: nextConnection })),
    });
    proactiveSocketRef.current = socket;
    socket.connect();
    const reconnect = () => socket.reconnect();
    window.addEventListener("online", reconnect);
    return () => {
      window.removeEventListener("online", reconnect);
      socket.close();
      if (proactiveSocketRef.current === socket) proactiveSocketRef.current = null;
    };
  }, [receiveProactiveNotice, receiveProactiveSnapshot]);

  /** 仅在 WebSocket 已连接时发送普通消息或运行中 guidance。 */
  const send = useCallback((contentOverride?: string, retryActionId?: string) => {
    const hasComposerContent = composerSegments.some((segment) => segment.type === "guidance" || Boolean(segment.text));
    const activeSegments = hasComposerContent ? composerSegments : [createTextSegment(sessionState.pendingDraft)];
    const content = (contentOverride || serializeComposerContent(activeSegments)).trim();
    if (!content || !socketRef.current || connection !== "connected") return;
    const actionId = retryActionId || crypto.randomUUID();
    if (messageActionIds.current.has(actionId)) return;
    messageActionIds.current.add(actionId);
    if (retryActionId) {
      dispatch({ type: "action.retry", actionId });
    } else {
      dispatch({
        type: "message.optimistic",
        action: {
          id: actionId,
          kind: sessionState.running && sessionId ? "guidance" : "message",
          content,
          sessionId,
          createdAt: new Date().toISOString(),
        },
      });
    }
    const sent = socketRef.current.send({ type: "message.send", session_id: sessionId, content, client_action_id: actionId });
    if (!sent) {
      messageActionIds.current.delete(actionId);
      dispatch({ type: "action.failed", actionId, message: "连接尚未建立" });
      setComposerSegments([createTextSegment()]);
      dispatch({ type: "draft.pending", content: "" });
      return;
    }
    setComposerSegments([createTextSegment()]);
    dispatch({ type: "draft.pending", content: "" });
  }, [composerSegments, connection, sessionId, sessionState.pendingDraft, sessionState.running]);

  useEffect(() => {
    /** 显式重试只在连接重新建立后自动发送一次。 */
    if (connection !== "connected" || !retryRequest.current) return;
    const request = retryRequest.current;
    retryRequest.current = null;
    send(request.content, request.actionId);
  }, [connection, send]);

  /** 请求当前任务在下一个安全检查点停止。 */
  const stop = useCallback(() => {
    if (connection !== "connected") return;
    if (!sessionId || !socketRef.current?.send({ type: "task.stop", session_id: sessionId, client_action_id: crypto.randomUUID() })) addNotice("当前没有可停止的任务");
  }, [addNotice, connection, sessionId]);

  /** 更新立即生效的全局自动审批策略。 */
  const setApproval = async (enabled: boolean) => {
    try {
      const updated = await api.patchUiSettings(enabled);
      setAutoApprove(updated.auto_approve_tools);
    } catch (error) {
      addNotice(error instanceof Error ? error.message : "更新权限设置失败");
    }
  };

  /** 提交指定工具审批的允许或拒绝决定。 */
  const resolveApproval = useCallback((approvalId: string, approved: boolean) => {
    if (connection !== "connected") return;
    if (!sessionId || !socketRef.current?.send({ type: "approval.resolve", session_id: sessionId, approval_id: approvalId, approved, client_action_id: crypto.randomUUID() })) {
      addNotice("审批已失效");
    }
  }, [addNotice, connection, sessionId]);

  /** 打开并读取当前会话检查器。 */
  const openInspector = async () => {
    if (!sessionId) return;
    if (inspectorCloseTimer.current) window.clearTimeout(inspectorCloseTimer.current);
    setInspectorClosing(false);
    clearControlInspection();
    setInspector(null);
    setInspectorOpen(true);
    try {
      setInspector(await api.observability(sessionId));
    } catch (error) {
      setInspectorOpen(false);
      addNotice(error instanceof Error ? error.message : "读取会话检查器失败");
    }
  };

  /** 立即收起检查器布局列，并保留短暂的退出帧。 */
  const closeInspector = () => {
    if (inspectorCloseTimer.current) window.clearTimeout(inspectorCloseTimer.current);
    clearControlInspection();
    setInspectorOpen(false);
    setInspectorClosing(true);
    inspectorCloseTimer.current = window.setTimeout(() => {
      setInspectorClosing(false);
      inspectorCloseTimer.current = null;
    }, 260);
  };

  useEffect(() => () => {
    if (inspectorCloseTimer.current) window.clearTimeout(inspectorCloseTimer.current);
  }, []);

  /** 更新会话标题、置顶或归档状态。 */
  const updateSession = async (id: string, payload: Partial<Pick<Session, "title" | "pinned" | "archived">>) => {
    await api.patchSession(id, payload);
    await refreshSessions();
  };

  /** 删除经用户确认的非活动会话。 */
  const deleteSession = async (id: string) => {
    await api.deleteSession(id);
    if (id === sessionId) navigate(null);
    await refreshSessions();
  };

  /** 强制建立新连接后重发失败轮次，并保留原始动作标识。 */
  const retryMessage = (message: ChatMessage) => {
    const actionId = String(message.metadata.retry_action_id || message.client_action_id || "");
    const content = String(message.metadata.retry_content || message.content || "").trim();
    if (!actionId || !content || retryRequest.current?.actionId === actionId) return;
    dispatch({
      type: "action.retry",
      actionId,
      retry: { id: actionId, kind: "message", content, sessionId, createdAt: message.created_at, requestId: message.request_id },
    });
    retryRequest.current = { actionId, content };
    if (connection === "connected") {
      const request = retryRequest.current;
      retryRequest.current = null;
      send(request.content, request.actionId);
      return;
    }
    socketRef.current?.reconnect();
  };

  /** 同步输入文本并清空停止后保留的 guidance 草稿。 */
  const changeSegments = (segments: ComposerSegment[]) => {
    setComposerSegments(segments);
    dispatch({ type: "draft.pending", content: "" });
  };

  /** 将空状态建议填入输入区，保留用户继续编辑的空间。 */
  const useEmptyStateStarter = (starter: string) => {
    changeSegments([createTextSegment(starter)]);
    setRestoreFocusVersion((version) => version + 1);
  };

  const openSlashRead = (entry: CommandEntry) => {
    if (!sessionId) return;
    const requestVersion = ++controlInspectionRequestVersion.current;
    const inspectedSessionId = sessionId;
    setInspectorOpen(true);
    if (entry.action === "open_context") {
      setControlInspection({ section: "context" });
      void api.sessionContext(inspectedSessionId).then((context) => {
        if (requestVersion === controlInspectionRequestVersion.current && sessionIdRef.current === inspectedSessionId) setControlInspection({ section: "context", context });
      }).catch((error: unknown) => {
        if (requestVersion === controlInspectionRequestVersion.current && sessionIdRef.current === inspectedSessionId) setControlInspection({ section: "context", error: error instanceof Error ? error.message : "上下文读取失败" });
      });
      return;
    }
    setControlInspection({ section: "tools" });
    void api.toolCalls(inspectedSessionId).then((toolCalls) => {
      if (requestVersion === controlInspectionRequestVersion.current && sessionIdRef.current === inspectedSessionId) setControlInspection({ section: "tools", toolCalls });
    }).catch((error: unknown) => {
      if (requestVersion === controlInspectionRequestVersion.current && sessionIdRef.current === inspectedSessionId) setControlInspection({ section: "tools", error: error instanceof Error ? error.message : "工具记录读取失败" });
    });
  };

  const current = useMemo(() => [...sessions, ...archived].find((item) => item.id === sessionId), [archived, sessionId, sessions]);
  const currentTurnCount = Object.values(sessionState.turns).reduce((count, turn) => count + turn.events.length, 0);
  const proactiveHealth = proactiveHealthFor(proactiveState);

  /** 点击主动通知仅切换工作面，并从内存通知队列移除该项。 */
  const navigateProactiveNotice = useCallback((notice: Notice) => {
    dismissNotice(notice.id);
    const target = notice.target && !notice.target.startsWith("#") ? `#${notice.target}` : notice.target;
    const domain = target ? proactiveRouteFromHash(target) : null;
    if (domain) openProactive(domain);
  }, [dismissNotice, openProactive]);

  /** 恢复归档会话后请求 Composer 聚焦输入框。 */
  const restoreSession = async () => {
    if (!current) return;
    await updateSession(current.id, { archived: false });
    setRestoreFocusVersion((version) => version + 1);
  };

  const sidebar = <SessionSidebar active={sessions} archived={archived} currentId={sessionId} mobileOpen={mobileMenu} collapsed={sidebarCollapsed} onCollapseChange={setSidebarCollapsed} onCloseMobile={() => setMobileMenu(false)} onNew={() => navigate(null)} onOpenSearch={() => setSearchOpen(true)} onOpenSettings={() => { window.location.hash = "settings"; }} onOpenProactive={() => openProactive()} proactiveHealth={proactiveHealth} onSelect={navigate} onUpdate={updateSession} onDelete={deleteSession} onError={addNotice} />;

  if (settingsOpen) return <>
    <SettingsWorkspace onReturnToChat={returnToChat} />
    <NoticeRegion notices={notices} onDismiss={dismissNotice} onNavigate={navigateProactiveNotice} />
  </>;

  if (proactiveDomain) return <div className={`app-shell ${sidebarCollapsed ? "is-sidebar-collapsed" : ""}`}>
    <a className="skip-link" href="#main-content">跳到主动能力</a>
    {sidebar}
    {mobileMenu && <button className="scrim" aria-label="关闭会话栏" onClick={() => setMobileMenu(false)} />}
    <ProactiveWorkspace domain={proactiveDomain} snapshots={proactiveState.snapshots} owner={proactiveState.owner} connection={proactiveState.connection} onSelectDomain={openProactive} onReturnToChat={returnToChat} onSnapshot={receiveProactiveSnapshot} onOpenSession={(id) => navigate(id)} />
    <SessionSearchDialog open={searchOpen} sessions={[...sessions, ...archived]} currentId={sessionId} onClose={() => setSearchOpen(false)} onSelect={navigate} />
    <NoticeRegion notices={notices} onDismiss={dismissNotice} onNavigate={navigateProactiveNotice} />
  </div>;

  return <div className={`app-shell ${sidebarCollapsed ? "is-sidebar-collapsed" : ""}`}>
    <a className="skip-link" href="#main-content">跳到对话</a>
    {sidebar}
    {mobileMenu && <button className="scrim" aria-label="关闭会话栏" onClick={() => setMobileMenu(false)} />}
    <main id="main-content" className={`conversation ${inspectorOpen ? "is-inspector-open" : ""} ${inspectorClosing ? "is-inspector-closing" : ""}`}>
      <header className="topbar">
        <button className="icon-button mobile-only" aria-label="打开会话栏" onClick={() => setMobileMenu(true)}><Menu /></button>
        <div className="title-block"><span className={`connection-dot ${sessionState.running ? "is-running" : ""}`} aria-hidden="true" /><h1>{current?.title || "新建会话"}</h1><span className="connection-label">{connectionLabel(connection)}</span>{current?.archived && <span className="readonly">已归档</span>}</div>
        <div className="top-actions"><button className="icon-button" aria-label="切换主题" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>{theme === "dark" ? <Sun /> : <Moon />}</button><button className="icon-button" aria-label="打开会话检查器" disabled={!sessionId} onClick={() => void openInspector()}><PanelRight /></button></div>
      </header>
      <ChatTimeline sessionId={sessionId} messages={sessionState.messages} turns={sessionState.turns} contentVersion={currentTurnCount} composerRef={composerRef} retryEnabled={connection === "connected"} onRetry={retryMessage} renderTurn={(turn) => <ActivityCluster key={turn.requestId} turn={turn} actionsEnabled={connection === "connected"} onResolveApproval={resolveApproval} />}>
        {!sessionId && sessionState.messages.length === 0 && <div className="empty-state"><span className="empty-kicker">准备就绪</span><h2>开始一个工作会话</h2><p>描述目标、提供上下文，或直接粘贴内容。首条消息发送后会创建本地会话记录。</p><div className="empty-examples" aria-label="任务起步示例"><span>选择一个起点</span><ul>{emptyStateStarters.map((starter) => <li key={starter}><button type="button" onClick={() => useEmptyStateStarter(starter)}>{starter}</button></li>)}</ul></div></div>}
      </ChatTimeline>
      <Composer rootRef={composerRef} session={current} running={sessionState.running} actionsEnabled={connection === "connected"} segments={composerSegments.some((segment) => segment.type === "guidance" || Boolean(segment.text)) || !sessionState.pendingDraft ? composerSegments : [createTextSegment(sessionState.pendingDraft)]} autoApprove={autoApprove} contextWindow={contextWindow} restoreFocusVersion={restoreFocusVersion} onSegmentsChange={changeSegments} onSend={() => send()} onStop={stop} onRestore={restoreSession} onAutoApproveChange={(enabled) => void setApproval(enabled)} onSlashRead={openSlashRead} />
      <InspectorLayer open={inspectorOpen} closing={inspectorClosing} data={inspector} control={controlInspection} onClose={closeInspector} />
    </main>
    <SessionSearchDialog open={searchOpen} sessions={[...sessions, ...archived]} currentId={sessionId} onClose={() => setSearchOpen(false)} onSelect={navigate} />
    <NoticeRegion notices={notices} onDismiss={dismissNotice} onNavigate={navigateProactiveNotice} />
    </div>;
}

/** 渲染检查器视觉留白层，避免开关改变内容列。 */
function InspectorLayer({ open, closing, data, control, onClose }: { open: boolean; closing: boolean; data: Observability | null; control: { section: "context" | "tools"; context?: SessionContextReadModel; toolCalls?: ToolCallPage; error?: string } | null; onClose: () => void }) {
  const visible = open || closing;
  return <aside className={`inspector-layer ${open ? "is-open" : ""} ${closing ? "is-closing" : ""}`} aria-hidden={!visible}>{visible && <SessionInspector data={data} control={control} onClose={onClose} />}</aside>;
}

/** 将连接状态转换为紧凑的用户可见文本。 */
function connectionLabel(state: ConnectionState): string {
  return { connecting: "正在连接", connected: "已连接", reconnecting: "正在重连", disconnected: "已断开" }[state];
}

/** 将主动领域的独立快照折叠为侧栏可读的全局健康状态。 */
function proactiveHealthFor(state: ProactiveState): ProactiveHealth {
  if (state.connection !== "connected") return { state: "unavailable", label: "连接中" };
  if (!state.owner?.writable) {
    return state.owner?.owner_id
      ? { state: "readonly", label: "只读" }
      : { state: "unavailable", label: "已停用" };
  }
  const incidents = state.snapshots.incidents?.data.incidents;
  if (Array.isArray(incidents) && incidents.some((item) => Boolean(item) && typeof item === "object" && (item as { state?: unknown }).state === "open")) {
    return { state: "incident", label: "存在异常" };
  }
  const running = Object.values(state.snapshots).some((snapshot) => {
    if (!snapshot) return false;
    return snapshot.runtime.running || Object.values(snapshot.runtime.entity_states).some((status) => status === "queued" || status === "running");
  });
  return running ? { state: "active", label: "运行中" } : { state: "idle", label: "空闲" };
}

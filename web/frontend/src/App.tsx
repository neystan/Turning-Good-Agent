import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { Menu, Moon, PanelRight, Sun } from "lucide-react";

import { api } from "./api";
import { ChatTimeline } from "./components/ChatTimeline";
import { Composer } from "./components/Composer";
import { NoticeRegion } from "./components/NoticeRegion";
import { SessionInspector } from "./components/SessionInspector";
import { SessionSearchDialog } from "./components/SessionSearchDialog";
import { SessionSidebar } from "./components/SessionSidebar";
import { ActivityCluster } from "./components/ActivityCluster";
import { SessionHistoryLoader } from "./state/history_loader";
import { applySessionAction, createSessionState } from "./state/session_state";
import { SessionSocketClient } from "./state/socket_client";
import type { ChatMessage, ConnectionState, Observability, Session, TaskEvent } from "./types";

type SocketMessage = Partial<TaskEvent> & { type: string; client_action_id?: string; message?: string; session_id?: string; request_id?: string };

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
  const [sessionState, dispatch] = useReducer(applySessionAction, undefined, createSessionState);
  const [draft, setDraft] = useState("");
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [inspectorClosing, setInspectorClosing] = useState(false);
  const [inspector, setInspector] = useState<Observability | null>(null);
  const [autoApprove, setAutoApprove] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">(() => localStorage.getItem("tga-theme") === "light" ? "light" : "dark");
  const [mobileMenu, setMobileMenu] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [searchOpen, setSearchOpen] = useState(false);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [notices, setNotices] = useState<{ id: string; message: string }[]>([]);
  const [restoreFocusVersion, setRestoreFocusVersion] = useState(0);
  const socketRef = useRef<SessionSocketClient | null>(null);
  const sessionIdRef = useRef<string | null>(sessionId);
  const historyLoader = useRef(new SessionHistoryLoader());
  const preserveNextSession = useRef(false);
  const eventHandler = useRef<(event: SocketMessage) => void>(() => undefined);
  const composerRef = useRef<HTMLElement>(null);
  const inspectorCloseTimer = useRef<number | null>(null);
  const messageActionIds = useRef(new Set<string>());

  /** 重新读取侧栏会话分组。 */
  const refreshSessions = useCallback(async () => {
    const [active, archivedItems] = await Promise.all([api.listSessions(false), api.listSessions(true)]);
    setSessions(active);
    setArchived(archivedItems);
  }, []);

  /** 添加短暂可关闭的操作错误提示。 */
  const addNotice = useCallback((message: string) => {
    const notice = `${Date.now()}-${message}`;
    setNotices((items) => [...items, { id: notice, message }]);
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
    setMobileMenu(false);
  }, []);

  /** 处理 WebSocket 事件与动作确认。 */
  eventHandler.current = (event) => {
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
    sessionIdRef.current = sessionId;
    socketRef.current?.setActiveSession(sessionId);
    const preserve = preserveNextSession.current;
    preserveNextSession.current = false;
    if (!preserve) dispatch({ type: "session.reset" });
    setInspector(null);
    if (!sessionId) return undefined;
    void historyLoader.current.load(sessionId, (signal) => api.messages(sessionId, signal)).then((messages) => {
      if (messages) dispatch({ type: "history.loaded", messages });
    }).catch((error: unknown) => addNotice(error instanceof Error ? error.message : "读取会话历史失败"));
    return () => historyLoader.current.cancel();
  }, [addNotice, sessionId]);

  useEffect(() => {
    /** 恢复浏览器前进后退对应的会话路由。 */
    const restoreRoute = () => setSessionId(activeSessionId());
    window.addEventListener("popstate", restoreRoute);
    return () => window.removeEventListener("popstate", restoreRoute);
  }, []);

  useEffect(() => {
    const socket = new SessionSocketClient({
      onEvent: (event) => eventHandler.current(event),
      onConnectionChange: setConnection,
    });
    socketRef.current = socket;
    socket.setActiveSession(sessionIdRef.current);
    socket.connect();
    return () => socket.close();
  }, []);

  /** 发送普通消息或运行中 guidance，并保留未送达内容。 */
  const send = useCallback((contentOverride?: string) => {
    const content = (contentOverride || draft || sessionState.pendingDraft).trim();
    if (!content || !socketRef.current) return;
    const actionId = crypto.randomUUID();
    messageActionIds.current.add(actionId);
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
    const sent = socketRef.current.send({ type: "message.send", session_id: sessionId, content, client_action_id: actionId });
    if (!sent) {
      messageActionIds.current.delete(actionId);
      dispatch({ type: "action.failed", actionId, message: "连接尚未建立" });
      setDraft("");
      dispatch({ type: "draft.pending", content: "" });
      return;
    }
    setDraft("");
    dispatch({ type: "draft.pending", content: "" });
  }, [addNotice, draft, sessionId, sessionState.pendingDraft, sessionState.running]);

  /** 请求当前任务在下一个安全检查点停止。 */
  const stop = useCallback(() => {
    if (!sessionId || !socketRef.current?.send({ type: "task.stop", session_id: sessionId, client_action_id: crypto.randomUUID() })) addNotice("当前没有可停止的任务");
  }, [addNotice, sessionId]);

  /** 提交指定工具审批的允许或拒绝决定。 */
  const resolveApproval = useCallback((approvalId: string, approved: boolean) => {
    if (!sessionId || !socketRef.current?.send({ type: "approval.resolve", session_id: sessionId, approval_id: approvalId, approved, client_action_id: crypto.randomUUID() })) {
      addNotice("审批已失效");
    }
  }, [addNotice, sessionId]);

  /** 更新全局自动审批策略。 */
  const setApproval = async (enabled: boolean) => {
    try {
      const updated = await api.patchUiSettings(enabled);
      setAutoApprove(updated.auto_approve_tools);
    } catch (error) {
      addNotice(error instanceof Error ? error.message : "更新权限设置失败");
    }
  };

  /** 打开并读取当前会话检查器。 */
  const openInspector = async () => {
    if (!sessionId) return;
    try {
      if (inspectorCloseTimer.current) window.clearTimeout(inspectorCloseTimer.current);
      setInspectorClosing(false);
      setInspector(await api.observability(sessionId));
      setInspectorOpen(true);
    } catch (error) {
      addNotice(error instanceof Error ? error.message : "读取会话检查器失败");
    }
  };

  /** 立即收起检查器布局列，并保留短暂的退出帧。 */
  const closeInspector = () => {
    if (inspectorCloseTimer.current) window.clearTimeout(inspectorCloseTimer.current);
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

  /** 重发聊天流中未送达的用户消息。 */
  const retryMessage = (message: ChatMessage) => send(message.content);

  /** 同步输入文本并清空停止后保留的 guidance 草稿。 */
  const changeDraft = (value: string) => {
    setDraft(value);
    dispatch({ type: "draft.pending", content: "" });
  };

  const current = useMemo(() => [...sessions, ...archived].find((item) => item.id === sessionId), [archived, sessionId, sessions]);
  const currentTurnCount = Object.values(sessionState.turns).reduce((count, turn) => count + turn.events.length, 0);

  /** 恢复归档会话后请求 Composer 聚焦输入框。 */
  const restoreSession = async () => {
    if (!current) return;
    await updateSession(current.id, { archived: false });
    setRestoreFocusVersion((version) => version + 1);
  };

  return <div className={`app-shell ${sidebarCollapsed ? "is-sidebar-collapsed" : ""}`}>
    <a className="skip-link" href="#main-content">跳到对话</a>
    <SessionSidebar active={sessions} archived={archived} currentId={sessionId} mobileOpen={mobileMenu} collapsed={sidebarCollapsed} onCollapseChange={setSidebarCollapsed} onCloseMobile={() => setMobileMenu(false)} onNew={() => navigate(null)} onOpenSearch={() => setSearchOpen(true)} onSelect={navigate} onUpdate={updateSession} onDelete={deleteSession} onError={addNotice} />
    {mobileMenu && <button className="scrim" aria-label="关闭会话栏" onClick={() => setMobileMenu(false)} />}
    <main id="main-content" className={`conversation ${inspectorOpen ? "is-inspector-open" : ""} ${inspectorClosing ? "is-inspector-closing" : ""}`}>
      <header className="topbar">
        <button className="icon-button mobile-only" aria-label="打开会话栏" onClick={() => setMobileMenu(true)}><Menu /></button>
        <div className="title-block"><span className={`connection-dot ${sessionState.running ? "is-running" : ""}`} aria-hidden="true" /><h1>{current?.title || "新建会话"}</h1><span className="connection-label">{connectionLabel(connection)}</span>{current?.archived && <span className="readonly">已归档</span>}</div>
        <div className="top-actions"><button className="icon-button" aria-label="切换主题" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>{theme === "dark" ? <Sun /> : <Moon />}</button><button className="icon-button" aria-label="打开会话检查器" disabled={!sessionId} onClick={() => void openInspector()}><PanelRight /></button></div>
      </header>
      <ChatTimeline sessionId={sessionId} messages={sessionState.messages} turns={sessionState.turns} contentVersion={currentTurnCount} composerRef={composerRef} onRetry={retryMessage} renderTurn={(turn) => <ActivityCluster key={turn.requestId} turn={turn} onResolveApproval={resolveApproval} />}>
        {!sessionId && sessionState.messages.length === 0 && <div className="empty-state"><h2>开始一个工作会话</h2><p>首条消息发送后才会创建本地会话记录。</p></div>}
      </ChatTimeline>
      <Composer rootRef={composerRef} session={current} running={sessionState.running} draft={draft || sessionState.pendingDraft} autoApprove={autoApprove} restoreFocusVersion={restoreFocusVersion} onDraftChange={changeDraft} onSend={() => send()} onStop={stop} onRestore={restoreSession} onAutoApproveChange={(enabled) => void setApproval(enabled)} />
      <InspectorLayer open={inspectorOpen} closing={inspectorClosing} data={inspector} onClose={closeInspector} />
    </main>
    <SessionSearchDialog open={searchOpen} sessions={[...sessions, ...archived]} currentId={sessionId} onClose={() => setSearchOpen(false)} onSelect={navigate} />
    <NoticeRegion notices={notices} onDismiss={dismissNotice} />
    </div>;
}

/** 渲染检查器视觉留白层，避免开关改变内容列。 */
function InspectorLayer({ open, closing, data, onClose }: { open: boolean; closing: boolean; data: Observability | null; onClose: () => void }) {
  const visible = open || closing;
  return <aside className={`inspector-layer ${open ? "is-open" : ""} ${closing ? "is-closing" : ""}`} aria-hidden={!visible}>{visible && <SessionInspector data={data} onClose={onClose} />}</aside>;
}

/** 将连接状态转换为紧凑的用户可见文本。 */
function connectionLabel(state: ConnectionState): string {
  return { connecting: "正在连接", connected: "已连接", reconnecting: "正在重连", disconnected: "已断开" }[state];
}

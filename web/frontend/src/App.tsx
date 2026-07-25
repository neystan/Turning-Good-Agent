import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { ArchiveRestore, CircleStop, Menu, Moon, PanelRight, Send, Sun, X } from "lucide-react";

import { api } from "./api";
import { ChatTimeline } from "./components/ChatTimeline";
import { NoticeRegion } from "./components/NoticeRegion";
import { SessionSidebar } from "./components/SessionSidebar";
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
  const [inspector, setInspector] = useState<Observability | null>(null);
  const [autoApprove, setAutoApprove] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">(() => localStorage.getItem("tga-theme") === "light" ? "light" : "dark");
  const [mobileMenu, setMobileMenu] = useState(false);
  const [connection, setConnection] = useState<ConnectionState>("connecting");
  const [notices, setNotices] = useState<string[]>([]);
  const socketRef = useRef<SessionSocketClient | null>(null);
  const sessionIdRef = useRef<string | null>(sessionId);
  const historyLoader = useRef(new SessionHistoryLoader());
  const preserveNextSession = useRef(false);
  const eventHandler = useRef<(event: SocketMessage) => void>(() => undefined);

  /** 重新读取侧栏会话分组。 */
  const refreshSessions = useCallback(async () => {
    const [active, archivedItems] = await Promise.all([api.listSessions(false), api.listSessions(true)]);
    setSessions(active);
    setArchived(archivedItems);
  }, []);

  /** 添加短暂可关闭的操作错误提示。 */
  const addNotice = useCallback((message: string) => {
    const notice = `${Date.now()}-${message}`;
    setNotices((items) => [...items, notice]);
  }, []);

  /** 删除指定提示。 */
  const dismissNotice = useCallback((notice: string) => {
    setNotices((items) => items.filter((item) => item !== notice));
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
      if (event.client_action_id) dispatch({ type: "action.failed", actionId: event.client_action_id, message: event.message || "请求失败" });
      addNotice(event.message || "请求失败");
      return;
    }
    if (event.type === "message.accepted" && event.client_action_id && event.session_id && event.request_id) {
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
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#0f1115" : "#f7f8fa");
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

  /** 发送普通消息或运行中 guidance，并保留可重试的乐观消息。 */
  const send = useCallback((contentOverride?: string) => {
    const content = (contentOverride || draft || sessionState.pendingDraft).trim();
    if (!content || !socketRef.current) return;
    const actionId = crypto.randomUUID();
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
      dispatch({ type: "action.failed", actionId, message: "连接尚未建立" });
      addNotice("连接尚未建立，消息未发送");
      return;
    }
    setDraft("");
    dispatch({ type: "draft.pending", content: "" });
  }, [addNotice, draft, sessionId, sessionState.pendingDraft, sessionState.running]);

  /** 请求当前任务在下一个安全检查点停止。 */
  const stop = useCallback(() => {
    if (!sessionId || !socketRef.current?.send({ type: "task.stop", session_id: sessionId, client_action_id: crypto.randomUUID() })) addNotice("当前没有可停止的任务");
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
      setInspector(await api.observability(sessionId));
      setInspectorOpen(true);
    } catch (error) {
      addNotice(error instanceof Error ? error.message : "读取会话检查器失败");
    }
  };

  /** 更新会话标题、置顶或归档状态。 */
  const updateSession = async (id: string, payload: Partial<Pick<Session, "title" | "pinned" | "archived">>) => {
    await api.patchSession(id, payload);
    await refreshSessions();
  };

  /** 删除经用户确认的非活动会话。 */
  const deleteSession = async (id: string) => {
    if (!window.confirm("确定删除这个会话及其所有本地记录吗？")) return;
    await api.deleteSession(id);
    if (id === sessionId) navigate(null);
    await refreshSessions();
  };

  /** 重发失败的用户消息。 */
  const retryMessage = (message: ChatMessage) => send(message.content);

  const current = useMemo(() => [...sessions, ...archived].find((item) => item.id === sessionId), [archived, sessionId, sessions]);
  const currentTurnCount = Object.keys(sessionState.turns).length;

  return <div className="app-shell">
    <a className="skip-link" href="#main-content">跳到对话</a>
    <SessionSidebar active={sessions} archived={archived} currentId={sessionId} mobileOpen={mobileMenu} onCloseMobile={() => setMobileMenu(false)} onNew={() => navigate(null)} onSelect={navigate} onUpdate={updateSession} onDelete={deleteSession} onError={addNotice} />
    {mobileMenu && <button className="scrim" aria-label="关闭会话栏" onClick={() => setMobileMenu(false)} />}
    <main id="main-content" className="conversation">
      <header className="topbar">
        <button className="icon-button mobile-only" title="打开会话栏" aria-label="打开会话栏" onClick={() => setMobileMenu(true)}><Menu /></button>
        <div className="title-block"><span className={`connection-dot ${sessionState.running ? "is-running" : ""}`} title={connection} /><h1>{current?.title || "新建会话"}</h1>{current?.archived && <span className="readonly">已归档</span>}</div>
        <div className="top-actions"><button className="icon-button" title="切换主题" aria-label="切换主题" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>{theme === "dark" ? <Sun /> : <Moon />}</button><button className="icon-button" title="打开会话检查器" aria-label="打开会话检查器" disabled={!sessionId} onClick={() => void openInspector()}><PanelRight /></button></div>
      </header>
      <ChatTimeline sessionId={sessionId} messages={sessionState.messages} contentVersion={currentTurnCount} onRetry={retryMessage}>
        {!sessionId && sessionState.messages.length === 0 && <div className="empty-state"><span className="empty-kicker">LOCAL AGENT</span><h2>开始一个工作会话</h2><p>首条消息发送后才会创建本地会话记录。</p></div>}
      </ChatTimeline>
      <footer className="composer"><label className="approval-toggle"><input type="checkbox" checked={autoApprove} onChange={(event) => void setApproval(event.target.checked)} /><span>自动批准</span></label>{current?.archived ? <button className="restore-session" onClick={() => void updateSession(current.id, { archived: false })}><ArchiveRestore size={16} />恢复并继续</button> : <textarea aria-label="消息内容" name="message" autoComplete="off" value={draft || sessionState.pendingDraft} onChange={(event) => { setDraft(event.target.value); dispatch({ type: "draft.pending", content: "" }); }} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); } }} placeholder={sessionState.running ? "补充当前任务方向…" : "发送消息…"} rows={1} />}{sessionState.running ? <button className="icon-button stop-button" title="停止任务" aria-label="停止任务" onClick={stop}><CircleStop /></button> : <button className="icon-button send-button" title="发送消息" aria-label="发送消息" onClick={() => send()} disabled={Boolean(current?.archived)}><Send /></button>}</footer>
    </main>
    {inspectorOpen && <Inspector data={inspector} onClose={() => setInspectorOpen(false)} />}
    <NoticeRegion notices={notices} onDismiss={dismissNotice} />
  </div>;
}

/** 渲染按需打开的会话观测抽屉。 */
function Inspector({ data, onClose }: { data: Observability | null; onClose: () => void }) {
  return <aside className="inspector"><header><div><span className="inspector-kicker">SESSION INSPECTOR</span><h2>会话检查器</h2></div><button className="icon-button" title="关闭检查器" aria-label="关闭会话检查器" onClick={onClose}><X /></button></header>{!data ? <p>正在读取观测数据...</p> : <div className="inspector-body"><InspectorSection title="真实 Token" rows={data.token_usage} /><InspectorSection title="状态追踪" rows={data.traces} /><InspectorSection title="工具调用" rows={data.tool_calls} /></div>}</aside>;
}

/** 渲染检查器中的一个可折叠数据分区。 */
function InspectorSection({ title, rows }: { title: string; rows: Record<string, unknown>[] }) {
  return <section className="inspector-section"><h3>{title}</h3><pre>{rows.length ? JSON.stringify(rows, null, 2) : "暂无记录"}</pre></section>;
}

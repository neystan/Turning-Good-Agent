import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import {
  Archive, Check, ChevronDown, CircleStop, Clipboard, Copy, FilePlus2, Info, Menu, MoreHorizontal,
  Moon, PanelRight, Pin, RotateCcw, Send, Sun, Trash2, X
} from "lucide-react";
import { api } from "./api";
import type { ChatMessage, Observability, Session, TaskEvent } from "./types";

type Approval = { approval_id: string; tool_name: string; args: string };

const railLabel: Record<string, string> = {
  "task.queued": "排队", "task.running": "运行", "task.stopping": "正在停止", "task.completed": "完成",
  "task.failed": "失败", "task.cancelled": "已停止", "tool.started": "调用工具", "tool.finished": "工具完成",
  "approval.requested": "等待审批", "task.status": "状态更新"
};

type ChatState = {
  messages: ChatMessage[];
  events: TaskEvent[];
  approvals: Approval[];
  pendingDraft: string;
  running: boolean;
};

type ChatAction =
  | { type: "reset" }
  | { type: "history"; messages: ChatMessage[] }
  | { type: "user"; content: string }
  | { type: "pending"; content: string }
  | { type: "event"; event: TaskEvent & { type: string } }
  | { type: "running"; value: boolean };

const initialChatState: ChatState = { messages: [], events: [], approvals: [], pendingDraft: "", running: false };

function chatReducer(state: ChatState, action: ChatAction): ChatState {
  /** 统一处理聊天、过程事件、审批和运行状态。 */
  if (action.type === "reset") return initialChatState;
  if (action.type === "history") return { ...state, messages: action.messages };
  if (action.type === "user") return { ...state, messages: [...state.messages, temporaryMessage("user", action.content)] };
  if (action.type === "pending") return { ...state, pendingDraft: action.content };
  if (action.type === "running") return { ...state, running: action.value };
  const event = action.event;
  let messages = state.messages;
  let approvals = state.approvals;
  let pendingDraft = state.pendingDraft;
  let running = state.running;
  let events = state.events;
  if (event.type === "session.snapshot") running = event.payload.state === "running" || event.payload.state === "stopping";
  if (event.type === "response.delta") messages = appendDelta(messages, String(event.payload.content || ""));
  if (event.type === "response.completed") messages = finalizeAssistant(messages, String(event.payload.content || ""));
  if (event.type === "response.error") messages = [...messages, temporaryMessage("assistant", String(event.payload.content || "请求失败"))];
  if (event.type === "approval.requested") approvals = [...approvals, event.payload as unknown as Approval];
  if (event.type === "approval.resolved") approvals = approvals.filter(item => item.approval_id !== event.payload.approval_id);
  if (event.type === "guidance.pending") pendingDraft = String((event.payload.items as string[]).join("\n"));
  if (["task.queued", "task.running", "task.stopping", "task.completed", "task.failed", "task.cancelled", "tool.started", "tool.finished", "approval.requested", "task.status"].includes(event.type)) events = [...events, event];
  if (["task.completed", "task.failed", "task.cancelled"].includes(event.type)) running = false;
  if (["task.queued", "task.running", "task.stopping"].includes(event.type)) running = true;
  return { messages, events, approvals, pendingDraft, running };
}

/** 从浏览器路由读取当前已持久化会话。 */
function activeSessionId(): string | null {
  const match = window.location.pathname.match(/^\/sessions\/([^/]+)$/);
  return match ? decodeURIComponent(match[1]) : null;
}

/** 渲染 Web 会话工作台的根组件。 */
export function App() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [archived, setArchived] = useState<Session[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(activeSessionId);
  const [{ messages, events, approvals, pendingDraft, running }, dispatchChat] = useReducer(chatReducer, initialChatState);
  const [draft, setDraft] = useState("");
  const [inspectorOpen, setInspectorOpen] = useState(false);
  const [inspector, setInspector] = useState<Observability | null>(null);
  const [autoApprove, setAutoApprove] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">(() => localStorage.getItem("tga-theme") === "light" ? "light" : "dark");
  const [mobileMenu, setMobileMenu] = useState(false);
  const socket = useRef<WebSocket | null>(null);
  const lastEvent = useRef<Record<string, number>>({});
  const sessionIdRef = useRef<string | null>(sessionId);

  const refreshSessions = useCallback(async () => {
    const [active, archivedItems] = await Promise.all([api.listSessions(false), api.listSessions(true)]);
    setSessions(active); setArchived(archivedItems);
  }, []);

  const loadSession = useCallback(async (id: string | null) => {
    dispatchChat({ type: "reset" }); setInspector(null);
    if (!id) return;
    const rows = await api.messages(id);
    dispatchChat({ type: "history", messages: rows });
    socket.current?.send(JSON.stringify({ type: "session.subscribe", session_id: id, after_event_id: lastEvent.current[id] ?? 0 }));
  }, []);

  useEffect(() => { void refreshSessions(); void api.uiSettings().then(item => setAutoApprove(item.auto_approve_tools)); }, [refreshSessions]);
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.querySelector('meta[name="theme-color"]')?.setAttribute("content", theme === "dark" ? "#111714" : "#f3f4ee");
    localStorage.setItem("tga-theme", theme);
  }, [theme]);
  useEffect(() => { sessionIdRef.current = sessionId; }, [sessionId]);
  useEffect(() => { void loadSession(sessionId); }, [sessionId, loadSession]);
  useEffect(() => {
    const restoreRoute = () => setSessionId(activeSessionId());
    window.addEventListener("popstate", restoreRoute);
    return () => window.removeEventListener("popstate", restoreRoute);
  }, []);

  useEffect(() => {
    const protocol = location.protocol === "https:" ? "wss" : "ws";
    const ws = new WebSocket(`${protocol}://${location.host}/ws`);
    socket.current = ws;
    ws.onopen = () => { const id = sessionIdRef.current; if (id) ws.send(JSON.stringify({ type: "session.subscribe", session_id: id, after_event_id: lastEvent.current[id] ?? 0 })); };
    ws.onmessage = event => handleEvent(JSON.parse(event.data) as TaskEvent & { type: string });
    return () => ws.close();
  // 连接仅在首屏创建，当前会话订阅由 loadSession 更新。
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const handleEvent = (event: TaskEvent & { type: string }) => {
    if (event.event_id && event.session_id) lastEvent.current[event.session_id] = event.event_id;
    if (event.type === "session.created") { navigate(event.session_id); return; }
    if (event.type === "session.snapshot") { dispatchChat({ type: "event", event }); return; }
    if (event.session_id !== sessionIdRef.current) { void refreshSessions(); return; }
    dispatchChat({ type: "event", event });
    if (["task.completed", "task.failed", "task.cancelled"].includes(event.type)) { void refreshSessions(); void loadSession(sessionIdRef.current); }
  };

  const navigate = (id: string | null) => {
    window.history.pushState({}, "", id ? `/sessions/${encodeURIComponent(id)}` : "/");
    setSessionId(id); setMobileMenu(false);
  };

  const send = () => {
    const content = (draft || pendingDraft).trim();
    if (!content || socket.current?.readyState !== WebSocket.OPEN) return;
    if (running && sessionId) socket.current.send(JSON.stringify({ type: "guidance.send", session_id: sessionId, content }));
    else socket.current.send(JSON.stringify({ type: "message.send", session_id: sessionId, content }));
    if (!running) dispatchChat({ type: "user", content });
    setDraft(""); dispatchChat({ type: "running", value: true });
  };

  const stop = () => { if (sessionId) socket.current?.send(JSON.stringify({ type: "task.stop", session_id: sessionId })); };
  const resolveApproval = (approval_id: string, approved: boolean) => sessionId && socket.current?.send(JSON.stringify({ type: "approval.resolve", session_id: sessionId, approval_id, approved }));
  const setApproval = async (enabled: boolean) => { const updated = await api.patchUiSettings(enabled); setAutoApprove(updated.auto_approve_tools); };
  const openInspector = async () => { if (!sessionId) return; setInspector(await api.observability(sessionId)); setInspectorOpen(true); };
  const updateSession = async (id: string, payload: Partial<Session>) => { await api.patchSession(id, payload); await refreshSessions(); };
  const deleteSession = async (id: string) => { if (confirm("确定删除这个会话及其所有本地记录吗？")) { await api.deleteSession(id); if (id === sessionId) navigate(null); await refreshSessions(); } };
  const current = useMemo(() => [...sessions, ...archived].find(item => item.id === sessionId), [sessions, archived, sessionId]);

  return <div className="app-shell">
    <a className="skip-link" href="#main-content">跳到对话</a>
    <aside className={`sidebar ${mobileMenu ? "is-open" : ""}`} aria-label="会话管理">
      <header className="brand"><span className="brand-mark">TG</span><span>Turning Good</span><button className="icon-button mobile-only" title="关闭会话栏" aria-label="关闭会话栏" onClick={() => setMobileMenu(false)}><X /></button></header>
      <button className="new-session" onClick={() => navigate(null)}><FilePlus2 size={16} />新建会话</button>
      <SessionSection label="置顶" items={sessions.filter(item => item.pinned)} current={sessionId} onSelect={navigate} onUpdate={updateSession} onDelete={deleteSession} />
      <SessionSection label="会话" items={sessions.filter(item => !item.pinned)} current={sessionId} onSelect={navigate} onUpdate={updateSession} onDelete={deleteSession} />
      <SessionSection label="已归档" items={archived} current={sessionId} archived onSelect={navigate} onUpdate={updateSession} onDelete={deleteSession} />
    </aside>
    {mobileMenu && <button className="scrim" aria-label="关闭会话栏" onClick={() => setMobileMenu(false)} />}
    <main id="main-content" className="conversation">
      <header className="topbar">
        <button className="icon-button mobile-only" title="打开会话栏" aria-label="打开会话栏" onClick={() => setMobileMenu(true)}><Menu /></button>
        <div className="title-block"><span className={`connection-dot ${running ? "is-running" : ""}`} /> <h1>{current?.title || "新建会话"}</h1>{current?.archived && <span className="readonly">已归档</span>}</div>
        <div className="top-actions"><button className="icon-button" title="切换主题" aria-label="切换主题" onClick={() => setTheme(theme === "dark" ? "light" : "dark")}>{theme === "dark" ? <Sun /> : <Moon />}</button><button className="icon-button" title="打开会话检查器" aria-label="打开会话检查器" disabled={!sessionId} onClick={() => void openInspector()}><PanelRight /></button></div>
      </header>
      <section className="chat-scroll" aria-live="polite">
        {!sessionId && messages.length === 0 && <div className="empty-state"><span className="empty-kicker">LOCAL AGENT</span><h2>开始一个工作会话</h2><p>首条消息发送后才会创建本地会话记录。</p></div>}
        {messages.map(message => <MessageView key={message.id} message={message} />)}
        {events.length > 0 && <TaskRail events={events} />}
        {approvals.map(item => <ApprovalCard key={item.approval_id} approval={item} onResolve={resolveApproval} />)}
      </section>
      <footer className="composer">
        <label className="approval-toggle"><input type="checkbox" checked={autoApprove} onChange={event => void setApproval(event.target.checked)} /><span>自动批准</span></label>
        <textarea aria-label="消息内容" name="message" autoComplete="off" value={draft || pendingDraft} onChange={event => { setDraft(event.target.value); dispatchChat({ type: "pending", content: "" }); }} onKeyDown={event => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); send(); } }} placeholder={current?.archived ? "请先恢复已归档会话…" : running ? "补充当前任务方向…" : "发送消息…"} disabled={Boolean(current?.archived)} rows={1} />
        {running ? <button className="icon-button stop-button" title="停止任务" aria-label="停止任务" onClick={stop}><CircleStop /></button> : <button className="icon-button send-button" title="发送消息" aria-label="发送消息" onClick={send}><Send /></button>}
      </footer>
    </main>
    {inspectorOpen && <Inspector data={inspector} onClose={() => setInspectorOpen(false)} />}
  </div>;
}

/** 渲染一个会话分组及其管理操作。 */
function SessionSection({ label, items, current, archived, onSelect, onUpdate, onDelete }: { label: string; items: Session[]; current: string | null; archived?: boolean; onSelect: (id: string) => void; onUpdate: (id: string, payload: Partial<Session>) => Promise<void>; onDelete: (id: string) => Promise<void> }) {
  const [open, setOpen] = useState(!archived);
  if (!items.length && archived) return null;
  return <section className="session-section"><button className="section-title" onClick={() => setOpen(!open)}><ChevronDown size={14} className={open ? "" : "rotated"} />{label}<span>{items.length}</span></button>{open && items.map(item => <div className={`session-row ${item.id === current ? "selected" : ""}`} key={item.id}><button className="session-select" onClick={() => onSelect(item.id)}>{item.pinned && <Pin size={12} fill="currentColor" />}<span>{item.title}</span></button><details><summary className="icon-button" title="会话操作" aria-label="会话操作"><MoreHorizontal size={15} /></summary><div className="session-menu"><button onClick={() => void onUpdate(item.id, { pinned: !item.pinned })}><Pin size={14} />{item.pinned ? "取消置顶" : "置顶"}</button><button onClick={() => { const title = prompt("会话名称", item.title); if (title?.trim()) void onUpdate(item.id, { title: title.trim() }); }}><Clipboard size={14} />重命名</button><button onClick={() => void onUpdate(item.id, { archived: !archived })}>{archived ? <RotateCcw size={14} /> : <Archive size={14} />}{archived ? "恢复" : "归档"}</button><button className="danger" onClick={() => void onDelete(item.id)}><Trash2 size={14} />删除</button></div></details></div>)}</section>;
}

/** 渲染一条安全 Markdown 对话消息。 */
function MessageView({ message }: { message: ChatMessage }) {
  const stopped = message.role === "assistant" && Boolean(message.metadata.incomplete);
  return <article className={`message ${message.role}`}><div className="message-meta">{message.role === "user" ? "你" : "Turning Good"}{stopped && <span className="stopped-badge">已停止</span>}</div><div className="markdown"><ReactMarkdown components={{ code: CodeBlock }}>{message.content}</ReactMarkdown></div></article>;
}

/** 渲染带语言标记和复制按钮的代码块。 */
function CodeBlock({ className, children, ...props }: { className?: string; children?: React.ReactNode }) {
  const [copied, setCopied] = useState(false);
  const content = String(children || "").replace(/\n$/, "");
  if (!className) return <code {...props}>{children}</code>;
  const language = className.replace("language-", "") || "text";
  const copy = async () => { await navigator.clipboard.writeText(content); setCopied(true); window.setTimeout(() => setCopied(false), 1200); };
  return <span className="code-block"><span className="code-language">{language}</span><button className="icon-button" title={copied ? "已复制" : "复制代码"} aria-label={copied ? "已复制代码" : "复制代码"} onClick={() => void copy()}>{copied ? <Check size={14} /> : <Copy size={14} />}</button><code className={className} {...props}>{children}</code></span>;
}

/** 渲染一个任务的可折叠过程轨迹。 */
function TaskRail({ events }: { events: TaskEvent[] }) {
  const [open, setOpen] = useState(false);
  const latest = events.at(-1);
  return <section className="task-rail"><button onClick={() => setOpen(!open)}><span className="rail-dot" /><span>任务过程</span><span className="rail-status">{latest ? railLabel[latest.type] || "处理中" : ""}</span><ChevronDown size={15} className={open ? "" : "rotated"} /></button>{open && <ol>{events.map((event, index) => <li key={`${event.type}-${index}`}><span /><div><strong>{railLabel[event.type] || event.type}</strong>{event.type === "tool.started" && <code>{String(event.payload.tool_name)}</code>}{event.type === "task.status" && <small>{String(event.payload.content)}</small>}</div></li>)}</ol>}</section>;
}

/** 渲染一次工具调用的审批卡。 */
function ApprovalCard({ approval, onResolve }: { approval: Approval; onResolve: (id: string, approved: boolean) => void }) {
  return <section className="approval-card"><div><Info size={17} /><strong>工具需要确认</strong></div><code>{approval.tool_name}</code><pre>{approval.args}</pre><div className="approval-actions"><button onClick={() => onResolve(approval.approval_id, false)}>拒绝</button><button className="primary" onClick={() => onResolve(approval.approval_id, true)}><Check size={15} />允许一次</button></div></section>;
}

/** 渲染按需打开的会话观测抽屉。 */
function Inspector({ data, onClose }: { data: Observability | null; onClose: () => void }) {
  return <aside className="inspector"><header><div><span className="inspector-kicker">SESSION INSPECTOR</span><h2>会话检查器</h2></div><button className="icon-button" title="关闭检查器" aria-label="关闭检查器" onClick={onClose}><X /></button></header>{!data ? <p>正在读取观测数据...</p> : <div className="inspector-body"><InspectorSection title="真实 Token" rows={data.token_usage} /><InspectorSection title="状态追踪" rows={data.traces} /><InspectorSection title="工具调用" rows={data.tool_calls} /></div>}</aside>;
}

/** 渲染检查器中的一个可折叠数据分区。 */
function InspectorSection({ title, rows }: { title: string; rows: Record<string, unknown>[] }) { const [open, setOpen] = useState(title === "真实 Token"); return <section className="inspector-section"><button onClick={() => setOpen(!open)}><span>{title}</span><span>{rows.length}</span><ChevronDown size={15} className={open ? "" : "rotated"} /></button>{open && <pre>{rows.length ? JSON.stringify(rows, null, 2) : "暂无记录"}</pre>}</section>; }

/** 创建未落盘的即时消息占位。 */
function temporaryMessage(role: "user" | "assistant", content: string): ChatMessage { return { id: `temp-${crypto.randomUUID()}`, role, content, created_at: new Date().toISOString(), metadata: {} }; }
/** 将流式文本追加到当前 assistant 占位消息。 */
function appendDelta(rows: ChatMessage[], delta: string): ChatMessage[] { const last = rows.at(-1); return last?.role === "assistant" && last.id.startsWith("temp-") ? [...rows.slice(0, -1), { ...last, content: last.content + delta }] : [...rows, temporaryMessage("assistant", delta)]; }
/** 用终态文本完成当前 assistant 占位消息。 */
function finalizeAssistant(rows: ChatMessage[], content: string): ChatMessage[] { const last = rows.at(-1); return last?.role === "assistant" && last.id.startsWith("temp-") ? [...rows.slice(0, -1), { ...last, content: last.content || content }] : content ? [...rows, temporaryMessage("assistant", content)] : rows; }

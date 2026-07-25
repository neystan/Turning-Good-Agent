import { useEffect, useRef, useState } from "react";
import { Archive, ChevronDown, FilePlus2, MoreHorizontal, PanelLeftClose, Pin, RotateCcw, SquarePen, Trash2, X } from "lucide-react";

import type { Session } from "../types";

type SessionSidebarProps = {
  active: Session[];
  archived: Session[];
  currentId: string | null;
  mobileOpen: boolean;
  onCloseMobile: () => void;
  onNew: () => void;
  onSelect: (id: string) => void;
  onUpdate: (id: string, payload: Partial<Pick<Session, "title" | "pinned" | "archived">>) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onError: (message: string) => void;
};

/** 渲染带受控操作菜单的会话侧栏。 */
export function SessionSidebar({ active, archived, currentId, mobileOpen, onCloseMobile, onNew, onSelect, onUpdate, onDelete, onError }: SessionSidebarProps) {
  const [archivedOpen, setArchivedOpen] = useState(false);
  const [openMenuId, setOpenMenuId] = useState<string | null>(null);
  const [renaming, setRenaming] = useState<Session | null>(null);
  const [title, setTitle] = useState("");
  const sidebarRef = useRef<HTMLElement>(null);
  const orderedActive = [...active].sort((left, right) => Number(right.pinned) - Number(left.pinned) || right.updated_at.localeCompare(left.updated_at));

  useEffect(() => {
    /** 点击侧栏外部或按 Escape 时关闭会话菜单。 */
    const closeMenu = (event: PointerEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent && event.key === "Escape") setOpenMenuId(null);
      if (event instanceof PointerEvent && sidebarRef.current && !sidebarRef.current.contains(event.target as Node)) setOpenMenuId(null);
    };
    document.addEventListener("pointerdown", closeMenu);
    document.addEventListener("keydown", closeMenu);
    return () => {
      document.removeEventListener("pointerdown", closeMenu);
      document.removeEventListener("keydown", closeMenu);
    };
  }, []);

  /** 执行会话操作并收起对应菜单。 */
  const runAction = async (action: () => Promise<void>) => {
    try {
      await action();
    } catch (error) {
      onError(error instanceof Error ? error.message : "会话操作失败");
    } finally {
      setOpenMenuId(null);
    }
  };

  /** 打开受控重命名弹层。 */
  const openRename = (session: Session) => {
    setRenaming(session);
    setTitle(session.title);
    setOpenMenuId(null);
  };

  /** 提交新的会话标题。 */
  const submitRename = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!renaming || !title.trim()) return;
    await runAction(async () => onUpdate(renaming.id, { title: title.trim() }));
    setRenaming(null);
  };

  /** 切换会话并关闭任何展开的菜单。 */
  const select = (sessionId: string) => {
    setOpenMenuId(null);
    onSelect(sessionId);
  };

  return <aside ref={sidebarRef} className={`sidebar ${mobileOpen ? "is-open" : ""}`} aria-label="会话管理">
    <header className="brand"><span className="brand-mark">TG</span><span>Turning Good</span><button className="icon-button mobile-only" title="关闭会话栏" aria-label="关闭会话栏" onClick={onCloseMobile}><X /></button></header>
    <button className="new-session" onClick={onNew}><FilePlus2 size={16} />新建会话</button>
    <SessionList label="会话" items={orderedActive} currentId={currentId} openMenuId={openMenuId} onSelect={select} onMenu={setOpenMenuId} onRename={openRename} onAction={runAction} onUpdate={onUpdate} onDelete={onDelete} />
    {archived.length > 0 && <section className="session-section archived-section"><button className="section-title" onClick={() => setArchivedOpen(!archivedOpen)}><ChevronDown size={14} className={archivedOpen ? "" : "rotated"} />已归档<span>{archived.length}</span></button>{archivedOpen && <SessionList items={archived} currentId={currentId} openMenuId={openMenuId} onSelect={select} onMenu={setOpenMenuId} onRename={openRename} onAction={runAction} onUpdate={onUpdate} onDelete={onDelete} archived />}</section>}
    {renaming && <form className="rename-dialog" role="dialog" aria-label="重命名会话" onSubmit={(event) => void submitRename(event)}><label>会话名称<input autoFocus value={title} onChange={(event) => setTitle(event.target.value)} /></label><div><button type="button" onClick={() => setRenaming(null)}>取消</button><button type="submit">保存</button></div></form>}
  </aside>;
}

type SessionListProps = Omit<SessionSidebarProps, "active" | "archived" | "mobileOpen" | "onCloseMobile" | "onNew" | "onError"> & {
  label?: string;
  items: Session[];
  openMenuId: string | null;
  onMenu: (id: string | null) => void;
  onRename: (session: Session) => void;
  onAction: (action: () => Promise<void>) => Promise<void>;
  archived?: boolean;
};

/** 渲染一个会话列表，置顶标记保持在标题右侧固定位置。 */
function SessionList({ label, items, currentId, openMenuId, onSelect, onMenu, onRename, onAction, onUpdate, onDelete, archived = false }: SessionListProps) {
  const rows = <>{items.map((session) => <div className={`session-row ${session.id === currentId ? "selected" : ""}`} key={session.id}><button className="session-select" onClick={() => onSelect(session.id)}><span>{session.title}</span><span className="pin-slot">{session.pinned && <Pin size={13} fill="currentColor" aria-label="已置顶" />}</span></button><div className="session-actions"><button className="icon-button" title="会话操作" aria-label={`${session.title} 会话操作`} aria-expanded={openMenuId === session.id} onClick={() => onMenu(openMenuId === session.id ? null : session.id)}><MoreHorizontal size={15} /></button>{openMenuId === session.id && <div className="session-menu" role="menu"><button onClick={() => void onAction(async () => onUpdate(session.id, { pinned: !session.pinned }))}><Pin size={14} />{session.pinned ? "取消置顶" : "置顶"}</button><button onClick={() => onRename(session)}><SquarePen size={14} />重命名</button><button onClick={() => void onAction(async () => onUpdate(session.id, { archived: !archived }))}>{archived ? <RotateCcw size={14} /> : <Archive size={14} />}{archived ? "恢复" : "归档"}</button><button className="danger" onClick={() => void onAction(async () => onDelete(session.id))}><Trash2 size={14} />删除</button></div>}</div></div>)}</>;
  if (!label) return rows;
  return <section className="session-section"><div className="section-title"><PanelLeftClose size={14} />{label}<span>{items.length}</span></div>{rows}</section>;
}

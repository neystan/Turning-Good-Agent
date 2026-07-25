import { useState } from "react";
import { Archive, ChevronDown, FilePlus2, MoreHorizontal, PanelLeftClose, Pin, RotateCcw, SquarePen, Trash2, X } from "lucide-react";

import { OverlayPortal } from "./OverlayPortal";
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

type MenuState = {
  session: Session;
  anchor: Pick<DOMRect, "right" | "bottom">;
};

/** 渲染带受控操作菜单的会话侧栏。 */
export function SessionSidebar({ active, archived, currentId, mobileOpen, onCloseMobile, onNew, onSelect, onUpdate, onDelete, onError }: SessionSidebarProps) {
  const [archivedOpen, setArchivedOpen] = useState(false);
  const [menu, setMenu] = useState<MenuState | null>(null);
  const [renaming, setRenaming] = useState<Session | null>(null);
  const [deleting, setDeleting] = useState<Session | null>(null);
  const [title, setTitle] = useState("");
  const orderedActive = [...active].sort((left, right) => Number(right.pinned) - Number(left.pinned) || right.updated_at.localeCompare(left.updated_at));

  /** 执行会话操作并收起对应菜单。 */
  const runAction = async (action: () => Promise<void>) => {
    try {
      await action();
    } catch (error) {
      onError(error instanceof Error ? error.message : "会话操作失败");
    } finally {
      setMenu(null);
    }
  };

  /** 打开受控重命名弹层。 */
  const openRename = (session: Session) => {
    setRenaming(session);
    setTitle(session.title);
    setMenu(null);
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
    setMenu(null);
    onSelect(sessionId);
  };

  /** 切换会话操作菜单并记录触发按钮的视口位置。 */
  const toggleMenu = (session: Session, anchor: Pick<DOMRect, "right" | "bottom">) => {
    setMenu((current) => current?.session.id === session.id ? null : { session, anchor });
  };

  /** 删除已确认的会话并清理确认状态。 */
  const deleteConfirmed = async () => {
    if (!deleting) return;
    await runAction(() => onDelete(deleting.id));
    setDeleting(null);
  };

  return <><aside className={`sidebar ${mobileOpen ? "is-open" : ""}`} aria-label="会话管理">
    <header className="brand"><span className="brand-mark">TG</span><span>Turning Good</span><button className="icon-button mobile-only" title="关闭会话栏" aria-label="关闭会话栏" onClick={onCloseMobile}><X /></button></header>
    <button className="new-session" onClick={onNew}><FilePlus2 size={16} />新建会话</button>
    <SessionList label="会话" items={orderedActive} currentId={currentId} onSelect={select} onMenu={toggleMenu} />
    {archived.length > 0 && <section className="session-section archived-section"><button className="section-title" onClick={() => setArchivedOpen(!archivedOpen)}><ChevronDown size={14} className={archivedOpen ? "" : "rotated"} />已归档<span>{archived.length}</span></button>{archivedOpen && <SessionList items={archived} currentId={currentId} onSelect={select} onMenu={toggleMenu} />}</section>}
  </aside>{menu && <OverlayPortal anchor={menu.anchor} onDismiss={() => setMenu(null)}><SessionMenu session={menu.session} onClose={() => setMenu(null)} onRename={openRename} onDelete={setDeleting} onAction={runAction} onUpdate={onUpdate} /></OverlayPortal>}{renaming && <OverlayPortal className="overlay-dialog-layer" onDismiss={() => setRenaming(null)}><form className="rename-dialog" role="dialog" aria-modal="true" aria-label="重命名会话" onSubmit={(event) => void submitRename(event)}><label>会话名称<input name="session-title" autoComplete="off" autoFocus value={title} onChange={(event) => setTitle(event.target.value)} /></label><div><button type="button" onClick={() => setRenaming(null)}>取消</button><button type="submit">保存</button></div></form></OverlayPortal>}{deleting && <OverlayPortal className="confirm-layer" onDismiss={() => setDeleting(null)}><section className="confirm-dialog" role="alertdialog" aria-modal="true" aria-labelledby="delete-session-title"><h2 id="delete-session-title">删除会话？</h2><p>将永久删除“{deleting.title}”及其本地记录。</p><div><button onClick={() => setDeleting(null)}>取消</button><button className="danger" onClick={() => void deleteConfirmed()}>删除会话</button></div></section></OverlayPortal>}</>;
}

type SessionListProps = {
  label?: string;
  items: Session[];
  currentId: string | null;
  onSelect: (id: string) => void;
  onMenu: (session: Session, anchor: Pick<DOMRect, "right" | "bottom">) => void;
};

/** 渲染一个会话列表，置顶标记保持在标题右侧固定位置。 */
function SessionList({ label, items, currentId, onSelect, onMenu }: SessionListProps) {
  const rows = <>{items.map((session) => <div className={`session-row ${session.id === currentId ? "selected" : ""}`} key={session.id}><button className="session-select" onClick={() => onSelect(session.id)}><span>{session.title}</span><span className="pin-slot">{session.pinned && <Pin size={13} fill="currentColor" aria-label="已置顶" />}</span></button><div className="session-actions"><button className="icon-button" title="会话操作" aria-label={`${session.title} 会话操作`} onPointerDown={(event) => event.stopPropagation()} onClick={(event) => onMenu(session, event.currentTarget.getBoundingClientRect())}><MoreHorizontal size={15} /></button></div></div>)}</>;
  if (!label) return rows;
  return <section className="session-section"><div className="section-title"><PanelLeftClose size={14} />{label}<span>{items.length}</span></div>{rows}</section>;
}

/** 渲染当前会话的悬浮操作菜单。 */
function SessionMenu({ session, onClose, onRename, onDelete, onAction, onUpdate }: { session: Session; onClose: () => void; onRename: (session: Session) => void; onDelete: (session: Session) => void; onAction: (action: () => Promise<void>) => Promise<void>; onUpdate: SessionSidebarProps["onUpdate"] }) {
  return <div className="session-menu" role="menu"><button onClick={() => void onAction(async () => onUpdate(session.id, { pinned: !session.pinned }))}><Pin size={14} />{session.pinned ? "取消置顶" : "置顶"}</button><button onClick={() => onRename(session)}><SquarePen size={14} />重命名</button><button onClick={() => void onAction(async () => onUpdate(session.id, { archived: !session.archived }))}>{session.archived ? <RotateCcw size={14} /> : <Archive size={14} />}{session.archived ? "恢复" : "归档"}</button><button className="danger" onClick={() => { onClose(); onDelete(session); }}><Trash2 size={14} />删除</button></div>;
}

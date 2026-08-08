import { useEffect, useRef, useState } from "react";
import { createPortal } from "react-dom";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import * as Dialog from "@radix-ui/react-dialog";
import { Archive, BellRing, BrainCircuit, CalendarClock, ChevronDown, CircleAlert, FilePlus2, MoreHorizontal, PanelLeft, Pin, RotateCcw, Search, Settings2, SquarePen, Trash2, WandSparkles, X } from "lucide-react";

import { ScrollArea } from "./ScrollArea";
import { sessionMenuPosition } from "../state/session_menu_position";
import type { ProactiveDomain, ProactiveRoute } from "../proactive_types";
import type { Session } from "../types";

type SessionSidebarProps = {
  active: Session[];
  archived: Session[];
  currentId: string | null;
  mobileOpen: boolean;
  collapsed: boolean;
  onCollapseChange: (collapsed: boolean) => void;
  onCloseMobile: () => void;
  onNew: () => void;
  onOpenSearch: () => void;
  onOpenSettings: () => void;
  activeProactiveRoute: ProactiveRoute | null;
  onOpenProactive: (route: ProactiveRoute) => void;
  proactiveHealth: Record<ProactiveDomain, { state: "idle" | "active" | "incident" | "unavailable"; label: string }>;
  onSelect: (id: string) => void;
  onUpdate: (id: string, payload: Partial<Pick<Session, "title" | "pinned" | "archived">>) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onError: (message: string) => void;
};

const brandAssetPath = import.meta.env.DEV ? "" : "/static";

const proactiveEntries: Array<{ domain: ProactiveDomain; label: string; icon: typeof CalendarClock }> = [
  { domain: "cron", label: "Cron", icon: CalendarClock },
  { domain: "breakbeat", label: "Breakbeat", icon: BellRing },
  { domain: "memory", label: "长期记忆与 Dream", icon: BrainCircuit },
  { domain: "skills", label: "Skill 自进化", icon: WandSparkles },
  { domain: "incidents", label: "Incidents", icon: CircleAlert },
];

/** 渲染由 Radix 管理菜单与对话框的会话侧栏。 */
export function SessionSidebar({ active, archived, currentId, mobileOpen, collapsed, onCollapseChange, onCloseMobile, onNew, onOpenSearch, onOpenSettings, activeProactiveRoute, onOpenProactive, proactiveHealth, onSelect, onUpdate, onDelete, onError }: SessionSidebarProps) {
  const [activeOpen, setActiveOpen] = useState(true);
  const [archivedOpen, setArchivedOpen] = useState(false);
  const [renaming, setRenaming] = useState<Session | null>(null);
  const [deleting, setDeleting] = useState<Session | null>(null);
  const [title, setTitle] = useState("");
  const orderedActive = [...active].sort((left, right) => Number(right.pinned) - Number(left.pinned) || right.updated_at.localeCompare(left.updated_at));

  /** 执行会话操作并将业务错误转交给通知区。 */
  const runAction = async (action: () => Promise<void>): Promise<boolean> => {
    try {
      await action();
      return true;
    } catch (error) {
      onError(error instanceof Error ? error.message : "会话操作失败");
      return false;
    }
  };

  /** 打开重命名对话框并带入当前标题。 */
  const openRename = (session: Session) => {
    setRenaming(session);
    setTitle(session.title);
  };

  /** 提交非空的新会话标题。 */
  const submitRename = async (event: React.FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!renaming || !title.trim()) return;
    if (await runAction(() => onUpdate(renaming.id, { title: title.trim() }))) setRenaming(null);
  };

  /** 删除经 AlertDialog 确认的会话。 */
  const deleteConfirmed = async () => {
    if (!deleting) return;
    if (await runAction(() => onDelete(deleting.id))) setDeleting(null);
  };

  return <>
    <aside className={`sidebar ${mobileOpen ? "is-open" : ""}`} aria-label="会话管理"><ScrollArea className="sidebar-scroll">
      <header className="brand"><button className="brand-mark-button" aria-label="打开会话栏" disabled={!collapsed} onClick={() => onCollapseChange(false)}><img className="brand-mark" src={`${brandAssetPath}/tga-brand.png`} width="50" height="50" alt="" /></button><span className="brand-wordmark-frame"><img className="brand-wordmark" src={`${brandAssetPath}/tga-wordmark.png`} width="195" height="54" alt="Turning Good Agent" /></span><button className="icon-button sidebar-collapse-control" aria-label="隐藏会话栏" onClick={() => onCollapseChange(true)}><PanelLeft /></button><button className="icon-button mobile-only" aria-label="关闭会话栏" onClick={onCloseMobile}><X /></button></header>
      <div className="sidebar-body"><div className="sidebar-commands"><button className="sidebar-primary-action" type="button" onClick={onNew}><FilePlus2 size={16} aria-hidden="true" /><span>新建会话</span></button><button className="sidebar-primary-action" type="button" aria-label="搜索" onClick={onOpenSearch}><Search size={16} aria-hidden="true" /><span>搜索</span></button></div>
      <div className="sidebar-proactive-section"><div className="sidebar-proactive-domains">{proactiveEntries.map((entry) => {
        const Icon = entry.icon;
        const health = proactiveHealth[entry.domain];
        return <button key={entry.domain} className="sidebar-proactive sidebar-proactive-domain" type="button" aria-label={`打开 ${entry.label}`} aria-current={activeProactiveRoute === entry.domain ? "page" : undefined} onClick={() => onOpenProactive(entry.domain)}><Icon size={16} aria-hidden="true" /><span>{entry.label}</span><span className="sidebar-proactive-health" role="status" aria-label={`${entry.label} 健康状态`} data-state={health.state}><i aria-hidden="true" /><span>{health.label}</span></span></button>;
      })}</div></div>
      <section className="session-section"><button className="section-title" aria-expanded={activeOpen} onClick={() => setActiveOpen(!activeOpen)}><ChevronDown size={14} className={activeOpen ? "" : "rotated"} />会话<span>{active.length}</span></button>{activeOpen && <SessionList items={orderedActive} currentId={currentId} onSelect={onSelect} onRename={openRename} onDelete={setDeleting} onAction={runAction} onUpdate={onUpdate} />}</section>
      {archived.length > 0 && <section className="session-section archived-section"><button className="section-title" aria-expanded={archivedOpen} onClick={() => setArchivedOpen(!archivedOpen)}><ChevronDown size={14} className={archivedOpen ? "" : "rotated"} />已归档<span>{archived.length}</span></button>{archivedOpen && <SessionList items={archived} currentId={currentId} onSelect={onSelect} onRename={openRename} onDelete={setDeleting} onAction={runAction} onUpdate={onUpdate} />}</section>}</div>
    </ScrollArea><nav className="sidebar-workspaces" aria-label="工作面导航">
      <button className="sidebar-settings" type="button" aria-label="打开设置" onClick={onOpenSettings}><Settings2 size={16} aria-hidden="true" /><span>设置</span></button>
    </nav></aside>
    <RenameDialog session={renaming} title={title} onTitleChange={setTitle} onSubmit={submitRename} onOpenChange={(open) => !open && setRenaming(null)} />
    <DeleteDialog session={deleting} onConfirm={() => void deleteConfirmed()} onOpenChange={(open) => !open && setDeleting(null)} />
  </>;
}

type SessionListProps = {
  items: Session[];
  currentId: string | null;
  onSelect: (id: string) => void;
  onRename: (session: Session) => void;
  onDelete: (session: Session) => void;
  onAction: (action: () => Promise<void>) => Promise<boolean>;
  onUpdate: SessionSidebarProps["onUpdate"];
};

/** 渲染一个会话列表，置顶标记与操作槽始终固定在右侧。 */
function SessionList({ items, currentId, onSelect, onRename, onDelete, onAction, onUpdate }: SessionListProps) {
  const rows = <>{items.map((session) => <div className={`session-row ${session.id === currentId ? "selected" : ""}`} key={session.id}><button className="session-select" onClick={() => onSelect(session.id)}><span>{session.title}</span><span className="pin-slot">{session.pinned && <Pin size={13} fill="currentColor" aria-label="已置顶" />}</span></button><SessionActionMenu session={session} onRename={onRename} onDelete={onDelete} onAction={onAction} onUpdate={onUpdate} /></div>)}</>;
  return rows;
}

type SessionActionMenuProps = Pick<SessionListProps, "onRename" | "onDelete" | "onAction" | "onUpdate"> & { session: Session };

/** 渲染固定定位的会话操作菜单，避免侧栏滚动裁切。 */
function SessionActionMenu({ session, onRename, onDelete, onAction, onUpdate }: SessionActionMenuProps) {
  const triggerRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);
  const [position, setPosition] = useState<{ top: number; left: number } | null>(null);
  const menuId = `session-menu-${session.id}`;

  /** 打开菜单并使用触发按钮的真实坐标固定定位。 */
  const openMenu = () => {
    if (position) {
      setPosition(null);
      return;
    }
    const trigger = triggerRef.current;
    if (!trigger) return;
    setPosition(sessionMenuPosition(trigger.getBoundingClientRect(), { width: window.innerWidth, height: window.innerHeight }));
  };

  /** 关闭菜单后再执行对应的会话操作。 */
  const runMenuAction = (action: () => void) => {
    setPosition(null);
    action();
  };

  useEffect(() => {
    if (!position) return;
    /** 点击外部、滚动或按 Escape 时关闭固定菜单。 */
    const closeMenu = (event?: Event) => {
      const target = event?.target as Node | null;
      if (target && (menuRef.current?.contains(target) || triggerRef.current?.contains(target))) return;
      setPosition(null);
    };
    /** 使用 Escape 快捷关闭当前固定菜单。 */
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeMenu();
    };
    document.addEventListener("pointerdown", closeMenu, true);
    document.addEventListener("keydown", closeOnEscape);
    window.addEventListener("resize", closeMenu);
    window.addEventListener("scroll", closeMenu, true);
    return () => {
      document.removeEventListener("pointerdown", closeMenu, true);
      document.removeEventListener("keydown", closeOnEscape);
      window.removeEventListener("resize", closeMenu);
      window.removeEventListener("scroll", closeMenu, true);
    };
  }, [position]);

  return <div className="session-actions"><button ref={triggerRef} className="icon-button" aria-label={`${session.title} 会话操作`} aria-controls={position ? menuId : undefined} aria-expanded={Boolean(position)} onClick={openMenu}><MoreHorizontal size={15} /></button>{position && createPortal(<div ref={menuRef} id={menuId} className="session-menu session-menu-fixed" role="menu" style={position}><button type="button" role="menuitem" onClick={() => runMenuAction(() => void onAction(() => onUpdate(session.id, { pinned: !session.pinned })))}><Pin size={14} />{session.pinned ? "取消置顶" : "置顶"}</button><button type="button" role="menuitem" onClick={() => runMenuAction(() => onRename(session))}><SquarePen size={14} />重命名</button><button type="button" role="menuitem" onClick={() => runMenuAction(() => void onAction(() => onUpdate(session.id, { archived: !session.archived })))}>{session.archived ? <RotateCcw size={14} /> : <Archive size={14} />}{session.archived ? "恢复" : "归档"}</button><button type="button" role="menuitem" className="danger" onClick={() => runMenuAction(() => onDelete(session))}><Trash2 size={14} />删除</button></div>, document.body)}</div>;
}

/** 渲染带标题校验的会话重命名对话框。 */
function RenameDialog({ session, title, onTitleChange, onSubmit, onOpenChange }: { session: Session | null; title: string; onTitleChange: (value: string) => void; onSubmit: (event: React.FormEvent<HTMLFormElement>) => Promise<void>; onOpenChange: (open: boolean) => void }) {
  return <Dialog.Root open={Boolean(session)} onOpenChange={onOpenChange}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="rename-dialog"><Dialog.Title>重命名会话</Dialog.Title><Dialog.Description>修改当前本地会话的标题。</Dialog.Description><form onSubmit={(event) => void onSubmit(event)}><label>会话名称<input name="session-title" autoComplete="off" autoFocus value={title} onChange={(event) => onTitleChange(event.target.value)} /></label><div className="dialog-actions"><Dialog.Close asChild><button type="button">取消</button></Dialog.Close><button type="submit" disabled={!title.trim()}>保存</button></div></form></Dialog.Content></Dialog.Portal></Dialog.Root>;
}

/** 渲染会话删除的明确危险操作确认框。 */
function DeleteDialog({ session, onConfirm, onOpenChange }: { session: Session | null; onConfirm: () => void; onOpenChange: (open: boolean) => void }) {
  return <AlertDialog.Root open={Boolean(session)} onOpenChange={onOpenChange}><AlertDialog.Portal><AlertDialog.Overlay className="dialog-overlay" /><AlertDialog.Content className="confirm-dialog"><AlertDialog.Title>删除会话？</AlertDialog.Title><AlertDialog.Description>将永久删除“{session?.title}”及其本地记录，无法恢复。</AlertDialog.Description><div className="dialog-actions"><AlertDialog.Cancel asChild><button>取消</button></AlertDialog.Cancel><AlertDialog.Action asChild><button className="danger" onClick={onConfirm}>删除会话</button></AlertDialog.Action></div></AlertDialog.Content></AlertDialog.Portal></AlertDialog.Root>;
}

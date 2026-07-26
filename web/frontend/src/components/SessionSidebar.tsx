import { useState } from "react";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import * as Dialog from "@radix-ui/react-dialog";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { Archive, ChevronDown, FilePlus2, MoreHorizontal, PanelLeftClose, Pin, RotateCcw, Search, SquarePen, Trash2, X } from "lucide-react";

import { IconTooltip } from "./IconTooltip";
import type { Session } from "../types";

type SessionSidebarProps = {
  active: Session[];
  archived: Session[];
  currentId: string | null;
  mobileOpen: boolean;
  onCloseMobile: () => void;
  onNew: () => void;
  onOpenSearch: () => void;
  onSelect: (id: string) => void;
  onUpdate: (id: string, payload: Partial<Pick<Session, "title" | "pinned" | "archived">>) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
  onError: (message: string) => void;
};

/** 渲染由 Radix 管理菜单与对话框的会话侧栏。 */
export function SessionSidebar({ active, archived, currentId, mobileOpen, onCloseMobile, onNew, onOpenSearch, onSelect, onUpdate, onDelete, onError }: SessionSidebarProps) {
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
    <aside className={`sidebar ${mobileOpen ? "is-open" : ""}`} aria-label="会话管理">
      <header className="brand"><span className="brand-mark">TG</span><span>Turning Good</span><IconTooltip label="关闭会话栏"><button className="icon-button mobile-only" aria-label="关闭会话栏" onClick={onCloseMobile}><X /></button></IconTooltip></header>
      <div className="sidebar-commands"><button className="new-session" onClick={onNew}><FilePlus2 size={16} />新建会话</button><IconTooltip label="搜索会话"><button className="icon-button" aria-label="搜索会话" onClick={onOpenSearch}><Search size={16} /></button></IconTooltip></div>
      <SessionList label="会话" items={orderedActive} currentId={currentId} onSelect={onSelect} onRename={openRename} onDelete={setDeleting} onAction={runAction} onUpdate={onUpdate} />
      {archived.length > 0 && <section className="session-section archived-section"><button className="section-title" onClick={() => setArchivedOpen(!archivedOpen)}><ChevronDown size={14} className={archivedOpen ? "" : "rotated"} />已归档<span>{archived.length}</span></button>{archivedOpen && <SessionList items={archived} currentId={currentId} onSelect={onSelect} onRename={openRename} onDelete={setDeleting} onAction={runAction} onUpdate={onUpdate} />}</section>}
    </aside>
    <RenameDialog session={renaming} title={title} onTitleChange={setTitle} onSubmit={submitRename} onOpenChange={(open) => !open && setRenaming(null)} />
    <DeleteDialog session={deleting} onConfirm={() => void deleteConfirmed()} onOpenChange={(open) => !open && setDeleting(null)} />
  </>;
}

type SessionListProps = {
  label?: string;
  items: Session[];
  currentId: string | null;
  onSelect: (id: string) => void;
  onRename: (session: Session) => void;
  onDelete: (session: Session) => void;
  onAction: (action: () => Promise<void>) => Promise<boolean>;
  onUpdate: SessionSidebarProps["onUpdate"];
};

/** 渲染一个会话列表，置顶标记与操作槽始终固定在右侧。 */
function SessionList({ label, items, currentId, onSelect, onRename, onDelete, onAction, onUpdate }: SessionListProps) {
  const rows = <>{items.map((session) => <div className={`session-row ${session.id === currentId ? "selected" : ""}`} key={session.id}><button className="session-select" onClick={() => onSelect(session.id)}><span>{session.title}</span><span className="pin-slot">{session.pinned && <Pin size={13} fill="currentColor" aria-label="已置顶" />}</span></button><SessionActionMenu session={session} onRename={onRename} onDelete={onDelete} onAction={onAction} onUpdate={onUpdate} /></div>)}</>;
  if (!label) return rows;
  return <section className="session-section"><div className="section-title"><PanelLeftClose size={14} />{label}<span>{items.length}</span></div>{rows}</section>;
}

type SessionActionMenuProps = Pick<SessionListProps, "onRename" | "onDelete" | "onAction" | "onUpdate"> & { session: Session };

/** 渲染由 Radix 负责定位和关闭的会话操作菜单。 */
function SessionActionMenu({ session, onRename, onDelete, onAction, onUpdate }: SessionActionMenuProps) {
  return <DropdownMenu.Root>
    <div className="session-actions"><IconTooltip label="会话操作"><DropdownMenu.Trigger asChild><button className="icon-button" aria-label={`${session.title} 会话操作`}><MoreHorizontal size={15} /></button></DropdownMenu.Trigger></IconTooltip></div>
    <DropdownMenu.Portal>
      <DropdownMenu.Content className="session-menu" align="end" side="right" sideOffset={6} collisionPadding={8}>
        <DropdownMenu.Item onSelect={() => void onAction(() => onUpdate(session.id, { pinned: !session.pinned }))}><Pin size={14} />{session.pinned ? "取消置顶" : "置顶"}</DropdownMenu.Item>
        <DropdownMenu.Item onSelect={() => onRename(session)}><SquarePen size={14} />重命名</DropdownMenu.Item>
        <DropdownMenu.Item onSelect={() => void onAction(() => onUpdate(session.id, { archived: !session.archived }))}>{session.archived ? <RotateCcw size={14} /> : <Archive size={14} />}{session.archived ? "恢复" : "归档"}</DropdownMenu.Item>
        <DropdownMenu.Separator className="session-menu-separator" />
        <DropdownMenu.Item className="danger" onSelect={() => onDelete(session)}><Trash2 size={14} />删除</DropdownMenu.Item>
      </DropdownMenu.Content>
    </DropdownMenu.Portal>
  </DropdownMenu.Root>;
}

/** 渲染带标题校验的会话重命名对话框。 */
function RenameDialog({ session, title, onTitleChange, onSubmit, onOpenChange }: { session: Session | null; title: string; onTitleChange: (value: string) => void; onSubmit: (event: React.FormEvent<HTMLFormElement>) => Promise<void>; onOpenChange: (open: boolean) => void }) {
  return <Dialog.Root open={Boolean(session)} onOpenChange={onOpenChange}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="rename-dialog"><Dialog.Title>重命名会话</Dialog.Title><Dialog.Description>修改当前本地会话的标题。</Dialog.Description><form onSubmit={(event) => void onSubmit(event)}><label>会话名称<input name="session-title" autoComplete="off" autoFocus value={title} onChange={(event) => onTitleChange(event.target.value)} /></label><div className="dialog-actions"><Dialog.Close asChild><button type="button">取消</button></Dialog.Close><button type="submit" disabled={!title.trim()}>保存</button></div></form></Dialog.Content></Dialog.Portal></Dialog.Root>;
}

/** 渲染会话删除的明确危险操作确认框。 */
function DeleteDialog({ session, onConfirm, onOpenChange }: { session: Session | null; onConfirm: () => void; onOpenChange: (open: boolean) => void }) {
  return <AlertDialog.Root open={Boolean(session)} onOpenChange={onOpenChange}><AlertDialog.Portal><AlertDialog.Overlay className="dialog-overlay" /><AlertDialog.Content className="confirm-dialog"><AlertDialog.Title>删除会话？</AlertDialog.Title><AlertDialog.Description>将永久删除“{session?.title}”及其本地记录，无法恢复。</AlertDialog.Description><div className="dialog-actions"><AlertDialog.Cancel asChild><button>取消</button></AlertDialog.Cancel><AlertDialog.Action asChild><button className="danger" onClick={onConfirm}>删除会话</button></AlertDialog.Action></div></AlertDialog.Content></AlertDialog.Portal></AlertDialog.Root>;
}

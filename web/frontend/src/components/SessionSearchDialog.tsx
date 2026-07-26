import { useEffect, useMemo, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Search } from "lucide-react";

import { filterSessions } from "../state/session_search";
import { ScrollArea } from "./ScrollArea";
import type { Session } from "../types";

type SessionSearchDialogProps = {
  open: boolean;
  sessions: Session[];
  currentId: string | null;
  onClose: () => void;
  onSelect: (id: string) => void;
};

/** 提供 Radix 焦点管理的会话标题搜索对话框。 */
export function SessionSearchDialog({ open, sessions, currentId, onClose, onSelect }: SessionSearchDialogProps) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const results = useMemo(() => filterSessions(sessions, query), [query, sessions]);

  useEffect(() => {
    /** 每次打开时清空条件并回到首个结果。 */
    if (!open) return;
    setQuery("");
    setActiveIndex(0);
  }, [open]);

  useEffect(() => {
    /** 筛选结果改变后保持有效的选中项。 */
    setActiveIndex((index) => Math.min(index, Math.max(0, results.length - 1)));
  }, [results.length]);

  /** 选择结果后关闭对话框并切换会话。 */
  const select = (sessionId: string) => {
    onClose();
    onSelect(sessionId);
  };

  /** 支持方向键循环选择和 Enter 打开会话。 */
  const onKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((index) => results.length ? (index + 1) % results.length : 0);
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((index) => results.length ? (index - 1 + results.length) % results.length : 0);
    }
    if (event.key === "Enter" && results[activeIndex]) {
      event.preventDefault();
      select(results[activeIndex].id);
    }
  };

  return <Dialog.Root open={open} onOpenChange={(nextOpen) => !nextOpen && onClose()}><Dialog.Portal><Dialog.Overlay className="dialog-overlay" /><Dialog.Content className="session-search-dialog"><Dialog.Title className="sr-only">搜索会话</Dialog.Title><Dialog.Description className="sr-only">按会话标题筛选本地会话。</Dialog.Description><label className="session-search-input"><Search size={18} aria-hidden="true" /><span className="sr-only">搜索会话</span><input name="session-search" autoComplete="off" autoFocus value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={onKeyDown} placeholder="搜索会话…" /></label><ScrollArea className="session-search-results" role="listbox" aria-label="会话结果">{results.length ? results.map((session, index) => <button key={session.id} role="option" aria-selected={index === activeIndex} className={index === activeIndex ? "is-active" : ""} onMouseEnter={() => setActiveIndex(index)} onClick={() => select(session.id)}><span>{session.title}</span>{session.id === currentId && <small>当前会话</small>}{session.archived && <small>已归档</small>}</button>) : <p>没有匹配的会话。</p>}</ScrollArea></Dialog.Content></Dialog.Portal></Dialog.Root>;
}

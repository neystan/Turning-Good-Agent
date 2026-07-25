import { useEffect, useMemo, useRef, useState } from "react";
import { Search } from "lucide-react";

import { filterSessions } from "../state/session_search";
import type { Session } from "../types";
import { OverlayPortal } from "./OverlayPortal";

type SessionSearchDialogProps = {
  open: boolean;
  sessions: Session[];
  currentId: string | null;
  onClose: () => void;
  onSelect: (id: string) => void;
};

/** 提供键盘可用的会话标题搜索对话框。 */
export function SessionSearchDialog({ open, sessions, currentId, onClose, onSelect }: SessionSearchDialogProps) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const results = useMemo(() => filterSessions(sessions, query), [query, sessions]);

  useEffect(() => {
    /** 每次打开时重置条件并将焦点放入搜索框。 */
    if (!open) return;
    setQuery("");
    setActiveIndex(0);
    window.setTimeout(() => inputRef.current?.focus(), 0);
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

  if (!open) return null;
  return <OverlayPortal className="overlay-search-layer" onDismiss={onClose}><section className="session-search-dialog" role="dialog" aria-modal="true" aria-labelledby="session-search-title"><h2 id="session-search-title" className="sr-only">搜索会话</h2><label className="session-search-input"><Search size={18} aria-hidden="true" /><span className="sr-only">搜索会话</span><input ref={inputRef} name="session-search" autoComplete="off" value={query} onChange={(event) => setQuery(event.target.value)} onKeyDown={onKeyDown} placeholder="搜索会话…" /></label><div className="session-search-results" role="listbox" aria-label="会话结果">{results.length ? results.map((session, index) => <button key={session.id} role="option" aria-selected={index === activeIndex} className={index === activeIndex ? "is-active" : ""} onMouseEnter={() => setActiveIndex(index)} onClick={() => select(session.id)}><span>{session.title}</span>{session.id === currentId && <small>当前会话</small>}{session.archived && <small>已归档</small>}</button>) : <p>没有匹配的会话。</p>}</div></section></OverlayPortal>;
}

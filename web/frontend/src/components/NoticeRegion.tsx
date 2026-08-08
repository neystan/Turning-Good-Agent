import { CircleAlert, CircleX, Info, X } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

export type Notice = {
  id: string;
  message: string;
  title?: string;
  target?: string;
  severity: "info" | "warning" | "error";
};

export const noticeLifetimeMs = { info: 4_000, warning: 4_000, error: 7_000 } as const;
const noticeExitMs = 180;

/** 渲染不会打断输入的可访问操作通知。 */
export function NoticeRegion({ notices, onDismiss, onNavigate, placement = "overlay" }: { notices: Notice[]; onDismiss: (id: string) => void; onNavigate?: (notice: Notice) => void; placement?: "overlay" | "conversation" }) {
  const [exitingIds, setExitingIds] = useState<string[]>([]);
  const exitTimers = useRef(new Map<string, number>());
  const dismissWithMotion = useCallback((id: string) => {
    if (exitTimers.current.has(id)) return;
    setExitingIds((items) => items.includes(id) ? items : [...items, id]);
    exitTimers.current.set(id, window.setTimeout(() => {
      exitTimers.current.delete(id);
      onDismiss(id);
    }, noticeExitMs));
  }, [onDismiss]);

  useEffect(() => () => {
    exitTimers.current.forEach((timer) => window.clearTimeout(timer));
    exitTimers.current.clear();
  }, []);

  return <div className={`notice-region notice-region--${placement}`} aria-live="polite" aria-atomic="false" aria-label="主动能力通知">
    {notices.slice(-3).map((notice) => <NoticeCard key={notice.id} notice={notice} exiting={exitingIds.includes(notice.id)} onDismiss={dismissWithMotion} onNavigate={onNavigate} />)}
  </div>;
}

function NoticeCard({ notice, exiting, onDismiss, onNavigate }: { notice: Notice; exiting: boolean; onDismiss: (id: string) => void; onNavigate?: (notice: Notice) => void }) {
  const [hovered, setHovered] = useState(false);
  const [focused, setFocused] = useState(false);
  const remainingMs = useRef<number>(noticeLifetimeMs[notice.severity]);
  const startedAt = useRef<number | null>(null);
  const timeout = useRef<number | null>(null);
  const paused = hovered || focused;
  const Icon = notice.severity === "error" ? CircleX : notice.severity === "warning" ? CircleAlert : Info;

  useEffect(() => {
    if (paused) {
      if (startedAt.current !== null) {
        remainingMs.current = Math.max(0, remainingMs.current - (performance.now() - startedAt.current));
        startedAt.current = null;
      }
      return;
    }
    const remaining = remainingMs.current;
    if (remaining <= 0) {
      onDismiss(notice.id);
      return;
    }
    startedAt.current = performance.now();
    timeout.current = window.setTimeout(() => {
      timeout.current = null;
      startedAt.current = null;
      remainingMs.current = 0;
      onDismiss(notice.id);
    }, remaining);
    return () => {
      if (timeout.current !== null) {
        window.clearTimeout(timeout.current);
        timeout.current = null;
      }
    };
  }, [notice.id, onDismiss, paused]);

  return <div className={`notice notice--${notice.severity}${exiting ? " is-exiting" : ""}`} onPointerEnter={() => setHovered(true)} onPointerLeave={() => setHovered(false)} onFocusCapture={() => setFocused(true)} onBlurCapture={(event) => {
    if (!event.currentTarget.contains(event.relatedTarget as Node | null)) setFocused(false);
  }}>
    <span className="notice-severity-icon" aria-hidden="true"><Icon size={16} /></span>
    {notice.target && onNavigate ? <button className="notice-content" type="button" onClick={() => onNavigate(notice)} aria-label={`查看：${notice.title || notice.message}`}>{notice.title && <strong>{notice.title}</strong>}<span>{notice.message}</span></button> : <div className="notice-content">{notice.title && <strong>{notice.title}</strong>}<span>{notice.message}</span></div>}
    <button className="icon-button" type="button" onClick={() => onDismiss(notice.id)} aria-label={`关闭提示：${notice.title || notice.message}`}><X size={15} /></button>
  </div>;
}

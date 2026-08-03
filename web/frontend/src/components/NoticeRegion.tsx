import { X } from "lucide-react";

export type Notice = {
  id: string;
  message: string;
  title?: string;
  target?: string;
};

/** 渲染不会打断输入的可访问操作通知。 */
export function NoticeRegion({ notices, onDismiss, onNavigate }: { notices: Notice[]; onDismiss: (id: string) => void; onNavigate?: (notice: Notice) => void }) {
  return <div className="notice-region" aria-live="polite" aria-atomic="true">
    {notices.slice(0, 3).map((notice) => <div className="notice" key={notice.id} role="status">
      {notice.target && onNavigate ? <button className="notice-content" type="button" onClick={() => onNavigate(notice)} aria-label={`查看：${notice.title || notice.message}`}><strong>{notice.title}</strong><span>{notice.message}</span></button> : <div className="notice-content"><strong>{notice.title}</strong><span>{notice.message}</span></div>}
      <button className="icon-button" type="button" onClick={() => onDismiss(notice.id)} aria-label={`关闭提示：${notice.title || notice.message}`}><X size={15} /></button>
    </div>)}
  </div>;
}

type Notice = { id: string; message: string };

/** 渲染不会打断输入的可访问操作通知。 */
export function NoticeRegion({ notices, onDismiss }: { notices: Notice[]; onDismiss: (id: string) => void }) {
  return <div className="notice-region" aria-live="polite" aria-atomic="true">
    {notices.map((notice) => <div className="notice" key={notice.id} role="status">
      <span>{notice.message}</span><button type="button" onClick={() => onDismiss(notice.id)} aria-label="关闭提示">关闭</button>
    </div>)}
  </div>;
}

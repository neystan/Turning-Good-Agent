import { ArchiveRestore, CircleStop, Hand, Send } from "lucide-react";

import type { Session } from "../types";

type ComposerProps = {
  session: Session | undefined;
  running: boolean;
  draft: string;
  autoApprove: boolean;
  onDraftChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  onRestore: () => void;
  onAutoApproveChange: (enabled: boolean) => void;
};

/** 渲染输入在上、权限与执行操作在下的对话 Composer。 */
export function Composer({ session, running, draft, autoApprove, onDraftChange, onSend, onStop, onRestore, onAutoApproveChange }: ComposerProps) {
  if (session?.archived) return <footer className="composer archived-composer"><div><strong>此会话已归档</strong><span>恢复后可继续发送消息。</span></div><button className="restore-session" onClick={onRestore}><ArchiveRestore size={16} />恢复并继续</button></footer>;
  /** 使用 Enter 发送，Shift+Enter 保留换行。 */
  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSend();
    }
  };
  return <footer className="composer"><textarea aria-label="消息内容" name="message" autoComplete="off" value={draft} onChange={(event) => onDraftChange(event.target.value)} onKeyDown={onKeyDown} placeholder={running ? "补充当前任务方向…" : "发送消息…"} rows={1} /><div className="composer-toolbar"><label className="permission-select"><Hand size={16} /><span className="sr-only">工具权限</span><select aria-label="工具权限" value={autoApprove ? "full" : "default"} onChange={(event) => onAutoApproveChange(event.target.value === "full")}><option value="default">默认权限</option><option value="full">完全访问权限</option></select></label>{autoApprove && <span className="permission-limit">仍受安全检查限制</span>}<span className="composer-spacer" />{running ? <button className="composer-action is-stop" title="停止任务" aria-label="停止任务" onClick={onStop}><CircleStop size={17} />停止</button> : <button className="composer-action is-send" title="发送消息" aria-label="发送消息" onClick={onSend}><Send size={17} />发送</button>}</div></footer>;
}

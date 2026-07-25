import { useEffect, useRef, useState } from "react";
import { ArchiveRestore, Check, ChevronDown, CircleStop, Hand, Send } from "lucide-react";

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
  const [permissionOpen, setPermissionOpen] = useState(false);
  const permissionRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    /** 在点击权限菜单外部或按 Escape 后关闭菜单。 */
    const closePermissionMenu = (event: PointerEvent | KeyboardEvent) => {
      if (event instanceof KeyboardEvent && event.key === "Escape") setPermissionOpen(false);
      if (event instanceof PointerEvent && permissionRef.current && !permissionRef.current.contains(event.target as Node)) setPermissionOpen(false);
    };
    document.addEventListener("pointerdown", closePermissionMenu);
    document.addEventListener("keydown", closePermissionMenu);
    return () => {
      document.removeEventListener("pointerdown", closePermissionMenu);
      document.removeEventListener("keydown", closePermissionMenu);
    };
  }, []);

  /** 选择权限策略后关闭菜单。 */
  const selectPermission = (enabled: boolean) => {
    setPermissionOpen(false);
    onAutoApproveChange(enabled);
  };

  /** 使用 Enter 发送，Shift+Enter 保留换行。 */
  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSend();
    }
  };

  if (session?.archived) return <footer className="composer archived-composer"><div><strong>此会话已归档</strong><span>恢复后可继续发送消息。</span></div><button className="restore-session" onClick={onRestore}><ArchiveRestore size={16} />恢复并继续</button></footer>;
  return <footer className="composer"><textarea aria-label="消息内容" name="message" autoComplete="off" value={draft} onChange={(event) => onDraftChange(event.target.value)} onKeyDown={onKeyDown} placeholder={running ? "补充当前任务方向…" : "发送消息…"} rows={1} /><div className="composer-toolbar"><div ref={permissionRef} className="permission-control"><button className="permission-trigger" aria-label="选择工具权限" aria-expanded={permissionOpen} onClick={() => setPermissionOpen(!permissionOpen)}><Hand size={16} /><span>{autoApprove ? "完全访问权限" : "默认权限"}</span><ChevronDown size={14} /></button>{permissionOpen && <div className="permission-menu" role="menu"><button role="menuitemradio" aria-checked={!autoApprove} onClick={() => selectPermission(false)}><span><Hand size={15} />默认权限</span>{!autoApprove && <Check size={15} />}</button><button className="is-full" role="menuitemradio" aria-checked={autoApprove} onClick={() => selectPermission(true)}><span><Hand size={15} />完全访问权限</span>{autoApprove && <Check size={15} />}<small>仍受安全检查限制</small></button></div>}</div><span className="composer-spacer" />{running ? <button className="composer-action is-stop" title="停止任务" aria-label="停止任务" onClick={onStop}><CircleStop size={17} />停止</button> : <button className="composer-action is-send" title="发送消息" aria-label="发送消息" onClick={onSend}><Send size={17} />发送</button>}</div></footer>;
}

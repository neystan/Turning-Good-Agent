import { useLayoutEffect, useRef, type Ref } from "react";
import * as Switch from "@radix-ui/react-switch";
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
  rootRef?: Ref<HTMLElement>;
};

/** 渲染输入在上、权限与执行操作在下的对话 Composer。 */
export function Composer({ session, running, draft, autoApprove, onDraftChange, onSend, onStop, onRestore, onAutoApproveChange, rootRef }: ComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useLayoutEffect(() => {
    /** 根据内容自动调整输入框高度，并限制其最大占用空间。 */
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 220)}px`;
    textarea.style.overflowY = textarea.scrollHeight > 220 ? "auto" : "hidden";
  }, [draft]);

  /** 使用 Enter 发送，Shift+Enter 保留换行。 */
  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSend();
    }
  };

  if (session?.archived) return <footer ref={rootRef} className="composer archived-composer"><div><strong>此会话已归档</strong><span>恢复后可继续发送消息。</span></div><button className="restore-session" onClick={onRestore}><ArchiveRestore size={16} />恢复并继续</button></footer>;
  return <footer ref={rootRef} className="composer"><textarea ref={textareaRef} aria-label="消息内容" name="message" autoComplete="off" value={draft} onChange={(event) => onDraftChange(event.target.value)} onKeyDown={onKeyDown} placeholder={running ? "补充当前任务方向…" : "发送消息…"} rows={1} /><div className="composer-toolbar"><div className="tool-approval"><Hand size={16} aria-hidden="true" /><strong>工具审批</strong><Switch.Root className="tool-approval-switch" checked={autoApprove} onCheckedChange={onAutoApproveChange} aria-label="自动批准后续工具操作"><Switch.Thumb className="tool-approval-thumb" /></Switch.Root></div><span className="composer-spacer" />{running ? <button className="composer-action is-stop" aria-label="停止任务" onClick={onStop}><CircleStop size={17} />停止</button> : <button className="composer-action is-send" aria-label="发送消息" onClick={onSend}><Send size={17} />发送</button>}</div></footer>;
}

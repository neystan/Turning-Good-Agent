import { useEffect, useLayoutEffect, useRef, useState, type Ref } from "react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { ArchiveRestore, ArrowUp, Check, ChevronDown, Hand, Square, TriangleAlert } from "lucide-react";

import type { Session } from "../types";

type ComposerProps = {
  session: Session | undefined;
  running: boolean;
  draft: string;
  autoApprove: boolean;
  onDraftChange: (value: string) => void;
  onSend: () => void;
  onStop: () => void;
  restoreFocusVersion: number;
  onRestore: () => Promise<void>;
  onAutoApproveChange: (enabled: boolean) => void;
  rootRef?: Ref<HTMLElement>;
};

/** 渲染输入在上、权限与执行操作在下的对话 Composer。 */
export function Composer({ session, running, draft, autoApprove, restoreFocusVersion, onDraftChange, onSend, onStop, onRestore, onAutoApproveChange, rootRef }: ComposerProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [scrollThumb, setScrollThumb] = useState<{ top: number; height: number } | null>(null);

  /** 根据滚动位置计算输入区自定义滚动条滑块。 */
  const updateScrollThumb = () => {
    const textarea = textareaRef.current;
    if (!textarea) return;
    const overflow = textarea.scrollHeight - textarea.clientHeight;
    if (overflow <= 0) {
      setScrollThumb(null);
      return;
    }
    const trackHeight = Math.max(0, textarea.clientHeight - 24);
    const height = Math.min(trackHeight, Math.max(18, trackHeight * textarea.clientHeight / textarea.scrollHeight));
    const top = textarea.scrollTop / overflow * (trackHeight - height);
    setScrollThumb({ top, height });
  };

  useLayoutEffect(() => {
    /** 根据内容自动调整输入框高度，并限制其最大占用空间。 */
    const textarea = textareaRef.current;
    if (!textarea) return;
    textarea.style.height = "auto";
    textarea.style.height = `${Math.min(textarea.scrollHeight, 220)}px`;
    textarea.style.overflowY = textarea.scrollHeight > 220 ? "auto" : "hidden";
    updateScrollThumb();
  }, [draft]);

  /** 恢复归档会话后将焦点交给输入框。 */
  useEffect(() => {
    if (restoreFocusVersion) textareaRef.current?.focus();
  }, [restoreFocusVersion]);

  /** 使用 Enter 发送，Shift+Enter 保留换行。 */
  const onKeyDown = (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      onSend();
    }
  };

  if (session?.archived) return <footer ref={rootRef} className="composer archived-composer"><strong>此会话已归档</strong><button className="restore-session" type="button" onClick={() => void onRestore()}><ArchiveRestore size={16} aria-hidden="true" />恢复会话</button></footer>;
  return <footer ref={rootRef} className="composer"><div className="composer-input"><textarea ref={textareaRef} aria-label="消息内容" name="message" autoComplete="off" value={draft} onChange={(event) => onDraftChange(event.target.value)} onScroll={updateScrollThumb} onKeyDown={onKeyDown} placeholder={running ? "补充当前任务方向…" : "发送消息…"} rows={1} />{scrollThumb && <span className="composer-scroll-thumb" style={{ height: scrollThumb.height, transform: `translateY(${scrollThumb.top}px)` }} />}</div><div className="composer-toolbar"><PermissionMenu autoApprove={autoApprove} onChange={onAutoApproveChange} /><span className="composer-spacer" />{running ? <button className="composer-action is-stop" type="button" aria-label="停止任务" onClick={onStop}><Square size={9} fill="currentColor" strokeWidth={0} aria-hidden="true" /></button> : <button className="composer-action is-send" type="button" aria-label="发送消息" onClick={onSend}><ArrowUp size={13} strokeWidth={2.5} aria-hidden="true" /></button>}</div></footer>;
}

/** 渲染全局工具权限选择菜单。 */
function PermissionMenu({ autoApprove, onChange }: { autoApprove: boolean; onChange: (enabled: boolean) => void }) {
  const label = autoApprove ? "完全访问" : "默认权限";
  const PermissionIcon = autoApprove ? TriangleAlert : Hand;

  return <DropdownMenu.Root><DropdownMenu.Trigger asChild><button className={`permission-trigger ${autoApprove ? "is-danger" : ""}`} aria-label={`工具权限：${label}`}><PermissionIcon size={13} aria-hidden="true" /><span>{label}</span><ChevronDown size={11} aria-hidden="true" /></button></DropdownMenu.Trigger><DropdownMenu.Portal><DropdownMenu.Content className="permission-menu" side="top" align="start" sideOffset={8}><DropdownMenu.RadioGroup value={autoApprove ? "auto" : "default"} onValueChange={(value) => onChange(value === "auto")}><DropdownMenu.RadioItem value="default"><Hand size={15} aria-hidden="true" /><span>默认权限</span><DropdownMenu.ItemIndicator className="permission-indicator"><Check size={15} aria-hidden="true" /></DropdownMenu.ItemIndicator></DropdownMenu.RadioItem><DropdownMenu.RadioItem className="is-danger" value="auto"><TriangleAlert size={15} aria-hidden="true" /><span>完全访问</span><DropdownMenu.ItemIndicator className="permission-indicator"><Check size={15} aria-hidden="true" /></DropdownMenu.ItemIndicator></DropdownMenu.RadioItem></DropdownMenu.RadioGroup></DropdownMenu.Content></DropdownMenu.Portal></DropdownMenu.Root>;
}

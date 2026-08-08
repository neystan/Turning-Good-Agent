import { useEffect, useLayoutEffect, useRef, useState, type Ref } from "react";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import { ArchiveRestore, ArrowUp, Check, ChevronDown, Hand, Square, TriangleAlert } from "lucide-react";

import { ContextWindowIndicator } from "./ContextWindowIndicator";
import { SlashCommandMenu } from "./SlashCommandMenu";
import { createGuidanceSegment, createTextSegment } from "../state/composer_segments";
import type { CommandEntry, ComposerSegment, ContextWindow, Session } from "../types";
import mcpIcon from "../assets/slash-icons/mcp.svg";
import skillIcon from "../assets/slash-icons/skill.svg";

const ZERO_WIDTH_SPACE = "\u200B";

type ComposerProps = {
  session: Session | undefined;
  running: boolean;
  actionsEnabled: boolean;
  segments: ComposerSegment[];
  autoApprove: boolean;
  contextWindow: ContextWindow | null;
  onSegmentsChange: (segments: ComposerSegment[]) => void;
  onSend: () => void;
  onStop: () => void;
  restoreFocusVersion: number;
  onRestore: () => Promise<void>;
  onAutoApproveChange: (enabled: boolean) => void;
  onSlashRead: (entry: CommandEntry) => void;
  rootRef?: Ref<HTMLElement>;
};

type Caret = { segmentId: string; offset: number };

export function Composer({ session, running, actionsEnabled, segments, autoApprove, contextWindow, restoreFocusVersion, onSegmentsChange, onSend, onStop, onRestore, onAutoApproveChange, onSlashRead, rootRef }: ComposerProps) {
  const editorRef = useRef<HTMLDivElement>(null);
  const [scrollThumb, setScrollThumb] = useState<{ top: number; height: number } | null>(null);
  const [slashToken, setSlashToken] = useState<string | null>(null);
  const pendingCaretSegmentId = useRef<string | null>(null);
  const latestCaret = useRef<Caret | null>(null);
  const internalEdit = useRef(false);

  const updateScrollThumb = () => {
    const editor = editorRef.current;
    if (!editor) return;
    const overflow = editor.scrollHeight - editor.clientHeight;
    if (overflow <= 0) {
      setScrollThumb(null);
      return;
    }
    const trackHeight = Math.max(0, editor.clientHeight - 24);
    const height = Math.min(trackHeight, Math.max(18, trackHeight * editor.clientHeight / editor.scrollHeight));
    setScrollThumb({ top: editor.scrollTop / overflow * (trackHeight - height), height });
  };

  useLayoutEffect(() => {
    const editor = editorRef.current;
    if (!editor) return;
    if (internalEdit.current) internalEdit.current = false;
    else renderEditorSegments(editor, segments);
    editor.style.height = "auto";
    editor.style.height = `${Math.min(editor.scrollHeight, 220)}px`;
    editor.style.overflowY = editor.scrollHeight > 220 ? "auto" : "hidden";
    updateScrollThumb();
    const segmentId = pendingCaretSegmentId.current;
    if (!segmentId) return;
    const target = editor.querySelector<HTMLElement>(`[data-composer-segment-id="${CSS.escape(segmentId)}"]`);
    const node = target?.firstChild;
    if (!target || !node) return;
    const selection = window.getSelection();
    const range = document.createRange();
    range.setStart(node, 0);
    range.collapse(true);
    selection?.removeAllRanges();
    selection?.addRange(range);
    editor.focus();
    pendingCaretSegmentId.current = null;
  }, [segments]);

  useEffect(() => {
    if (restoreFocusVersion) editorRef.current?.focus();
  }, [restoreFocusVersion]);

  const currentCaret = (): Caret | null => {
    const editor = editorRef.current;
    const selection = window.getSelection();
    if (!editor || !selection?.rangeCount || !selection.isCollapsed || !selection.anchorNode) return latestCaret.current;
    const anchor = selection.anchorNode instanceof HTMLElement ? selection.anchorNode : selection.anchorNode.parentElement;
    const segment = anchor?.closest<HTMLElement>("[data-composer-segment-id]");
    if (!segment || !editor.contains(segment) || segment.dataset.segmentType !== "text") return latestCaret.current;
    const text = segment.textContent?.replaceAll(ZERO_WIDTH_SPACE, "") || "";
    const offset = selection.anchorNode.nodeType === Node.TEXT_NODE ? Math.min(selection.anchorOffset, text.length) : text.length;
    return { segmentId: segment.dataset.composerSegmentId || "", offset };
  };

  const refreshSlashToken = () => {
    const caret = currentCaret();
    const segment = caret ? editorRef.current?.querySelector<HTMLElement>(`[data-composer-segment-id="${CSS.escape(caret.segmentId)}"]`) : undefined;
    const text = caret ? (segment?.innerText || segment?.textContent || "").replaceAll(ZERO_WIDTH_SPACE, "").slice(0, caret.offset) : "";
    setSlashToken(text?.match(/(?:^|\s)(\/\S*)$/)?.[1] || null);
  };

  const readSegments = (): ComposerSegment[] => {
    const editor = editorRef.current;
    if (!editor) return segments;
    const current = new Map(segments.map((segment) => [segment.id, segment]));
    const fallbackText = segments.find((segment) => segment.type === "text");
    return Array.from(editor.childNodes).flatMap((node): ComposerSegment[] => {
      if (node.nodeType === Node.TEXT_NODE) {
        const text = node.textContent?.replaceAll(ZERO_WIDTH_SPACE, "") || "";
        return [{ ...(fallbackText?.type === "text" ? fallbackText : createTextSegment()), text }];
      }
      if (!(node instanceof HTMLElement)) return [];
      const segment = current.get(node.dataset.composerSegmentId || "");
      if (!segment) return [{ ...createTextSegment(), text: (node.innerText || node.textContent || "").replaceAll(ZERO_WIDTH_SPACE, "") }];
      if (segment.type === "guidance") return [segment];
      return [{ ...segment, text: (node.innerText || node.textContent || "").replaceAll(ZERO_WIDTH_SPACE, "") }];
    });
  };

  const removeGuidance = (guidanceId: string, caretSegmentId: string) => {
    pendingCaretSegmentId.current = caretSegmentId;
    onSegmentsChange(segments.filter((segment) => segment.id !== guidanceId));
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLDivElement>) => {
    if (event.defaultPrevented) return;
    if (event.key === "Enter" && !event.shiftKey && actionsEnabled) {
      event.preventDefault();
      onSend();
      return;
    }
    const caret = currentCaret();
    if (!caret || event.key !== "Backspace" && event.key !== "Delete") return;
    const index = segments.findIndex((segment) => segment.id === caret.segmentId);
    const text = segments[index];
    if (text?.type !== "text") return;
    const neighborIndex = event.key === "Backspace" && caret.offset === 0 ? index - 1 : event.key === "Delete" && caret.offset === text.text.length ? index + 1 : -1;
    const neighbor = segments[neighborIndex];
    if (neighbor?.type !== "guidance") return;
    event.preventDefault();
    removeGuidance(neighbor.id, text.id);
  };

  const onPaste = (event: React.ClipboardEvent<HTMLDivElement>) => {
    event.preventDefault();
    insertPlainText(event.currentTarget, event.clipboardData.getData("text/plain").replaceAll("\r\n", "\n"));
  };

  const selectSlash = (entry: CommandEntry) => {
    const caret = currentCaret();
    if (!caret) return;
    const index = segments.findIndex((segment) => segment.id === caret.segmentId);
    const textSegment = segments[index];
    if (index < 0 || textSegment?.type !== "text") return;
    const before = textSegment.text.slice(0, caret.offset);
    const match = before.match(/(?:^|\s)(\/\S*)$/);
    if (!match) return;
    const tokenStart = before.length - match[1].length;
    const prefix = textSegment.text.slice(0, tokenStart);
    const suffix = textSegment.text.slice(caret.offset);
    const leading = createTextSegment(prefix);
    const trailing = createTextSegment(suffix);
    const replacement = entry.action === "insert_text" && entry.insert_text && (entry.kind === "skill" || entry.kind === "mcp")
      ? [leading, createGuidanceSegment(entry as Extract<ComposerSegment, { type: "guidance" }>["entry"]), trailing]
      : [leading, trailing];
    pendingCaretSegmentId.current = trailing.id;
    latestCaret.current = { segmentId: trailing.id, offset: 0 };
    onSegmentsChange([...segments.slice(0, index), ...replacement, ...segments.slice(index + 1)]);
    if (entry.action !== "insert_text") onSlashRead(entry);
  };

  if (session?.archived) return <footer ref={rootRef} className="composer archived-composer"><strong>此会话已归档</strong><button className="restore-session" type="button" onClick={() => void onRestore()}><ArchiveRestore size={16} aria-hidden="true" />恢复会话</button></footer>;
  const connectionHint = actionsEnabled ? undefined : running ? "当前操作正在运行，完成前不可输入或停止" : "正在重连，恢复后可操作";
  const empty = segments.every((segment) => segment.type === "text" && !segment.text);
  const guidance = segments.filter((segment): segment is Extract<ComposerSegment, { type: "guidance" }> => segment.type === "guidance");
  const placeholder = running && !actionsEnabled ? "当前操作正在运行…" : running ? "补充当前任务方向…" : "发送消息…";
  return <footer ref={rootRef} className="composer"><SlashCommandMenu slashToken={slashToken} onSelect={selectSlash} /><div className="composer-input">{empty && <span className="composer-placeholder" aria-hidden="true">{placeholder}</span>}<div ref={editorRef} className="composer-editor" aria-label="消息内容" aria-multiline="true" aria-disabled={!actionsEnabled} aria-describedby={guidance.length ? "composer-selected-guidance" : undefined} contentEditable={actionsEnabled} suppressContentEditableWarning role="textbox" tabIndex={0} onInput={(event) => { const next = readSegments(); const text = event.currentTarget.innerText.replaceAll(ZERO_WIDTH_SPACE, ""); const textSegment = [...next].reverse().find((segment) => segment.type === "text"); if (textSegment?.type === "text") latestCaret.current = { segmentId: textSegment.id, offset: textSegment.text.length }; internalEdit.current = true; onSegmentsChange(next); setSlashToken(text.match(/(?:^|\s)(\/\S*)$/)?.[1] || null); }} onKeyUp={refreshSlashToken} onClick={refreshSlashToken} onScroll={updateScrollThumb} onKeyDown={onKeyDown} onPaste={onPaste} />{guidance.length > 0 && <span id="composer-selected-guidance" className="sr-only">{guidance.map((segment) => `${segment.entry.kind === "mcp" ? "MCP" : "Skill"}：${segment.entry.label}`).join("，")}</span>}{scrollThumb && <span className="composer-scroll-thumb" style={{ height: scrollThumb.height, transform: `translateY(${scrollThumb.top}px)` }} />}</div><div className="composer-toolbar"><PermissionMenu autoApprove={autoApprove} onChange={onAutoApproveChange} /><span className="composer-spacer" /><ContextWindowIndicator context={contextWindow} />{running ? <button className="composer-action is-stop" type="button" aria-label="停止任务" title={connectionHint} disabled={!actionsEnabled} onClick={onStop}><Square size={9} fill="currentColor" strokeWidth={0} aria-hidden="true" /></button> : <button className="composer-action is-send" type="button" aria-label="发送消息" title={connectionHint} disabled={!actionsEnabled} onClick={onSend}><ArrowUp size={13} strokeWidth={2.5} aria-hidden="true" /></button>}</div></footer>;
}

function insertPlainText(editor: HTMLElement, text: string): void {
  const selection = window.getSelection();
  const range = selection?.rangeCount && selection.anchorNode && editor.contains(selection.anchorNode)
    ? selection.getRangeAt(0)
    : document.createRange();
  if (!selection?.rangeCount || !selection.anchorNode || !editor.contains(selection.anchorNode)) {
    range.selectNodeContents(editor);
    range.collapse(false);
  }
  range.deleteContents();
  const textNode = document.createTextNode(text);
  range.insertNode(textNode);
  range.setStartAfter(textNode);
  range.collapse(true);
  selection?.removeAllRanges();
  selection?.addRange(range);
  editor.dispatchEvent(new InputEvent("input", { bubbles: true, data: text, inputType: "insertFromPaste" }));
}

function renderEditorSegments(editor: HTMLElement, segments: ComposerSegment[]): void {
  const fragment = document.createDocumentFragment();
  for (const segment of segments) {
    const node = document.createElement("span");
    node.dataset.composerSegmentId = segment.id;
    if (segment.type === "text") {
      node.className = "composer-text-segment";
      node.dataset.segmentType = "text";
      node.textContent = segment.text || ZERO_WIDTH_SPACE;
    } else {
      const isMcp = segment.entry.kind === "mcp";
      const icon = isMcp ? mcpIcon : skillIcon;
      node.className = `composer-command-tag is-${segment.entry.kind}`;
      node.contentEditable = "false";
      node.dataset.guidanceId = segment.entry.id;
      node.dataset.segmentType = "guidance";
      node.setAttribute("aria-label", `${isMcp ? "MCP" : "Skill"}：${segment.entry.label}`);
      const iconNode = document.createElement("i");
      iconNode.setAttribute("aria-hidden", "true");
      iconNode.style.webkitMaskImage = `url("${icon}")`;
      iconNode.style.maskImage = `url("${icon}")`;
      const label = document.createElement("span");
      label.textContent = segment.entry.label;
      node.append(iconNode, label);
    }
    fragment.append(node);
  }
  editor.replaceChildren(fragment);
}

function PermissionMenu({ autoApprove, onChange }: { autoApprove: boolean; onChange: (enabled: boolean) => void }) {
  const label = autoApprove ? "完全访问" : "默认权限";
  const PermissionIcon = autoApprove ? TriangleAlert : Hand;
  return <DropdownMenu.Root><DropdownMenu.Trigger asChild><button className={`permission-trigger ${autoApprove ? "is-danger" : ""}`} aria-label={`工具权限：${label}`}><PermissionIcon size={13} aria-hidden="true" /><span>{label}</span><ChevronDown size={11} aria-hidden="true" /></button></DropdownMenu.Trigger><DropdownMenu.Portal><DropdownMenu.Content className="permission-menu" side="top" align="start" sideOffset={8}><DropdownMenu.RadioGroup value={autoApprove ? "auto" : "default"} onValueChange={(value) => onChange(value === "auto")}><DropdownMenu.RadioItem value="default"><Hand size={15} aria-hidden="true" /><span>默认权限</span><DropdownMenu.ItemIndicator className="permission-indicator"><Check size={15} aria-hidden="true" /></DropdownMenu.ItemIndicator></DropdownMenu.RadioItem><DropdownMenu.RadioItem className="is-danger" value="auto"><TriangleAlert size={15} aria-hidden="true" /><span>完全访问</span><DropdownMenu.ItemIndicator className="permission-indicator"><Check size={15} aria-hidden="true" /></DropdownMenu.ItemIndicator></DropdownMenu.RadioItem></DropdownMenu.RadioGroup></DropdownMenu.Content></DropdownMenu.Portal></DropdownMenu.Root>;
}

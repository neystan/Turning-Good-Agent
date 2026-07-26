import { useEffect, useLayoutEffect, useRef, useState, type RefObject } from "react";
import { Check, Copy } from "lucide-react";
import ReactMarkdown from "react-markdown";

import type { ChatMessage, TurnState } from "../types";
import { shouldFollowLatest } from "../state/chat_scroll";
import { buildTimelineEntries } from "../state/timeline_entries";

type ChatTimelineProps = {
  sessionId: string | null;
  messages: ChatMessage[];
  turns?: Record<string, TurnState>;
  contentVersion?: number;
  children?: React.ReactNode;
  onRetry?: (message: ChatMessage) => void;
  renderTurn?: (turn: TurnState) => React.ReactNode;
  composerRef?: RefObject<HTMLElement | null>;
};

const savedScrollTop: Record<string, number> = {};

/** 渲染稳定滚动的聊天消息列表。 */
export function ChatTimeline({ sessionId, messages, turns = {}, contentVersion = 0, children, onRetry, renderTurn, composerRef }: ChatTimelineProps) {
  const scrollRef = useRef<HTMLElement>(null);
  const previousSessionId = useRef<string | null>(null);
  const nearBottom = useRef(true);
  const [hasUnread, setHasUnread] = useState(false);

  useLayoutEffect(() => {
    const node = scrollRef.current;
    if (!node || previousSessionId.current === sessionId) return;
    previousSessionId.current = sessionId;
    node.scrollTop = sessionId ? savedScrollTop[sessionId] || node.scrollHeight : 0;
    nearBottom.current = true;
    setHasUnread(false);
  }, [sessionId]);

  useEffect(() => {
    const node = scrollRef.current;
    if (!node) return;
    if (shouldFollowLatest({ nearBottom: nearBottom.current, latestRole: messages.at(-1)?.role, forced: false })) {
      node.scrollTop = node.scrollHeight;
      setHasUnread(false);
    } else {
      setHasUnread(true);
    }
  }, [messages, contentVersion]);

  useEffect(() => {
    /** 输入区高度变化时，仅在用户位于底部时继续跟随最新消息。 */
    const composer = composerRef?.current;
    if (!composer || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      const node = scrollRef.current;
      if (node && nearBottom.current) node.scrollTop = node.scrollHeight;
    });
    observer.observe(composer);
    return () => observer.disconnect();
  }, [composerRef]);

  /** 记录当前位置，仅在底部附近时自动跟随新内容。 */
  const onScroll = () => {
    const node = scrollRef.current;
    if (!node || !sessionId) return;
    savedScrollTop[sessionId] = node.scrollTop;
    nearBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight <= 96;
    if (nearBottom.current) setHasUnread(false);
  };

  /** 将消息列表滚动到最新内容。 */
  const scrollToLatest = () => {
    const node = scrollRef.current;
    if (!node) return;
    node.scrollTop = node.scrollHeight;
    nearBottom.current = true;
    setHasUnread(false);
  };

  const entries = buildTimelineEntries(messages, turns);
  return <section ref={scrollRef} className="chat-scroll" aria-live="polite" onScroll={onScroll}>
    {entries.map((entry) => entry.kind === "message" ? <MessageView key={entry.id} message={entry.message} onRetry={onRetry} /> : <div key={entry.id}>{renderTurn?.(entry.turn)}</div>)}
    {children}
    {hasUnread && <button className="new-message-button" onClick={scrollToLatest}>有新消息</button>}
  </section>;
}

/** 渲染一条安全 Markdown 对话消息。 */
function MessageView({ message, onRetry }: { message: ChatMessage; onRetry?: (message: ChatMessage) => void }) {
  const stopped = message.role === "assistant" && Boolean(message.metadata.incomplete);
  const failed = message.delivery === "failed";
  return <article className={`message ${message.role}`}><div className="message-meta">{message.role === "user" ? "你" : "TGA"}{stopped && <span className="stopped-badge">已停止</span>}{message.delivery === "sending" && <span>发送中</span>}{failed && <><span className="message-error">发送失败</span>{onRetry && <button className="message-retry" onClick={() => onRetry(message)}>重试</button>}</>}</div><div className="markdown"><ReactMarkdown components={{ code: CodeBlock }}>{message.content}</ReactMarkdown></div></article>;
}

/** 渲染带语言标记和复制按钮的代码块。 */
function CodeBlock({ className, children, ...props }: { className?: string; children?: React.ReactNode }) {
  const [copied, setCopied] = useState(false);
  const content = String(children || "").replace(/\n$/, "");
  /** 复制代码并短暂反馈成功状态。 */
  const copy = async () => {
    await navigator.clipboard.writeText(content);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1_200);
  };
  if (!className) return <code {...props}>{children}</code>;
  const language = className.replace("language-", "") || "text";
  const label = copied ? "已复制代码" : "复制代码";
  return <span className="code-block"><span className="code-language">{language}</span><button className="icon-button" aria-label={label} onClick={() => void copy()}>{copied ? <Check size={14} /> : <Copy size={14} />}</button><code className={className} {...props}>{children}</code></span>;
}

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import { Check, Copy } from "lucide-react";
import ReactMarkdown from "react-markdown";

import type { ChatMessage, TurnState } from "../types";

type ChatTimelineProps = {
  sessionId: string | null;
  messages: ChatMessage[];
  turns?: Record<string, TurnState>;
  contentVersion?: number;
  children?: React.ReactNode;
  onRetry?: (message: ChatMessage) => void;
  renderTurn?: (turn: TurnState) => React.ReactNode;
};

const savedScrollTop: Record<string, number> = {};

/** 渲染稳定滚动的聊天消息列表。 */
export function ChatTimeline({ sessionId, messages, turns = {}, contentVersion = 0, children, onRetry, renderTurn }: ChatTimelineProps) {
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
    if (nearBottom.current || messages.at(-1)?.role === "user") {
      node.scrollTop = node.scrollHeight;
      setHasUnread(false);
    } else {
      setHasUnread(true);
    }
  }, [messages, contentVersion]);

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

  const renderedTurns = new Set(messages.filter((message) => message.role === "assistant" && message.request_id).map((message) => message.request_id as string));
  return <section ref={scrollRef} className="chat-scroll" aria-live="polite" onScroll={onScroll}>
    {messages.map((message) => <div key={message.id}><MessageView message={message} onRetry={onRetry} />{message.role === "assistant" && message.request_id && turns[message.request_id] && renderTurn?.(turns[message.request_id])}</div>)}
    {Object.values(turns).filter((turn) => !renderedTurns.has(turn.requestId)).map((turn) => <div key={turn.requestId}>{renderTurn?.(turn)}</div>)}
    {children}
    {hasUnread && <button className="new-message-button" onClick={scrollToLatest}>有新消息</button>}
  </section>;
}

/** 渲染一条安全 Markdown 对话消息。 */
function MessageView({ message, onRetry }: { message: ChatMessage; onRetry?: (message: ChatMessage) => void }) {
  const stopped = message.role === "assistant" && Boolean(message.metadata.incomplete);
  const failed = message.delivery === "failed";
  return <article className={`message ${message.role}`}><div className="message-meta">{message.role === "user" ? "你" : "Turning Good"}{stopped && <span className="stopped-badge">已停止</span>}{message.delivery === "sending" && <span>发送中</span>}{failed && <><span className="message-error">发送失败</span>{onRetry && <button className="message-retry" onClick={() => onRetry(message)}>重试</button>}</>}</div><div className="markdown"><ReactMarkdown components={{ code: CodeBlock }}>{message.content}</ReactMarkdown></div></article>;
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
  return <span className="code-block"><span className="code-language">{language}</span><button className="icon-button" title={copied ? "已复制" : "复制代码"} aria-label={copied ? "已复制代码" : "复制代码"} onClick={() => void copy()}>{copied ? <Check size={14} /> : <Copy size={14} />}</button><code className={className} {...props}>{children}</code></span>;
}

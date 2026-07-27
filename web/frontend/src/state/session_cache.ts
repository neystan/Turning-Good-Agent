import type { ChatMessage, TurnState } from "../types";

type SessionCache = { messages: ChatMessage[]; turns: Record<string, TurnState>; hiddenMessageIds: string[] };

const cacheKey = (sessionId: string) => `tga-session-cache:${sessionId}`;

/** 读取当前浏览器标签页内尚未落盘的会话内容。 */
export function readSessionCache(sessionId: string): SessionCache {
  try {
    const value = sessionStorage.getItem(cacheKey(sessionId));
    if (!value) return { messages: [], turns: {}, hiddenMessageIds: [] };
    const cached = JSON.parse(value) as Partial<SessionCache>;
    return {
      messages: Array.isArray(cached.messages) ? cached.messages : [],
      turns: cached.turns || {},
      hiddenMessageIds: Array.isArray(cached.hiddenMessageIds) ? cached.hiddenMessageIds : [],
    };
  } catch {
    return { messages: [], turns: {}, hiddenMessageIds: [] };
  }
}

/** 保存当前标签页的即时消息与任务过程，不写入会话 JSONL。 */
export function writeSessionCache(sessionId: string, cache: SessionCache): void {
  try {
    sessionStorage.setItem(cacheKey(sessionId), JSON.stringify(cache));
  } catch {
    // 浏览器禁用存储时仍保持当前页面可用。
  }
}

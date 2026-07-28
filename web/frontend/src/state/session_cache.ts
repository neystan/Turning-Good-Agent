import type { ChatMessage, TurnState } from "../types";

export type SessionCache = {
  messages: ChatMessage[];
  turns: Record<string, TurnState>;
  hiddenMessageIds: string[];
  lastEventId: number;
};

const cacheKey = (sessionId: string) => `tga-session-cache:${sessionId}`;

/** 仅在指定会话完成缓存恢复后允许写入，避免空初始状态覆盖原记录。 */
export function shouldWriteSessionCache(sessionId: string | null, hydratedSessionId: string | null): boolean {
  return Boolean(sessionId && sessionId === hydratedSessionId);
}

/** 读取当前浏览器标签页内尚未落盘的会话内容。 */
export function readSessionCache(sessionId: string): SessionCache {
  try {
    const value = sessionStorage.getItem(cacheKey(sessionId));
    if (!value) return emptyCache();
    return normalizeCache(JSON.parse(value) as Partial<SessionCache>);
  } catch {
    return emptyCache();
  }
}

/** 保存当前标签页的消息关联、任务过程和事件游标，不写入会话 JSONL。 */
export function writeSessionCache(sessionId: string, cache: SessionCache): void {
  try {
    sessionStorage.setItem(cacheKey(sessionId), JSON.stringify(normalizeCache(cache)));
  } catch {
    // 浏览器禁用存储时仍保持当前页面可用。
  }
}

/** 规整浏览器缓存，同时保留活动记录与其消息锚点。 */
function normalizeCache(cache: Partial<SessionCache>): SessionCache {
  const messages = Array.isArray(cache.messages) ? cache.messages : [];
  const sourceTurns = cache.turns && typeof cache.turns === "object" ? cache.turns : {};
  const storedCursor = typeof cache.lastEventId === "number" && Number.isInteger(cache.lastEventId) && cache.lastEventId >= 0
    ? cache.lastEventId
    : latestEventId(sourceTurns);
  return {
    messages,
    turns: sourceTurns,
    hiddenMessageIds: Array.isArray(cache.hiddenMessageIds) ? cache.hiddenMessageIds : [],
    lastEventId: storedCursor,
  };
}

/** 兼容旧缓存：从已有事件中补出游标，避免再次回放。 */
function latestEventId(turns: Record<string, TurnState>): number {
  return Object.values(turns).flatMap((turn) => turn.events).reduce((latest, event) =>
    typeof event.event_id === "number" ? Math.max(latest, event.event_id) : latest, 0);
}

function emptyCache(): SessionCache {
  return { messages: [], turns: {}, hiddenMessageIds: [], lastEventId: 0 };
}

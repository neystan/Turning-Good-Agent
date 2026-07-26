import type { ChatMessage, TurnState } from "../types";

export type TimelineEntry =
  | { kind: "message"; id: string; message: ChatMessage }
  | { kind: "turn"; id: string; turn: TurnState };

/** 将消息与真实 turn 交错为稳定的对话时间线。 */
export function buildTimelineEntries(messages: ChatMessage[], turns: Record<string, TurnState>): TimelineEntry[] {
  const entries: TimelineEntry[] = [];
  const renderedTurns = new Set<string>();
  for (const message of messages) {
    const turn = message.request_id ? turns[message.request_id] : undefined;
    if (message.role === "assistant" && turn && !renderedTurns.has(turn.requestId)) {
      entries.push(turnEntry(turn));
      renderedTurns.add(turn.requestId);
    }
    entries.push({ kind: "message", id: `message-${message.id}`, message });
    if (message.role === "user" && turn && !renderedTurns.has(turn.requestId)) {
      entries.push(turnEntry(turn));
      renderedTurns.add(turn.requestId);
    }
  }
  for (const turn of Object.values(turns)) {
    if (!renderedTurns.has(turn.requestId)) entries.push(turnEntry(turn));
  }
  return entries;
}

/** 创建可复用的 turn 时间线条目。 */
function turnEntry(turn: TurnState): TimelineEntry {
  return { kind: "turn", id: `turn-${turn.requestId}`, turn };
}

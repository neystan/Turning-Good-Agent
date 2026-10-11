import { SessionHistoryLoader } from "../web/frontend/src/state/history_loader";

/** 验证旧会话的慢响应不会覆盖新会话。 */
async function testNewerHistoryLoadWins(): Promise<void> {
  const loader = new SessionHistoryLoader();
  let resolveA: ((value: string[]) => void) | undefined;
  let resolveB: ((value: string[]) => void) | undefined;
  const loadA = loader.load("session-a", () => new Promise<string[]>((resolve) => { resolveA = resolve; }));
  const loadB = loader.load("session-b", () => new Promise<string[]>((resolve) => { resolveB = resolve; }));

  resolveB?.(["B"]);
  const resultB = await loadB;
  resolveA?.(["A"]);
  const resultA = await loadA;

  if (resultB?.[0] !== "B") throw new Error("最新会话历史未返回");
  if (resultA !== null) throw new Error("旧会话历史覆盖了新会话");
}

void testNewerHistoryLoadWins().then(() => console.log("web history loader assertions passed"));

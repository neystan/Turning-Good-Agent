import { expect, test, type Page } from "@playwright/test";

const baseUrl = process.env.TGA_WEB_URL || "http://127.0.0.1:8000";

async function mockAppShell(page: Page) {
  await page.route(/\/api\/sessions\?archived=false$/, (route) => route.fulfill({ contentType: "application/json", body: "[]" }));
  await page.route(/\/api\/sessions\?archived=true$/, (route) => route.fulfill({ contentType: "application/json", body: "[]" }));
  await page.route("**/api/settings/ui", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ auto_approve_tools: false }) }));
}

async function installNoticeSocket(page: Page) {
  await page.addInitScript(() => {
    const sockets: Array<{ url: string; onopen: ((event: Event) => void) | null; onmessage: ((event: MessageEvent<string>) => void) | null; onclose: ((event: CloseEvent) => void) | null }> = [];
    class FakeWebSocket {
      static OPEN = 1;
      static CONNECTING = 0;
      static CLOSING = 2;
      static CLOSED = 3;
      readyState = FakeWebSocket.OPEN;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent<string>) => void) | null = null;
      onclose: ((event: CloseEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;

      constructor(readonly url: string) {
        sockets.push(this);
        queueMicrotask(() => this.onopen?.(new Event("open")));
      }

      send() {}

      close() {
        this.readyState = FakeWebSocket.CLOSED;
        this.onclose?.(new Event("close") as CloseEvent);
      }
    }
    Object.assign(window, {
      WebSocket: FakeWebSocket,
      __tgaEmitProactive: (payload: unknown) => sockets.filter((socket) => socket.url.includes("/ws/proactive")).forEach((socket) => socket.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>)),
    });
  });
}

function notice(id: string, title: string, target: string) {
  return {
    type: "notice",
    id,
    domain: "incident",
    entity_id: id,
    severity: "warning",
    title,
    message: `${title} 的后台结果已准备好。`,
    target,
    proactive_revision: 1,
    owner: { mode: "owner", writable: true, owner_id: "web-owner", owner_kind: "web", owner_pid: 42 },
  };
}

test("proactive notices persist in memory, queue after three, and navigate to their target", async ({ page }) => {
  await installNoticeSocket(page);
  await mockAppShell(page);
  await page.goto(baseUrl);

  for (const payload of [
    notice("notice-1", "Cron 已完成", "#proactive/cron"),
    notice("notice-2", "Dream 已更新", "#proactive/memory"),
    notice("notice-3", "Skill Draft 已生成", "#proactive/skills"),
    notice("notice-4", "发现新的 Incident", "#proactive/incidents"),
  ]) {
    await page.evaluate((value) => (window as unknown as { __tgaEmitProactive: (payload: unknown) => void }).__tgaEmitProactive(value), payload);
  }

  await expect(page.locator(".notice")).toHaveCount(3);
  await expect(page.getByRole("button", { name: "查看：发现新的 Incident" })).toHaveCount(0);

  await page.getByRole("button", { name: "关闭提示：Cron 已完成" }).click();
  await expect(page.getByRole("button", { name: "查看：发现新的 Incident" })).toBeVisible();

  await page.getByRole("button", { name: "查看：发现新的 Incident" }).click();
  await expect(page).toHaveURL(/#proactive\/incidents$/);
  await expect(page.locator(".notice")).toHaveCount(2);
});

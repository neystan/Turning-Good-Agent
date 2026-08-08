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

function notice(id: string, title: string, target: string, severity: "info" | "warning" | "error" = "info") {
  return {
    type: "notice",
    id,
    domain: "incident",
    entity_id: id,
    severity,
    title,
    message: `${title} 的后台结果已准备好。`,
    target,
    proactive_revision: 1,
    owner: { mode: "owner", writable: true, owner_id: "web-owner", owner_kind: "web", owner_pid: 42 },
  };
}

test("proactive notices cap visible cards at three and navigate to their target", async ({ page }) => {
  await page.clock.install();
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
  await expect(page.locator(".conversation > .notice-region")).toHaveCount(1);
  await expect(page.locator(".notice-content strong")).toHaveText(["Dream 已更新", "Skill Draft 已生成", "发现新的 Incident"]);
  const [noticeRail, composer] = await Promise.all([page.locator(".notice-region--conversation").boundingBox(), page.locator(".composer").boundingBox()]);
  expect(noticeRail?.width).toBe(composer?.width);

  await page.getByRole("button", { name: "关闭提示：Skill Draft 已生成" }).click();
  await expect(page.locator(".notice.is-exiting")).toHaveCount(1);
  await page.clock.fastForward(180);
  await expect(page.getByRole("button", { name: "查看：发现新的 Incident" })).toBeVisible();

  await page.getByRole("button", { name: "查看：发现新的 Incident" }).click();
  await expect(page).toHaveURL(/#proactive\/incidents$/);
  await expect(page.locator(".notice")).toHaveCount(2);
});

test("proactive notices expire by severity, pause under pointer or keyboard focus, and preserve Composer focus", async ({ page }) => {
  await page.clock.install();
  await installNoticeSocket(page);
  await mockAppShell(page);
  await page.goto(baseUrl);

  const composer = page.getByRole("textbox", { name: "消息内容" });
  await composer.focus();
  await expect(composer).toBeFocused();

  await page.evaluate((payload) => (window as unknown as { __tgaEmitProactive: (value: unknown) => void }).__tgaEmitProactive(payload), notice("notice-info", "信息提醒", "#proactive/cron"));
  await expect(page.getByRole("button", { name: "查看：信息提醒" })).toBeVisible();
  await expect(composer).toBeFocused();
  await page.clock.fastForward(3_900);
  await expect(page.getByRole("button", { name: "查看：信息提醒" })).toBeVisible();
  await page.clock.fastForward(200);
  await expect(page.getByRole("button", { name: "查看：信息提醒" })).toHaveCount(0);

  await page.evaluate((payload) => (window as unknown as { __tgaEmitProactive: (value: unknown) => void }).__tgaEmitProactive(payload), notice("notice-error", "错误提醒", "#proactive/incidents", "error"));
  await expect(page.getByRole("button", { name: "查看：错误提醒" })).toBeVisible();
  await page.clock.fastForward(6_900);
  await expect(page.getByRole("button", { name: "查看：错误提醒" })).toBeVisible();
  await page.clock.fastForward(200);
  await expect(page.getByRole("button", { name: "查看：错误提醒" })).toHaveCount(0);

  await page.evaluate((payload) => (window as unknown as { __tgaEmitProactive: (value: unknown) => void }).__tgaEmitProactive(payload), notice("notice-warning", "警告提醒", "#proactive/breakbeat", "warning"));
  const warningNotice = page.locator(".notice--warning", { hasText: "警告提醒" });
  await expect(warningNotice).toBeVisible();
  await page.clock.fastForward(3_900);
  await expect(warningNotice).toBeVisible();
  await page.clock.fastForward(200);
  await expect(warningNotice).toHaveCount(0);

  await page.evaluate((payload) => (window as unknown as { __tgaEmitProactive: (value: unknown) => void }).__tgaEmitProactive(payload), notice("notice-hover", "悬停时暂停", "#proactive/breakbeat"));
  const hoverNotice = page.locator(".notice", { hasText: "悬停时暂停" });
  await page.clock.fastForward(1_000);
  await hoverNotice.hover();
  await page.clock.fastForward(5_000);
  await expect(hoverNotice).toBeVisible();
  await page.mouse.move(0, 0);
  await page.clock.fastForward(2_800);
  await expect(hoverNotice).toBeVisible();
  await page.clock.fastForward(300);
  await expect(hoverNotice).toHaveCount(0);

  await page.evaluate((payload) => (window as unknown as { __tgaEmitProactive: (value: unknown) => void }).__tgaEmitProactive(payload), notice("notice-focus", "键盘焦点暂停", "#proactive/memory"));
  const focusAction = page.getByRole("button", { name: "查看：键盘焦点暂停" });
  await page.clock.fastForward(1_000);
  await focusAction.focus();
  await page.clock.fastForward(5_000);
  await expect(focusAction).toBeVisible();
  await composer.focus();
  await page.clock.fastForward(2_800);
  await expect(focusAction).toBeVisible();
  await page.clock.fastForward(300);
  await expect(focusAction).toHaveCount(0);
});

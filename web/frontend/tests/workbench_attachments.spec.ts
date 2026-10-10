import { expect, test } from "@playwright/test";

const baseUrl = process.env.TGA_WEB_URL || "http://127.0.0.1:8000";

async function mockWorkbench(page: import("@playwright/test").Page, supportsVision: boolean) {
  const session = { id: "attachment-session", channel: "web", title: "附件测试", pinned: false, archived: false, created_at: "2026-08-20T00:00:00Z", updated_at: "2026-08-20T00:00:00Z" };
  await page.route(/\/api\/sessions\?archived=false$/, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify([]) }));
  await page.route(/\/api\/sessions\?archived=true$/, (route) => route.fulfill({ contentType: "application/json", body: "[]" }));
  await page.route("**/api/settings/ui", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ auto_approve_tools: false }) }));
  await page.route("**/api/control/config", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ active: { multi_agent: { enabled: false } } }) }));
  await page.route("**/api/control/commands", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ entries: [] }) }));
  await page.route("**/api/control/model-capabilities", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ supports_vision: supportsVision }) }));
  await page.route("**/api/sessions", async (route) => {
    if (route.request().method() === "POST") return route.fulfill({ contentType: "application/json", body: JSON.stringify(session) });
    return route.fallback();
  });
  await page.addInitScript(() => {
    class FakeWebSocket {
      static OPEN = 1;
      static CONNECTING = 0;
      readyState = FakeWebSocket.OPEN;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent<string>) => void) | null = null;
      onclose: ((event: CloseEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      constructor(readonly url: string) { queueMicrotask(() => this.onopen?.(new Event("open"))); }
      send(raw: string) {
        const action = JSON.parse(raw) as { type?: string; client_action_id?: string; session_id?: string };
        (window as typeof window & { __tgaAttachmentActions?: unknown[] }).__tgaAttachmentActions ||= [];
        (window as typeof window & { __tgaAttachmentActions: unknown[] }).__tgaAttachmentActions.push(action);
        if (action.type === "message.send") queueMicrotask(() => this.onmessage?.({ data: JSON.stringify({ type: "message.accepted", session_id: action.session_id || "attachment-session", request_id: "request-attachment", client_action_id: action.client_action_id }) } as MessageEvent<string>));
      }
      close() { this.onclose?.({} as CloseEvent); }
    }
    Object.assign(window, { WebSocket: FakeWebSocket });
  });
  return session;
}

test("document attachment-only send uploads first and then sends opaque ids", async ({ page }) => {
  const sent: Record<string, unknown>[] = [];
  const session = await mockWorkbench(page, false);
  await page.route(`**/api/control/sessions/${session.id}/attachments`, async (route) => {
    expect(route.request().method()).toBe("POST");
    await route.fulfill({ contentType: "application/json", body: JSON.stringify({ attachments: [{ attachment_id: "att_note", source: "document", filename: "note.txt", mime_type: "text/plain", size_bytes: 5, processing: "ready", truncated: false, error: null }] }) });
  });
  await page.goto(`${baseUrl}/`);
  await page.setInputFiles('[data-testid="attachment-input"]', { name: "note.txt", mimeType: "text/plain", buffer: Buffer.from("hello") });
  await expect(page.locator(".attachment-card")).toContainText("note.txt");
  await expect(page.getByRole("button", { name: "发送消息" })).toBeEnabled();
  await page.getByRole("button", { name: "发送消息" }).click();

  await expect.poll(() => page.evaluate(() => (window as typeof window & { __tgaAttachmentActions?: Array<Record<string, unknown>> }).__tgaAttachmentActions?.find((item) => item.type === "message.send"))).toMatchObject({ attachment_ids: ["att_note"] });
  await expect(page.locator(".attachment-card")).toHaveCount(0);
  await expect(page.locator(".message.user .sent-attachment.document")).toContainText("note.txt");
  sent.push(...await page.evaluate(() => (window as typeof window & { __tgaAttachmentActions?: Array<Record<string, unknown>> }).__tgaAttachmentActions || []));
  expect(sent.some((item) => item.type === "message.send")).toBe(true);
});

test("unsupported vision keeps the image card visible and disables send", async ({ page }) => {
  await mockWorkbench(page, false);
  await page.goto(`${baseUrl}/`);
  await page.setInputFiles('[data-testid="attachment-input"]', { name: "photo.png", mimeType: "image/png", buffer: Buffer.from("png") });

  await expect(page.locator(".attachment-capability-error")).toHaveText("当前模型不支持图片理解");
  await expect(page.getByRole("button", { name: "发送消息" })).toBeDisabled();
  await expect(page.locator(".attachment-card")).toContainText("photo.png");
});

test("sent image metadata renders an authenticated preview control", async ({ page }) => {
  const session = await mockWorkbench(page, true);
  await page.route(`**/api/sessions/${session.id}/messages`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify([{ id: "message-1", role: "user", content: "", created_at: "2026-08-20T00:00:00Z", metadata: { attachments: [{ attachment_id: "att_photo", source: "image", filename: "photo.png", mime_type: "image/png", size_bytes: 8, processing: "ready", truncated: false, error: null }] } }]) }));
  await page.route(`**/api/control/sessions/${session.id}/attachments/att_photo/preview`, (route) => route.fulfill({ contentType: "image/png", body: Buffer.from("png") }));
  await page.goto(`${baseUrl}/sessions/${session.id}`);

  const card = page.locator(".sent-attachment.image");
  await expect(card).toBeVisible();
  await card.click();
  await expect(page.getByRole("dialog", { name: "图片预览" })).toBeVisible();
  await expect(page.getByRole("button", { name: "关闭图片预览" })).toBeVisible();
});

test("attachment cards keep the Composer stable across themes and narrow viewports", async ({ page }, testInfo) => {
  await mockWorkbench(page, true);
  await page.goto(`${baseUrl}/`);
  const longName = "quarterly-report-with-a-deliberately-long-filename-for-overflow-check.pdf";
  const png = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII=", "base64");
  await page.setInputFiles('[data-testid="attachment-input"]', [
    { name: longName, mimeType: "application/pdf", buffer: Buffer.from("pdf") },
    { name: "preview.png", mimeType: "image/png", buffer: png },
  ]);

  for (const theme of ["dark", "light"] as const) {
    if (theme === "light") await page.getByRole("button", { name: "切换主题" }).click();
    await expect(page.locator(".attachment-card")).toHaveCount(2);
    const geometry = await page.locator(".composer").evaluate((composer) => {
      const toolbar = composer.querySelector<HTMLElement>(".composer-toolbar")!.getBoundingClientRect();
      const cards = composer.querySelector<HTMLElement>(".attachment-cards")!.getBoundingClientRect();
      const name = composer.querySelector<HTMLElement>(".attachment-name")!;
      return { toolbarHeight: toolbar.height, cardsBottom: cards.bottom, toolbarTop: toolbar.top, nameOverflow: name.scrollWidth > name.clientWidth, border: getComputedStyle(composer.querySelector<HTMLElement>(".attachment-card")!).borderTopWidth };
    });
    expect(geometry.toolbarHeight).toBeGreaterThanOrEqual(48);
    expect(geometry.nameOverflow).toBe(true);
    expect(geometry.border).toBe("0px");
    await testInfo.attach(`attachments-${theme}`, { body: await page.screenshot({ fullPage: true }), contentType: "image/png" });
  }

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.locator(".attachment-card").first()).toBeInViewport();
  const narrowWidth = await page.locator(".composer").evaluate((node) => node.getBoundingClientRect().width);
  expect(narrowWidth).toBeLessThanOrEqual(390);
  await testInfo.attach("attachments-narrow", { body: await page.screenshot({ fullPage: true }), contentType: "image/png" });
});

test("image preview closes by Escape and returns focus to the Composer", async ({ page }) => {
  const session = await mockWorkbench(page, true);
  await page.route(`**/api/sessions/${session.id}/messages`, (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify([{ id: "message-2", role: "user", content: "", created_at: "2026-08-20T00:00:00Z", metadata: { attachments: [{ attachment_id: "att_photo", source: "image", filename: "photo.png", mime_type: "image/png", size_bytes: 8, processing: "ready", truncated: false, error: null }] } }]) }));
  await page.goto(`${baseUrl}/sessions/${session.id}`);
  await page.locator(".sent-attachment.image").click();
  await expect(page.getByRole("dialog", { name: "图片预览" })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog", { name: "图片预览" })).toHaveCount(0);
  await expect(page.getByLabel("消息内容")).toBeFocused();
});

import { expect, test } from "@playwright/test";

const baseUrl = process.env.TGA_WEB_URL || "http://127.0.0.1:8000";

test("empty state stays visually close to the composer", async ({ page }) => {
  await page.goto(baseUrl);

  const gap = await page.evaluate(() => {
    const emptyState = document.querySelector<HTMLElement>(".empty-state");
    const composer = document.querySelector<HTMLElement>(".composer");
    if (!emptyState || !composer) throw new Error("empty workbench controls are missing");
    return composer.getBoundingClientRect().top - emptyState.getBoundingClientRect().bottom;
  });

  expect(gap).toBeLessThanOrEqual(160);
});

test("light theme separates the navigation and workspace surfaces", async ({ page }) => {
  await page.goto(baseUrl);
  await page.getByRole("button", { name: "切换主题" }).click();

  const contrast = await page.evaluate(() => {
    const parseRgb = (value: string) => value.match(/\d+/g)?.slice(0, 3).map(Number) || [];
    const sidebarColor = parseRgb(getComputedStyle(document.querySelector<HTMLElement>(".sidebar")!).backgroundColor);
    const topbarColor = parseRgb(getComputedStyle(document.querySelector<HTMLElement>(".topbar")!).backgroundColor);
    const workspaceColor = parseRgb(getComputedStyle(document.querySelector<HTMLElement>(".conversation")!).backgroundColor);
    const distance = (left: number[], right: number[]) => Math.abs(left[0] - right[0]) + Math.abs(left[1] - right[1]) + Math.abs(left[2] - right[2]);
    return { chromeDifference: distance(sidebarColor, topbarColor), workspaceDifference: distance(sidebarColor, workspaceColor) };
  });

  expect(contrast.chromeDifference).toBeLessThanOrEqual(6);
  expect(contrast.workspaceDifference).toBeGreaterThanOrEqual(18);
});

test("empty state provides three concise task starting points", async ({ page }) => {
  await page.goto(baseUrl);
  await expect(page.locator(".empty-examples li")).toHaveCount(3);
});

test("dark theme keeps its original workspace depth without outlined input surfaces", async ({ page }) => {
  await page.goto(baseUrl);

  const result = await page.evaluate(() => {
    const conversation = document.querySelector<HTMLElement>(".conversation")!;
    const sidebar = document.querySelector<HTMLElement>(".sidebar")!;
    const composer = document.querySelector<HTMLElement>(".composer")!;
    return {
      conversationColor: getComputedStyle(conversation).backgroundColor,
      pageColor: getComputedStyle(document.body).backgroundColor,
      sidebarColor: getComputedStyle(sidebar).backgroundColor,
      composerBorderWidth: getComputedStyle(composer).borderTopWidth,
    };
  });

  expect(result.conversationColor).toBe(result.pageColor);
  expect(result.sidebarColor).not.toBe(result.conversationColor);
  expect(result.composerBorderWidth).toBe("0px");
});

test("topbar and composer keep a shared 36px control baseline without outlines", async ({ page }) => {
  await page.goto(baseUrl);

  const metrics = await page.evaluate(() => {
    const sizes = (selector: string) => [...document.querySelectorAll<HTMLElement>(selector)].map((element) => {
      const box = element.getBoundingClientRect();
      return { width: box.width, height: box.height };
    });
    const title = document.querySelector<HTMLElement>(".title-block h1")!;
    const label = document.querySelector<HTMLElement>(".connection-label")!;
    const toolbar = document.querySelector<HTMLElement>(".composer-toolbar")!;
    const action = document.querySelector<HTMLElement>(".composer-action")!;
    const context = document.querySelector<HTMLElement>(".context-window-indicator")!;
    const toolbarBox = toolbar.getBoundingClientRect();
    return {
      topActions: sizes(".top-actions .icon-button"),
      titleSize: Number.parseFloat(getComputedStyle(title).fontSize),
      labelSize: Number.parseFloat(getComputedStyle(label).fontSize),
      composerBorderWidth: getComputedStyle(document.querySelector<HTMLElement>(".composer")!).borderTopWidth,
      actionHeight: action.getBoundingClientRect().height,
      contextHeight: context.getBoundingClientRect().height,
      actionOffset: Math.abs(action.getBoundingClientRect().top + action.getBoundingClientRect().height / 2 - (toolbarBox.top + toolbarBox.height / 2)),
      contextOffset: Math.abs(context.getBoundingClientRect().top + context.getBoundingClientRect().height / 2 - (toolbarBox.top + toolbarBox.height / 2)),
    };
  });

  expect(metrics.topActions).toEqual([{ width: 36, height: 36 }, { width: 36, height: 36 }]);
  expect(metrics.titleSize).toBeGreaterThanOrEqual(16);
  expect(metrics.labelSize).toBe(12);
  expect(metrics.composerBorderWidth).toBe("0px");
  expect(metrics.actionHeight).toBe(36);
  expect(metrics.contextHeight).toBe(36);
  expect(metrics.actionOffset, JSON.stringify(metrics)).toBeLessThanOrEqual(1);
  expect(metrics.contextOffset).toBeLessThanOrEqual(1);
});

test("long composer input expands upward without moving its action baseline", async ({ page }) => {
  await page.goto(baseUrl);

  const before = await page.locator(".composer-action").boundingBox();
  await page.getByLabel("消息内容").fill(Array.from({ length: 24 }, () => "保留工具栏稳定位置的长输入内容").join("\n"));
  const after = await page.locator(".composer-action").boundingBox();

  expect(before).not.toBeNull();
  expect(after).not.toBeNull();
  expect(after!.y).toBeCloseTo(before!.y, 0);
  expect(after!.height).toBe(36);
});

test("proactive card walls stay natural-height, two-column, token-themed, and narrow-safe", async ({ page }, testInfo) => {
  const owner = { mode: "owner", writable: true, owner_id: "web-owner", owner_kind: "web", owner_pid: 42 };
  const usage = { calls: 0, input_tokens: 0, output_tokens: 0, total_tokens: 0 };
  const empty = (domain: "breakbeat" | "dream" | "skill" | "incident") => ({
    type: "snapshot",
    domain,
    data: domain === "breakbeat" ? { items: [], cursors: {}, next_run_at: null, usage }
      : domain === "dream" ? { cursors: {}, next_run_at: null, usage, memory: { user: "", soul: "" }, memory_tokens: { user_tokens: 0, soul_tokens: 0, total_tokens: 0 }, profile_limits: { user_profile_token_limit: 12000, soul_profile_token_limit: 4000, profile_total_token_limit: 16000 }, timezone: "Asia/Shanghai" }
        : domain === "skill" ? { observations: [], drafts: [], next_run_at: null, usage }
          : { incidents: [] },
    runtime: { running: false, next_run_at: null, entity_states: {} },
    proactive_revision: 1,
    owner,
  });
  const longPrompt = Array.from({ length: 28 }, (_, index) => `完整 Prompt 第 ${index + 1} 行`).join("\n");
  const cron = {
    type: "snapshot",
    domain: "cron",
    data: { jobs: [
      { id: "visual-a", cron: "0 9 * * *", created_at: "2026-08-01T08:00:00+08:00", prompt: longPrompt, recurring: true, delivery_channels: ["cli"], updated_at: "2026-08-02T08:00:00+08:00", next_run_at: "2026-08-03T09:00:00+08:00" },
      { id: "visual-b", cron: null, created_at: "2026-08-01T08:00:00+08:00", prompt: "短 Prompt", recurring: false, delivery_channels: ["web"], updated_at: "2026-08-02T08:00:00+08:00", next_run_at: "2026-08-04T09:00:00+08:00" },
    ], usage },
    runtime: { running: false, next_run_at: "2026-08-03T09:00:00+08:00", entity_states: { "visual-a": "idle", "visual-b": "idle" } },
    proactive_revision: 1,
    owner,
  };
  await page.addInitScript((snapshots) => {
    const urls: string[] = [];
    class FakeWebSocket {
      static OPEN = 1;
      static CONNECTING = 0;
      readyState = 1;
      onopen: ((event: Event) => void) | null = null;
      onmessage: ((event: MessageEvent<string>) => void) | null = null;
      onclose: ((event: CloseEvent) => void) | null = null;
      onerror: ((event: Event) => void) | null = null;
      constructor(readonly url: string) {
        urls.push(url);
        queueMicrotask(() => {
          this.onopen?.(new Event("open"));
          if (url.includes("/ws/proactive")) snapshots.forEach((payload) => this.onmessage?.({ data: JSON.stringify(payload) } as MessageEvent<string>));
        });
      }
      send() {}
      close() {}
    }
    Object.assign(window, { WebSocket: FakeWebSocket, __tgaSocketUrls: () => urls });
  }, [cron, empty("breakbeat"), empty("dream"), empty("skill"), empty("incident")]);
  await page.route(/\/api\/sessions\?archived=(?:false|true)$/, (route) => route.fulfill({ contentType: "application/json", body: "[]" }));
  await page.route("**/api/settings/ui", (route) => route.fulfill({ contentType: "application/json", body: JSON.stringify({ auto_approve_tools: false }) }));

  await page.goto(`${baseUrl}/#proactive/cron`);
  const socketUrls = await page.evaluate(() => (window as typeof window & { __tgaSocketUrls: () => string[] }).__tgaSocketUrls());
  expect(socketUrls.some((url) => url.endsWith("/ws/web"))).toBe(true);
  expect(socketUrls.some((url) => url.endsWith("/ws"))).toBe(false);
  await expect(page.locator('[data-proactive-domain="cron"]')).toBeVisible();
  await testInfo.attach("proactive-cron-dark", { body: await page.screenshot({ fullPage: true }), contentType: "image/png" });

  await page.getByRole("button", { name: "隐藏会话栏" }).click();
  const sidebar = page.getByRole("complementary", { name: "会话管理" });
  await expect(sidebar.getByRole("button", { name: "打开 Cron", exact: true })).toBeVisible();
  await expect(sidebar.getByRole("button", { name: "打开 Incidents", exact: true })).toBeVisible();
  const desktop = await page.locator('.proactive-card-grid').evaluate((grid) => {
    const cards = [...grid.children] as HTMLElement[];
    return {
      columns: new Set(cards.map((card) => Math.round(card.getBoundingClientRect().x))).size,
      firstHeight: cards[0].getBoundingClientRect().height,
      firstScrollHeight: cards[0].scrollHeight,
      backgroundImages: cards.map((card) => getComputedStyle(card).backgroundImage),
      borderWidths: cards.map((card) => getComputedStyle(card).borderTopWidth),
    };
  });
  expect(desktop.columns).toBe(2);
  expect(desktop.firstHeight).toBe(desktop.firstScrollHeight);
  expect(desktop.firstHeight).toBeGreaterThan(500);
  expect(desktop.backgroundImages).toEqual(["none", "none"]);
  expect(desktop.borderWidths).toEqual(["0px", "0px"]);
  await testInfo.attach("proactive-dark-desktop", { body: await page.screenshot({ fullPage: true }), contentType: "image/png" });

  await page.evaluate(() => localStorage.setItem("tga-theme", "light"));
  await page.reload();
  await expect(page.locator('[data-theme="light"]')).toHaveCount(1);
  await page.setViewportSize({ width: 740, height: 760 });
  const narrowColumns = await page.locator('.proactive-card-grid').evaluate((grid) => new Set([...grid.children].map((card) => Math.round((card as HTMLElement).getBoundingClientRect().x))).size);
  expect(narrowColumns).toBe(1);
  await page.locator(".proactive-scroll-viewport").evaluate((viewport) => { viewport.scrollTop = viewport.scrollHeight; });
  await expect(page.getByRole("button", { name: "删除 Cron visual-b" })).toBeVisible();
  await testInfo.attach("proactive-light-narrow", { body: await page.screenshot({ fullPage: true }), contentType: "image/png" });
});

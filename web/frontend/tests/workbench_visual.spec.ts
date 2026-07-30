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

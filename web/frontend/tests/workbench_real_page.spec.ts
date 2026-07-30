import { expect, test } from "@playwright/test";

const baseUrl = process.env.TGA_WEB_URL || "http://127.0.0.1:8000";
const enabled = process.env.TGA_REAL_PAGE === "1";

async function liveSessionUrl(page: import("@playwright/test").Page) {
  const response = await page.request.get(`${baseUrl}/api/sessions?archived=false`);
  expect(response.ok()).toBeTruthy();
  const sessions = await response.json() as Array<{ id: string }>;
  test.skip(!sessions[0], "The live browser review needs one active session.");
  return `${baseUrl}/sessions/${encodeURIComponent(sessions[0].id)}`;
}

test.describe("live workbench review", () => {
  test.skip(!enabled, "Set TGA_REAL_PAGE=1 to run against the local API without network mocks.");

  test("real page hides Slash catalog when focus leaves Composer without clearing draft", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 980 });
    await page.goto(await liveSessionUrl(page), { waitUntil: "domcontentloaded" });
    await page.getByLabel("消息内容").fill("/");
    await expect(page.getByRole("listbox", { name: "Slash 命令" })).toBeVisible();

    await page.getByRole("button", { name: "打开会话检查器" }).click();

    await expect(page.getByRole("listbox", { name: "Slash 命令" })).toBeHidden();
    await expect(page.getByLabel("消息内容")).toContainText("/");
  });

  test("real page removes the Slash surface when its query has no matches", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 980 });
    await page.goto(await liveSessionUrl(page), { waitUntil: "domcontentloaded" });

    const composer = page.getByLabel("消息内容");
    await composer.fill("/");
    await expect(page.getByRole("listbox", { name: "Slash 命令" })).toBeVisible();

    await composer.fill("/tga-no-such-command");
    await expect(page.getByRole("listbox", { name: "Slash 命令" })).toHaveCount(0);
  });

  test("real page completes a Slash command after regular text", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 980 });
    await page.goto(await liveSessionUrl(page), { waitUntil: "domcontentloaded" });

    await page.getByLabel("消息内容").fill("请 /con");
    await expect(page.getByRole("listbox", { name: "Slash 命令" })).toBeVisible();
    await expect(page.getByRole("option", { name: "查看上下文" })).toBeVisible();
  });

  test("real page anchors Slash command summaries to the menu right edge", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 980 });
    await page.goto(await liveSessionUrl(page), { waitUntil: "domcontentloaded" });
    await page.getByLabel("消息内容").fill("/");

    const menu = page.getByRole("listbox", { name: "Slash 命令" });
    await expect(menu).toBeVisible();
    const geometry = await menu.getByRole("option").first().evaluate((option) => {
      const summary = option.querySelector<HTMLElement>(".slash-command-summary");
      if (!summary) throw new Error("Slash command summary is missing");
      const optionBox = option.getBoundingClientRect();
      const summaryBox = summary.getBoundingClientRect();
      return { optionWidth: optionBox.width, rightInset: optionBox.right - summaryBox.right };
    });

    expect(geometry.optionWidth).toBeGreaterThan(700);
    expect(geometry.rightInset).toBeCloseTo(9, 0);
  });

  test("real page treats a selected Skill as inline editable text", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 980 });
    await page.goto(await liveSessionUrl(page), { waitUntil: "domcontentloaded" });

    const editor = page.getByLabel("消息内容");
    await editor.fill("分析任务 /grilling");
    await page.getByRole("option", { name: "grilling" }).click();

    const tag = editor.locator('[data-guidance-id="skill.grilling"]');
    await expect(tag).toHaveAttribute("contenteditable", "false");
    await expect(tag).toHaveCSS("background-color", "rgba(0, 0, 0, 0)");
    await expect(tag).toHaveCSS("vertical-align", "baseline");
    await editor.press("End");
    await page.keyboard.type(" 继续");
    await expect.poll(() => editor.evaluate((node) => node.textContent?.replaceAll("\u200B", ""))).toContain("分析任务 grilling 继续");
  });

  test("real page restores the regular inspector after closing a Slash read", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 980 });
    await page.goto(await liveSessionUrl(page), { waitUntil: "domcontentloaded" });
    await page.getByLabel("消息内容").fill("/context");
    await page.getByRole("option", { name: "查看上下文" }).click();
    await expect(page.locator(".inspector-control-content")).toBeVisible();

    await page.getByRole("button", { name: "关闭会话检查器" }).click();
    await expect(page.locator(".conversation")).not.toHaveClass(/is-inspector-open/);
    await page.getByRole("button", { name: "打开会话检查器" }).click();

    await expect(page.locator(".inspector-control-content")).toHaveCount(0);
  });

  test("real page closes and clears a Slash read when switching sessions", async ({ page }) => {
    const response = await page.request.get(`${baseUrl}/api/sessions?archived=false`);
    expect(response.ok()).toBeTruthy();
    const sessions = await response.json() as Array<{ id: string }>;
    test.skip(sessions.length < 2, "The live browser review needs two active sessions.");

    await page.setViewportSize({ width: 1440, height: 980 });
    await page.goto(`${baseUrl}/sessions/${encodeURIComponent(sessions[0].id)}`, { waitUntil: "domcontentloaded" });
    await page.getByLabel("消息内容").fill("/context");
    await page.getByRole("option", { name: "查看上下文" }).click();
    await expect(page.locator(".inspector-control-content")).toBeVisible();

    await page.locator(".session-select").nth(1).click();

    await expect(page.locator(".conversation")).not.toHaveClass(/is-inspector-open/);
    await expect(page.locator(".inspector-control-content")).toHaveCount(0);
  });

  test("real page scrolls the Slash menu with its keyboard selection", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 980 });
    await page.goto(await liveSessionUrl(page), { waitUntil: "domcontentloaded" });
    await page.getByLabel("消息内容").fill("/");

    const menu = page.getByRole("listbox", { name: "Slash 命令" });
    const viewport = menu.locator(".scroll-area-viewport");
    await expect(menu).toBeVisible();
    const options = menu.getByRole("option");
    await expect(options.first()).toBeVisible();
    const optionCount = await options.count();
    test.skip(optionCount < 3, "The live Slash Catalog does not contain enough entries to exercise keyboard selection.");
    const overflowBeforeSelection = await viewport.evaluate((element) => element.scrollHeight > element.clientHeight);

    for (let index = 0; index < optionCount - 1; index += 1) await page.keyboard.press("ArrowDown");

    if (overflowBeforeSelection) await expect.poll(() => viewport.evaluate((element) => element.scrollTop)).toBeGreaterThan(0);
    else await expect(options.last()).toBeInViewport();
  });

  test("real Settings keeps the clear-key row visible above the sticky Apply bar", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 980 });
    await page.goto(`${baseUrl}/#settings`, { waitUntil: "domcontentloaded" });

    const clearKey = page.locator(".settings-clear-api-key");
    const applyBar = page.locator(".settings-apply-bar");
    await expect(clearKey).toBeVisible();
    await expect(page.getByText("清除已配置 API Key")).toBeVisible();

    const geometry = await page.evaluate(() => {
      const rect = (selector: string) => {
        const element = document.querySelector<HTMLElement>(selector);
        if (!element) throw new Error(`${selector} is missing`);
        const box = element.getBoundingClientRect();
        return { top: box.top, bottom: box.bottom };
      };
      return { clearKey: rect(".settings-clear-api-key"), applyBar: rect(".settings-apply-bar") };
    });

    expect(geometry.clearKey.bottom, JSON.stringify(geometry)).toBeLessThanOrEqual(geometry.applyBar.top);

    const overlaps = await page.evaluate(() => {
      const applyBar = document.querySelector<HTMLElement>(".settings-apply-bar")!.getBoundingClientRect();
      const scrollViewport = document.querySelector<HTMLElement>(".settings-editor-scroll")!.getBoundingClientRect();
      return [...document.querySelectorAll<HTMLElement>(".settings-field, .settings-clear-api-key")].filter((field) => {
        const box = field.getBoundingClientRect();
        const top = Math.max(box.top, scrollViewport.top);
        const bottom = Math.min(box.bottom, scrollViewport.bottom);
        return bottom > top && bottom > applyBar.top && top < applyBar.bottom;
      }).map((field) => field.textContent?.trim());
    });

    expect(overlaps).toEqual([]);
  });

  test("real Settings uses row controls without numeric spinners or focus outlines", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 980 });
    await page.goto(`${baseUrl}/#settings`, { waitUntil: "domcontentloaded" });

    await expect(page.getByLabel("模型名称")).toBeVisible();
    await expect(page.getByText("编辑仅保留在当前浏览器页面，点击应用后将在空闲时生效。")).toHaveCount(0);
    const timeout = page.getByLabel("请求超时（秒）");
    await expect(timeout).toHaveAttribute("type", "text");

    const streaming = page.getByRole("switch", { name: "启用流式输出" });
    await expect(streaming).toHaveText("");
    const initialState = await streaming.getAttribute("aria-checked");
    await streaming.click();
    await expect(streaming).toHaveAttribute("aria-checked", initialState === "true" ? "false" : "true");

    await timeout.focus();
    const fieldStyles = await timeout.evaluate((element) => {
      const row = element.closest<HTMLElement>(".settings-field");
      if (!row) throw new Error("settings row is missing");
      return { columns: getComputedStyle(row).gridTemplateColumns, outline: getComputedStyle(element).outlineStyle, shadow: getComputedStyle(element).boxShadow };
    });
    expect(fieldStyles.columns.split(" ").length).toBe(2);
    expect(fieldStyles.outline).toBe("none");
    expect(fieldStyles.shadow).toBe("none");
  });

  test("real Settings renders Tool approvals as the same text-free capsule switch", async ({ page }) => {
    await page.setViewportSize({ width: 1440, height: 980 });
    await page.goto(`${baseUrl}/#settings`, { waitUntil: "domcontentloaded" });

    const streaming = page.getByRole("switch", { name: "启用流式输出" });
    await expect(page.locator(".tool-permission-row").first()).toBeVisible();
    const row = page.locator(".tool-permission-row").first();
    const approval = row.getByRole("switch");
    test.skip(await approval.count() === 0, "The live Tool Catalog has no editable tools.");

    await approval.scrollIntoViewIfNeeded();
    await expect(approval).toBeVisible();
    await expect(approval).toHaveText("");
    const switchStyles = await Promise.all([streaming, approval].map((switchControl) => switchControl.evaluate((element) => {
      const styles = getComputedStyle(element);
      return { width: styles.width, height: styles.height, radius: styles.borderRadius };
    })));
    expect(switchStyles[1]).toEqual(switchStyles[0]);
    const rowStyles = await row.evaluate((element) => {
      const styles = getComputedStyle(element);
      const copy = element.querySelector<HTMLElement>(".tool-permission-copy");
      if (!copy) throw new Error("tool permission copy is missing");
      return { display: styles.display, height: styles.height, copyWhiteSpace: getComputedStyle(copy).whiteSpace };
    });
    expect(rowStyles).toEqual({ display: "grid", height: "44px", copyWhiteSpace: "nowrap" });
  });
});

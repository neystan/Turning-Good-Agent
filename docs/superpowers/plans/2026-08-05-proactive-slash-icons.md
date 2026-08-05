# Proactive Slash 图标 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add four transparent, manually drawn SVG/PNG assets for proactive Slash commands and make the frontend render them whenever the command catalog returns their icon identifiers.

**Architecture:** Store the vector source files beside the existing Slash assets and generate matching public PNG previews from those sources. Extend only the frontend `CommandEntry.icon` union and the `SlashCommandMenu` mask-resource map; do not invent new Slash actions or change command behavior, because proactive command execution is outside this icon task.

**Tech Stack:** React, TypeScript, Vite SVG asset imports, CSS masks, Sharp for PNG rasterization, Playwright.

## Global Constraints

- SVG uses a 24×24 viewBox, transparent canvas, `stroke="currentColor"`, stroke width 1.9, round caps and joins.
- PNG exports are 64×64 RGBA with transparent corners.
- Runtime icons inherit foreground color through CSS masks; PNG files are previews/fallback assets, not React runtime assets.
- Preserve all existing Slash request, filtering, keyboard navigation, selection, and command action behavior.
- Do not add command-catalog entries or infer execution prompts for `/compress`, `/dream`, `/breakbeat`, or `/skill-deposit`.

---

### Task 1: Add command-icon mapping coverage

**Files:**
- Modify: `web/frontend/tests/control_plane_mock.spec.ts`
- Modify: `web/frontend/src/types.ts`

**Interfaces:**
- Consumes: `CommandEntry.icon` from `web/frontend/src/types.ts`.
- Produces: a browser regression test that requires four distinct masked icon resources for `compress`, `dream`, `breakbeat`, and `skill-deposit` catalog entries.

- [ ] **Step 1: Write the failing browser test**

Add a test route that returns four catalog entries using `kind: "skill"`, `action: "insert_text"`, and these `icon` values:

```ts
[
  { id: "proactive.compress", icon: "compress", slash: "/compress" },
  { id: "proactive.dream", icon: "dream", slash: "/dream" },
  { id: "proactive.breakbeat", icon: "breakbeat", slash: "/breakbeat" },
  { id: "proactive.skill-deposit", icon: "skill-deposit", slash: "/skill-deposit" },
]
```

Fill the Composer with `/`, then assert the four `.slash-command-icon` elements have computed `maskImage` values containing `compress.svg`, `dream.svg`, `breakbeat.svg`, and `skill-deposit.svg` respectively.

```ts
test("renders distinct proactive Slash icons", async ({ page }) => {
  await page.route("**/api/control/commands", (route) => route.fulfill({
    contentType: "application/json",
    body: JSON.stringify({ entries: [
      { id: "proactive.compress", kind: "skill", icon: "compress", slash: "/compress", label: "压缩", description: "将长对话压缩为短记忆", action: "insert_text", insert_text: "请压缩当前对话：" },
      { id: "proactive.dream", kind: "skill", icon: "dream", slash: "/dream", label: "dream", description: "进入发散构思", action: "insert_text", insert_text: "请进入 dream 模式：" },
      { id: "proactive.breakbeat", kind: "skill", icon: "breakbeat", slash: "/breakbeat", label: "breakbeat", description: "整理待办事项", action: "insert_text", insert_text: "请整理待办：" },
      { id: "proactive.skill-deposit", kind: "skill", icon: "skill-deposit", slash: "/skill-deposit", label: "skill 沉淀", description: "将重复工作流写为 Skill", action: "insert_text", insert_text: "请沉淀为 Skill：" },
    ] }),
  }));
  await page.goto(baseUrl);
  await page.getByLabel("消息内容").fill("/");
  const masks = await page.locator(".slash-command-icon").evaluateAll((nodes) => nodes.map((node) => getComputedStyle(node).maskImage));
  expect(masks).toEqual(expect.arrayContaining([expect.stringContaining("compress.svg"), expect.stringContaining("dream.svg"), expect.stringContaining("breakbeat.svg"), expect.stringContaining("skill-deposit.svg")]));
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run:

```powershell
$env:PATH='C:\Users\stan\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin;' + $env:PATH
& 'C:\Users\stan\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd' exec playwright test tests/control_plane_mock.spec.ts --grep "proactive Slash icons" --reporter=line
```

Expected: FAIL because the four SVG files and mask mappings do not exist.

- [ ] **Step 3: Extend the TypeScript icon union**

Change `CommandEntry.icon` to include the four literal values:

```ts
icon: "context" | "tools" | "skill" | "mcp" | "compress" | "dream" | "breakbeat" | "skill-deposit";
```

- [ ] **Step 4: Run TypeScript compilation to confirm the test fixture is type-safe**

Run:

```powershell
$env:PATH='C:\Users\stan\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin;' + $env:PATH
& 'C:\Users\stan\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd' run build
```

Expected: compilation reaches Vite or fails only because the asset imports and mapping have not yet been added.

### Task 2: Hand-draw the four transparent SVG sources

**Files:**
- Create: `web/frontend/src/assets/slash-icons/compress.svg`
- Create: `web/frontend/src/assets/slash-icons/dream.svg`
- Create: `web/frontend/src/assets/slash-icons/breakbeat.svg`
- Create: `web/frontend/src/assets/slash-icons/skill-deposit.svg`

**Interfaces:**
- Consumes: the global SVG constraints and the selected C1/A/A/S1 semantic design.
- Produces: four importable Vite SVG URLs that can be passed to CSS `mask-image`.

- [ ] **Step 1: Create `compress.svg`**

Draw the confirmed C1 dual-direction compression mark: upper and lower arrows converge on a single central horizontal line. The mark must use only paths, `currentColor`, 1.9 stroke width, round caps, and round joins.

- [ ] **Step 2: Create `dream.svg`**

Draw the confirmed A mark: a crescent moon plus one four-point capability sparkle. Keep the sparkle detached from the moon so it remains readable at 16px.

- [ ] **Step 3: Create `breakbeat.svg`**

Draw the confirmed A mark: three checklist rows, each with a checkmark and a compact horizontal task line. Keep all checkmarks visually distinct at 16px.

- [ ] **Step 4: Create `skill-deposit.svg`**

Draw the confirmed S1 knowledge-deposit mark: an open Skill handbook above a down-arrow entering one storage baseline. It must express workflow solidification, not file deletion or generic download.

- [ ] **Step 5: Validate SVG structure**

Run:

```powershell
Get-ChildItem 'web\frontend\src\assets\slash-icons\compress.svg','web\frontend\src\assets\slash-icons\dream.svg','web\frontend\src\assets\slash-icons\breakbeat.svg','web\frontend\src\assets\slash-icons\skill-deposit.svg' |
  ForEach-Object { [xml](Get-Content -Raw $_.FullName) | Out-Null; $_.Name }
```

Expected: each filename prints; no XML parsing error occurs.

### Task 3: Export transparent PNG previews and wire runtime mask assets

**Files:**
- Create: `web/frontend/public/slash-icons/compress.png`
- Create: `web/frontend/public/slash-icons/dream.png`
- Create: `web/frontend/public/slash-icons/breakbeat.png`
- Create: `web/frontend/public/slash-icons/skill-deposit.png`
- Modify: `web/frontend/src/components/SlashCommandMenu.tsx`

**Interfaces:**
- Consumes: SVG URL modules from Task 2 and the extended `CommandEntry.icon` union from Task 1.
- Produces: `commandIconAssets` entries for every supported icon and static transparent PNG preview files.

- [ ] **Step 1: Import all four SVGs**

Add four default URL imports in `SlashCommandMenu.tsx`:

```ts
import breakbeatIcon from "../assets/slash-icons/breakbeat.svg";
import compressIcon from "../assets/slash-icons/compress.svg";
import dreamIcon from "../assets/slash-icons/dream.svg";
import skillDepositIcon from "../assets/slash-icons/skill-deposit.svg";
```

- [ ] **Step 2: Add complete mask-resource entries**

Add the literal keys below to `commandIconAssets` without changing the fallback behavior:

```ts
breakbeat: breakbeatIcon,
compress: compressIcon,
dream: dreamIcon,
"skill-deposit": skillDepositIcon,
```

- [ ] **Step 3: Generate 64×64 transparent PNG files**

Run Sharp with the bundled Node runtime. Replace `currentColor` with `#e1e4e9` only in the export buffer, then write each result into `web/frontend/public/slash-icons/`.

```powershell
$node='C:\Users\stan\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe'
$env:NODE_PATH='C:\Users\stan\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\node_modules'
& $node -e 'const fs=require("fs");const path=require("path");const sharp=require("sharp");const names=["compress","dream","breakbeat","skill-deposit"];Promise.all(names.map(async(name)=>{const source=path.join("web","frontend","src","assets","slash-icons",`${name}.svg`);const target=path.join("web","frontend","public","slash-icons",`${name}.png`);const svg=fs.readFileSync(source,"utf8").replaceAll("currentColor","#e1e4e9");await sharp(Buffer.from(svg)).resize(64,64).png().toFile(target);})).catch((error)=>{console.error(error);process.exit(1);});'
```

- [ ] **Step 4: Verify RGBA dimensions and transparent corners**

Run a Sharp metadata/statistics check for all four PNG files. Assert `width === 64`, `height === 64`, `channels === 4`, and alpha has both a zero minimum and a 255 maximum.

- [ ] **Step 5: Re-run the focused browser test**

Run the Task 1 Playwright command.

Expected: PASS; every proactive command row has a distinct 16px CSS mask URL.

- [ ] **Step 6: Commit the focused icon implementation**

Stage only the four SVGs, four PNGs, `types.ts`, `SlashCommandMenu.tsx`, and the focused test. Do not stage unrelated existing changes.

```bash
git add web/frontend/src/assets/slash-icons/{compress,dream,breakbeat,skill-deposit}.svg
git add web/frontend/public/slash-icons/{compress,dream,breakbeat,skill-deposit}.png
git add web/frontend/src/types.ts web/frontend/src/components/SlashCommandMenu.tsx web/frontend/tests/control_plane_mock.spec.ts
git commit -m "feat(web): add proactive slash command icons"
```

### Task 4: Validate production integration

**Files:**
- Modify: none

**Interfaces:**
- Consumes: assets and frontend mappings from Tasks 1–3.
- Produces: build and visual evidence that the new assets work at the production Slash icon size.

- [ ] **Step 1: Build the frontend**

Run:

```powershell
$env:PATH='C:\Users\stan\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin;' + $env:PATH
& 'C:\Users\stan\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd' run build
```

Expected: `tsc -b && vite build` succeeds.

- [ ] **Step 2: Run full control-plane Slash regression tests**

Run:

```powershell
$env:PATH='C:\Users\stan\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin;' + $env:PATH
& 'C:\Users\stan\.cache\codex-runtimes\codex-primary-runtime\dependencies\bin\fallback\pnpm.cmd' exec playwright test tests/control_plane_mock.spec.ts --reporter=line
```

Expected: all tests pass.

- [ ] **Step 3: Inspect 16px dark and light theme rendering**

Open the local app, trigger the Slash menu with a mocked proactive catalog, and inspect computed styles for each `.slash-command-icon`. Confirm each is 16×16, has a mask URL, and changes foreground color between themes.

- [ ] **Step 4: Record the final verification**

Report exact SVG/PNG paths, alpha verification, build output, test result, and any intentionally uncommitted files caused by pre-existing worktree changes.

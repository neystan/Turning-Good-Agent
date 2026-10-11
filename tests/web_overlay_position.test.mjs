import assert from "node:assert/strict";
import test from "node:test";

import { placeOverlay } from "/tmp/tga-overlay-position.mjs";

test("placeOverlay 将菜单限制在视口内", () => {
  const position = placeOverlay(
    { right: 318, bottom: 740 },
    { width: 180, height: 120 },
    { width: 320, height: 768 },
  );

  assert.deepEqual(position, { top: 640, left: 132 });
});

test("placeOverlay 为左上角菜单保留安全间距", () => {
  const position = placeOverlay(
    { right: 3, bottom: 2 },
    { width: 180, height: 120 },
    { width: 320, height: 768 },
  );

  assert.deepEqual(position, { top: 10, left: 8 });
});

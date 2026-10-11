const assert = require("node:assert/strict");
const test = require("node:test");

const { sessionMenuPosition } = require("/tmp/tga-web-menu/session_menu_position.js");

test("底部会话菜单固定定位到触发按钮上方", () => {
  const position = sessionMenuPosition({ top: 700, bottom: 732, right: 320 }, { width: 1024, height: 768 });

  assert.deepEqual(position, { top: 604, left: 322 });
});

test("空间充足时会话菜单固定定位到触发按钮右下", () => {
  const position = sessionMenuPosition({ top: 64, bottom: 96, right: 320 }, { width: 1024, height: 768 });

  assert.deepEqual(position, { top: 64, left: 322 });
});

const assert = require("node:assert/strict");
const test = require("node:test");

const { isCompactCodeBlock } = require("/tmp/tga-web-code/code_presentation.js");

test("短单行无语言代码使用紧凑展示", () => {
  assert.equal(isCompactCodeBlock("Hello, world!"), true);
  assert.equal(isCompactCodeBlock("first\nsecond"), false);
  assert.equal(isCompactCodeBlock("x".repeat(97)), false);
});

import assert from "node:assert/strict";
import { test } from "node:test";

import { nextUiScale, terminalFontSize, uiScaleOf } from "../src/lib/ui-scale.ts";

test("an unknown or missing choice is the default, large", () => {
  assert.equal(uiScaleOf(null), "large");
  assert.equal(uiScaleOf("huge"), "large");
  assert.equal(uiScaleOf("xlarge"), "xlarge");
});

test("the button cycles through the three sizes", () => {
  assert.equal(nextUiScale("normal"), "large");
  assert.equal(nextUiScale("large"), "xlarge");
  assert.equal(nextUiScale("xlarge"), "normal");
});

test("the terminal grows with the page", () => {
  assert.deepEqual(["normal", "large", "xlarge"].map((s) => terminalFontSize(s as "normal")), [14, 16, 18]);
});

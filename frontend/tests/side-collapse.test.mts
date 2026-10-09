import assert from "node:assert/strict";
import { test } from "node:test";

import { foldedWaitingMark, sideCollapsedOf } from "../src/lib/side-collapse.ts";

test("the column is open unless it was folded on purpose", () => {
  assert.equal(sideCollapsedOf(null), false);
  assert.equal(sideCollapsedOf("0"), false);
  assert.equal(sideCollapsedOf("yes"), false);
  assert.equal(sideCollapsedOf("1"), true);
});

test("the folded strip still says what waits, and says when it cannot tell", () => {
  assert.equal(foldedWaitingMark(3), "3");
  assert.equal(foldedWaitingMark(0), null);
  assert.equal(foldedWaitingMark(null), "?");
});

import assert from "node:assert/strict";
import test from "node:test";

import { unavailableKeyOf, untilText, usageLevel } from "../src/lib/usage-state.ts";

test("a window warns from 80% and is about to stop from 95%; reset is unknown, not zero (CR-87)", () => {
  assert.equal(usageLevel(0), "ok");
  assert.equal(usageLevel(79.9), "ok");
  assert.equal(usageLevel(80), "warn");
  assert.equal(usageLevel(95), "high");
  assert.equal(usageLevel(null), "unknown");
});

test("the time to a reset is said in minutes, hours or days, and not once it has passed", () => {
  const now = 1_000_000;
  assert.equal(untilText(now + 30, now), "1m");
  assert.equal(untilText(now + 12 * 60, now), "12m");
  assert.equal(untilText(now + 2 * 3600 + 5 * 60, now), "2h 05m");
  assert.equal(untilText(now + 3 * 86400 + 4 * 3600, now), "3d 4h");
  assert.equal(untilText(now, now), null);
  assert.equal(untilText(now - 60, now), null);
  assert.equal(untilText(null, now), null);
});

test("why a reading is unavailable is said; an unknown reason is shown as it came", () => {
  assert.deepEqual(unavailableKeyOf("kiro_no_api"), { key: "kiroNoApi", detail: null });
  assert.deepEqual(unavailableKeyOf("no_limits"), { key: "noLimits", detail: null });
  assert.deepEqual(unavailableKeyOf("codex is not installed"), { key: "notInstalled", detail: null });
  assert.deepEqual(unavailableKeyOf("codex app-server did not answer"),
    { key: "failed", detail: "codex app-server did not answer" });
});

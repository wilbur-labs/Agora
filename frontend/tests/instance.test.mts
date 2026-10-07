import assert from "node:assert/strict";
import { test } from "node:test";

import { instanceBadgeOf } from "../src/lib/instance.ts";

test("stable and dev are only what /health said", () => {
  assert.deepEqual(instanceBadgeOf({ status: "ok", version: "1.2.0", instance: "stable" }), { kind: "stable", version: "1.2.0" });
  assert.deepEqual(instanceBadgeOf({ status: "ok", version: "1.2.0", instance: "dev" }), { kind: "dev", version: "1.2.0" });
});

test("an older server or a strange value is unknown, never stable", () => {
  assert.deepEqual(instanceBadgeOf({ status: "ok", version: "1.0.0" }), { kind: "unknown", version: "1.0.0" });
  assert.deepEqual(instanceBadgeOf({ status: "ok", instance: "production" }), { kind: "unknown", version: null });
});

test("not yet asked and could not ask are different", () => {
  assert.deepEqual(instanceBadgeOf(null), { kind: "pending" });
  assert.deepEqual(instanceBadgeOf("failed"), { kind: "failed" });
});

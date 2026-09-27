import assert from "node:assert/strict";
import { test } from "node:test";

import { isUsageLimit, reviewFailure, stoodInFor } from "../src/lib/review-failure.ts";

const QUOTA =
  "agent reported: You’ve hit your usage limit. Upgrade to Pro (https://chatgpt.com/explore/pro), " +
  "visit https://chatgpt.com/codex/settings/usage to purchase more credits or try again at 8:44 PM.";

test("a usage limit is read as quota, with the time the provider gave", () => {
  assert.deepEqual(reviewFailure(QUOTA), { kind: "quota", when: "8:44 PM" });
});

test("a usage limit without a time says so rather than inventing one", () => {
  assert.deepEqual(reviewFailure("agent reported: usage limit reached"), { kind: "quota", when: null });
});

test("any other reason is shown as said, without the server's prefix", () => {
  assert.deepEqual(reviewFailure("agent reported: login expired"), { kind: "other", text: "login expired" });
  assert.deepEqual(reviewFailure("process exited with a non-zero status"), {
    kind: "other", text: "process exited with a non-zero status",
  });
});

test("no reason on record is null, not an empty sentence", () => {
  assert.equal(reviewFailure(null), null);
  assert.equal(reviewFailure("   "), null);
});

const codex = { run_id: "r1", adapter: "codex", state: "failed" };
const kiro = { run_id: "r2", adapter: "kiro", state: "succeeded" };

test("kiro after codex ran out of quota is a stand-in", () => {
  const reasons: Record<string, string> = { r1: QUOTA };
  assert.equal(stoodInFor(kiro, [codex, kiro], (id) => reasons[id]), codex);
});

test("an unread or non-quota reason makes no stand-in claim", () => {
  assert.equal(stoodInFor(kiro, [codex, kiro], () => undefined), null);
  assert.equal(stoodInFor(kiro, [codex, kiro], () => "agent reported: crashed"), null);
});

test("only the configured pair counts, and a review never stands in for itself", () => {
  const claude = { run_id: "r3", adapter: "claude", state: "succeeded" };
  assert.equal(stoodInFor(claude, [codex, claude], () => QUOTA), null);
  assert.equal(stoodInFor(codex, [codex], () => QUOTA), null);
  assert.ok(isUsageLimit(QUOTA));
});

test("a generic exit-status error gives way to what stderr said", () => {
  assert.deepEqual(reviewFailure("process exited with a non-zero status", "x\nauthentication expired\n"), {
    kind: "other", text: "authentication expired",
  });
  assert.deepEqual(reviewFailure("agent reported: boom", "ignored"), { kind: "other", text: "boom" });
});

test("a transient rate limit or a full disk is not quota", () => {
  assert.equal(isUsageLimit("rate limit exceeded; retry in 1 second"), false);
  assert.equal(isUsageLimit("Disk quota exceeded"), false);
});

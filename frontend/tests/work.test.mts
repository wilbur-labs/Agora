import assert from "node:assert/strict";
import test from "node:test";

import { bannerLineOf, bannerLinesOf, bannerSettled, projectStateOf, type WorkProjectRow } from "../src/lib/work-state.ts";

function row(over: Partial<WorkProjectRow> = {}): WorkProjectRow {
  return {
    key: "k", source: "/w", task: "alpha", title: null, workspace: "claude", openable: true, reason: null,
    want_open: true, terminal: { terminal_id: "t", alive: true }, starting: false, launch: null, stale: false,
    agent_exited: false, ...over,
  };
}

test("the left column speaks about the terminal, and says claude quit only when it did", () => {
  assert.equal(projectStateOf(row()), "open");
  assert.equal(projectStateOf(row({ agent_exited: true })), "agentExited");
  assert.equal(projectStateOf(row({ terminal: { terminal_id: "t", alive: false } })), "ended");
  assert.equal(projectStateOf(row({ terminal: null })), "notOpen");
  assert.equal(projectStateOf(row({ starting: true, terminal: null })), "starting");
});

test("a resume is called resumed only once the launch's first report confirms it", () => {
  const entry = { task: "alpha", source: "/w", result: "resume" as const };
  const launch = (confirm: NonNullable<WorkProjectRow["launch"]>["confirm"]) =>
    row({ launch: { result: "resume", detail: null, restored: true, confirm } });
  assert.equal(bannerLineOf("k", entry, launch("pending"))?.message, "resuming");
  assert.equal(bannerLineOf("k", entry, launch("pending"))?.tone, "pending");
  assert.equal(bannerLineOf("k", entry, launch("resumed"))?.message, "resumed");
  assert.equal(bannerLineOf("k", entry, launch("resumed_other"))?.message, "resumedOther");
  assert.equal(bannerLineOf("k", entry, launch("not_resumed"))?.message, "notResumed");
  assert.equal(bannerLineOf("k", entry, launch("unconfirmed"))?.message, "unconfirmed");
  // A launch this server did not start as a restore confirms nothing about the restore.
  assert.equal(bannerLineOf("k", entry, row({ launch: { result: "resume", detail: null, restored: false, confirm: "resumed" } }))?.message, "resuming");
});

test("a new conversation is said with why, and as started only once claude reported", () => {
  const entry = { task: "alpha", source: "/w", result: "fresh_missing" as const };
  const launch = (confirm: NonNullable<WorkProjectRow["launch"]>["confirm"]) =>
    row({ launch: { result: "fresh_missing", detail: null, restored: true, confirm } });
  assert.equal(bannerLineOf("k", entry, launch("pending"))?.message, "freshMissingPending");
  assert.equal(bannerLineOf("k", entry, launch("started"))?.message, "freshMissing");
  assert.equal(bannerLineOf("k", entry, launch("unconfirmed"))?.message, "freshMissingUnconfirmed");
});

test("what was never gone or was closed is not in the banner; the rest is", () => {
  assert.equal(bannerLineOf("k", { task: "a", source: "/w", result: "already_open" }, undefined), null);
  assert.equal(bannerLineOf("k", { task: "a", source: "/w", result: "closed" }, undefined), null);
  assert.equal(bannerLineOf("k", { task: "a", source: "/w", result: "maybe_running", detail: "pid 4" }, undefined)?.tone, "warn");
  assert.equal(bannerLineOf("k", { task: "a", source: "/w", result: "unavailable" }, undefined)?.tone, "bad");
});

test("the banner closes only when every line is a confirmed resume with nothing stale", () => {
  const resumed = row({ launch: { result: "resume", detail: null, restored: true, confirm: "resumed" } });
  const view = { restore: { k: { task: "alpha", source: "/w", result: "resume" as const } }, projects: [resumed] };
  assert.ok(bannerSettled(bannerLinesOf(view)));
  assert.ok(!bannerSettled(bannerLinesOf({ ...view, projects: [{ ...resumed, stale: true }] })));
  assert.deepEqual(bannerLinesOf({ restore: null, projects: [] }), []);
});

test("a restore the person later replaced by opening the project again leaves the banner", () => {
  const entry = { task: "alpha", source: "/w", result: "resume" as const, superseded: true };
  assert.equal(bannerLineOf("k", entry, row({ launch: { result: "resume", detail: null, restored: false, confirm: "resumed" } })), null);
  assert.ok(bannerSettled(bannerLinesOf({ restore: { k: entry }, projects: [] })));
});

test("a restored project no longer listed is unconfirmed, not resuming forever", () => {
  assert.equal(bannerLineOf("k", { task: "a", source: "/w", result: "resume" }, undefined)?.message, "unconfirmed");
});

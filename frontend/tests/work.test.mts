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
  const launch = (confirm: NonNullable<WorkProjectRow["launch"]>["confirm"], stale = false) =>
    row({ launch: { result: "resume", detail: null, restored: true, confirm }, stale });
  // Going well asks nothing of the person, so it says nothing (owner, 11-03).
  assert.equal(bannerLineOf("k", entry, launch("pending")), null);
  assert.equal(bannerLineOf("k", entry, launch("resumed")), null);
  assert.equal(bannerLineOf("k", entry, launch("resumed_other"))?.message, "resumedOther");
  assert.equal(bannerLineOf("k", entry, launch("not_resumed"))?.message, "notResumed");
  assert.equal(bannerLineOf("k", entry, launch("unconfirmed"))?.message, "unconfirmed");
  // A point that may not be the latest is said either way.
  assert.equal(bannerLineOf("k", entry, launch("pending", true))?.message, "resuming");
  assert.equal(bannerLineOf("k", entry, launch("pending", true))?.tone, "pending");
  assert.equal(bannerLineOf("k", entry, launch("resumed", true))?.message, "resumed");
  // A launch this server did not start as a restore confirms nothing about the restore.
  assert.equal(bannerLineOf("k", entry, row({ launch: { result: "resume", detail: null, restored: false, confirm: "resumed" }, stale: true }))?.message, "resuming");
});

test("a new conversation where there was nothing to bring back is said only when it went wrong", () => {
  for (const result of ["fresh_missing", "fresh_empty", "fresh_no_record"] as const) {
    const entry = { task: "alpha", source: "/w", result };
    const launch = (confirm: NonNullable<WorkProjectRow["launch"]>["confirm"]) =>
      row({ launch: { result, detail: null, restored: true, confirm } });
    assert.equal(bannerLineOf("k", entry, launch("pending")), null, result);
    assert.equal(bannerLineOf("k", entry, launch("started")), null, result);
    assert.match(bannerLineOf("k", entry, launch("unconfirmed"))?.message ?? "", /Unconfirmed$/, result);
  }
});

test("a recorded conversation that could not be checked is said with why", () => {
  const entry = { task: "alpha", source: "/w", result: "fresh_untrusted" as const };
  const launch = (confirm: NonNullable<WorkProjectRow["launch"]>["confirm"]) =>
    row({ launch: { result: "fresh_untrusted", detail: null, restored: true, confirm } });
  assert.equal(bannerLineOf("k", entry, launch("pending"))?.message, "freshUntrustedPending");
  assert.equal(bannerLineOf("k", entry, launch("started"))?.message, "freshUntrusted");
  assert.equal(bannerLineOf("k", entry, launch("unconfirmed"))?.message, "freshUntrustedUnconfirmed");
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

test("a project paused or archived after the restore leaves the banner; one that just vanished stays said", () => {
  const entry = { task: "career-assets", source: "E:\\Work", result: "fresh_no_record" as const };
  assert.deepEqual(bannerLinesOf({ restore: { k: entry }, projects: [], put_away: ["k"] }), []);
  // Not put away here and not listed (unreadable, gone, or only a same-named archive):
  // still unconfirmed, not dropped (codex review).
  assert.equal(bannerLinesOf({ restore: { k: entry }, projects: [], put_away: ["other-key"] })[0]?.message,
    "freshNoRecordUnconfirmed");
  assert.equal(bannerLinesOf({ restore: { k: entry }, projects: [] })[0]?.message, "freshNoRecordUnconfirmed");
});

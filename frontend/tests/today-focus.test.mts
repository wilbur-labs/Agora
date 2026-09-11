import assert from "node:assert/strict";
import test from "node:test";

import {
  ADVANCING_WINDOW_DAYS,
  TODAY_CARD_DEFECTS,
  TODAY_CARD_NO_READER,
  advancingCountOf,
  continueTaskOf,
  focusCapacityOf,
  goalLineOf,
  openTaskCountOf,
  promoteRefusalOf,
  taskActivityOf,
  taskIsRecentOpenWork,
  todayTiersOf,
  type TodayFocusLike,
  type TodayTaskLike,
} from "../src/lib/today-focus.ts";
import type { TaskInventoryReach } from "../src/lib/portfolio-bucket.ts";

const DAY = 24 * 60 * 60 * 1000;
const NOW = Date.parse("2026-09-11T12:00:00Z");

function task(
  project_id: string,
  daysAgo: number,
  overrides: Partial<TodayTaskLike> & { task_id?: string } = {},
): TodayTaskLike & { task_id: string } {
  return {
    task_id: overrides.task_id ?? `${project_id}-${daysAgo}`,
    project_id,
    state: "running",
    updated_at: new Date(NOW - daysAgo * DAY).toISOString(),
    ...overrides,
  };
}

const projects = ["agora", "kimono", "mind", "sleepy"].map((project_id) => ({ project_id }));

const focus: TodayFocusLike = {
  project_ids: ["kimono", "agora"],
  unresolved_ids: [],
  limit: 3,
  is_full: false,
};

// --- tiers ----------------------------------------------------------------------

test("focus keeps the server's promotion order, not the project list's", () => {
  const tiers = todayTiersOf({ projects, focus, tasks: [], taskReach: "complete", nowMs: NOW });
  assert.deepEqual(tiers.focus, ["kimono", "agora"]);
  assert.equal(tiers.focusReported, true);
});

test("advancing is an observed open task inside the window; dormant is the rest, on a complete read", () => {
  const tasks = [task("mind", 3), task("sleepy", 20)];
  const tiers = todayTiersOf({ projects, focus, tasks, taskReach: "complete", nowMs: NOW });
  assert.deepEqual(tiers.advancing, ["mind"]);
  assert.deepEqual(tiers.dormant, ["sleepy"]);
  assert.deepEqual(tiers.unplaced, []);
});

test("a project in focus is never also advancing or dormant — but it is counted as active", () => {
  const tiers = todayTiersOf({ projects, focus, tasks: [task("agora", 1), task("mind", 1)], taskReach: "complete", nowMs: NOW });
  assert.ok(!tiers.advancing.includes("agora"));
  assert.ok(!tiers.dormant.includes("agora"));
  // The heading's "N 个在推进" counts every moving project, focus cards included; the
  // second tier is only those not in focus. The mockup's 4 includes its focus cards.
  assert.deepEqual(tiers.active, ["agora", "mind"]);
  assert.deepEqual(tiers.advancing, ["mind"]);
});

for (const reach of ["capped", "unreadable", "pending"] as TaskInventoryReach[]) {
  test(`on a ${reach} task read, dormant cannot be said — the project is unplaced, while an observed one still advances`, () => {
    const tasks = [task("mind", 3)];
    const tiers = todayTiersOf({ projects, focus, tasks, taskReach: reach, nowMs: NOW });
    // Presence is provable from any page.
    assert.deepEqual(tiers.advancing, ["mind"]);
    // Absence is not.
    assert.deepEqual(tiers.dormant, []);
    assert.deepEqual(tiers.unplaced, ["sleepy"]);
  });
}

test("the window boundary: exactly 14 days is still advancing, a day more is not", () => {
  assert.equal(taskIsRecentOpenWork(task("p", ADVANCING_WINDOW_DAYS), NOW), true);
  assert.equal(taskIsRecentOpenWork(task("p", ADVANCING_WINDOW_DAYS + 1), NOW), false);
});

test("terminal tasks do not make a project advancing, however recent — and archiving is not a state", () => {
  // The three terminal states of backend/agora/tasks/transitions.py. `failed` is one of
  // them: it has no outgoing transition, so a "continue" pointed at it would be a lie.
  for (const state of ["done", "cancelled", "failed"]) {
    assert.equal(taskIsRecentOpenWork(task("p", 0, { state }), NOW), false, state);
  }
  // An archived task keeps its lifecycle state; a running one is still open work. The
  // judgement does not read `archived_at` at all, so a manifest carrying it changes nothing.
  const archived = { ...task("p", 0), archived_at: "2026-09-01T00:00:00Z" };
  assert.equal(taskIsRecentOpenWork(archived, NOW), true);
  // Every other lifecycle state is open work.
  for (const state of ["backlog", "requirements", "design", "planned", "running", "blocked", "review", "verified"]) {
    assert.equal(taskIsRecentOpenWork(task("p", 0, { state }), NOW), true, state);
  }
});

test("an unparseable timestamp proves nothing — neither recent nor dormant; a future one is not inactivity", () => {
  assert.equal(taskActivityOf(task("p", 0, { updated_at: "not a date" }), NOW), "unknown");
  assert.equal(taskIsRecentOpenWork(task("p", 0, { updated_at: "not a date" }), NOW), false);
  assert.equal(taskActivityOf(task("p", -2), NOW), "recent");
  assert.equal(taskActivityOf(task("p", 40), NOW), "stale");
  assert.equal(taskActivityOf(task("p", 0, { state: "done" }), NOW), "closed");
  // A project whose only open task carries an unreadable stamp is not dormant, even on a
  // complete read: "no activity in 14 days" is a claim that task cannot support.
  const tiers = todayTiersOf({
    projects, focus, tasks: [task("sleepy", 0, { updated_at: "not a date" })], taskReach: "complete", nowMs: NOW,
  });
  assert.deepEqual(tiers.dormant, ["mind"]);
  assert.deepEqual(tiers.unplaced, ["sleepy"]);
  // Named as the reason, so the row does not blame a task read that was in fact complete.
  assert.deepEqual(tiers.undated, ["sleepy"]);
  // And "most recently updated" cannot be established for that project either.
  const tasks = [task("agora", 3, { task_id: "dated" }), task("agora", 0, { task_id: "undated", updated_at: "?" })];
  assert.equal(continueTaskOf(tasks, "agora", "complete"), null);
});

test("an unreported focus tier is reported as such, and the other tiers still resolve", () => {
  const tiers = todayTiersOf({ projects, focus: null, tasks: [task("agora", 1)], taskReach: "complete", nowMs: NOW });
  assert.equal(tiers.focusReported, false);
  assert.deepEqual(tiers.focus, []);
  assert.deepEqual(tiers.advancing, ["agora"]);
  assert.deepEqual(tiers.dormant, ["kimono", "mind", "sleepy"]);
});

test("unresolved focus ids are carried, not dropped — they hold seats the screen must explain", () => {
  const withGhost: TodayFocusLike = { ...focus, unresolved_ids: ["deleted-project"] };
  const tiers = todayTiersOf({ projects, focus: withGhost, tasks: [], taskReach: "complete", nowMs: NOW });
  assert.deepEqual(tiers.unresolvedFocus, ["deleted-project"]);
  // And a promoted id the list does not know is reported the same way rather than shown as a card.
  const stranger: TodayFocusLike = { ...focus, project_ids: ["kimono", "who"] };
  const tiers2 = todayTiersOf({ projects, focus: stranger, tasks: [], taskReach: "complete", nowMs: NOW });
  assert.deepEqual(tiers2.focus, ["kimono"]);
  assert.deepEqual(tiers2.unresolvedFocus, ["who"]);
});

// --- capacity --------------------------------------------------------------------

test("fullness is the server's word: an unresolved id occupies a seat and is_full is repeated, not recomputed", () => {
  // Two resolvable + one unresolved: the server says full, and so must the screen —
  // recomputing from project_ids.length would offer a fourth seat the server refuses.
  const full: TodayFocusLike = { project_ids: ["a", "b"], unresolved_ids: ["gone"], limit: 3, is_full: true };
  assert.deepEqual(focusCapacityOf(full), { kind: "full", used: 3, limit: 3 });
  // And the other way: three ids but the server says open — repeat the server.
  const odd: TodayFocusLike = { project_ids: ["a", "b", "c"], unresolved_ids: [], limit: 3, is_full: false };
  assert.deepEqual(focusCapacityOf(odd), { kind: "open", used: 3, limit: 3 });
  assert.deepEqual(focusCapacityOf(null), { kind: "unreported" });
});

// --- refusal ---------------------------------------------------------------------

test("a refused promotion is told apart by status, since the detail's discriminator does not survive the parser", () => {
  assert.equal(promoteRefusalOf({ status: 409, code: null }), "full");
  assert.equal(promoteRefusalOf({ status: 409, code: "today_focus_full" }), "full");
  assert.equal(promoteRefusalOf({ status: 404, code: null }), "unknownProject");
  assert.equal(promoteRefusalOf({ status: 500, code: null }), "failed");
  assert.equal(promoteRefusalOf({ status: null, code: null }), "failed");
});

// --- counts ----------------------------------------------------------------------

test("the heading's count is exact on a complete read, a floor on a capped one, and nothing on a failed one", () => {
  assert.deepEqual(advancingCountOf(4, "complete"), { kind: "exact", count: 4 });
  // A capped page was read: its rows are a floor.
  assert.deepEqual(advancingCountOf(4, "capped"), { kind: "atLeast", count: 4 });
  // No page at all: "at least 0" would be true and useless, and it would be printed.
  for (const reach of ["unreadable", "pending"] as TaskInventoryReach[]) {
    assert.deepEqual(advancingCountOf(0, reach), { kind: "unknown" });
  }
});

test("a row's open-task count counts observed open tasks, with the same three outcomes", () => {
  const tasks = [task("mind", 1), task("mind", 30), task("mind", 2, { state: "done" }), task("other", 1)];
  assert.deepEqual(openTaskCountOf(tasks, "mind", "complete"), { kind: "exact", count: 2 });
  assert.deepEqual(openTaskCountOf(tasks, "mind", "capped"), { kind: "atLeast", count: 2 });
  assert.deepEqual(openTaskCountOf([], "mind", "unreadable"), { kind: "unknown" });
  assert.deepEqual(openTaskCountOf([], "mind", "pending"), { kind: "unknown" });
});

test("continue is offered only from a complete task read — a capped page is ordered by priority, not recency", () => {
  const tasks = [task("agora", 5, { task_id: "old" }), task("agora", 1, { task_id: "newest" })];
  for (const reach of ["capped", "unreadable", "pending"] as TaskInventoryReach[]) {
    assert.equal(continueTaskOf(tasks, "agora", reach), null, reach);
  }
});

test("continue goes to the most recently updated open task, or nowhere", () => {
  const tasks = [
    task("agora", 5, { task_id: "old" }),
    task("agora", 1, { task_id: "newest" }),
    task("agora", 0, { task_id: "closed", state: "done" }),
    task("kimono", 0, { task_id: "elsewhere" }),
  ];
  assert.equal(continueTaskOf(tasks, "agora", "complete")?.task_id, "newest");
  assert.equal(continueTaskOf(tasks, "sleepy", "complete"), null);
  assert.equal(continueTaskOf([task("p", 0, { state: "done" })], "p", "complete"), null);
  assert.equal(continueTaskOf([task("p", 0, { state: "failed" })], "p", "complete"), null);
});

// --- card fields ------------------------------------------------------------------

test("the goal line is the charter's sentence or a plain absence — whitespace is not a goal", () => {
  assert.deepEqual(goalLineOf("Ship it"), { kind: "goal", text: "Ship it" });
  assert.deepEqual(goalLineOf("   "), { kind: "none" });
  assert.deepEqual(goalLineOf(undefined), { kind: "none" });
});

test("every card field the mockup draws without a reader is a capability slot, none of them a count", () => {
  for (const [field, cap] of Object.entries(TODAY_CARD_NO_READER)) {
    assert.equal(cap.kind, "noReader", field);
  }
  // Known defects share the nav's slot, so the two surfaces cannot disagree.
  assert.deepEqual(TODAY_CARD_DEFECTS, { kind: "noReader", reason: "no_structured_source" });
});

// --- the page must take these decisions from here ----------------------------------

test("the Today page places tiers, states capacity and reads refusals through this module only", async () => {
  const { readFile } = await import("node:fs/promises");
  const page = await readFile(new URL("../src/app/page.tsx", import.meta.url), "utf8");
  const source = page.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");
  for (const call of ["todayTiersOf(", "focusCapacityOf(", "promoteRefusalOf(", "advancingCountOf("]) {
    assert.ok(source.includes(call), `the page must call ${call}`);
  }
  // No second opinion on fullness: the page must not compare a length against the limit.
  assert.ok(!/\.length\s*>=\s*[\w.]*limit/.test(source), "the page must not recompute is_full");
  assert.ok(!/is_full\s*=/.test(source), "the page must not assign is_full");
  // And no zero invented for a missing number.
  assert.ok(!source.includes("?? 0"), "the page must not read a missing number as zero");
});

test("the focus card renders every no-reader field through the shared slot and never blanks one", async () => {
  const { readFile } = await import("node:fs/promises");
  const card = await readFile(new URL("../src/components/today-focus-card.tsx", import.meta.url), "utf8");
  const source = card.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");
  assert.ok(source.includes("CapabilitySlot"), "the card must use the shared capability slot");
  for (const field of Object.keys(TODAY_CARD_NO_READER)) {
    assert.ok(source.includes(`TODAY_CARD_NO_READER.${field}`), `the card must render the ${field} slot`);
  }
  assert.ok(source.includes("TODAY_CARD_DEFECTS"), "the card must render the defects slot");
  // The next-step, blocking count and progress come from the existing judgements.
  for (const call of ["workspaceProjectStripOf(", "progressBarOf("]) {
    assert.ok(source.includes(call), `the card must derive from ${call}`);
  }
  assert.ok(!source.includes("?? 0"), "the card must not read a missing number as zero");
  // The mockup's "换个 agent" button was cut by decision; the card must not grow one back.
  assert.ok(!/agent/i.test(source.replace(/worker_agent|reviewer_agent/g, "")), "no agent picker on the card");
});

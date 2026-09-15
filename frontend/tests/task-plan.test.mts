import { strict as assert } from "node:assert";
import { test } from "node:test";

import {
  PLAN_PROGRESS_UNREADABLE,
  nextOpenStepOf,
  planHeadlineOf,
  planRowsOf,
  planWaitingOf,
  type PlanRow,
} from "../src/lib/task-plan.ts";
import type { AttentionItem, TaskManifest, TaskState } from "../src/lib/control-plane.ts";

function task(overrides: Partial<TaskManifest> & { task_id: string }): TaskManifest {
  return {
    project_id: "nichimate",
    title: "a step",
    description: "",
    kind: "feature",
    state: "planned" as TaskState,
    risk: "low",
    priority: 100,
    primary_agent: null,
    reviewers: [],
    acceptance: [],
    budget: { max_cost_usd: null, max_minutes: null },
    metadata: {},
    version: 1,
    created_by: "user",
    created_at: "2026-09-15T00:00:00Z",
    updated_at: "2026-09-15T00:00:00Z",
    archived_at: null,
    clarifications: null,
    ...overrides,
  };
}

function item(overrides: Partial<AttentionItem> & { task_id: string }): AttentionItem {
  return {
    item_id: "att_1",
    project_id: "nichimate",
    run_id: null,
    kind: "question",
    state: "open",
    urgency: "normal",
    ...overrides,
  } as AttentionItem;
}

const PLAN = {
  attention: [] as AttentionItem[],
  attentionReach: "complete" as const,
  runningTaskIds: new Set<string>(),
  runsAreCurrent: true,
  projectGate: "open" as const,
};

test("steps are numbered in the order given, never re-sorted here", () => {
  // Deliberately handed to the module out of priority order: the board decides the
  // order, and a second sort in the frontend is the defect this guards.
  const rows = planRowsOf({
    ...PLAN,
    tasks: [
      task({ task_id: "c", priority: 10, title: "third by priority" }),
      task({ task_id: "a", priority: 100, title: "first by priority" }),
    ],
  });
  assert.deepEqual(rows.map((row) => [row.step, row.task_id]), [[1, "c"], [2, "a"]]);
});

test("a complete read that found nothing is a settled 'nothing is waiting'", () => {
  assert.deepEqual(planWaitingOf({ items: [], reach: "complete" }), { kind: "none" });
});

test("a capped read that found nothing says unknown, never 'nothing'", () => {
  assert.deepEqual(planWaitingOf({ items: [], reach: "capped" }), { kind: "unknown" });
});

test("a failed read that found nothing says unknown too", () => {
  assert.deepEqual(planWaitingOf({ items: [], reach: "unreadable" }), { kind: "unknown" });
});

test("items found on a capped read are still real — existence needs no complete read", () => {
  const waiting = planWaitingOf({
    items: [item({ task_id: "a", kind: "blocker" })],
    reach: "capped",
  });
  assert.deepEqual(waiting, { kind: "open", count: 1, exact: false, kinds: ["blocker"] });
});

test("only open items count; a responded one is a gate already dealt with", () => {
  const waiting = planWaitingOf({
    items: [item({ task_id: "a", state: "responded" }), item({ task_id: "a", item_id: "att_2" })],
    reach: "complete",
  });
  assert.deepEqual(waiting, { kind: "open", count: 1, exact: true, kinds: ["question"] });
});

test("kinds are distinct and keep the order they were seen in", () => {
  const waiting = planWaitingOf({
    items: [
      item({ task_id: "a", item_id: "1", kind: "approval" }),
      item({ task_id: "a", item_id: "2", kind: "question" }),
      item({ task_id: "a", item_id: "3", kind: "approval" }),
    ],
    reach: "complete",
  });
  assert.deepEqual(waiting, { kind: "open", count: 3, exact: true, kinds: ["approval", "question"] });
});

test("a stale run snapshot never lights 'running', and never invites a run either", () => {
  const rows = planRowsOf({
    ...PLAN,
    runsAreCurrent: false,
    runningTaskIds: new Set(["a"]),
    tasks: [task({ task_id: "a" })],
  });
  assert.equal(rows[0].running, false);
  assert.equal(rows[0].decision.ready, false);
  // The refusal must be the "cannot tell" one, not a claim about the task itself.
  assert.equal(rows[0].decision.block, "runsUnknown");
});

test("an unknown 'waiting on' blocks continue rather than reading as nothing open", () => {
  const rows = planRowsOf({
    ...PLAN,
    attentionReach: "capped",
    tasks: [task({ task_id: "a" })],
  });
  assert.deepEqual(rows[0].waiting, { kind: "unknown" });
  assert.equal(rows[0].decision.ready, false);
  assert.equal(rows[0].decision.block, "attentionUnknown");
});

test("a project with no goal refuses every step, not just the first", () => {
  const rows = planRowsOf({
    ...PLAN,
    projectGate: "noCharter",
    tasks: [task({ task_id: "a" }), task({ task_id: "b" })],
  });
  assert.deepEqual(rows.map((row) => row.decision.block), ["noCharter", "noCharter"]);
});

test("per-task progress is a single unreadable value, never a number", () => {
  const rows = planRowsOf({ ...PLAN, tasks: [task({ task_id: "a" })] });
  assert.deepEqual(rows[0].progress, PLAN_PROGRESS_UNREADABLE);
});

test("the headline counts finished steps and nothing else toward percent", () => {
  const rows = planRowsOf({
    ...PLAN,
    runningTaskIds: new Set(["b"]),
    tasks: [
      task({ task_id: "a", state: "done" }),
      task({ task_id: "b", state: "running" }),
      task({ task_id: "c" }),
      task({ task_id: "d" }),
    ],
  });
  const headline = planHeadlineOf(rows, "complete");
  assert.equal(headline.total, 4);
  assert.equal(headline.done, 1);
  assert.equal(headline.running, 1);
  // 1 of 4 — the in-flight step is not worth half a point.
  assert.equal(headline.percent, 25);
});

test("abandoned work is counted as abandoned, not as done", () => {
  const rows = planRowsOf({
    ...PLAN,
    tasks: [
      task({ task_id: "a", state: "done" }),
      task({ task_id: "b", state: "cancelled" }),
      task({ task_id: "c", state: "failed" }),
    ],
  });
  const headline = planHeadlineOf(rows, "complete");
  assert.equal(headline.done, 1);
  assert.equal(headline.abandoned, 2);
  assert.equal(headline.percent, 33);
});

test("an empty plan has no percent rather than a zero one", () => {
  const headline = planHeadlineOf([], "complete");
  assert.equal(headline.total, 0);
  assert.equal(headline.percent, null);
});

test("the headline discloses when a step's gate could not be read", () => {
  const complete = planHeadlineOf(planRowsOf({ ...PLAN, tasks: [task({ task_id: "a" })] }), "complete");
  assert.equal(complete.waitingKnown, true);
  const capped = planHeadlineOf(
    planRowsOf({ ...PLAN, attentionReach: "capped", tasks: [task({ task_id: "a" })] }),
    "complete",
  );
  assert.equal(capped.waitingKnown, false);
});

test("the next open step is the first one whose gate says a run may start", () => {
  const rows = planRowsOf({
    ...PLAN,
    runningTaskIds: new Set(["b"]),
    tasks: [
      task({ task_id: "a", state: "done" }),
      task({ task_id: "b", state: "running" }),
      task({ task_id: "c", state: "planned" }),
      task({ task_id: "d", state: "planned" }),
    ],
  });
  const next = nextOpenStepOf(rows) as PlanRow;
  assert.equal(next.task_id, "c");
});

test("no open step is a real answer, not an empty one", () => {
  const rows = planRowsOf({
    ...PLAN,
    tasks: [task({ task_id: "a", state: "done" }), task({ task_id: "b", state: "backlog" })],
  });
  assert.equal(nextOpenStepOf(rows), null);
});

// --- the three rules the kiro review of 2026-09-15 found missing ---

test("a capped task read never claims how many steps the plan has", () => {
  const rows = planRowsOf({
    ...PLAN,
    tasks: [task({ task_id: "a", state: "done" }), task({ task_id: "b" })],
  });
  const capped = planHeadlineOf(rows, "capped");
  assert.equal(capped.totalKnown, false);
  // The count of what was seen stays true; what is withheld is the claim and the
  // fraction, because the denominator is not the plan's size.
  assert.equal(capped.total, 2);
  assert.equal(capped.percent, null);
  const failed = planHeadlineOf(rows, "unreadable");
  assert.equal(failed.totalKnown, false);
  assert.equal(failed.percent, null);
  const complete = planHeadlineOf(rows, "complete");
  assert.equal(complete.totalKnown, true);
  assert.equal(complete.percent, 50);
});

test("items found on a capped read are a floor, not a count", () => {
  const waiting = planWaitingOf({ items: [item({ task_id: "a" })], reach: "capped" });
  assert.deepEqual(waiting, { kind: "open", count: 1, exact: false, kinds: ["question"] });
});

test("items left over from a failed read are not a floor at all", () => {
  // The page keeps the previous array when the refresh rejects. Those items may have
  // been answered since, so even "at least 1" is a claim about a moment that has passed.
  const waiting = planWaitingOf({ items: [item({ task_id: "a" })], reach: "unreadable" });
  assert.deepEqual(waiting, { kind: "unknown" });
});

test("a floor reaches the continue gate as unknown, never as a tally", () => {
  const rows = planRowsOf({
    ...PLAN,
    attentionReach: "capped",
    attention: [item({ task_id: "a" })],
    tasks: [task({ task_id: "a" })],
  });
  assert.equal(rows[0].decision.ready, false);
  assert.equal(rows[0].decision.block, "attentionUnknown");
  // And the headline must not present that row as a known gate.
  assert.equal(planHeadlineOf(rows, "complete").waitingKnown, false);
});

test("a queued run is work in flight: no Continue, and it counts as running", () => {
  const rows = planRowsOf({
    ...PLAN,
    runningTaskIds: new Set(["a"]),
    tasks: [task({ task_id: "a" })],
  });
  assert.equal(rows[0].running, true);
  assert.equal(rows[0].decision.ready, false);
  assert.equal(rows[0].decision.block, "runActive");
  assert.equal(planHeadlineOf(rows, "complete").running, 1);
});

// --- kiro round 2 (2026-09-15): the surfaces the round-1 fixes opened ---

test("before any read has settled, nothing is known — least of all 'no tasks'", () => {
  const headline = planHeadlineOf([], "pending");
  assert.equal(headline.totalKnown, false);
  assert.equal(headline.total, 0);
  assert.equal(headline.percent, null);
});

test("a settled complete read of an empty board *is* allowed to say none", () => {
  assert.equal(planHeadlineOf([], "complete").totalKnown, true);
});

test("a run page that could not be vouched for refuses, exactly as a stale one does", () => {
  // The page collapses "stale" and "capped" into runsAreCurrent=false, because they
  // fail the same question: can this snapshot support "no run is active".
  const rows = planRowsOf({
    ...PLAN,
    runsAreCurrent: false,
    tasks: [task({ task_id: "a" })],
  });
  assert.equal(rows[0].decision.ready, false);
  assert.equal(rows[0].decision.block, "runsUnknown");
  assert.equal(planHeadlineOf(rows, "complete").running, 0);
});

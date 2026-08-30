import assert from "node:assert/strict";
import test from "node:test";

import {
  RUN_REVIEWS_KEY,
  RUN_ROLE_KEY,
  type ColumnRunLike,
} from "../src/lib/run-column.ts";
import {
  FAILED_RUN_STATES,
  nestedFailureCount,
  runTreeOf,
  type UnplaceableReason,
} from "../src/lib/run-tree.ts";
import { workspace as workspaceDictionary } from "../src/lib/i18n/dictionaries/workspace.ts";

type Locale = "en" | "zh";

function lookup(locale: Locale, key: string): string | undefined {
  const parts = key.split(".");
  assert.equal(parts.shift(), "workspace", `key must be in the workspace namespace: ${key}`);
  let node: unknown = workspaceDictionary[locale];
  for (const part of parts) {
    if (!node || typeof node !== "object" || !(part in (node as Record<string, unknown>))) {
      return undefined;
    }
    node = (node as Record<string, unknown>)[part];
  }
  return typeof node === "string" ? node : undefined;
}

let seq = 0;
function run(over: Partial<ColumnRunLike> = {}): ColumnRunLike {
  seq += 1;
  const at = `2026-08-31T00:00:${String(seq).padStart(2, "0")}Z`;
  return {
    run_id: over.run_id ?? `run-${seq}`,
    state: over.state ?? "succeeded",
    adapter: over.adapter ?? "codex",
    queued_at: over.queued_at ?? at,
    started_at: over.started_at ?? at,
    finished_at: over.finished_at ?? at,
    result_metadata: over.result_metadata,
    ...over,
  };
}

const worker = (over: Partial<ColumnRunLike> = {}) =>
  run({ ...over, result_metadata: { [RUN_ROLE_KEY]: "worker", ...(over.result_metadata ?? {}) } });

const review = (reviews: string, over: Partial<ColumnRunLike> = {}) =>
  run({
    ...over,
    result_metadata: { [RUN_ROLE_KEY]: "review", [RUN_REVIEWS_KEY]: reviews, ...(over.result_metadata ?? {}) },
  });

const ids = (runs: readonly ColumnRunLike[]) => runs.map((r) => r.run_id);

test("the top level lists worker runs and nests their reviews", () => {
  const w1 = worker({ run_id: "w1", queued_at: "2026-08-31T01:00:00Z" });
  const w2 = worker({ run_id: "w2", queued_at: "2026-08-31T03:00:00Z" });
  const r1 = review("w1", { run_id: "r1", queued_at: "2026-08-31T02:00:00Z" });
  const r2 = review("w2", { run_id: "r2", queued_at: "2026-08-31T04:00:00Z" });

  // Deliberately out of order in the input: order is a property of the tree.
  const tree = runTreeOf([r2, w1, r1, w2]);
  assert.deepEqual(ids(tree.units.map((u) => u.run)), ["w1", "w2"]);
  assert.deepEqual(ids(tree.units[0].nested), ["r1"]);
  assert.deepEqual(ids(tree.units[1].nested), ["r2"]);
  assert.deepEqual(tree.unplaceable, []);
});

test("a review whose reviewed run is not in the page is unplaceable, not dropped", () => {
  // The run page is capped at 100 and a per-run detail read can fail, so this is an
  // ordinary read outcome — and it means the placement is unknown, never that the
  // review did not happen.
  const orphan = review("w-older-than-the-page", { run_id: "r-orphan" });
  const tree = runTreeOf([worker({ run_id: "w1" }), orphan]);
  assert.deepEqual(ids(tree.units.map((u) => u.run)), ["w1"]);
  assert.deepEqual(tree.units[0].nested, []);
  assert.equal(tree.unplaceable.length, 1);
  assert.equal(tree.unplaceable[0].run.run_id, "r-orphan");
  assert.equal(tree.unplaceable[0].because, "parentNotLoaded");
  // The load-bearing property: every input run is still somewhere in the output.
  const rendered = [
    ...tree.units.flatMap((u) => [u.run.run_id, ...ids(u.nested)]),
    ...tree.unplaceable.map((u) => u.run.run_id),
  ];
  assert.deepEqual(rendered.sort(), ["r-orphan", "w1"]);
});

test("no run is ever lost, whatever the page contains", () => {
  const runs = [
    worker({ run_id: "w1" }),
    review("w1", { run_id: "r1" }),
    review("missing", { run_id: "r2" }),
    review("", { run_id: "r3" }),
    review("r4", { run_id: "r4" }),
    run({ run_id: "x1", result_metadata: { [RUN_ROLE_KEY]: "audit" } }),
    run({ run_id: "legacy" }),
    // A review of a review, and a pair of reviews naming each other. Dispatch links
    // reviews to worker runs today, but `reviews_run_id` is unconstrained JSON, so
    // both are records this page can be handed — and neither may vanish.
    review("r1", { run_id: "r-of-r" }),
    review("c2", { run_id: "c1" }),
    review("c1", { run_id: "c2" }),
  ];
  const tree = runTreeOf(runs);
  const rendered = new Set([
    ...tree.units.flatMap((u) => [u.run.run_id, ...ids(u.nested)]),
    ...tree.unplaceable.map((u) => u.run.run_id),
  ]);
  assert.deepEqual([...rendered].sort(), ids(runs).sort());
});

test("a page that is nothing but a review cycle still renders every run", () => {
  // The failure this pins: both runs pass "the run I review is loaded", neither is a
  // top-level run, so a placement pass that only attaches to top-level runs returns
  // {units: [], unplaceable: []} — the page does not hang, it silently empties.
  const runs = [review("c2", { run_id: "c1" }), review("c1", { run_id: "c2" })];
  const tree = runTreeOf(runs);
  assert.deepEqual(tree.units, []);
  assert.deepEqual(
    tree.unplaceable.map((u) => [u.run.run_id, u.because]),
    [["c1", "chainNotRooted"], ["c2", "chainNotRooted"]],
  );
});

test("a review of a review belongs to the work unit at the root of the chain", () => {
  // One level of nesting is all the timeline renders, and the root run is the work
  // unit the chain is about — which is in the data, not guessed. What is not allowed
  // is the third option: dropping the outer review because it was never reached.
  const w = worker({ run_id: "w1", queued_at: "2026-08-31T01:00:00Z" });
  const inner = review("w1", { run_id: "r1", queued_at: "2026-08-31T02:00:00Z" });
  const outer = review("r1", { run_id: "r2", queued_at: "2026-08-31T03:00:00Z" });
  const tree = runTreeOf([outer, inner, w]);
  assert.deepEqual(ids(tree.units.map((u) => u.run)), ["w1"]);
  assert.deepEqual(ids(tree.units[0].nested), ["r1", "r2"]);
  assert.deepEqual(tree.unplaceable, []);
});

test("a chain whose ancestor cannot be placed is unplaceable rather than dropped", () => {
  // The inner review's own parent is off the page, so the chain never reaches a
  // top-level run. Both runs are shown, and only the inner one's reason is about the
  // page cap, because that is what its own record says.
  const inner = review("w-older-than-the-page", { run_id: "r1" });
  const outer = review("r1", { run_id: "r2" });
  const tree = runTreeOf([inner, outer]);
  assert.deepEqual(tree.units, []);
  assert.deepEqual(
    tree.unplaceable.map((u) => [u.run.run_id, u.because]),
    [["r1", "parentNotLoaded"], ["r2", "chainNotRooted"]],
  );
});

test("a review that names no run says so instead of being nested at random", () => {
  const tree = runTreeOf([worker({ run_id: "w1" }), review("", { run_id: "r-unlinked" })]);
  assert.deepEqual(tree.units[0].nested, []);
  assert.deepEqual(
    tree.unplaceable.map((u) => [u.run.run_id, u.because]),
    [["r-unlinked", "namesNoRun"]],
  );
});

test("a review naming itself is unplaceable rather than its own child", () => {
  const tree = runTreeOf([review("r-self", { run_id: "r-self" })]);
  assert.deepEqual(tree.units, []);
  assert.deepEqual(
    tree.unplaceable.map((u) => [u.run.run_id, u.because]),
    [["r-self", "namesItself"]],
  );
});

test("a retry is a top-level work unit, because no retry relation is recorded", () => {
  // Run identity is exactly {agora_role, reviews_run_id, task_version}; nothing says
  // "this run retries that one". Two worker runs on the same task are therefore two
  // work units, and inferring a parent from adjacency would assert a relation the
  // control plane never recorded.
  const first = worker({ run_id: "w1", state: "failed", queued_at: "2026-08-31T01:00:00Z" });
  const retry = worker({ run_id: "w2", queued_at: "2026-08-31T02:00:00Z" });
  const tree = runTreeOf([first, retry]);
  assert.deepEqual(ids(tree.units.map((u) => u.run)), ["w1", "w2"]);
  assert.ok(tree.units.every((u) => u.nested.length === 0));
  assert.deepEqual(tree.unplaceable, []);
});

test("a legacy run with no role, and an unknown role, stay top level", () => {
  // A missing role is a legacy worker run; a role this UI does not know is not
  // rounded to review, so it is never hunted for a parent.
  const tree = runTreeOf([
    run({ run_id: "legacy", result_metadata: null }),
    run({ run_id: "future", result_metadata: { [RUN_ROLE_KEY]: "audit", [RUN_REVIEWS_KEY]: "legacy" } }),
  ]);
  assert.deepEqual(ids(tree.units.map((u) => u.run)).sort(), ["future", "legacy"]);
  assert.deepEqual(tree.unplaceable, []);
});

test("several reviews of one run nest together, ordered by when they started", () => {
  const w = worker({ run_id: "w1", queued_at: "2026-08-31T01:00:00Z" });
  const later = review("w1", { run_id: "r-later", queued_at: "2026-08-31T05:00:00Z" });
  const earlier = review("w1", { run_id: "r-earlier", queued_at: "2026-08-31T02:00:00Z" });
  const tree = runTreeOf([later, w, earlier]);
  assert.deepEqual(ids(tree.units[0].nested), ["r-earlier", "r-later"]);
});

test("units and nesting are ordered by queue time, not by settle time", () => {
  // A long run queued first but finished last still began first: the timeline is a
  // stream of when work started.
  const slow = worker({
    run_id: "slow", queued_at: "2026-08-31T01:00:00Z", finished_at: "2026-08-31T09:00:00Z",
  });
  const quick = worker({
    run_id: "quick", queued_at: "2026-08-31T02:00:00Z", finished_at: "2026-08-31T02:05:00Z",
  });
  assert.deepEqual(ids(runTreeOf([quick, slow]).units.map((u) => u.run)), ["slow", "quick"]);
});

test("ties are broken by run id so the order does not flip between polls", () => {
  const a = worker({ run_id: "aaa", queued_at: "2026-08-31T01:00:00Z" });
  const b = worker({ run_id: "bbb", queued_at: "2026-08-31T01:00:00Z" });
  assert.deepEqual(ids(runTreeOf([b, a]).units.map((u) => u.run)), ["aaa", "bbb"]);
  assert.deepEqual(ids(runTreeOf([a, b]).units.map((u) => u.run)), ["aaa", "bbb"]);
});

test("an unread run list is an empty tree, not a crash", () => {
  assert.deepEqual(runTreeOf(null), { units: [], unplaceable: [] });
  assert.deepEqual(runTreeOf([]), { units: [], unplaceable: [] });
});

test("a failed nested run is counted so nesting cannot bury it", () => {
  const w = worker({ run_id: "w1" });
  const tree = runTreeOf([
    w,
    review("w1", { run_id: "r-ok", state: "succeeded" }),
    review("w1", { run_id: "r-bad", state: "failed" }),
    review("w1", { run_id: "r-timeout", state: "timed_out" }),
    review("w1", { run_id: "r-cancelled", state: "cancelled" }),
  ]);
  // A succeeded worker run with a failed review under it is exactly the pairing a
  // human has to notice, and indentation de-emphasises it — so the unit reports it.
  assert.equal(nestedFailureCount(tree.units[0]), 2);
  // Cancelled is settled without being a failure; counting it would cry wolf.
  assert.ok(!FAILED_RUN_STATES.has("cancelled"));
  assert.ok(!FAILED_RUN_STATES.has("succeeded"));
  assert.equal(nestedFailureCount({ run: w, nested: [] }), 0);
});

test("every unplaceable reason and the nesting labels are worded in both locales", () => {
  const keys: Record<UnplaceableReason, string> = {
    parentNotLoaded: "workspace.timeline.nested.unplacedParentNotLoaded",
    namesNoRun: "workspace.timeline.nested.unplacedNamesNoRun",
    namesItself: "workspace.timeline.nested.unplacedNamesItself",
    chainNotRooted: "workspace.timeline.nested.unplacedChainNotRooted",
  };
  for (const key of [
    ...Object.values(keys),
    "workspace.timeline.nested.label",
    "workspace.timeline.nested.failed",
    "workspace.timeline.nested.unplacedTitle",
  ]) {
    for (const locale of ["en", "zh"] as const) {
      assert.ok(lookup(locale, key), `${locale} is missing ${key}`);
    }
  }
});

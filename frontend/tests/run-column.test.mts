import assert from "node:assert/strict";
import test from "node:test";

import {
  GATE_UNKNOWN_REASON_KEY,
  GATE_VERDICT_KEY,
  REVIEW_REQUESTER,
  REVIEW_STANDING_KEY,
  RUN_REVIEWS_KEY,
  RUN_ROLE_KEY,
  SETTLED_RUN_STATES,
  gateVerdict,
  isReviewGateItem,
  latestDeliveredOf,
  outcomeClaimNotice,
  outcomeClaimOf,
  reviewStandingNotice,
  reviewStandingOf,
  runColumnOf,
  runIsUnsettled,
  runRole,
  type ColumnRunLike,
  type GateVerdict,
  type OutcomeClaim,
  type OutcomeFacts,
  type ReviewGateFacts,
  type ReviewGateLike,
  type ReviewStanding,
} from "../src/lib/run-column.ts";
import { taskAttentionView, type TaskAttentionView } from "../src/lib/task-attention.ts";
import { workspace as workspaceDictionary } from "../src/lib/i18n/dictionaries/workspace.ts";

type Locale = "en" | "zh";

/** Resolve a "workspace.…" message key against one locale, or undefined. */
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

const complete = <R extends ColumnRunLike>(runs: readonly R[] | null, active: readonly string[] | null) => ({
  runs, runsReadFailed: false, runsTruncated: false, activeRunIds: active, activeReadFailed: false,
});

const attentionOf = (
  items: readonly { item_id: string; state: string; kind: string; title: string; urgency: string }[] | null,
  over: { readFailed?: boolean; truncated?: boolean } = {},
) => taskAttentionView({ items, readFailed: over.readFailed ?? false, truncated: over.truncated ?? false });

const item = (id: string, kind = "approval") => ({
  item_id: id, state: "open", kind, title: `${kind} ${id}`, urgency: "normal",
});

/**
 * One attention item as the review gate check sees it. Defaults to the shape that
 * *is* Agora's review gate — `agora-review` requester, `approval` kind — so a test
 * that means "not the gate" has to say which field it changed.
 */
let gateSeq = 0;
const gateItem = (over: Partial<ReviewGateLike> = {}): ReviewGateLike => {
  gateSeq += 1;
  return {
    item_id: over.item_id ?? `i-${gateSeq}`,
    run_id: over.run_id ?? null,
    kind: over.kind ?? "approval",
    state: over.state ?? "open",
    requester: over.requester ?? REVIEW_REQUESTER,
    ...over,
  };
};

/** The run-scoped attention read, answered for `runId` unless said otherwise. */
const gateRead = (
  runId: string,
  items: readonly ReviewGateLike[] | null,
  over: { readFailed?: boolean; truncated?: boolean; forRunId?: string | null } = {},
): ReviewGateFacts<ReviewGateLike> => ({
  items,
  readFailed: over.readFailed ?? false,
  truncated: over.truncated ?? false,
  forRunId: over.forRunId === undefined ? runId : over.forRunId,
});

// --- the selection is made by run state, not by a stored tab -----------------

test("an unsettled run puts its own output in the column", () => {
  const live = run({ run_id: "r-live", state: "running", finished_at: null });
  const done = worker({ run_id: "r-done", state: "succeeded" });
  const column = runColumnOf(complete([done, live], ["r-live"]));
  assert.equal(column.kind, "live");
  assert.equal(column.kind === "live" && column.run.run_id, "r-live");
  assert.equal(column.kind === "live" && column.alsoActive, 0);
});

test("a settled run puts its own snapshot in the column", () => {
  const first = worker({ run_id: "r-1", finished_at: "2026-08-31T01:00:00Z" });
  const second = worker({ run_id: "r-2", finished_at: "2026-08-31T02:00:00Z" });
  const column = runColumnOf(complete([first, second], []));
  assert.equal(column.kind, "settled");
  assert.equal(column.kind === "settled" && column.run.run_id, "r-2");
  assert.equal(column.kind === "settled" && column.latestCertain, true);
});

test("every run state resolves to exactly one of the two states", () => {
  for (const state of ["queued", "running", ...SETTLED_RUN_STATES]) {
    const only = run({ run_id: "r", state, finished_at: runIsUnsettled({ state }) ? null : "2026-08-31T03:00:00Z" });
    const active = runIsUnsettled({ state }) ? ["r"] : [];
    const column = runColumnOf(complete([only], active));
    assert.equal(column.kind, runIsUnsettled({ state }) ? "live" : "settled", state);
  }
});

test("the live state wins even when the run list is unreadable or truncated", () => {
  // Presence is provable from any read. A live run must never be displaced by a
  // settled snapshot because the history read was imperfect.
  const live = run({ run_id: "r-live", state: "queued", started_at: null, finished_at: null });
  const column = runColumnOf({
    runs: [worker({ run_id: "r-old" }), live],
    runsReadFailed: true,
    runsTruncated: true,
    activeRunIds: ["r-live"],
    activeReadFailed: false,
  });
  assert.equal(column.kind, "live");
  assert.equal(column.kind === "live" && column.run.run_id, "r-live");
});

test("several unsettled runs show the newest and say how many others there are", () => {
  const a = run({ run_id: "r-a", state: "running", queued_at: "2026-08-31T01:00:00Z", started_at: "2026-08-31T01:00:00Z", finished_at: null });
  const b = run({ run_id: "r-b", state: "queued", queued_at: "2026-08-31T02:00:00Z", started_at: null, finished_at: null });
  const column = runColumnOf(complete([a, b], ["r-a", "r-b"]));
  assert.equal(column.kind === "live" && column.run.run_id, "r-b");
  assert.equal(column.kind === "live" && column.alsoActive, 1);
});

test("an active run with no record in hand is its own state, never a settled one", () => {
  // The authoritative active read names a run this view holds no record for. Falling
  // back to the settled snapshot would show a finished run as the current state of a
  // task that is executing.
  const column = runColumnOf(complete([worker({ run_id: "r-old" })], ["r-new"]));
  assert.equal(column.kind, "liveWithoutRecord");
  assert.equal(column.kind === "liveWithoutRecord" && column.count, 1);
});

// --- unknown must not render as absence -------------------------------------

test("a failed active-run read is unreadable, not an idle column", () => {
  const column = runColumnOf({
    runs: [worker({ run_id: "r-1" })],
    runsReadFailed: false,
    runsTruncated: false,
    activeRunIds: null,
    activeReadFailed: true,
  });
  assert.equal(column.kind, "unreadable");
});

test("no answer yet about active runs is loading, not unreadable and not none", () => {
  const column = runColumnOf({
    runs: null, runsReadFailed: false, runsTruncated: false, activeRunIds: null, activeReadFailed: false,
  });
  assert.equal(column.kind, "loading");
});

test("a truncated history cannot claim the task has never run", () => {
  const capped = runColumnOf({
    runs: [], runsReadFailed: false, runsTruncated: true, activeRunIds: [], activeReadFailed: false,
  });
  assert.equal(capped.kind, "unreadable");
  const clean = runColumnOf(complete([], []));
  assert.equal(clean.kind, "none");
});

test("a truncated or partial history does not claim its newest run is the latest", () => {
  for (const over of [{ runsTruncated: true }, { runsReadFailed: true }]) {
    const column = runColumnOf({
      runs: [worker({ run_id: "r-1" })],
      runsReadFailed: false,
      runsTruncated: false,
      activeRunIds: [],
      activeReadFailed: false,
      ...over,
    });
    assert.equal(column.kind, "settled");
    assert.equal(column.kind === "settled" && column.latestCertain, false, JSON.stringify(over));
  }
});

// --- the settled column shows what was DELIVERED -----------------------------

test("the settled column shows the worker run's snapshot, not the review's", () => {
  // R4 (2026-08-31). The automatic review settles AFTER the worker run it reviews, so
  // "the newest settled run" was the review run, and the column fetched ITS
  // deliverable snapshot — normally "No files changed" — under the heading "this
  // run's changes", in place of the files the worker delivered.
  const w = worker({ run_id: "w-1", finished_at: "2026-08-31T01:00:00Z" });
  const v = review("w-1", {
    run_id: "v-1", state: "succeeded", adapter: "claude", finished_at: "2026-08-31T02:00:00Z",
  });
  const column = runColumnOf(complete([w, v], []));
  assert.equal(column.kind, "settled");
  assert.equal(column.kind === "settled" && column.run.run_id, "w-1");
  // The review is not dropped — it is reported as the standing between that
  // delivery and done.
  assert.deepEqual(
    reviewStandingOf({
      run: (column as { run: ColumnRunLike }).run,
      runs: [w, v], runsReadFailed: false, runsTruncated: false,
      gate: gateRead("w-1", []),
    }),
    { kind: "noGateAfterReview", adapter: "claude", state: "succeeded" },
  );
});

test("a page holding only review runs still shows the row it has", () => {
  // Not a rounding: with no settled worker run in hand, showing the review row —
  // which `reviewStandingOf` labels "not an implementation run" — beats claiming the
  // task has produced nothing.
  const v = review("w-0", { run_id: "v-1", state: "succeeded", adapter: "claude" });
  const column = runColumnOf(complete([v], []));
  assert.equal(column.kind === "settled" && column.run.run_id, "v-1");
  assert.equal(
    runRole((column as { run: ColumnRunLike }).run) === "worker", false,
  );
});

test("an unknown future role is not shown as the delivered result either", () => {
  // Same rule as the role check everywhere else: only "worker" (or a missing role) is
  // the implementation. An `auditor` run is not promoted to it just by being settled.
  const w = worker({ run_id: "w-1", finished_at: "2026-08-31T01:00:00Z" });
  const other = run({
    run_id: "x-1", state: "succeeded", finished_at: "2026-08-31T02:00:00Z",
    result_metadata: { [RUN_ROLE_KEY]: "auditor" },
  });
  const column = runColumnOf(complete([w, other], []));
  assert.equal(column.kind === "settled" && column.run.run_id, "w-1");
});

// --- the delivered result, on the header's path too ---------------------------

test("the delivered result carries the read flags, not only the column does", () => {
  // R3 (2026-08-31). The outcome header consumed the same capped page through a plain
  // "latest success or null", so a task with more than RUN_HISTORY_LIMIT runs whose
  // only successful worker run is in the older history printed "Nothing produced
  // yet" — the exact absence-from-an-unknown the column refuses to draw from the
  // same read, one component away.
  const ok = worker({ run_id: "w-1", state: "succeeded" });
  const failed = worker({ run_id: "w-2", state: "failed" });
  const cases: Array<[
    { runs: readonly ColumnRunLike[] | null; runsReadFailed: boolean; runsTruncated: boolean },
    string,
    string,
  ]> = [
    [{ runs: null, runsReadFailed: false, runsTruncated: false }, "loading", "no answer yet"],
    [{ runs: null, runsReadFailed: true, runsTruncated: false }, "unknown", "the read failed"],
    [{ runs: [], runsReadFailed: false, runsTruncated: false }, "none", "a complete, empty read"],
    [{ runs: [], runsReadFailed: false, runsTruncated: true }, "unknown", "capped and empty"],
    [{ runs: [failed], runsReadFailed: false, runsTruncated: true }, "unknown", "capped, no success in hand"],
    [{ runs: [failed], runsReadFailed: true, runsTruncated: false }, "unknown", "records dropped"],
    [{ runs: [failed], runsReadFailed: false, runsTruncated: false }, "none", "complete, only a failure"],
    [{ runs: [ok], runsReadFailed: false, runsTruncated: false }, "run", "complete, one success"],
    [{ runs: [ok], runsReadFailed: false, runsTruncated: true }, "run", "capped, one success found"],
  ];
  for (const [facts, kind, why] of cases) {
    assert.equal(latestDeliveredOf(facts).kind, kind, why);
  }
  // A success that was found is real; only its primacy depends on the read.
  const certain = latestDeliveredOf({ runs: [ok], runsReadFailed: false, runsTruncated: false });
  assert.equal(certain.kind === "run" && certain.certain, true);
  for (const over of [{ runsTruncated: true }, { runsReadFailed: true }]) {
    const claim = latestDeliveredOf({ runs: [ok], runsReadFailed: false, runsTruncated: false, ...over });
    assert.equal(claim.kind === "run" && claim.certain, false, JSON.stringify(over));
  }
});

test("the delivered result is the newest successful WORKER run", () => {
  const first = worker({ run_id: "w-1", state: "succeeded", finished_at: "2026-08-31T01:00:00Z" });
  const second = worker({ run_id: "w-2", state: "succeeded", finished_at: "2026-08-31T03:00:00Z" });
  const reviewed = review("w-2", {
    run_id: "v-1", state: "succeeded", finished_at: "2026-08-31T04:00:00Z",
  });
  const claim = latestDeliveredOf({
    runs: [first, second, reviewed], runsReadFailed: false, runsTruncated: false,
  });
  assert.equal(claim.kind === "run" && claim.run.run_id, "w-2");
  // A review run that succeeded is not a delivered result, even alone.
  assert.equal(
    latestDeliveredOf({ runs: [reviewed], runsReadFailed: false, runsTruncated: false }).kind,
    "none",
  );
});

// --- the review standing ------------------------------------------------------
//
// The standing is derived from the fact the CONTROL PLANE releases the review
// obligation on: a gate item scoped to the reviewed worker run, in any state
// (ExecutionRunStore._has_review_gate_item), or an unsettled review run
// (_has_accounting_review_run, ACTIVE_RUN_STATES only). A settled review run — in
// ANY state, succeeded included — accounts for nothing there, and these tests pin
// that, because the previous version of this suite pinned the opposite.

test("an unsettled review run is what accounts for the review while it runs", () => {
  // The one review-run state that does account for anything: while it is unsettled
  // the backend refuses the next worker run on it (_reject_active_review), so
  // reporting it is not a claim the backend can contradict.
  const w = worker({ run_id: "w-1", state: "succeeded" });
  const active = review("w-1", { run_id: "v-1", state: "running", adapter: "claude", finished_at: null });
  assert.deepEqual(
    reviewStandingOf({
      run: w, runs: [w, active], runsReadFailed: false, runsTruncated: false,
      gate: gateRead("w-1", []),
    }),
    { kind: "active", adapter: "claude" },
  );
});

/**
 * Pins that NO settled review-run state accounts for the review — the rule
 * `_has_accounting_review_run` states — and that the column therefore reports the
 * arrangement (`noGateAfterReview`) instead of a verdict.
 *
 * **This test replaces one that asserted the defect.** The old
 * "a settled worker run with a linked review reports that review" pinned
 * `{ kind: "settled", state: "failed" }` as the standing, and its sibling
 * `CLEAR_REVIEW = { kind: "settled", state: "succeeded" }` then drove the
 * gate-verdict suite, so `clear` was pinned as the correct headline for a settled
 * review run. That expectation was wrong on the backend's own terms: a review run
 * that ended having written no gate item is the case
 * `_has_accounting_review_run` exists to refuse ("worse than no review at all,
 * because it *looks* finished"), and `cancelled` was removed from the accounting
 * states for the same reason (N1). With auto-review on, a worker run succeeding, its
 * review settling, and both gate writes failing, the old rule said the review was
 * accounted for and the column said `clear`, while the very next run was refused
 * `review_owed` by the same database.
 *
 * It also fails if the frontend ever goes back to reading the review run's state:
 * every settled state is enumerated here, including `succeeded`, and none of them
 * may produce an accounted standing or a `clear`/`reviewRunning` verdict.
 */
test("no settled review-run state accounts for the review without a gate item", () => {
  const w = worker({ run_id: "w-1", state: "succeeded" });
  for (const state of ["succeeded", "failed", "timed_out", "cancelled", "abandoned"]) {
    const done = review("w-1", { run_id: `v-${state}`, state, adapter: "claude" });
    const standing = reviewStandingOf({
      run: w, runs: [w, done], runsReadFailed: false, runsTruncated: false,
      // A complete run-scoped read that found no approval and no blocker for w-1.
      gate: gateRead("w-1", []),
    });
    assert.deepEqual(
      standing, { kind: "noGateAfterReview", adapter: "claude", state }, state,
    );
    // And it must not reach a headline the backend would contradict.
    const verdict = gateVerdict({ attention: attentionOf([]), review: standing });
    assert.deepEqual(verdict, { kind: "reviewNotAccounted" }, state);
  }
});

test("the gate item accounts for the review even when the review run failed", () => {
  // The other half of the same rule, and the sharper regression test: the review run
  // ended `failed`, which under the old state-reading rule was "settled" and under a
  // naive fix would be "nothing accounts for it". What accounts for it is the blocker
  // `_raise_review_attention` recorded against the worker run — so the standing must
  // follow the ITEM, not the run.
  const w = worker({ run_id: "w-1", state: "succeeded" });
  const done = review("w-1", { run_id: "v-1", state: "failed", adapter: "claude" });
  const blocker = gateItem({ run_id: "w-1", kind: "blocker", state: "cancelled" });
  const standing = reviewStandingOf({
    run: w, runs: [w, done], runsReadFailed: false, runsTruncated: false,
    gate: gateRead("w-1", [blocker]),
  });
  assert.deepEqual(standing, { kind: "gateSettled", count: 1 });
  assert.deepEqual(gateVerdict({ attention: attentionOf([]), review: standing }), { kind: "clear" });
});

test("a responded review approval releases the obligation, so the column may say clear", () => {
  // Why the run-scoped read asks for EVERY state. `_has_review_gate_item` matches the
  // item whatever state it is in, "cancelled and responded included, because the
  // human's disposition of it is the decision" — and those are exactly the items the
  // page's own `state=open` read cannot see. Reading absence out of that read is how
  // a released obligation looked like no gate at all.
  const w = worker({ run_id: "w-1", state: "succeeded" });
  const done = review("w-1", { run_id: "v-1", state: "succeeded", adapter: "claude" });
  const answered = gateItem({ run_id: "w-1", kind: "approval", state: "responded" });
  const standing = reviewStandingOf({
    run: w, runs: [w, done], runsReadFailed: false, runsTruncated: false,
    gate: gateRead("w-1", [answered]),
  });
  assert.deepEqual(standing, { kind: "gateSettled", count: 1 });
  assert.deepEqual(gateVerdict({ attention: attentionOf([]), review: standing }), { kind: "clear" });
});

test("an open review gate item is action required, never an accounted-and-done standing", () => {
  const w = worker({ run_id: "w-1", state: "succeeded" });
  const open = gateItem({ run_id: "w-1", kind: "approval", state: "open" });
  const standing = reviewStandingOf({
    run: w, runs: [w], runsReadFailed: false, runsTruncated: false,
    gate: gateRead("w-1", [open]),
  });
  assert.deepEqual(standing, { kind: "gateOpen", count: 1 });
  // Even if the task-scoped open list has not caught up with it yet, the read that
  // says an item IS open decides.
  assert.deepEqual(
    gateVerdict({ attention: attentionOf([]), review: standing }),
    { kind: "actionRequired", count: 1 },
  );
});

test("only Agora's own review gate item counts, and only for this run", () => {
  const w = worker({ run_id: "w-1", state: "succeeded" });
  const done = review("w-1", { run_id: "v-1", state: "succeeded", adapter: "claude" });
  const notAGate = [
    // Someone else's item on this run: `requester` is the authority marker.
    gateItem({ run_id: "w-1", requester: "user", state: "responded" }),
    // A question is not the review's gate (REVIEW_GATE_KINDS is approval/blocker).
    gateItem({ run_id: "w-1", kind: "question", state: "responded" }),
    // The right shape, the wrong run.
    gateItem({ run_id: "w-other", state: "responded" }),
    // Not scoped to a run at all.
    gateItem({ run_id: null, state: "responded" }),  ];
  for (const item of notAGate) {
    assert.deepEqual(
      reviewStandingOf({
        run: w, runs: [w, done], runsReadFailed: false, runsTruncated: false,
        gate: gateRead("w-1", [item]),
      }),
      { kind: "noGateAfterReview", adapter: "claude", state: "succeeded" },
      JSON.stringify(item),
    );
  }
});

test("a review belonging to a different run is not this run's review", () => {
  const w = worker({ run_id: "w-1" });
  const other = review("w-other", { run_id: "v-1", adapter: "claude" });
  assert.deepEqual(
    reviewStandingOf({
      run: w, runs: [w, other], runsReadFailed: false, runsTruncated: false,
      gate: gateRead("w-1", []),
    }),
    { kind: "noGateNoReview" },
  );
});

test("an unread or capped gate read cannot assert that no gate item exists", () => {
  const w = worker({ run_id: "w-1" });
  const cases: Array<[ReturnType<typeof gateRead>, string]> = [
    [gateRead("w-1", null), "no answer yet"],
    [gateRead("w-1", null, { readFailed: true }), "the read failed"],
    [gateRead("w-1", [], { truncated: true }), "the page hit its cap"],
    // The answer in hand is about a different run — the column moved on before this
    // response landed, so it is not an answer about this one.
    [gateRead("w-1", [], { forRunId: "w-other" }), "answer for another run"],
    [gateRead("w-1", [], { forRunId: null }), "no run recorded for the answer"],
  ];
  for (const [gate, why] of cases) {
    assert.deepEqual(
      reviewStandingOf({ run: w, runs: [w], runsReadFailed: false, runsTruncated: false, gate }),
      { kind: "unknown", because: "gateUnread" },
      why,
    );
  }
});

test("an incomplete run list leaves the standing unknown, in both directions", () => {
  const w = worker({ run_id: "w-1" });
  for (const over of [{ runsReadFailed: true }, { runsTruncated: true }]) {
    // A complete, empty gate read still cannot pick between "a review run settled
    // without its gate" and "no review run at all" — and cannot rule out an unsettled
    // one either, which is the fact that would account for the review.
    assert.deepEqual(
      reviewStandingOf({
        run: w, runs: [w], runsReadFailed: false, runsTruncated: false,
        gate: gateRead("w-1", []), ...over,
      }),
      { kind: "unknown", because: "runsIncomplete" },
      JSON.stringify(over),
    );
    // And "nothing was ever owed for this run" is an absence too: on a capped page
    // the worker run the backend nominates can be one this read never saw.
    const notWorker = review("w-0", { run_id: "v-1", state: "succeeded" });
    assert.deepEqual(
      reviewStandingOf({
        run: notWorker, runs: [notWorker], runsReadFailed: false, runsTruncated: false,
        gate: gateRead("v-1", []), ...over,
      }),
      { kind: "unknown", because: "runsIncomplete" },
      JSON.stringify(over),
    );
  }
  assert.deepEqual(
    reviewStandingOf({
      run: w, runs: null, runsReadFailed: false, runsTruncated: false,
      gate: gateRead("w-1", []),
    }),
    { kind: "unknown", because: "runsIncomplete" },
  );
});

test("only a successful worker run is treated as owing a review", () => {
  const failed = worker({ run_id: "w-1", state: "failed" });
  assert.deepEqual(
    reviewStandingOf({
      run: failed, runs: [failed], runsReadFailed: false, runsTruncated: false,
      gate: gateRead("w-1", []),
    }),
    { kind: "notOwed", because: "runDidNotSucceed" },
  );
  const reviewRun = review("w-0", { run_id: "v-1", state: "succeeded" });
  assert.deepEqual(
    reviewStandingOf({
      run: reviewRun, runs: [reviewRun], runsReadFailed: false, runsTruncated: false,
      gate: gateRead("v-1", []),
    }),
    { kind: "notOwed", because: "notWorker" },
  );
  // A role this UI does not know is not rounded to "worker".
  const future = run({ run_id: "x-1", result_metadata: { [RUN_ROLE_KEY]: "auditor" } });
  assert.deepEqual(
    reviewStandingOf({
      run: future, runs: [future], runsReadFailed: false, runsTruncated: false,
      gate: gateRead("x-1", []),
    }),
    { kind: "notOwed", because: "notWorker" },
  );
  // A missing role is a legacy worker run.
  assert.equal(runRole(run({ result_metadata: undefined })), "worker");
  assert.equal(runRole(run({ result_metadata: {} })), "worker");
});

// --- the gate verdict ---------------------------------------------------------

/** The one settled-and-accounted standing: the review's gate item, disposed of. */
const CLEAR_REVIEW: ReviewStanding = { kind: "gateSettled", count: 1 };

test("an observed gate item outranks everything, from a complete or a capped page", () => {
  for (const truncated of [false, true]) {
    const verdict = gateVerdict({
      attention: attentionOf([item("a-1")], { truncated }),
      review: { kind: "active", adapter: "claude" },
    });
    assert.deepEqual(verdict, { kind: "actionRequired", count: 1 }, `truncated=${truncated}`);
  }
});

test("an unreadable or capped-and-empty attention read is unknown, never clear", () => {
  const cases: Array<[TaskAttentionView<ReturnType<typeof item>>, GateVerdict["kind"], string]> = [
    [attentionOf(null), "unknown", "attentionLoading"],
    [attentionOf(null, { readFailed: true }), "unknown", "attentionUnreadable"],
    [attentionOf([], { truncated: true }), "unknown", "attentionPartial"],
  ];
  for (const [attention, kind, reason] of cases) {
    const verdict: GateVerdict = gateVerdict({ attention, review: CLEAR_REVIEW });
    assert.equal(verdict.kind, kind, reason);
    assert.equal(verdict.kind === "unknown" && verdict.reason, reason);
  }
});

test("a running review is reported before any unknown about the review", () => {
  assert.deepEqual(
    gateVerdict({ attention: attentionOf([]), review: { kind: "active", adapter: "kiro" } }),
    { kind: "reviewRunning", adapter: "kiro" },
  );
  for (const because of ["runsIncomplete", "gateUnread"] as const) {
    assert.deepEqual(
      gateVerdict({ attention: attentionOf([]), review: { kind: "unknown", because } }),
      { kind: "unknown", reason: "reviewUnknown" },
      because,
    );
  }
  assert.deepEqual(
    gateVerdict({ attention: attentionOf([]), review: { kind: "noGateNoReview" } }),
    { kind: "reviewNotAccounted" },
  );
});

test("clear is reachable only from what the control plane accepts as accounting", () => {
  // The review's gate item, disposed of by the human (_has_review_gate_item), and
  // "nothing was owed for this run at all". Those are the two.
  assert.deepEqual(gateVerdict({ attention: attentionOf([]), review: CLEAR_REVIEW }), { kind: "clear" });
  for (const because of ["runDidNotSucceed", "notWorker"] as const) {
    assert.deepEqual(
      gateVerdict({ attention: attentionOf([]), review: { kind: "notOwed", because } }),
      { kind: "clear" },
      because,
    );
  }
  // Nothing else may reach it — in particular nothing derived from a review run
  // having finished, which is what the backend refuses to accept as accounting.
  for (const review of [
    { kind: "unknown", because: "runsIncomplete" } as ReviewStanding,
    { kind: "unknown", because: "gateUnread" } as ReviewStanding,
    { kind: "noGateNoReview" } as ReviewStanding,
    { kind: "noGateAfterReview", adapter: "claude", state: "succeeded" } as ReviewStanding,
    { kind: "active", adapter: "claude" } as ReviewStanding,
    { kind: "gateOpen", count: 1 } as ReviewStanding,
  ]) {
    assert.notEqual(
      gateVerdict({ attention: attentionOf([]), review }).kind, "clear", review.kind,
    );
  }
  for (const attention of [
    attentionOf(null),
    attentionOf(null, { readFailed: true }),
    attentionOf([], { truncated: true }),
    attentionOf([item("a-1")]),
  ]) {
    assert.notEqual(gateVerdict({ attention, review: CLEAR_REVIEW }).kind, "clear");
  }
});

test("isReviewGateItem is exactly (run, requester, kind) and nothing else", () => {
  const gate = gateItem({ run_id: "w-1" });
  assert.equal(isReviewGateItem(gate, "w-1"), true);
  assert.equal(isReviewGateItem(gate, "w-2"), false);
  assert.equal(isReviewGateItem({ ...gate, requester: "user" }, "w-1"), false);
  assert.equal(isReviewGateItem({ ...gate, kind: "question" }, "w-1"), false);
  assert.equal(isReviewGateItem({ ...gate, kind: "blocker" }, "w-1"), true);
  // State is deliberately not part of it: the human's disposition of the item is the
  // decision, so a responded or cancelled one still IS the review's gate.
  for (const state of ["open", "responded", "cancelled", "expired"]) {
    assert.equal(isReviewGateItem({ ...gate, state }, "w-1"), true, state);
  }
});

// --- every sentence exists, in both locales ----------------------------------

test("every verdict, unknown reason and review standing has a message in both locales", () => {
  const keys = [
    ...Object.values(GATE_VERDICT_KEY),
    ...Object.values(GATE_UNKNOWN_REASON_KEY),
    ...Object.values(REVIEW_STANDING_KEY),
    // The two states the column itself renders as one sentence.
    "workspace.column.loading",
    "workspace.column.unreadable",
    "workspace.column.none",
    "workspace.column.liveWithoutRecord",
    "workspace.column.maybeNotLatest",
    "workspace.column.openRuns",
    "workspace.column.title.live",
    "workspace.column.title.settled",
    "workspace.column.title.loading",
    "workspace.column.title.unreadable",
    "workspace.column.title.none",
    "workspace.column.live.header",
    "workspace.column.live.alsoActive",
    "workspace.column.live.noOutputYet",
    "workspace.column.changes.header",
    // The outcome header's own claims about the same capped run history (R3), and
    // the handoff paths' new home in the gate block (R4).
    "workspace.summary.nothingYet",
    "workspace.summary.producedUnknown",
    "workspace.panels.changes.files",
    "workspace.panels.changes.moreFiles",
    "workspace.timeline.run.partial",
    // Requirements moved to the header strip; its own sentences must exist too.
    "workspace.requirements.label",
    "workspace.requirements.loading",
    "workspace.requirements.scenarioCount",
  ];
  for (const key of keys) {
    for (const locale of ["en", "zh"] as const) {
      assert.ok(lookup(locale, key), `${locale} is missing ${key}`);
    }
  }
});

test("every review standing has its own sentence, and no two share one", () => {
  // `settled`/`noneRecorded` were replaced by four sentences that say which
  // arrangement was found, because "a review finished" and "the review is accounted
  // for" are different facts and one sentence for both is how the column came to
  // report the second while only the first was true.
  const keys = Object.values(REVIEW_STANDING_KEY);
  assert.equal(new Set(keys).size, keys.length, JSON.stringify(keys));
  for (const locale of ["en", "zh"] as const) {
    assert.equal(lookup(locale, "workspace.column.review.settled"), undefined);
    assert.equal(lookup(locale, "workspace.column.review.noneRecorded"), undefined);
    assert.equal(lookup(locale, "workspace.column.gate.noReviewRecorded"), undefined);
  }
});

test("the review notice localizes the review run's own state through the caller", () => {
  const notice = reviewStandingNotice(
    { kind: "noGateAfterReview", state: "timed_out", adapter: "claude" },
    (state) => `<${state}>`,
  );
  assert.equal(notice.key, REVIEW_STANDING_KEY.noGateAfterReview);
  assert.deepEqual(notice.vars, { adapter: "claude", state: "<timed_out>" });
  // A sentence with a placeholder must have something to put in it.
  for (const locale of ["en", "zh"] as const) {
    const template = lookup(locale, notice.key)!;
    assert.match(template, /\{adapter\}/);
    assert.match(template, /\{state\}/);
  }
  // Every standing is worded, and the two unknowns do not share one sentence: which
  // read could not answer is the actionable half.
  const standings: ReviewStanding[] = [
    { kind: "notOwed", because: "notWorker" },
    { kind: "notOwed", because: "runDidNotSucceed" },
    { kind: "active", adapter: "claude" },
    { kind: "gateOpen", count: 2 },
    { kind: "gateSettled", count: 1 },
    { kind: "noGateNoReview" },
    { kind: "unknown", because: "runsIncomplete" },
    { kind: "unknown", because: "gateUnread" },
  ];
  for (const standing of standings) {
    const worded = reviewStandingNotice(standing, (state) => state);
    for (const locale of ["en", "zh"] as const) {
      assert.ok(lookup(locale, worded.key), `${locale} is missing ${worded.key}`);
    }
  }
  assert.notEqual(
    reviewStandingNotice({ kind: "unknown", because: "runsIncomplete" }, (s) => s).key,
    reviewStandingNotice({ kind: "unknown", because: "gateUnread" }, (s) => s).key,
  );
});

test("the tab vocabulary is gone from the dictionary as well as from the view", () => {
  // The column is derived; a leftover tab label is an invitation to put the tabs
  // back, and a dictionary entry that names nothing on screen is untrue by omission.
  for (const locale of ["en", "zh"] as const) {
    assert.equal(lookup(locale, "workspace.tabs.requirements"), undefined);
    assert.equal(lookup(locale, "workspace.panels.run.latest"), undefined);
  }
});

// --- S1: the outcome header's four answers stay four answers -----------------

/**
 * The header's facts. Defaults are the case that used to collapse: a success was
 * found, its output held nothing printable, and the delivery record was read and
 * listed no files.
 */
const outcomeFacts = (
  over: Partial<OutcomeFacts<ColumnRunLike>> = {},
): OutcomeFacts<ColumnRunLike> => ({
  delivered: { kind: "run", run: worker({ run_id: "w-1" }), certain: true },
  hasOutcomeLine: false,
  fileCount: 0,
  filesKnown: true,
  deliveryReadFailed: false,
  ...over,
});

test("a run that succeeded and recorded nothing does not share wording with 'never succeeded'", () => {
  const ranAndRecordedNothing = outcomeClaimOf(outcomeFacts());
  const neverSucceeded = outcomeClaimOf(outcomeFacts({ delivered: { kind: "none" } }));
  assert.deepEqual(ranAndRecordedNothing, { kind: "producedNothingRecorded" });
  assert.deepEqual(neverSucceeded, { kind: "noSuccessfulRun" });
  // The defect this fixes was at the render layer: both printed
  // `workspace.summary.nothingYet`.
  assert.notEqual(
    outcomeClaimNotice(ranAndRecordedNothing),
    outcomeClaimNotice(neverSucceeded),
  );
});

test("all four of latestDeliveredOf's answers reach the header distinctly", () => {
  const notices = new Set([
    outcomeClaimNotice(outcomeClaimOf(outcomeFacts({ delivered: { kind: "loading" } }))),
    outcomeClaimNotice(outcomeClaimOf(outcomeFacts({ delivered: { kind: "unknown" } }))),
    outcomeClaimNotice(outcomeClaimOf(outcomeFacts({ delivered: { kind: "none" } }))),
    outcomeClaimNotice(outcomeClaimOf(outcomeFacts())),
  ]);
  assert.equal(notices.size, 4, "each answer needs its own sentence");
});

test("a success with output or files still shows the substance, not a sentence", () => {
  assert.equal(outcomeClaimNotice(outcomeClaimOf(outcomeFacts({ hasOutcomeLine: true }))), null);
  assert.equal(
    outcomeClaimNotice(outcomeClaimOf(outcomeFacts({ fileCount: 3 }))),
    null,
  );
  // Files found are evidence even when no successful run was found in the page: the
  // delivery record is its own read.
  assert.equal(
    outcomeClaimNotice(outcomeClaimOf(outcomeFacts({ delivered: { kind: "none" }, fileCount: 2 }))),
    null,
  );
});

test("a zero file count that was never read is not 'the handoff lists no files'", () => {
  // The header fetches the task's handoff only for a task in state `done`, so an
  // unasked read yields 0 — claiming the handoff is empty from it is the same false
  // absence the rest of this module refuses.
  assert.deepEqual(
    outcomeClaimOf(outcomeFacts({ filesKnown: false })),
    { kind: "producedFilesUnknown", because: "notRead" },
  );
  assert.deepEqual(
    outcomeClaimOf(outcomeFacts({ filesKnown: false, deliveryReadFailed: true })),
    { kind: "producedFilesUnknown", because: "readFailed" },
  );
  assert.notEqual(
    outcomeClaimNotice(outcomeClaimOf(outcomeFacts({ filesKnown: false }))),
    outcomeClaimNotice(outcomeClaimOf(outcomeFacts())),
  );
  // The two reasons are separately worded: one is an error, the other is a read this
  // surface does not make.
  assert.notEqual(
    outcomeClaimNotice(outcomeClaimOf(outcomeFacts({ filesKnown: false }))),
    outcomeClaimNotice(outcomeClaimOf(outcomeFacts({ filesKnown: false, deliveryReadFailed: true }))),
  );
});

test("a failed delivery read never becomes 'no successful run'", () => {
  // D-6, kept: with no success in a complete run page AND a failed delivery read,
  // "nothing was produced" is not provable.
  assert.deepEqual(
    outcomeClaimOf(outcomeFacts({ delivered: { kind: "none" }, deliveryReadFailed: true })),
    { kind: "deliveryUnreadable" },
  );
});

test("a truncated run page cannot say a run succeeded and recorded nothing", () => {
  // `unknown` is the R3 case: the success may be outside the page entirely, so
  // nothing may be claimed about what the latest one recorded.
  assert.deepEqual(outcomeClaimOf(outcomeFacts({ delivered: { kind: "unknown" } })), { kind: "unknownRuns" });
});

test("loading is a sentence, not an em dash", () => {
  // Under a heading that asks "what it did", a lone dash is indistinguishable from a
  // rendered empty value — the one thing this block may not look like.
  const key = outcomeClaimNotice(outcomeClaimOf(outcomeFacts({ delivered: { kind: "loading" } })));
  assert.equal(key, "workspace.summary.producedLoading");
  for (const locale of ["en", "zh"] as const) {
    const text = lookup(locale, key!);
    assert.ok(text && text.trim().length > 1, `${locale} needs real words for ${key}`);
    assert.ok(!/^[—–-]+$/.test(text.trim()));
  }
});

test("every outcome claim has a sentence in both locales", () => {
  const claims: OutcomeClaim[] = [
    { kind: "loading" },
    { kind: "producedNothingRecorded" },
    { kind: "producedFilesUnknown", because: "notRead" },
    { kind: "producedFilesUnknown", because: "readFailed" },
    { kind: "unknownRuns" },
    { kind: "deliveryUnreadable" },
    { kind: "noSuccessfulRun" },
  ];
  const keys = new Set<string>();
  for (const claim of claims) {
    const key = outcomeClaimNotice(claim);
    assert.ok(key, `${claim.kind} must have a message key`);
    keys.add(key!);
    for (const locale of ["en", "zh"] as const) {
      assert.ok(lookup(locale, key!), `${locale} is missing ${key}`);
    }
  }
  // Seven claims, seven distinct sentences — no two states share wording.
  assert.equal(keys.size, claims.length);
});

test("the outcome header no longer reads the verified percentage", () => {
  // S3: `workspace.project.strip` interpolated `{verified}%` from
  // `metrics.verified_pct`, which counts a criterion as verified from any historical
  // successful run of its task. The strip states the task tally only; the progress
  // figure comes from the snapshot (lib/progress-snapshot.ts).
  for (const locale of ["en", "zh"] as const) {
    const strip = lookup(locale, "workspace.project.strip");
    assert.ok(strip, `${locale} is missing workspace.project.strip`);
    assert.ok(!strip.includes("{verified}"), `${locale} strip must not interpolate a verified %`);
  }
});

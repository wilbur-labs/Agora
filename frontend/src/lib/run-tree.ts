// The task timeline's run structure: work units, not process launches.
//
// The recorded UI decision: runs are not joined by drawn connectors, they are
// nested. A review run is indented under the worker run it reviews, and the
// top-level timeline lists worker runs only. The reasons recorded with it were that
// connector lines turn into spaghetti, that the relation already exists in the data
// (`reviews_run_id`), and that the timeline should be ordered by work unit rather
// than by how many processes happened to start.
//
// Nesting introduces exactly one new way to lie, and this module's job is to refuse
// it: a nested run whose parent is not in the loaded page must not disappear. The
// page is capped at 100 runs and a per-run detail read can fail, so "the run this
// reviews is not here" is an ordinary outcome, and it means *I cannot place this
// run* — never *this run does not exist*. Unplaceable runs come back as their own
// list, with the reason, so the view can show them instead of dropping them.
//
// That guarantee holds for any relation graph, not just the one dispatch writes today:
// `reviews_run_id` is a JSON field with no constraint behind it, so a review of a
// review, and two reviews naming each other, are both possible records. The set of run
// ids in the output therefore equals the set in the input in every case — a chain is
// placed in the unit at its root, and a chain that never reaches a top-level run is
// unplaceable with a reason.
//
// **Retries carry no relation, and none is invented here.** A run's identity is
// exactly `{agora_role, reviews_run_id, task_version}`
// (`IDENTITY_METADATA_KEYS` in backend/agora/execution/models.py); nothing records
// "this run retries that one", and no endpoint writes such a field. So a retry is an
// ordinary top-level work unit. Guessing a parent from adjacency or from a shared
// worktree would be a relation the control plane never asserted.
//
// Deliberately free of React and of "@/" path aliases: `node --test` executes the
// companion test file (tests/run-tree.test.mts) directly. The imports are values
// from a sibling module that has the same property.

import {
  ROLE_REVIEW,
  reviewedRunId,
  runRole,
  type ColumnRunLike,
} from "./run-column.ts";

/** Why a nested run could not be placed under a parent. */
export type UnplaceableReason =
  /** It names a run that is not in the loaded page (the cap, or a failed detail read). */
  | "parentNotLoaded"
  /** It is a review run that names no run at all — the link was never recorded. */
  | "namesNoRun"
  /** It names itself. A run cannot be its own work unit's child. */
  | "namesItself"
  /**
   * It reviews another review, and following that chain never reaches a top-level
   * run: the chain loops, or it ends at a review that cannot be placed either.
   */
  | "chainNotRooted";

/** One work unit: a top-level run and the runs recorded against it. */
export interface RunUnit<R extends ColumnRunLike> {
  run: R;
  /** Runs nested under it, ordered like the top level. Empty for most units. */
  nested: readonly R[];
}

/** A run the view must still show, and the reason it has no place in the tree. */
export interface UnplacedRun<R extends ColumnRunLike> {
  run: R;
  because: UnplaceableReason;
}

/**
 * The timeline's runs as work units plus the runs that could not be placed.
 *
 * `unplaceable` is not an error state and not a rounding of "no runs": it is the
 * honest rendering of a partial read, and the view is expected to show those runs
 * with a line saying why they are not nested under anything.
 */
export interface RunTree<R extends ColumnRunLike> {
  units: readonly RunUnit<R>[];
  unplaceable: readonly UnplacedRun<R>[];
}

/**
 * Ordering key for the timeline: when the work unit *began*, not when it settled.
 * The timeline interleaves runs with events chronologically, and a unit's place in
 * that stream is where its work started — a long run that finishes later did not
 * happen later.
 */
function startKey(run: ColumnRunLike): string {
  return run.queued_at || run.started_at || "";
}

function byStart<R extends ColumnRunLike>(a: R, b: R): number {
  const byTime = startKey(a).localeCompare(startKey(b));
  // run_id breaks ties so the order does not flip between polls for two runs
  // recorded in the same instant.
  return byTime !== 0 ? byTime : a.run_id.localeCompare(b.run_id);
}

/**
 * The work unit a review run belongs to: follow `reviews_run_id` until it reaches a
 * top-level (non-review) run of this page, or return the reason it never does.
 *
 * Dispatch links reviews to worker runs today, but the relation is stored as JSON with
 * nothing forbidding a review of a review — or a pair of reviews naming each other — so
 * this walk is what makes the module's one promise ("nothing vanishes") true for any
 * graph rather than only for the shape production happens to write. A review of a
 * review is placed in the unit at the root of its chain: the timeline renders one level
 * of nesting, and the root is the work unit the chain is about, which is in the data
 * instead of guessed. Every way the walk can fail is a reason, never a drop — the first
 * step keeps its own names because they are what the record actually shows, and any
 * failure deeper in (an ancestor off the page, an ancestor linking to nothing, or a
 * loop) is `chainNotRooted`.
 */
function unitRunOf<R extends ColumnRunLike>(
  run: R,
  byId: ReadonlyMap<string, R>,
): R | UnplaceableReason {
  const seen = new Set<string>([run.run_id]);
  let cursor: R = run;
  // Terminates: every pass either returns or adds one previously unseen id from a
  // finite map to `seen`.
  for (;;) {
    const firstStep = cursor.run_id === run.run_id;
    const parentId = reviewedRunId(cursor);
    if (parentId === "") return firstStep ? "namesNoRun" : "chainNotRooted";
    if (parentId === cursor.run_id) return firstStep ? "namesItself" : "chainNotRooted";
    const parent = byId.get(parentId);
    if (parent === undefined) return firstStep ? "parentNotLoaded" : "chainNotRooted";
    // Role rule as everywhere else: only a review run has a parent to look for, so the
    // first non-review ancestor is the top-level run whose unit this is.
    if (runRole(parent) !== ROLE_REVIEW) return parent;
    if (seen.has(parent.run_id)) return "chainNotRooted";
    seen.add(parent.run_id);
    cursor = parent;
  }
}

export function runTreeOf<R extends ColumnRunLike>(runs: readonly R[] | null): RunTree<R> {
  const held = runs ?? [];
  const byId = new Map<string, R>();
  for (const run of held) byId.set(run.run_id, run);

  const tops: R[] = [];
  const nestedBy = new Map<string, R[]>();
  const unplaceable: UnplacedRun<R>[] = [];

  for (const run of held) {
    // Role rule as everywhere else: a missing role is a legacy worker run, and a role
    // this UI does not know is NOT rounded to review. Only a review run has a parent
    // to look for; anything else is its own work unit, which is what keeps retries and
    // unknown roles at the top level rather than guessed into someone's subtree.
    if (runRole(run) !== ROLE_REVIEW) {
      tops.push(run);
      continue;
    }
    const parent = unitRunOf(run, byId);
    if (typeof parent === "string") {
      // Its place is unknown. The run is real and stays on screen, with the reason.
      unplaceable.push({ run, because: parent });
      continue;
    }
    const siblings = nestedBy.get(parent.run_id);
    if (siblings) siblings.push(run);
    else nestedBy.set(parent.run_id, [run]);
  }

  const units = [...tops].sort(byStart).map((run) => ({
    run,
    nested: [...(nestedBy.get(run.run_id) ?? [])].sort(byStart) as readonly R[],
  }));
  return { units, unplaceable: [...unplaceable].sort((a, b) => byStart(a.run, b.run)) };
}

/**
 * Run states that are a failure a human needs to see. Shared with the run card so
 * one list decides what "failed" means; `cancelled` is deliberately not here — it is
 * settled without being a failure.
 */
export const FAILED_RUN_STATES: ReadonlySet<string> = new Set(["failed", "timed_out", "abandoned"]);

/**
 * How many of a unit's nested runs failed.
 *
 * Nesting must not bury a nested run's own state: a failed review under a succeeded
 * worker run is exactly the case a person has to notice, and indentation naturally
 * de-emphasises it. So the unit reports the count and the header can carry it, in
 * addition to each nested card keeping its own state badge.
 */
export function nestedFailureCount<R extends ColumnRunLike>(unit: RunUnit<R>): number {
  return unit.nested.filter((run) => FAILED_RUN_STATES.has(run.state)).length;
}

// The Task Workspace's right column, decided by the state of this task's runs.
//
// The column used to be a fixed four-tab bar (requirements / run / attention /
// changes) with the selection held in component state. That made the most
// prominent surface on the page answer "here are four things you could look at"
// while a run was executing three feet away. The recorded UI decision is that the
// column has two states and no stored tab:
//
//   Run unsettled  -> the column IS that run's output.
//   Run settled    -> the column IS what that run changed, plus the gate standing
//                     between it and done.
//
// So the selection is derived here, from runs, and nothing in the view may
// override it. That is the load-bearing property: a tab index can disagree with
// reality, a derivation cannot.
//
// The second load-bearing property is the project's standing rule, which this
// module is where the column decides it: an unknown must never render as an
// absence. "The run list could not be read" is not "no run is live", and "the run
// list came back at its page limit" is not "these are all the runs there are". Both
// would otherwise present a stale settled snapshot as the current state of a task
// that is executing right now — the exact substitution the two-state column exists
// to remove, reintroduced one layer down.
//
// Deliberately free of React and of "@/" path aliases: `node --test` executes the
// companion test file (tests/run-column.test.mts) directly. The one import is
// type-only, so nothing is resolved at runtime.

import type { AttentionLike, TaskAttentionView } from "./task-attention.ts";

/**
 * The fields the column needs from a run record; a structural subset.
 *
 * A run's identity — its role and the run it reviews — arrives in one of two places.
 * The full record (`ExecutionRun`) carries it inside `result_metadata` under the keys
 * below. A **summary** (`RunSummary`, what the run *list* returns) carries no metadata at
 * all and instead lifts the two keys to top-level fields, precisely so that a page of
 * summaries can nest reviews (backend/agora/execution/models.py, `RunSummary`). Both
 * shapes are read here; a reader that looked at the metadata alone filed every review on
 * a summaries page as a worker turn of its own — which is what the conversation did.
 */
export interface ColumnRunLike {
  run_id: string;
  state: string;
  adapter: string;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  result_metadata?: Record<string, unknown> | null;
  /** The summary's lifted identity. `undefined` when the shape does not carry it. */
  role?: string;
  reviews_run_id?: string | null;
}

/**
 * Run states that have settled. The single definition: `lib/execution.ts` exports
 * its typed `TERMINAL_RUN_STATES` from this set rather than listing the states a
 * second time, because two lists of run states are two things to keep in step and
 * the one that drifts decides whether the column shows a terminal or a snapshot.
 */
export const SETTLED_RUN_STATES: ReadonlySet<string> = new Set([
  "succeeded", "failed", "timed_out", "cancelled", "abandoned",
]);

export function runIsUnsettled(run: { state: string }): boolean {
  return !SETTLED_RUN_STATES.has(run.state);
}

/** Run identity keys, mirroring ROLE_KEY / REVIEWS_RUN_KEY in execution/models.py. */
export const RUN_ROLE_KEY = "agora_role";
export const RUN_REVIEWS_KEY = "reviews_run_id";
export const ROLE_WORKER = "worker";
export const ROLE_REVIEW = "review";

/**
 * A run's role, by the same rule the dispatcher and handoff use: a missing role is
 * a legacy worker run, but anything present must match exactly. An unknown or
 * future role is never rounded to "worker" — it is returned as itself, so the
 * caller declines to treat it as the delivered result rather than mislabelling it.
 */
export function runRole(run: ColumnRunLike): string {
  // The summary's lifted field first: when the shape carries it, it is the backend's own
  // reading of the metadata (`role_of`), and the metadata is not on the summary at all.
  if (typeof run.role === "string") return run.role;
  const raw = (run.result_metadata ?? {})[RUN_ROLE_KEY];
  return raw === undefined || raw === null ? ROLE_WORKER : String(raw);
}

/** The run a review run reviews, or "" when it names none. */
export function reviewedRunId(run: ColumnRunLike): string {
  // `undefined` means the shape has no such field; `null` means it has one and it names
  // nothing. Only the first falls through to the metadata.
  if (run.reviews_run_id !== undefined) return run.reviews_run_id ?? "";
  const raw = (run.result_metadata ?? {})[RUN_REVIEWS_KEY];
  return raw === undefined || raw === null ? "" : String(raw);
}

/**
 * Ordering key for "which run is the current one". `finished_at` for a settled
 * run, else when it started, else when it was queued — the same fallback chain the
 * rail already uses for activity time.
 */
function orderKey(run: ColumnRunLike): string {
  return run.finished_at ?? run.started_at ?? run.queued_at;
}

function newest<R extends ColumnRunLike>(runs: readonly R[]): R {
  // run_id breaks ties so the column does not flip between two runs recorded in
  // the same instant on successive polls.
  return [...runs].sort((a, b) => {
    const byTime = orderKey(a).localeCompare(orderKey(b));
    return byTime !== 0 ? byTime : a.run_id.localeCompare(b.run_id);
  })[runs.length - 1];
}

/**
 * What the column is showing.
 *
 * `liveWithoutRecord` is not a rounding of `live`: the authoritative active-run
 * read can name a run this view holds no record for (it was dispatched a moment
 * ago, or the history read failed), and falling back to `settled` there would put
 * a finished run's snapshot on screen while a run is executing — a stale answer
 * presented as the present.
 *
 * `unreadable` and `loading` are kept apart for the reason they always are here: a
 * spinner and an error are different things to show a person, and neither may be
 * shown as `none`.
 */
export type RunColumn<R extends ColumnRunLike> =
  | { kind: "loading" }
  | { kind: "unreadable" }
  | { kind: "none" }
  | { kind: "live"; run: R; alsoActive: number }
  | { kind: "liveWithoutRecord"; count: number }
  | { kind: "settled"; run: R; latestCertain: boolean };

/**
 * The reads the column is derived from.
 *
 * `activeRunIds` is the authoritative "is a run unsettled?" answer, asked as its
 * own request; null means no answer yet or the read failed, told apart by
 * `activeReadFailed`. `runs` is the page of full run records — capped, hence
 * `runsTruncated`, and partially readable, hence `runsReadFailed`.
 */
export interface RunColumnFacts<R extends ColumnRunLike> {
  runs: readonly R[] | null;
  runsReadFailed: boolean;
  runsTruncated: boolean;
  activeRunIds: readonly string[] | null;
  activeReadFailed: boolean;
}

export function runColumnOf<R extends ColumnRunLike>(facts: RunColumnFacts<R>): RunColumn<R> {
  const held = facts.runs ?? [];
  // Presence is provable from any read, complete or not: a run that is unsettled is
  // unsettled. Both sources count, because each knows something the other does not
  // — the active read is authoritative and record-free, the held records include a
  // run dispatched from this page a moment ago.
  const unsettled = new Set<string>(facts.activeRunIds ?? []);
  for (const run of held) if (runIsUnsettled(run)) unsettled.add(run.run_id);
  if (unsettled.size > 0) {
    const records = held.filter((run) => unsettled.has(run.run_id));
    if (records.length === 0) return { kind: "liveWithoutRecord", count: unsettled.size };
    return { kind: "live", run: newest(records), alsoActive: unsettled.size - 1 };
  }

  // Past here the column would say "nothing is running", which no failed or
  // unfinished read can support.
  if (facts.activeReadFailed) return { kind: "unreadable" };
  if (facts.activeRunIds === null) return { kind: "loading" };
  if (facts.runs === null) return facts.runsReadFailed ? { kind: "unreadable" } : { kind: "loading" };

  const settled = held.filter((run) => !runIsUnsettled(run));
  if (settled.length === 0) {
    // "This task has never run" is a claim about the whole history, so a read that
    // dropped records or stopped at its cap cannot make it.
    return facts.runsReadFailed || facts.runsTruncated ? { kind: "unreadable" } : { kind: "none" };
  }
  // The settled column exists to show *what a run produced*, and in the auto-review
  // flow the newest settled row is usually the review run — which produced nothing,
  // so the column rendered "No files changed" in place of the worker's delivered
  // files and called it "this run's changes". The newest settled **worker** run is
  // the delivered result; the review is reported separately, as the standing between
  // that result and done (`reviewStandingOf` below). Role rule as everywhere else: a
  // missing role is a legacy worker run, anything present must equal "worker".
  //
  // Falling back to the newest settled row of any role is not a rounding: it is only
  // reached when the page holds no settled worker run at all, and showing the row we
  // do have — labelled by `reviewStandingOf` as "not an implementation run" — beats
  // claiming the task has never produced anything.
  const workers = settled.filter((run) => runRole(run) === ROLE_WORKER);
  return {
    kind: "settled",
    run: newest(workers.length > 0 ? workers : settled),
    // The newest run we hold is only *the* newest if the read saw every run.
    latestCertain: !facts.runsReadFailed && !facts.runsTruncated,
  };
}

/**
 * The latest *delivered* result of this task: its newest successful worker run, or
 * the reason there is no such answer.
 *
 * This exists because the outcome header consumed the same capped, partially
 * readable run page as the column and drew an absence out of it: with more than
 * `RUN_HISTORY_LIMIT` runs whose older history holds the only successful worker
 * run, "latest success or null" printed "Nothing produced yet", and a failed detail
 * read on the newest success silently promoted an older one to "What it did" with
 * none of the column's uncertainty. `runsTruncated`/`runsReadFailed` have to apply
 * on **every** path that consumes that page, not only inside `runColumnOf`.
 *
 * The asymmetry is the project's usual one: a success that was *found* is real
 * whatever the read dropped (`kind: "run"`), but only a complete read may say the
 * task produced nothing (`kind: "none"`); an incomplete one is `unknown`. `certain`
 * carries the other half — the run is real, its primacy is not.
 */
export type DeliveredResult<R extends ColumnRunLike> =
  | { kind: "loading" }
  | { kind: "unknown" }
  | { kind: "none" }
  | { kind: "run"; run: R; certain: boolean };

export function latestDeliveredOf<R extends ColumnRunLike>(facts: {
  runs: readonly R[] | null;
  runsReadFailed: boolean;
  runsTruncated: boolean;
}): DeliveredResult<R> {
  const complete = !facts.runsReadFailed && !facts.runsTruncated;
  const delivered = (facts.runs ?? []).filter(
    (run) => runRole(run) === ROLE_WORKER && run.state === "succeeded",
  );
  if (delivered.length > 0) {
    return { kind: "run", run: newest(delivered), certain: complete };
  }
  if (facts.runs === null) return facts.runsReadFailed ? { kind: "unknown" } : { kind: "loading" };
  return complete ? { kind: "none" } : { kind: "unknown" };
}

/**
 * What the outcome header may say under "What it did".
 *
 * `latestDeliveredOf` already distinguishes four answers, and the header collapsed
 * two of them at the render layer: the successful-run presentation was chosen only
 * when there was an output line or the task's delivery listed files, so a run that
 * succeeded while emitting only whitespace and recording no handoff files fell
 * through to the same sentence as "no successful run is on record" — and, because
 * the run was found, it could be paired with "this may not be the most recent one",
 * which reads as a contradiction: nothing was produced, but possibly not the latest
 * nothing.
 *
 * "A run succeeded and recorded nothing" is a real, distinct result. It is what a
 * verified no-op looks like, and it is also what a misconfigured agent looks like;
 * either way the human needs to know a run *ran*. So it gets its own wording.
 *
 * The scope of that wording is bounded by what this header actually reads, and the
 * strings say so: the file count is the **task's handoff** record, not the run's own
 * deliverable snapshot (the run card reads that, lazily, further down the page). So
 * the claim is "nothing to show here" — no printable output line and no handoff files
 * — never "this run wrote no files anywhere", which nothing on this header knows.
 *
 * The remaining subtlety is that the handoff is not always read: `fetchDelivery` is
 * called only for a task in state `done`, so a zero file count is frequently "not
 * asked" rather than "no files". Claiming "recorded nothing" from an unasked read
 * would be the same class of mistake one step over, so the two travel separately
 * (`filesKnown`) and an unasked or failed delivery read yields
 * `producedFilesUnknown` instead.
 */
export type OutcomeClaim =
  /** No answer about this task's runs yet. */
  | { kind: "loading" }
  /** Something concrete to show: an output line, delivered files, or both. */
  | { kind: "produced" }
  /** A successful run exists; it printed nothing and the task's handoff lists no files. */
  | { kind: "producedNothingRecorded" }
  /** A successful run exists with no output line, and no handoff was read for it. */
  | { kind: "producedFilesUnknown"; because: "notRead" | "readFailed" }
  /** The run history could not support any claim about what was produced. */
  | { kind: "unknownRuns" }
  /** No success was found and the delivery read failed: "nothing" is not provable. */
  | { kind: "deliveryUnreadable" }
  /** A complete read: this task has no successful run on record. */
  | { kind: "noSuccessfulRun" };

/** The facts the header has in hand. Each is a separate read, and stays separate. */
export interface OutcomeFacts<R extends ColumnRunLike> {
  delivered: DeliveredResult<R>;
  /** Whether the run's own output yielded a usable one-line summary. */
  hasOutcomeLine: boolean;
  /** Files listed by the task's handoff record. Only meaningful with `filesKnown`. */
  fileCount: number;
  /** Whether the handoff read happened and answered (it is asked only for `done`). */
  filesKnown: boolean;
  /** Whether that read failed, as opposed to never having been made. */
  deliveryReadFailed: boolean;
}

export function outcomeClaimOf<R extends ColumnRunLike>(facts: OutcomeFacts<R>): OutcomeClaim {
  // Presence first, as everywhere in this module: found files and a real output line
  // are evidence whatever else the reads dropped.
  if (facts.hasOutcomeLine || (facts.filesKnown && facts.fileCount > 0)) {
    return { kind: "produced" };
  }
  switch (facts.delivered.kind) {
    case "loading":
      return { kind: "loading" };
    case "unknown":
      return { kind: "unknownRuns" };
    case "none":
      // No success in a complete read. "Nothing was produced" is still a claim about
      // the delivery too, so a failed delivery read cannot support it (D-6).
      return facts.deliveryReadFailed ? { kind: "deliveryUnreadable" } : { kind: "noSuccessfulRun" };
    default:
      // A success was found, with no output line worth showing.
      if (facts.deliveryReadFailed) return { kind: "producedFilesUnknown", because: "readFailed" };
      if (!facts.filesKnown) return { kind: "producedFilesUnknown", because: "notRead" };
      return { kind: "producedNothingRecorded" };
  }
}

/** The sentence each claim renders. `produced` has none: the header shows the substance. */
export const OUTCOME_CLAIM_KEY: Record<Exclude<OutcomeClaim["kind"], "produced">, string> = {
  loading: "workspace.summary.producedLoading",
  producedNothingRecorded: "workspace.summary.producedNothing",
  // Overridden per reason by `outcomeClaimNotice`; present so every kind has a key.
  producedFilesUnknown: "workspace.summary.producedFilesNotRead",
  unknownRuns: "workspace.summary.producedUnknown",
  deliveryUnreadable: "workspace.panels.unavailable",
  noSuccessfulRun: "workspace.summary.nothingYet",
};

/** Why the files behind a successful, silent run are not known — one sentence each. */
export const OUTCOME_FILES_UNKNOWN_KEY: Record<
  Extract<OutcomeClaim, { kind: "producedFilesUnknown" }>["because"], string
> = {
  notRead: "workspace.summary.producedFilesNotRead",
  readFailed: "workspace.summary.producedFilesUnreadable",
};

/** The message key for one claim, or null when the claim renders substance instead. */
export function outcomeClaimNotice(claim: OutcomeClaim): string | null {
  if (claim.kind === "produced") return null;
  if (claim.kind === "producedFilesUnknown") return OUTCOME_FILES_UNKNOWN_KEY[claim.because];
  return OUTCOME_CLAIM_KEY[claim.kind];
}

/**
 * What accounts for the independent cross-review of a settled worker run.
 *
 * **This is derived from the same fact the control plane uses, and that is the
 * correction.** The previous version reported the linked *review run's state* and
 * treated any settled one — `succeeded` or `cancelled` — as accounting for the
 * review. It avoided mirroring the backend's `review_owed` predicate and then made
 * a different claim that contradicted it anyway:
 * `ExecutionRunStore._has_accounting_review_run` is explicit that **no** settled
 * review state accounts for a review, because a review run that produced no gate
 * item is worse than no review at all — it looks finished. So with auto-review on,
 * a worker run succeeding, its review settling, and both gate writes failing, this
 * module said the review was accounted for, `gateVerdict` fell through to `clear`,
 * and the next run was refused `review_owed` by the same database the column had
 * just called clear.
 *
 * The releasing fact in `_reviews_owed` is: an **unsettled** linked review run
 * (`_has_accounting_review_run`, ACTIVE_RUN_STATES only), or a gate item scoped to
 * the reviewed worker run — `run_id` = that run, `requester` = `agora-review`,
 * `kind` in (approval, blocker) — **in any state**, because the human's disposition
 * of that item is the decision (`_has_review_gate_item`). Both are observable from
 * this page, the second only through a *run-scoped* attention read that does not
 * filter by state: the page's ordinary `state=open` read cannot see the responded
 * approval that released the obligation, and reading absence out of it is what
 * produced the disagreement above.
 *
 * What is still not knowable here is whether `auto_review` is on at all, and this
 * type does not claim to know: `noGateAfterReview`/`noGateNoReview` state the
 * arrangement on record — the same two arrangements the backend's `cause` labels
 * distinguish — and `gateVerdict` renders them as "nothing accounts for this
 * review", never as a prediction that the next run will be refused, and never as
 * `clear`.
 */
export type ReviewStanding =
  /** No review is owed for this run at all, and the backend agrees. */
  | { kind: "notOwed"; because: "notWorker" | "runDidNotSucceed" }
  /** A linked review run has not settled: the backend holds the task on it. */
  | { kind: "active"; adapter: string }
  /** The review's own gate item for this run is open — that item IS the review gate. */
  | { kind: "gateOpen"; count: number }
  /** It was raised and the human has disposed of it. The obligation is released. */
  | { kind: "gateSettled"; count: number }
  /** A review run settled and left no gate item behind (backend: NO_GATE_ITEM / REVIEW_CANCELLED). */
  | { kind: "noGateAfterReview"; adapter: string; state: string }
  /** No review run is linked and no gate item exists (backend: NO_REVIEW_RUN). */
  | { kind: "noGateNoReview" }
  /** A read that cannot support any of the above. */
  | { kind: "unknown"; because: "runsIncomplete" | "gateUnread" };

/** Kinds of attention item that are the review's human gate; mirrors REVIEW_GATE_KINDS. */
export const REVIEW_GATE_KINDS: ReadonlySet<string> = new Set(["approval", "blocker"]);

/**
 * The requester that marks an item as *Agora's own* review gate, mirroring
 * `REVIEW_REQUESTER` in backend/agora/attention/models.py. It is a reserved
 * requester the API refuses in public input, which is what lets this be read as an
 * authority marker rather than as any item that happens to be shaped like one.
 */
export const REVIEW_REQUESTER = "agora-review";

/** The fields the review gate check needs from an attention item; a structural subset. */
export interface ReviewGateLike {
  item_id: string;
  run_id: string | null;
  kind: string;
  state: string;
  requester: string;
}

/** Whether one attention item is the review path's gate for `runId`. */
export function isReviewGateItem(item: ReviewGateLike, runId: string): boolean {
  return item.run_id === runId
    && item.requester === REVIEW_REQUESTER
    && REVIEW_GATE_KINDS.has(item.kind);
}

/**
 * The run-scoped attention read this standing needs: items for ONE run in ANY
 * state.
 *
 * `forRunId` is carried with the items rather than assumed by the caller: this read
 * is keyed to whichever run the column is showing, and a response still in flight
 * for the previous run must not be counted against the new one. When it does not
 * match the run being judged, the facts are treated as not yet read.
 */
export interface ReviewGateFacts<I extends ReviewGateLike> {
  items: readonly I[] | null;
  readFailed: boolean;
  truncated: boolean;
  forRunId: string | null;
}

export function reviewStandingOf<R extends ColumnRunLike, I extends ReviewGateLike>(input: {
  run: R;
  runs: readonly R[] | null;
  runsReadFailed: boolean;
  runsTruncated: boolean;
  gate: ReviewGateFacts<I>;
}): ReviewStanding {
  const runsComplete = input.runs !== null && !input.runsReadFailed && !input.runsTruncated;
  // "Nothing is owed for this run" is an absence, so only a complete read may assert
  // it. The role and the state of the run in hand are certain either way, but the
  // verdict built on them is `clear`, and on a capped page the worker run the
  // backend actually nominates can be one this read never saw.
  if (runRole(input.run) !== ROLE_WORKER || input.run.state !== "succeeded") {
    if (!runsComplete) return { kind: "unknown", because: "runsIncomplete" };
    // A review is never reviewed, and a role this UI does not know is not treated as
    // a worker run. Only a successful worker run is cross-reviewed
    // (dispatcher._after_run), so a failed one owes nothing; its own failure is what
    // the human reads.
    return {
      kind: "notOwed",
      because: runRole(input.run) !== ROLE_WORKER ? "notWorker" : "runDidNotSucceed",
    };
  }

  // Gate items that are *found* are real whatever else the read dropped, so presence
  // is read from a truncated page too; only absence needs the read to be complete.
  const matched = input.gate.items !== null && input.gate.forRunId === input.run.run_id
    ? input.gate.items.filter((item) => isReviewGateItem(item, input.run.run_id))
    : null;
  if (matched !== null && matched.length > 0) {
    const open = matched.filter((item) => item.state === "open");
    if (open.length > 0) return { kind: "gateOpen", count: open.length };
  }

  const reviews = (input.runs ?? []).filter(
    (run) => runRole(run) === ROLE_REVIEW && reviewedRunId(run) === input.run.run_id,
  );
  const unsettled = reviews.filter((run) => runIsUnsettled(run));
  // An unsettled review run is the other releasing fact, and the one the backend
  // enforces from the other side (`_reject_active_review` refuses the next worker
  // run while it stands). Provable from any run read.
  if (unsettled.length > 0) return { kind: "active", adapter: newest(unsettled).adapter };

  if (matched !== null && matched.length > 0) return { kind: "gateSettled", count: matched.length };
  // No gate item was observed. Whether none exists depends on the read: the item may
  // be one the run-scoped read never returned.
  if (matched === null || input.gate.readFailed || input.gate.truncated) {
    return { kind: "unknown", because: "gateUnread" };
  }
  // The gate read is complete and empty. What is on record about a review run at all
  // still comes from the capped run page, so an incomplete one cannot pick between
  // the two arrangements below — and cannot rule out an unsettled review run either.
  if (!runsComplete) return { kind: "unknown", because: "runsIncomplete" };
  if (reviews.length > 0) {
    const current = newest(reviews);
    return { kind: "noGateAfterReview", adapter: current.adapter, state: current.state };
  }
  return { kind: "noGateNoReview" };
}

/**
 * The one-line headline over the settled column's gate: what stands between this
 * run and done.
 *
 * Ordering is by what can be proven, not by severity. An observed open gate item
 * comes first because presence is provable from any read; an unsettled review run
 * comes next for the same reason. Only then do the unknowns speak, and `clear` is
 * last.
 *
 * **What `clear` may now be reached from** is the part R1 changed. It is only
 * `notOwed` — nothing was ever owed for this run — and `gateSettled` — the review's
 * gate item exists and the human disposed of it, which is exactly
 * `_has_review_gate_item`, the fact that releases the backend's obligation. A
 * settled review *run* no longer reaches it from any direction, because the control
 * plane does not accept one as accounting for a review.
 */
export type GateVerdict =
  | { kind: "actionRequired"; count: number }
  | { kind: "reviewRunning"; adapter: string }
  | { kind: "unknown"; reason: "attentionLoading" | "attentionUnreadable" | "attentionPartial" | "reviewUnknown" }
  | { kind: "reviewNotAccounted" }
  | { kind: "clear" };

export function gateVerdict<T extends AttentionLike>(input: {
  attention: TaskAttentionView<T>;
  review: ReviewStanding;
}): GateVerdict {
  const { attention, review } = input;
  // Items that are actually there, from either a complete or a truncated page.
  if (attention.kind === "open") {
    return { kind: "actionRequired", count: attention.items.length };
  }
  if (attention.kind === "partial" && attention.items.length > 0) {
    return { kind: "actionRequired", count: attention.items.length };
  }
  // The review's own gate item, observed as open by the run-scoped read. Normally
  // the branches above already counted it — it is one of this task's open items —
  // but the two reads are different requests and may disagree for a moment, and the
  // one that says an item IS open is the one to believe.
  if (review.kind === "gateOpen") return { kind: "actionRequired", count: review.count };
  if (review.kind === "active") return { kind: "reviewRunning", adapter: review.adapter };
  // No item was observed. Whether that means none exists depends on the read.
  if (attention.kind === "loading") return { kind: "unknown", reason: "attentionLoading" };
  if (attention.kind === "unreadable") return { kind: "unknown", reason: "attentionUnreadable" };
  if (attention.kind === "partial") return { kind: "unknown", reason: "attentionPartial" };
  if (review.kind === "unknown") return { kind: "unknown", reason: "reviewUnknown" };
  if (review.kind === "noGateAfterReview" || review.kind === "noGateNoReview") {
    return { kind: "reviewNotAccounted" };
  }
  return { kind: "clear" };
}

/** Headline message keys. Every kind is present, so no verdict can go unworded. */
export const GATE_VERDICT_KEY: Record<GateVerdict["kind"], string> = {
  actionRequired: "workspace.column.gate.actionRequired",
  reviewRunning: "workspace.column.gate.reviewRunning",
  unknown: "workspace.column.gate.unknown",
  reviewNotAccounted: "workspace.column.gate.reviewNotAccounted",
  clear: "workspace.column.gate.clear",
};

/** The reason line under an `unknown` headline; each names its own read. */
export const GATE_UNKNOWN_REASON_KEY: Record<
  Extract<GateVerdict, { kind: "unknown" }>["reason"], string
> = {
  attentionLoading: "workspace.panels.attention.loading",
  attentionUnreadable: "workspace.panels.unavailable",
  attentionPartial: "workspace.panels.attention.partialEmpty",
  reviewUnknown: "workspace.column.review.unknown",
};

/** The distinct sentences the review block can say. */
export type ReviewNotice =
  | "notWorker"
  | "runDidNotSucceed"
  | "active"
  | "gateOpen"
  | "gateSettled"
  | "noGateAfterReview"
  | "noGateNoReview"
  | "unknownRuns"
  | "unknownGate";

/** What the review block says. Each standing gets its own sentence, no sharing. */
export const REVIEW_STANDING_KEY: Record<ReviewNotice, string> = {
  notWorker: "workspace.column.review.notWorker",
  runDidNotSucceed: "workspace.column.review.runDidNotSucceed",
  active: "workspace.column.review.active",
  gateOpen: "workspace.column.review.gateOpen",
  gateSettled: "workspace.column.review.gateSettled",
  noGateAfterReview: "workspace.column.review.noGateAfterReview",
  noGateNoReview: "workspace.column.review.noGateNoReview",
  unknownRuns: "workspace.column.review.unknown",
  unknownGate: "workspace.column.review.gateUnread",
};

/**
 * The message key for one review standing, and the variables it interpolates.
 *
 * `describeRunState` localizes the review run's own state and is passed in — the
 * same shape `provisionResultGate` uses — so this module stays free of React and
 * of the i18n module while the sentence still reads in the user's language.
 */
export function reviewStandingNotice(
  standing: ReviewStanding,
  describeRunState: (state: string) => string,
): { key: string; vars: Record<string, string | number> } {
  switch (standing.kind) {
    case "notOwed":
      return { key: REVIEW_STANDING_KEY[standing.because], vars: {} };
    case "active":
      return { key: REVIEW_STANDING_KEY.active, vars: { adapter: standing.adapter } };
    case "gateOpen":
      return { key: REVIEW_STANDING_KEY.gateOpen, vars: { count: standing.count } };
    case "gateSettled":
      return { key: REVIEW_STANDING_KEY.gateSettled, vars: { count: standing.count } };
    case "noGateAfterReview":
      return {
        key: REVIEW_STANDING_KEY.noGateAfterReview,
        vars: { adapter: standing.adapter, state: describeRunState(standing.state) },
      };
    case "noGateNoReview":
      return { key: REVIEW_STANDING_KEY.noGateNoReview, vars: {} };
    default:
      return {
        key: standing.because === "gateUnread"
          ? REVIEW_STANDING_KEY.unknownGate
          : REVIEW_STANDING_KEY.unknownRuns,
        vars: {},
      };
  }
}

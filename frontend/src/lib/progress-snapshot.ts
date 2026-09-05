// The project progress bar, decided by the RESUME progress snapshot — and by
// nothing else.
//
// The recorded UI decision this implements: progress is snapshot-driven and does
// report a percentage. The data source is the ✅ / 🔵 / ⬜ counts of the project's
// resume progress snapshot; the bar is two segments (solid done, translucent in
// progress); the percentage counts **done only**, because half credit for work in
// flight is a number nobody can act on; a project with no snapshot renders empty
// rather than a manufactured zero.
//
// The load-bearing part is the last clause of that decision: *the display layer
// stops using the control plane's `verified_pct`*. That number has a known defect —
// `tasks/progress.py` marks a criterion produced/verified from any historical
// successful worker run of the same task, so after a requirements change the new
// criteria read as already produced by a run that predates them. It cannot be fixed
// by rewording a label, so this module exists to replace the source, not the copy.
//
// Which means this module's whole job is to be honest about three answers that a
// careless implementation collapses into one:
//
//   no snapshot        -> render nothing. Not 0%.
//   counts unreadable  -> say so. Not 0%, and not "no snapshot" either.
//   other marks        -> say the file uses marks the reader does not count. Not
//                         "tracks no items", which would be a false claim about a
//                         file that plainly records work.
//   mixed conventions  -> say that. Counting either one alone reports a floor.
//   counts read        -> a percentage, from `done` only.
//
// The types make the collapse unrepresentable: only the two count variants carry a
// percentage at all, so no other outcome can render a number by accident.
//
// The two count variants exist because the two checklist conventions do not carry
// the same information. Markers (✅/🔵/⬜) split the work three ways. Checkboxes
// split it two ways — and that is a coarsening, not a gap: an item a human would
// call in progress is written as an unchecked box, so the total is still exact and
// so is `done / total`. What is missing is only the split of not-done, so
// `countsDoneOnly` has no in-progress field at all rather than a zero standing in
// for an unknown.
//
// Deliberately free of React and of "@/" path aliases: `node --test` executes the
// companion test file (tests/progress-snapshot.test.mts) directly.

/**
 * The snapshot as the dashboard reports it (backend:
 * `projects/progress_snapshot.py`). Counts are `null` whenever `available` is
 * false — the payload never carries a zero it does not mean.
 *
 * The one exception, and the reason `convention` exists: a snapshot written in
 * Markdown checkboxes is available with `in_progress: null`, because that
 * convention has no form for work in flight and a 0 there would assert there is
 * none. `convention` is what tells this module to expect the null instead of
 * reading it as a contradiction.
 */
export interface ProgressSnapshotLike {
  available: boolean;
  /**
   * "absent" | "too_large" | "unreadable" | "uncounted_markers" |
   * "mixed_conventions" when unavailable.
   */
  reason?: string | null;
  done?: number | null;
  /** `null` under the `checkboxes` convention: unknown, never zero. */
  in_progress?: number | null;
  /** Under `checkboxes` this is *not done*, which may include work in flight. */
  todo?: number | null;
  /** The path that was consulted, so the UI can name what it read. */
  source?: string | null;
  /**
   * Which convention the counts were read in: `markers` or `checkboxes`. Absent
   * from a server older than this field, which is read as `markers` — the only
   * shape that server could send.
   */
  convention?: string | null;
}

/** The reason the file yields no snapshot at all: `absent`, and only `absent`. */
export const SNAPSHOT_ABSENT_REASON = "absent";

/**
 * The reason the file was read fine but speaks a checklist convention the backend's
 * reader does not count (backend: `UNCOUNTED_MARKERS_REASON`). Its own sentence,
 * because "this file uses markers the reader does not count" is a fact about the
 * reader and the file — not "no progress", and not "the file could not be read".
 */
export const SNAPSHOT_UNCOUNTED_MARKERS_REASON = "uncounted_markers";

/**
 * The reason the file carries both checklist conventions at once (backend:
 * `MIXED_CONVENTIONS_REASON`), so neither reading is the file's own and counting
 * either one alone would report a floor.
 */
export const SNAPSHOT_MIXED_CONVENTIONS_REASON = "mixed_conventions";

/** The three-bucket convention: ✅ / 🔵 / ⬜. */
export const CONVENTION_MARKERS = "markers";

/** The two-bucket convention: `[x]` / `[ ]`. Done and not-done, with no split. */
export const CONVENTION_CHECKBOXES = "checkboxes";

/**
 * Read fine, and it lists no item of either convention. Its own value rather than an
 * absent field, so that an absent field keeps meaning one thing only: a server that
 * predates the field, whose counts this module will not vouch for.
 */
export const CONVENTION_NONE = "none";

/**
 * What the bar shows.
 *
 * `empty` and `unknown` are separate because they are separate facts, and the whole
 * point of the decision is not to print a number for either: "this project keeps no
 * snapshot" is a settled answer, "the counts could not be read" is the absence of
 * one. `noItems` is kept apart from `noSnapshot` for the same reason one layer down —
 * a snapshot that exists and tracks nothing yet is not a missing snapshot.
 *
 * `notReported` is the case a UI usually gets wrong: a server older than this
 * frontend sends no `progress_snapshot` field, and reading a missing field as "this
 * project has no snapshot" would state a fact about the project from a fact about
 * the server.
 */
export type ProgressBar =
  | { kind: "loading" }
  | {
    kind: "unknown";
    because:
      | "dashboardUnread"
      | "snapshotUnread"
      | "notReported"
      | "countsUnusable"
      /** Read fine; its checklist markers are not the ones the reader counts. */
      | "markersNotCounted"
      /** Read fine; it carries both conventions, so neither count is the file's. */
      | "mixedConventions"
      /**
       * The server did not say which convention it counted, so its counts cannot be
       * shown to be the whole file. Older servers preferred markers in a mixed file
       * and reported that count alone.
       */
      | "conventionNotReported";
  }
  | { kind: "empty"; because: "noSnapshot" | "noItems" }
  | {
    kind: "counts";
    done: number;
    inProgress: number;
    todo: number;
    total: number;
    /** The reported percentage: done / total. In-progress items are not half credit. */
    donePct: number;
    /** Width of the translucent segment, clamped so the two segments never exceed 100. */
    inProgressPct: number;
  }
  /**
   * A checkbox snapshot: an exact total and an exact done percentage, with the
   * not-done half unsplit. A separate variant rather than a `counts` with nulls in
   * it, so a renderer cannot reach for an in-progress segment that has no number
   * behind it — there is no `inProgress` or `inProgressPct` field to read, not even
   * a zero. `notDone` is deliberately not called `todo`: it may hold work in
   * flight, and the wording upstream has to say "not done", never "not started".
   */
  | {
    kind: "countsDoneOnly";
    done: number;
    notDone: number;
    total: number;
    donePct: number;
  };

/** The reads the bar is derived from. */
export interface ProgressBarFacts {
  /**
   * The `progress_snapshot` field of the dashboard response. `undefined` means the
   * response carried no such field (an older server); `null` means the field was
   * explicitly null. Both are unknown, never "no snapshot".
   */
  snapshot: ProgressSnapshotLike | null | undefined;
  /** Whether a dashboard response is in hand at all. */
  dashboardLoaded: boolean;
  /** Whether the dashboard read failed, as opposed to not having answered yet. */
  dashboardReadFailed: boolean;
}

/** Half-up, matching the backend's `_pct`, so one project never shows two roundings. */
function pct(numerator: number, denominator: number): number {
  return Math.floor((numerator * 100) / denominator + 0.5);
}

function usableCount(value: unknown): number | null {
  return typeof value === "number" && Number.isInteger(value) && value >= 0 ? value : null;
}

/**
 * The reported percentage, done only.
 *
 * Rounding never erases a finished item: 1 of 300 is 1%, not 0%, because a 0% next
 * to a non-empty done count reads as "nothing is done" — the same false absence this
 * module exists to prevent, produced by arithmetic instead of by a missing field. It
 * also never rounds up to 100 while anything is unfinished.
 */
function donePctOf(done: number, total: number): number {
  if (done === 0) return 0;
  return Math.min(Math.max(pct(done, total), 1), done === total ? 100 : 99);
}

export function progressBarOf(facts: ProgressBarFacts): ProgressBar {
  // **A failed read outranks having a dashboard in hand.** This used to ask about the
  // failure only inside `!dashboardLoaded`, so the one state it was written for --
  // a page that keeps the dashboard it had and marks the refresh failed -- walked
  // straight past it and the old snapshot was classified as if it were current. The
  // bar then showed a confident percentage between a task fraction and a next action
  // that had both just been withheld for being stale.
  //
  // The test that claimed to cover this passed `dashboardLoaded: false`, so it
  // exercised the branch that already worked and gave false confidence about the one
  // that did not.
  if (facts.dashboardReadFailed) return { kind: "unknown", because: "dashboardUnread" };
  if (!facts.dashboardLoaded) return { kind: "loading" };
  const snapshot = facts.snapshot;
  // A field that is not there says nothing about the project.
  if (snapshot === null || snapshot === undefined) {
    return { kind: "unknown", because: "notReported" };
  }
  if (snapshot.available !== true) {
    // Only the one reason means "there is no snapshot". Every other reason — an
    // unreadable file, one past the read bound, or a reason this UI does not know
    // because the backend is newer — is a read that did not answer.
    if (snapshot.reason === SNAPSHOT_ABSENT_REASON) return { kind: "empty", because: "noSnapshot" };
    // Read, and not empty: it tracks items in markers the reader does not count. Its
    // own sentence, because "no number for this file" and "nothing tracked" are
    // different statements and only one of them is true here.
    if (snapshot.reason === SNAPSHOT_UNCOUNTED_MARKERS_REASON) {
      return { kind: "unknown", because: "markersNotCounted" };
    }
    // Both conventions in one file: also read fine, also not a number, and its own
    // sentence — "it mixes two conventions" is actionable in a way "unreadable" is not.
    if (snapshot.reason === SNAPSHOT_MIXED_CONVENTIONS_REASON) {
      return { kind: "unknown", because: "mixedConventions" };
    }
    return { kind: "unknown", because: "snapshotUnread" };
  }
  // No `convention` at all is a server that predates the field, and its counts cannot
  // be vouched for: that server preferred markers in a file that also had checkboxes
  // and returned the marker count alone, so `- ✅ done` + `- [ ] pending` reached this
  // module as done 1 of 1 and rendered 100% for a half-finished checklist. Reading the
  // missing field as "markers" is what let that through — a missing field proving a
  // fact about the file, which is the one inference this module exists to refuse. It
  // costs the bar on every project served by an older backend, and that is the right
  // side to be wrong on: a sentence saying the figure is unavailable is recoverable by
  // upgrading, a confident 100% is not recoverable at all.
  if (snapshot.convention === null || snapshot.convention === undefined) {
    return { kind: "unknown", because: "conventionNotReported" };
  }
  const convention = snapshot.convention;
  if (convention === CONVENTION_NONE) {
    // Read fine, lists nothing. The counts are three zeros and there is no percentage
    // of zero items — the same answer as an empty marker file, reached explicitly
    // rather than by falling through a convention branch.
    const done = usableCount(snapshot.done);
    const inProgress = usableCount(snapshot.in_progress);
    const todo = usableCount(snapshot.todo);
    if (done === null || inProgress === null || todo === null) {
      return { kind: "unknown", because: "countsUnusable" };
    }
    if (done + inProgress + todo > 0) {
      // "No convention" with items counted in one is a contradiction in the payload.
      return { kind: "unknown", because: "countsUnusable" };
    }
    return { kind: "empty", because: "noItems" };
  }
  if (convention === CONVENTION_CHECKBOXES) {
    const done = usableCount(snapshot.done);
    const notDone = usableCount(snapshot.todo);
    // Under this convention `in_progress` must be absent. A number there is a
    // contradiction in the payload, not a bucket to render.
    const inProgressReported = snapshot.in_progress !== null && snapshot.in_progress !== undefined;
    if (done === null || notDone === null || inProgressReported) {
      return { kind: "unknown", because: "countsUnusable" };
    }
    const total = done + notDone;
    if (total === 0) return { kind: "empty", because: "noItems" };
    return { kind: "countsDoneOnly", done, notDone, total, donePct: donePctOf(done, total) };
  }
  if (convention !== CONVENTION_MARKERS) {
    return { kind: "unknown", because: "countsUnusable" };
  }
  const done = usableCount(snapshot.done);
  const inProgress = usableCount(snapshot.in_progress);
  const todo = usableCount(snapshot.todo);
  if (done === null || inProgress === null || todo === null) {
    // `available: true` with counts this module cannot use is a contradiction in the
    // payload. It is unknown — emphatically not zero, which is what `?? 0` would
    // have made of it.
    return { kind: "unknown", because: "countsUnusable" };
  }
  const total = done + inProgress + todo;
  // A snapshot that tracks nothing yet. There is no percentage of zero items, and
  // 0% would claim there is work and none of it is done.
  if (total === 0) return { kind: "empty", because: "noItems" };
  const donePct = donePctOf(done, total);
  const inProgressPct = inProgress === 0
    ? 0
    : Math.min(Math.max(pct(inProgress, total), 1), 100 - donePct);
  return { kind: "counts", done, inProgress, todo, total, donePct, inProgressPct };
}

/** Message keys for the wordless states. Every kind that has no number has a sentence. */
export const PROGRESS_BAR_KEY: Record<
  Extract<ProgressBar, { kind: "empty" }>["because"]
  | Extract<ProgressBar, { kind: "unknown" }>["because"], string
> = {
  noSnapshot: "common.progress.noSnapshot",
  noItems: "common.progress.noItems",
  dashboardUnread: "common.progress.unreadable",
  snapshotUnread: "common.progress.unreadable",
  notReported: "common.progress.notReported",
  countsUnusable: "common.progress.unreadable",
  markersNotCounted: "common.progress.markersNotCounted",
  mixedConventions: "common.progress.mixedConventions",
  conventionNotReported: "common.progress.conventionNotReported",
};

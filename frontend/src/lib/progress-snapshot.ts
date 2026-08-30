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
//   other markers      -> say the file uses markers the reader does not count. Not
//                         "tracks no items": Agora's own snapshot is a thousand
//                         checkbox lines, and calling that empty is a false claim.
//   counts read        -> a percentage, from `done` only.
//
// The types make the collapse unrepresentable: only the `counts` variant carries a
// percentage at all, so no other outcome can render a number by accident.
//
// Deliberately free of React and of "@/" path aliases: `node --test` executes the
// companion test file (tests/progress-snapshot.test.mts) directly.

/**
 * The snapshot as the dashboard reports it (backend:
 * `projects/progress_snapshot.py`). Counts are `null` whenever `available` is
 * false — the payload never carries a zero it does not mean.
 */
export interface ProgressSnapshotLike {
  available: boolean;
  /** "absent" | "too_large" | "unreadable" when unavailable. */
  reason?: string | null;
  done?: number | null;
  in_progress?: number | null;
  todo?: number | null;
  /** The path that was consulted, so the UI can name what it read. */
  source?: string | null;
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
      | "markersNotCounted";
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

export function progressBarOf(facts: ProgressBarFacts): ProgressBar {
  if (!facts.dashboardLoaded) {
    return facts.dashboardReadFailed
      ? { kind: "unknown", because: "dashboardUnread" }
      : { kind: "loading" };
  }
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
    return { kind: "unknown", because: "snapshotUnread" };
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
  // Rounding never erases a finished item: 1 of 300 is 1%, not 0%, because a 0% next
  // to a non-empty done count reads as "nothing is done" — the same false absence
  // this module exists to prevent, produced by arithmetic instead of by a missing
  // field. It also never rounds up to 100 while anything is unfinished.
  const donePct = done === 0 ? 0 : Math.min(Math.max(pct(done, total), 1), done === total ? 100 : 99);
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
};

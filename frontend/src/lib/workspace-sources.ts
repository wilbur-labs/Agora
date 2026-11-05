// What a registered workspace directory yields, and what the panel may say about it.
//
// W7. Agora reads the tasks a claude-workspace directory already holds rather than
// asking for the work to be typed in twice. The reads are honest by construction
// (backend/agora/projects/workspace_tasks.py): a directory that could not be opened,
// one that holds no `tasks/`, and one that holds an empty `tasks/` are three different
// answers, and this module keeps them three.
//
// The rule this file exists to enforce is the one the product keeps relearning: **a
// reason is not a count.** `tasks: null` is "I could not look"; `tasks: []` is "I
// looked and there is nothing". Rendering either as "0 tasks" would be the same
// substitution the timeline and the portfolio were both corrected for.
//
// Deliberately free of React and of "@/" path aliases: `node --test` executes the
// companion test file (tests/workspace-sources.test.mts) directly. The relative
// `./progress-snapshot.ts` import (extension and all) follows the same rule — it is how
// timeline.ts already reaches run-column.ts under the same runner.

import {
  SNAPSHOT_ABSENT_REASON,
  progressBarOf,
  type ProgressBar,
  type ProgressSnapshotLike,
} from "./progress-snapshot.ts";

/** Why a source has nothing to show. Mirrors `WorkspaceSource.reason`. */
export type SourceReason = "absent" | "unreadable" | "not_a_directory" | "no_tasks_dir";

/** Why a task's `task.yaml` could not be read. Mirrors `TaskManifestRead.reason`. */
export type ManifestReason =
  | "absent" | "unreadable" | "too_large" | "malformed" | "duplicate_key"
  | "unknown_schema";

export interface ManifestPayload {
  available: boolean;
  primary_repo: string | null;
  reason: ManifestReason | null;
  title: string | null;
  goal: string | null;
  status: string | null;
  machine: string | null;
  milestones: { id: string; status: string }[] | null;
}

export interface ResumePayload {
  available: boolean;
  reason: string | null;
  current_state: string | null;
  next_step: string | null;
  progress: ProgressVerdictPayload | null;
}

/**
 * The 进度快照 section's verdict, mirroring `progress_snapshot.ProgressVerdict` as the
 * RESUME reader flattens it onto the payload.
 *
 * The counts are nullable for one honest reason each: on the `checkboxes` convention
 * `in_progress` is `null` because that convention has no form for work in flight (a 0
 * there would assert nothing is in flight, which the file never said); and all three are
 * `null` when `reason` is set, because the section was read but cannot be counted.
 *
 * Exactly one of `convention` / `reason` is ever set, the same discipline the backend
 * keeps: `convention` names how it was counted (`markers` | `checkboxes` | `none`),
 * `reason` names why it could not be (`mixed_conventions` | `uncounted_markers`). A
 * consumer reads which is present to tell "counted, here it is" from "read fine, cannot
 * be counted" — never collapsing the second into "empty" or into "RESUME unreadable".
 */
export interface ProgressVerdictPayload {
  done: number | null;
  in_progress: number | null;
  todo: number | null;
  convention: string | null;
  reason: string | null;
}

export interface CrLogPayload {
  available: boolean;
  reason: string | null;
  rows: unknown[] | null;
  open_count: number | null;
  unrecognised_status_count: number | null;
  source: string;
}

export interface DecisionsPayload {
  available: boolean;
  reason: string | null;
  records: unknown[] | null;
  source: string;
}

/** Whether this task's repository is one Agora already knows. Mirrors `RepoLink`. */
export type RepoLinkState =
  | "linked"
  | "not_registered"
  /** The file names no repository — a line to add. */
  | "not_recorded"
  /** It names one and the value is not a path — a line to fix. Different work. */
  | "repo_malformed"
  /** The task read fine; the project list did not. Not an answer about this task. */
  | "projects_unreadable"
  | "unknown";

export interface RepoLinkPayload {
  state: RepoLinkState;
  repo: string | null;
  project_id: string | null;
}

export interface WorkspaceTaskPayload {
  name: string;
  repo_link: RepoLinkPayload;
  manifest: ManifestPayload;
  resume: ResumePayload;
  cr_log: CrLogPayload;
  decisions: DecisionsPayload;
  has_workflow: boolean;
  /** The commit review gate's record in `primary_repo` (CR-75 slice 2). Absent from a
   *  server older than this UI: nothing is said then. */
  review_gate?: ReviewGatePayload;
}

export type ReviewGatePayload =
  | { state: "not_installed"; reason: null }
  | { state: "unreadable"; reason: string | null }
  | { state: "read"; reviewed: number; skipped: number; merges: number; unrecorded: number; truncated: boolean };

export interface WorkspaceSourcePayload {
  available: boolean;
  reason: SourceReason | null;
  root: string;
  tasks: WorkspaceTaskPayload[] | null;
  skipped: string[] | null;
  /**
   * Entries under `tasks/` the server listed but could not stat.
   *
   * The backend names them for one reason: an entry dropped from the listing is a task
   * deleted from the only place it is visible. Not declaring the field here undid
   * exactly that — the panel read the shortened `tasks` array and said the folder was
   * empty. Carried into the verdict below so the claim cannot be made without it.
   */
  unreadable_entries: string[] | null;
}

/**
 * What the panel says about a source, as one value.
 *
 * A discriminated union rather than a flag plus a message, for the reason the timeline
 * was rebuilt around one: the count exists only on the variant that has tasks, so
 * "0 tasks" cannot be rendered for a directory nobody could read.
 */
export type SourceVerdict =
  /** Read whole, and holding tasks. */
  | { show: "tasks"; count: number; skipped: readonly string[] }
  /** Read whole, and genuinely holding none — a real answer, not a failure. */
  | { show: "empty"; skipped: readonly string[] }
  /**
   * Read, but an entry under `tasks/` could not be. `count` is what was read and a
   * floor, never a total.
   *
   * Its own variant rather than a field on the two above, so that "this folder is
   * empty" and a bare task count are **unreachable** while something went unread.
   * A flag would have left both sentences one forgotten `if` away.
   */
  | { show: "partial"; count: number; skipped: readonly string[]; unreadable: readonly string[] }
  /** Not read. `reason` says which of the four, and the panel names it. */
  | { show: "unavailable"; reason: SourceReason };

export function sourceVerdictOf(source: WorkspaceSourcePayload): SourceVerdict {
  if (!source.available || source.tasks === null) {
    // `reason` should always be set when unavailable; if the server ever sends neither,
    // "unreadable" is the answer that claims least.
    return { show: "unavailable", reason: source.reason ?? "unreadable" };
  }
  const skipped = source.skipped ?? [];
  const unreadable = source.unreadable_entries ?? [];
  if (unreadable.length > 0) {
    return { show: "partial", count: source.tasks.length, skipped, unreadable };
  }
  return source.tasks.length > 0
    ? { show: "tasks", count: source.tasks.length, skipped }
    : { show: "empty", skipped };
}

/**
 * One section of a task's RESUME, as one value.
 *
 * Three answers, and the panel has to keep them apart: the text, "this task records no
 * such section", and "this file could not be read". `ResumePayload` gives `null` for the
 * last two alike, so a renderer reading the field alone shows a missing section and an
 * unreadable file identically — as nothing at all, which reads as "nothing to say".
 */
export type SectionRead =
  | { show: "text"; text: string }
  /** The file was read; it has no such heading. A fact about the task. */
  | { show: "missing" }
  /** The file was not read. `reason` is why, and it belongs on screen. */
  | { show: "unreadable"; reason: string };

/**
 * The two sections the panel shows: where the task stopped, and what is next.
 *
 * These are the two questions the person asked out loud ("how do I see where a project
 * got to?"), and the API had been answering them since the first slice while nothing
 * rendered either.
 */
export function resumeSectionsOf(resume: ResumePayload): {
  currentState: SectionRead;
  nextStep: SectionRead;
} {
  if (!resume.available) {
    const reason = resume.reason ?? "unreadable";
    return { currentState: { show: "unreadable", reason }, nextStep: { show: "unreadable", reason } };
  }
  const read = (text: string | null): SectionRead =>
    text && text.trim() ? { show: "text", text } : { show: "missing" };
  return { currentState: read(resume.current_state), nextStep: read(resume.next_step) };
}

/**
 * The task's progress bar, read from its RESUME 「进度快照」 verdict.
 *
 * This module does **not** decide the bar. `ProgressVerdictPayload` is the same shape
 * the dashboard's `progress_snapshot` produces — both come from the backend's one
 * `count_progress_verdict` — and `lib/progress-snapshot.ts` already owns the honest
 * decision over that shape: which convention was counted, an unknown convention, a
 * numeric `in_progress` under checkboxes, nulls or non-integers among the counts, a
 * done-only percentage, and every wordless "there is no number here". Deciding it a
 * second time here is exactly what produced round-2 finding 3 and round-3 finding B —
 * two modules answering one question and drifting apart. So this maps the verdict to a
 * `ProgressSnapshotLike` (the same map the backend's `ProgressVerdict.as_snapshot`
 * makes) and hands it to `progressBarOf`.
 *
 * The one thing that has no snapshot counterpart is kept here, and only that one: the
 * RESUME **file** could not be read. `progressBarOf` reasons about a snapshot's text; it
 * has no notion of the file the section lives in being unreadable, and folding that into
 * any snapshot reason would state a fact about the snapshot from a fact about the file —
 * the very collapse this slice keeps correcting. `progress: null` with a readable RESUME
 * is *not* this case: it means the file was read and records no 进度快照 section, which
 * maps to the snapshot's own "no snapshot" (`absent`) answer.
 */
export type TaskProgress =
  /** The RESUME file was not read. `reason` says why, and it is not "no snapshot". The
   *  one answer with no `ProgressSnapshotLike` counterpart, so it stays its own value. */
  | { show: "resumeUnreadable"; reason: string }
  /** Everything the snapshot decision already covers: counts, empty, no snapshot,
   *  uncountable, unknown convention — decided once, in `progressBarOf`. */
  | { show: "bar"; bar: ProgressBar };

/**
 * Map the RESUME verdict onto the snapshot shape `progressBarOf` decides over, mirroring
 * the backend's `ProgressVerdict.as_snapshot`: an uncountable verdict is unavailable with
 * its reason; a counted one carries the counts and the convention it was counted in. A
 * missing 进度快照 section (`progress: null` on a readable RESUME) is the snapshot's
 * `absent` — the file records no snapshot — which `progressBarOf` renders as "no
 * snapshot", blank and never 0%.
 */
function verdictAsSnapshot(verdict: ProgressVerdictPayload | null): ProgressSnapshotLike {
  if (verdict === null) {
    return { available: false, reason: SNAPSHOT_ABSENT_REASON };
  }
  if (verdict.reason !== null) {
    return { available: false, reason: verdict.reason };
  }
  return {
    available: true,
    reason: null,
    done: verdict.done,
    in_progress: verdict.in_progress,
    todo: verdict.todo,
    convention: verdict.convention,
  };
}

export function taskProgressOf(resume: ResumePayload): TaskProgress {
  // A read that failed is never "no snapshot", and it is the one answer with no snapshot
  // counterpart: `progressBarOf` decides over a snapshot's text and has no "the file was
  // unreadable" verdict to reach for. Kept here, and only here — everything downstream of
  // a readable RESUME is the snapshot decision, made once in `progressBarOf`.
  if (!resume.available) {
    return { show: "resumeUnreadable", reason: resume.reason ?? "unreadable" };
  }
  // Readable RESUME. `progress: null` (no 进度快照 section) and every counted or
  // uncountable verdict all map onto the snapshot shape and are decided there — a
  // dashboard snapshot is always in hand and always read, so those two facts are fixed.
  return {
    show: "bar",
    bar: progressBarOf({
      snapshot: verdictAsSnapshot(resume.progress),
      dashboardLoaded: true,
      dashboardReadFailed: false,
    }),
  };
}

/**
 * What the panel offers as the way onward from a task, as one value.
 *
 * Four states because each has a different next move, and the two that look alike are
 * the ones that must not merge: "the file names no repository" is something to write
 * into `task.yaml`, while "the repository it names is not registered here" is a
 * directory to add as a project. `unknown` claims nothing at all — it is what a caller
 * that could not read the manifest, or did not look at the projects, is entitled to say.
 */
export type TaskOnward =
  | { show: "open"; projectId: string; href: string }
  | { show: "notRegistered"; repo: string }
  | { show: "noRepoRecorded" }
  /** `primary_repo` is there and unusable. Says so, because "not recorded" would send
   *  someone to add a line that already exists. */
  | { show: "repoMalformed" }
  /** Nothing about this task failed — the project list did, and a person whose links
   *  just disappeared is owed that sentence rather than a blank space. */
  | { show: "projectsUnreadable" }
  | { show: "unknown" };

export function taskOnwardOf(link: RepoLinkPayload): TaskOnward {
  if (link.state === "linked" && link.project_id) {
    // The workspace board already deep-links by project; this reuses that route rather
    // than inventing a second way in.
    return {
      show: "open",
      projectId: link.project_id,
      href: `/workspace?project=${encodeURIComponent(link.project_id)}`,
    };
  }
  if (link.state === "not_registered" && link.repo) return { show: "notRegistered", repo: link.repo };
  if (link.state === "not_recorded") return { show: "noRepoRecorded" };
  if (link.state === "repo_malformed") return { show: "repoMalformed" };
  if (link.state === "projects_unreadable") return { show: "projectsUnreadable" };
  // Includes `linked` with no project_id and `not_registered` with no path: a server
  // that sends half an answer gets the variant that claims nothing, not a crash and not
  // a guess.
  return { show: "unknown" };
}

/**
 * Only the fields of a mouse click that decide whether we may replace the browser's own
 * navigation with in-page state. A structural subset of React's `MouseEvent`, so the
 * component can pass the event straight in, but small enough to construct in a node test
 * without a DOM.
 */
export type ClickIntent = {
  readonly button: number;
  readonly metaKey: boolean;
  readonly ctrlKey: boolean;
  readonly shiftKey: boolean;
  readonly altKey: boolean;
};

/**
 * Should a click on an in-page `<Link>` be intercepted (default prevented, state changed
 * in place) instead of left to the browser?
 *
 * Only a plain primary-button click is ours. A modified click — Ctrl/Cmd (new tab),
 * Shift (new window), Alt (download), or any non-primary button (middle-click new tab) —
 * is the browser expressing an intent our in-page handler cannot honour, so we must let
 * the default happen against the real `href`. Calling `preventDefault` on those was the
 * bug: it cancelled exactly the new-tab/new-window cases the `href` was kept for.
 */
export function shouldInterceptClick(event: ClickIntent): boolean {
  if (event.button !== 0) return false;
  if (event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return false;
  return true;
}

/**
 * What the workbench's workspace-task board shows as a whole, before any one source is
 * rendered.
 *
 * Three answers the Settings card already kept apart and the workbench must not merge,
 * the same `声明范围 > 检查范围` rule one level up from a single source: the list read
 * failing is not the same as the list being empty, and neither is "still loading".
 *
 *   loading      -> no read has settled yet (`sources` null, read not marked failed).
 *   unreadable   -> the `GET /api/workspace-sources` read failed. NOT "none registered":
 *                   a person who has sources registered must not be told they have none
 *                   because one request lost.
 *   none         -> the read succeeded and returned an empty list. A settled answer.
 *   sources      -> the read succeeded and returned sources; render each with
 *                   `sourceVerdictOf`. `count` is how many, not how many have tasks —
 *                   a registered source that is unreadable or empty is still one the
 *                   person registered and still belongs on the board.
 */
export type WorkspaceBoard =
  | { show: "loading" }
  | { show: "unreadable" }
  | { show: "none" }
  | { show: "sources"; count: number };

export function workspaceBoardOf(
  { sources, readFailed }: { sources: readonly WorkspaceSourcePayload[] | null; readFailed: boolean },
): WorkspaceBoard {
  // A failed read outranks a stale list: if the last read failed we say so, even when an
  // earlier read left sources in hand, because the board cannot vouch that they are still
  // registered. This matches every other read in the workbench that keeps "could not
  // read" apart from "there are none".
  if (readFailed) return { show: "unreadable" };
  if (sources === null) return { show: "loading" };
  return sources.length === 0 ? { show: "none" } : { show: "sources", count: sources.length };
}

/** The i18n key for each reason a source has nothing to show. */
export const SOURCE_REASON_KEY: Record<SourceReason, string> = {
  absent: "settings.sources.reason.absent",
  unreadable: "settings.sources.reason.unreadable",
  not_a_directory: "settings.sources.reason.notADirectory",
  no_tasks_dir: "settings.sources.reason.noTasksDir",
};

/** The i18n key for each reason a task's manifest could not be read. */
export const MANIFEST_REASON_KEY: Record<ManifestReason, string> = {
  absent: "settings.sources.manifest.absent",
  unreadable: "settings.sources.manifest.unreadable",
  too_large: "settings.sources.manifest.tooLarge",
  malformed: "settings.sources.manifest.malformed",
  duplicate_key: "settings.sources.manifest.duplicateKey",
  // Its own sentence, not "malformed": nothing is broken, it is another tool's file.
  unknown_schema: "settings.sources.manifest.unknownSchema",
};

/**
 * What a task card may claim about its CR log.
 *
 * `open` never travels without `unrecognised`, which is the rule that earned its place
 * on the first day this reader ran: pointed at the author's own RESUME it reported nine
 * rows whose status it could not read, and "11 open" alone would have been presented as
 * the whole truth about a log a quarter of which had not been understood.
 */
export type CrSummary =
  | { show: "counts"; open: number; unrecognised: number }
  | { show: "unavailable"; reason: string };

export function crSummaryOf(log: CrLogPayload): CrSummary {
  if (!log.available || log.open_count === null) {
    return { show: "unavailable", reason: log.reason ?? "unreadable" };
  }
  return {
    show: "counts",
    open: log.open_count,
    // A null here would be the same collapse one field to the left: report it as
    // unknown-shaped rather than as zero rows needing attention.
    unrecognised: log.unrecognised_status_count ?? 0,
  };
}

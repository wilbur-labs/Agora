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
// companion test file (tests/workspace-sources.test.mts) directly.

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
  progress: Record<string, number> | null;
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
}

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

/**
 * The overview page's judgements (workspace CR-75 slice 3): one card per project the work
 * page lists, carrying what that project's own record says — its progress snapshot and
 * how many of its CRs are not closed — next to whether its terminal is open.
 *
 * Two reads, joined here: the work page's list (`/api/work/projects`, which decides what
 * a project is and whether its terminal runs) and the workspace sources
 * (`/api/workspace-sources`, which reads each project's task.yaml and RESUME). A project
 * the second read did not cover is said as such — never drawn with an empty bar or a
 * zero. Free of React and "@/" aliases so `node --test` runs it directly.
 */

import { projectStateOf, type ProjectState, type WorkView } from "./work-state.ts";
import {
  taskProgressOf,
  type CrLogPayload,
  type TaskProgress,
  type WorkspaceSourcePayload,
  type WorkspaceTaskPayload,
} from "./workspace-sources.ts";

/** What a card may say about the project's CRs. */
export type CrCount =
  /** Rows read as OPEN or DECIDED — not closed yet. `unrecognised` travels with it: rows
   *  whose status could not be read are neither open nor closed, and "0" alone would
   *  claim the whole log. */
  | { show: "counts"; notClosed: number; unrecognised: number }
  /** The log is not there: a project that keeps none. Nothing to count, nothing to say. */
  | { show: "none" }
  /** It is there and could not be read (malformed, too large, unreadable). */
  | { show: "unreadable"; reason: string };

/** Where the project's own record stands, as far as the sources read covers it. */
export type CardRecord =
  | { show: "read"; title: string | null; progress: TaskProgress; cr: CrCount }
  /** The sources read is still out. */
  | { show: "loading" }
  /** The sources read failed, or did not cover this project: nothing is claimed. */
  | { show: "unread" };

export interface OverviewCard {
  key: string;
  task: string;
  source: string;
  workspace: "claude" | "kiro";
  state: ProjectState;
  record: CardRecord;
  /** The restore after a restart has something to say about this project. */
  attention: boolean;
}

/** One spelling per directory, for joining the two reads: `E:\Work` and `E:/Work/` are
 *  the same source. Case is folded as on Windows, where both reads come from. */
export function sourceKeyOf(path: string): string {
  return path.replace(/\\/g, "/").replace(/\/+$/, "").toLowerCase();
}

export function crCountOf(log: CrLogPayload): CrCount {
  if (!log.available || log.rows === null) {
    return log.reason === "absent" ? { show: "none" } : { show: "unreadable", reason: log.reason ?? "unreadable" };
  }
  let notClosed = 0;
  let unrecognised = 0;
  for (const row of log.rows) {
    // The rows travel untyped; a row without a readable token is unrecognised, not closed.
    const token = typeof row === "object" && row !== null ? (row as { status_token?: unknown }).status_token : null;
    if (token === "open" || token === "decided") notClosed += 1;
    else if (token !== "done") unrecognised += 1;
  }
  return { show: "counts", notClosed, unrecognised };
}

function recordOf(task: WorkspaceTaskPayload): CardRecord {
  return {
    show: "read",
    title: task.manifest.title,
    progress: taskProgressOf(task.resume),
    cr: crCountOf(task.cr_log),
  };
}

/**
 * The cards, in the work page's order. `sources` is null while that read is out;
 * `sourcesFailed` marks it failed, which outranks a list an earlier read left in hand.
 * `attentionKeys` are the projects the restore banner has a line for.
 */
export function overviewCardsOf(
  view: Pick<WorkView, "projects">,
  sources: readonly WorkspaceSourcePayload[] | null,
  sourcesFailed: boolean,
  attentionKeys: ReadonlySet<string> = new Set(),
): OverviewCard[] {
  const tasks = new Map<string, WorkspaceTaskPayload>();
  for (const source of sources ?? []) {
    if (!source.available || !source.tasks) continue;
    for (const task of source.tasks) tasks.set(`${sourceKeyOf(source.root)}\0${task.name}`, task);
  }
  return view.projects.map((row) => {
    let record: CardRecord;
    if (sourcesFailed) record = { show: "unread" };
    else if (sources === null) record = { show: "loading" };
    else {
      const task = tasks.get(`${sourceKeyOf(row.source)}\0${row.task}`);
      record = task ? recordOf(task) : { show: "unread" };
    }
    return {
      key: row.key,
      task: row.task,
      source: row.source,
      workspace: row.workspace,
      state: projectStateOf(row),
      record,
      attention: attentionKeys.has(row.key),
    };
  });
}

/** The line above the cards: how many projects, how many with a terminal open. */
export function overviewSummaryOf(cards: readonly OverviewCard[]): { projects: number; open: number } {
  return {
    projects: cards.length,
    open: cards.filter((card) => card.state === "open" || card.state === "agentExited" || card.state === "starting").length,
  };
}

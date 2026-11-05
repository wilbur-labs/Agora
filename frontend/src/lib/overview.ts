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

import { orderedRows, projectStateOf, type ProjectState, type Stage, type WorkView } from "./work-state.ts";
import {
  taskProgressOf,
  type CrLogPayload,
  type ReviewGatePayload,
  type TaskProgress,
  type WorkspaceSourcePayload,
  type WorkflowPayload,
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

/** What a card says about the commit review gate (CR-75 slice 2). Null — nothing on the
 *  card — when the gate is not installed, or every commit since carries a review: quiet is
 *  the normal state. A failed read is said, never shown as nothing. */
export type GateLine =
  | { show: "counts"; unrecorded: number; skipped: number; truncated: boolean }
  | { show: "unreadable"; reason: string };

export function gateLineOf(gate: ReviewGatePayload | undefined): GateLine | null {
  if (!gate || gate.state === "not_installed") return null;
  if (gate.state === "unreadable") return { show: "unreadable", reason: gate.reason ?? "unreadable" };
  if (gate.unrecorded === 0 && gate.skipped === 0 && !gate.truncated) return null;
  return { show: "counts", unrecorded: gate.unrecorded, skipped: gate.skipped, truncated: gate.truncated };
}

/** The six workflow switches (CR-75 slice 4), in the order the card lists them. The same
 *  keys and defaults as the server (agora/projects/workflow_selection.py). */
export const WORKFLOW_FEATURES = ["requirements", "breakdown", "aidlc", "deviation", "review", "records"] as const;
export type WorkflowFeature = (typeof WORKFLOW_FEATURES)[number];
export type WorkflowFeatures = Record<WorkflowFeature, boolean>;

/** What the card can do with the project's workflow selection. Null — no button — when
 *  the project is not a claude-workspace one (nothing else reads it yet: offering the
 *  switch would say otherwise) or its task.yaml was not read. */
export type WorkflowView =
  | {
      show: "editable";
      /** The file has no block: every switch at its default. */
      isDefault: boolean;
      features: WorkflowFeatures;
      base: string;
      /** Switches turned off — aidlc not counted, it is off by default. */
      off: number;
      /** The repository has the review gate installed: turning review off does not stop git
       *  enforcing it, and the panel says so. */
      gateInstalled: boolean;
    }
  | { show: "unreadable"; reason: string };

export function workflowViewOf(task: WorkspaceTaskPayload, workspace: "claude" | "kiro"): WorkflowView | null {
  const wf: WorkflowPayload | null | undefined = task.manifest.workflow;
  if (workspace !== "claude" || !wf) return null;
  if (wf.state === "unreadable" || !wf.features) return { show: "unreadable", reason: wf.reason ?? "unreadable" };
  const features = {} as WorkflowFeatures;
  for (const key of WORKFLOW_FEATURES) features[key] = wf.features[key] === true;
  const off = WORKFLOW_FEATURES.filter((key) => key !== "aidlc" && !features[key]).length;
  const gate = task.review_gate;
  return {
    show: "editable",
    isDefault: wf.state === "default",
    features,
    base: wf.base,
    off,
    // "unreadable" counts: the gate's marker is there, it is the record that would not read.
    gateInstalled: gate !== undefined && gate.state !== "not_installed",
  };
}

/** Where the project's own record stands, as far as the sources read covers it. */
export type CardRecord =
  | {
      show: "read"; title: string | null; progress: TaskProgress; cr: CrCount; gate: GateLine | null;
      workflow: WorkflowView | null;
    }
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
  /** The person's board (CR-81): only "doing" is listed on the work page. */
  stage: Stage;
  /** Its terminal is open or meant to be, so it cannot be moved to "todo". */
  busy: boolean;
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

function recordOf(task: WorkspaceTaskPayload, workspace: "claude" | "kiro"): CardRecord {
  return {
    show: "read",
    title: task.manifest.title,
    progress: taskProgressOf(task.resume),
    cr: crCountOf(task.cr_log),
    gate: gateLineOf(task.review_gate),
    workflow: workflowViewOf(task, workspace),
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
  return orderedRows(view.projects).map((row) => {
    let record: CardRecord;
    if (sourcesFailed) record = { show: "unread" };
    else if (sources === null) record = { show: "loading" };
    else {
      const task = tasks.get(`${sourceKeyOf(row.source)}\0${row.task}`);
      record = task ? recordOf(task, row.workspace) : { show: "unread" };
    }
    return {
      key: row.key,
      task: row.task,
      source: row.source,
      workspace: row.workspace,
      state: projectStateOf(row),
      stage: row.stage,
      // A terminal open, opening, or meant to be: "todo" would be refused (CR-81).
      busy: Boolean(row.starting || row.want_open || row.terminal?.alive),
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

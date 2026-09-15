/**
 * The project's plan: how many steps it has, which one it is on, what each step is
 * waiting for, and whether it may be continued.
 *
 * Why this exists as its own module. Until now a project's tasks were a *list* — the
 * left column's quiet rail — and the only way to learn where the project stood was to
 * open one task at a time. W10 changed what the product produces: adopting a kickoff
 * writes a whole ordered plan at once (`kickoff_router` gives the approved tasks
 * descending priorities, and the board reads back `priority DESC, created_at DESC`).
 * Nothing rendered that plan *as* a plan, so the order existed in the data and nowhere
 * on screen. This module owns the question "what does this project's plan say", and it
 * is a module rather than component code because components cannot be tested in this
 * repository (CR-18).
 *
 * Three rules hold it honest, and each one has cost something before:
 *
 *   1. **The order is the server's, not ours.** Rows are numbered in the order they
 *      arrive. Nothing here sorts by priority — a second ordering judgment beside the
 *      board's own is how the product starts disagreeing with itself (CR-68 is the
 *      placement dialog's version of exactly that).
 *
 *      One difference is open and tracked, not accepted quietly: these numbers count
 *      **every** non-archived task, while the placement dialog positions among the live
 *      ones only (`kickoff_router._order` drops done/failed/cancelled). So "step 3" can
 *      name different tasks on the two surfaces. Counting all of them is what lets the
 *      plan answer "how many steps does this project have" — a number that shrank as
 *      work finished could not — but two numbering schemes in one product is a defect
 *      waiting to be hit, and it is **CR-72**, open, not a settled trade-off.
 *   2. **A read that cannot vouch for a task says "unknown", never "nothing".** The
 *      attention read behind "what is this waiting for" is global and capped; absence
 *      from a capped page is not evidence of absence (`AttentionReach`).
 *   3. **Percent counts only what is done.** In-flight work is not half a point — the
 *      mockup's words: "一折算就开始骗自己".
 *
 * What this module deliberately does **not** own: whether a run may actually start
 * (`lib/continue-run.ts` owns that, and this module calls it rather than re-deciding),
 * and per-task progress (there is no source for it — see `PlanRow.progress`).
 */
import { attentionReadIsComplete, type AttentionReach } from "./rail-group.ts";
import { continueEntryGate, type ContinueDecision } from "./continue-run.ts";
import type { TaskInventoryReach } from "./portfolio-bucket.ts";
import type { AttentionItem, AttentionKind, TaskManifest, TaskState } from "./control-plane.ts";

/**
 * A finished step. Kept apart from "abandoned" because they are different sentences:
 * one is progress, the other is work the plan no longer contains.
 */
export const PLAN_DONE_STATES: readonly TaskState[] = ["done"];

/** Work that left the plan without finishing it. */
export const PLAN_ABANDONED_STATES: readonly TaskState[] = ["failed", "cancelled"];

/**
 * What a step is waiting on, as three distinguishable answers.
 *
 * `unknown` is the one that matters. The page's attention read is one global, capped
 * request; when it did not cover this task, "nothing is waiting" is a claim the read
 * does not support, and the plan says so instead of printing an empty cell.
 */
export type PlanWaiting =
  | { kind: "none" }
  | { kind: "unknown" }
  /**
   * `exact` is false when the read behind it was capped or failed: the items found are
   * real, but they are a floor, not a tally — the rest of this task's items may not have
   * been returned, and a stale array can still hold one that has since been answered.
   * Rendering a floor as a count is the same substitution `unknown` exists to prevent,
   * one step further in.
   */
  | { kind: "open"; count: number; exact: boolean; kinds: AttentionKind[] };

/**
 * Per-task progress. There is no source for it: `lib/progress-snapshot.ts` reads the
 * ✅/🔵 counts out of a *workspace* task's RESUME.md, and Agora's own tasks have no such
 * file. So this is a single-valued type rather than a number — the plan says the step's
 * progress cannot be read, and never draws a bar that means nothing.
 *
 * It is a type rather than a comment so that the day a source exists, every render site
 * fails to compile until it handles the new case.
 */
export type PlanProgress = { kind: "unreadable"; reason: "no_task_snapshot_source" };

export const PLAN_PROGRESS_UNREADABLE: PlanProgress = {
  kind: "unreadable",
  reason: "no_task_snapshot_source",
};

export interface PlanRow {
  task_id: string;
  /** 1-based, in the order the board returned. See rule 1. */
  step: number;
  title: string;
  kind: string;
  state: TaskState;
  running: boolean;
  /** Finished, abandoned, or neither — the three the headline counts. */
  standing: "done" | "abandoned" | "open";
  waiting: PlanWaiting;
  progress: PlanProgress;
  /** Whether "Continue" may be offered, decided by the module that owns that question. */
  decision: ContinueDecision;
}

/**
 * What the task list read can support — plus the state before any read has settled.
 *
 * "pending" exists because the first render has no rows *and* no read: treating that as
 * "complete" makes the emptiest possible claim ("this project has no tasks") out of the
 * least possible evidence. It was `"complete"` for one round, with a comment explaining
 * why that was safe. It was not.
 */
export type PlanTotalReach = TaskInventoryReach | "pending";

export interface PlanHeadline {
  /** How many steps were *seen*. Only a total when `totalKnown`. */
  total: number;
  /**
   * Whether `total` is the plan's size or merely how much of it this read returned.
   * The task list is one global, paged request; a full page is not evidence that the
   * page was the whole list, and "6 steps" read off a capped page is a number the read
   * cannot support. False also suppresses `percent`: a fraction of an unknown whole is
   * not a progress figure.
   */
  totalKnown: boolean;
  done: number;
  running: number;
  abandoned: number;
  /**
   * Percent of steps finished, or null when there is nothing to divide by. Only `done`
   * counts; nothing in flight is worth a fraction.
   */
  percent: number | null;
  /**
   * Whether every row's "waiting on" could be answered. False means at least one step's
   * gate is unknown — the headline is still true about counts, and the caller must not
   * present it as a complete picture of what needs the owner.
   */
  waitingKnown: boolean;
}

function standingOf(state: TaskState): PlanRow["standing"] {
  if (PLAN_DONE_STATES.includes(state)) return "done";
  if (PLAN_ABANDONED_STATES.includes(state)) return "abandoned";
  return "open";
}

/**
 * What this task is waiting on, given the page's one global attention read.
 *
 * `items` is that read narrowed to this task, and it is evidence of *existence only*:
 * items found are real, but finding none means nothing unless the read was complete.
 * That asymmetry is the whole function.
 */
export function planWaitingOf(input: {
  items: readonly AttentionItem[];
  reach: AttentionReach;
}): PlanWaiting {
  // A failed read is not a smaller read. The page keeps the previous array when the
  // refresh rejects, so those items are a *snapshot*, not a floor: any of them may have
  // been answered since, and the ones raised since are missing. "At least N open" is a
  // claim about now, and nothing here is about now — so it is `unknown`, whatever the
  // array still holds. A capped read is different: what it returned it returned in this
  // refresh, so those are real and a floor is honest.
  if (input.reach === "unreadable") return { kind: "unknown" };
  const complete = attentionReadIsComplete(input.reach);
  const open = input.items.filter((item) => item.state === "open");
  if (open.length > 0) {
    // Distinct kinds, in the order they were seen: the row shows what sort of answer is
    // wanted ("a question" reads differently from "a blocker"), not a bare number.
    const kinds: AttentionKind[] = [];
    for (const item of open) if (!kinds.includes(item.kind)) kinds.push(item.kind);
    return { kind: "open", count: open.length, exact: complete, kinds };
  }
  if (!complete) return { kind: "unknown" };
  return { kind: "none" };
}

/**
 * The plan, one row per task, in the order given.
 *
 * `tasks` must already be this project's and already in the board's order — the caller
 * filters, this module never sorts. `runsAreCurrent` is false when a sibling read failed
 * and the run snapshot can no longer be vouched for; then "running" is not claimed and
 * the continue gate is told the active-run count is unknown rather than zero, which is
 * the difference between "you may start one" and "I cannot tell whether one is going".
 */
export function planRowsOf(input: {
  tasks: readonly TaskManifest[];
  attention: readonly AttentionItem[];
  attentionReach: AttentionReach;
  runningTaskIds: ReadonlySet<string>;
  runsAreCurrent: boolean;
  projectGate: "open" | "noCharter" | "unknown";
}): PlanRow[] {
  return input.tasks.map((task, index) => {
    const waiting = planWaitingOf({
      items: input.attention.filter((item) => item.task_id === task.task_id),
      reach: input.attentionReach,
    });
    const running = input.runsAreCurrent && input.runningTaskIds.has(task.task_id);
    return {
      task_id: task.task_id,
      step: index + 1,
      title: task.title,
      kind: task.kind,
      state: task.state,
      running,
      standing: standingOf(task.state),
      waiting,
      progress: PLAN_PROGRESS_UNREADABLE,
      decision: continueEntryGate({
        taskState: task.state,
        projectGate: input.projectGate,
        // Unknown, not zero, when the run snapshot is stale — `continueEntryGate` turns
        // null into a "cannot tell" refusal rather than an invitation.
        activeRunCount: input.runsAreCurrent ? (running ? 1 : 0) : null,
        // A floor is not a count: an inexact "1 open" could be one that has already been
        // answered on a stale array, and could be hiding others. Either way the honest
        // input is "cannot tell", which `continueEntryGate` turns into its own refusal.
        openAttentionCount: waiting.kind === "unknown" ? null
          : waiting.kind === "open" ? (waiting.exact ? waiting.count : null)
            : 0,
        // Deliberately false, and this is an *entry* gate, not the full one. The
        // handoff window (worker succeeded, its review not yet queued) is a per-mount
        // timer on the task page and cannot be seen from here, so a row can read
        // "Continue" for a few seconds while the product would refuse. It cannot become
        // a wrong dispatch — this button opens the step (CR-73) and the real gate runs
        // there, with the control plane's `review_owed` refusal behind it — but the
        // label is optimistic in that window. Tracked as CR-72's neighbour, CR-76.
        followUpArmed: false,
        dispatching: false,
      }),
    };
  });
}

/**
 * The one line above the table: how many steps, how many finished, how many in flight.
 *
 * `taskReach` is what the task list read can support — `readPage` knows whether a further
 * row exists, and `taskInventoryReachOf` turns that into this verdict. Without it the
 * line would print "6 steps" off a page that happened to be full, which is the defect
 * this repository already fixed once for the portfolio board (CR-28).
 */
export function planHeadlineOf(
  rows: readonly PlanRow[],
  taskReach: PlanTotalReach,
): PlanHeadline {
  const total = rows.length;
  const done = rows.filter((row) => row.standing === "done").length;
  const abandoned = rows.filter((row) => row.standing === "abandoned").length;
  const running = rows.filter((row) => row.running).length;
  const totalKnown = taskReach === "complete";
  return {
    total,
    totalKnown,
    done,
    running,
    abandoned,
    // No percent without a denominator anyone can vouch for.
    percent: !totalKnown || total === 0 ? null : Math.round((done / total) * 100),
    waitingKnown: rows.every(
      (row) => row.waiting.kind !== "unknown" && !(row.waiting.kind === "open" && !row.waiting.exact),
    ),
  };
}

/**
 * The step to open when the owner asks for "the one to work on now" — the first row
 * whose gate says a run may start. Null when none does, which is a real answer: every
 * step is finished, blocked, already running, or waiting on the owner.
 */
export function nextOpenStepOf(rows: readonly PlanRow[]): PlanRow | null {
  return rows.find((row) => row.decision.ready) ?? null;
}

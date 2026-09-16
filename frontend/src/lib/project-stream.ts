/**
 * The project conversation (W12): one stream per project, every run and every gate
 * in it, and one box to type into.
 *
 * The person's words: "一个项目就一个项目对话就行了，不用再往下分……旁边会显示项目任务进行到
 * 哪里了、项目目标而已". Tasks stay the unit the control plane runs against — a run
 * still belongs to a task — but the screen no longer makes the person enter and leave
 * them. This module decides what the stream holds and what a typed line means; the
 * component only fetches and draws.
 *
 * Every judgement here has a "cannot say" answer where the reads can fail to support
 * one, and none of them turns an absence in a cut page into a fact.
 */
import { runTreeOf, type RunUnit, type UnplaceableReason } from "./run-tree.ts";
import type { ColumnRunLike } from "./run-column.ts";

export interface StreamRunLike extends ColumnRunLike {
  task_id: string;
  adapter: string;
  queued_at: string;
}

export interface StreamGateLike {
  item_id: string;
  task_id: string;
  state: string;
  created_at: string;
}

export interface StreamStepLike {
  task_id: string;
  step: number;
  title: string;
  state: string;
}

export type StreamItem<R extends StreamRunLike, G extends StreamGateLike> =
  | { type: "run"; at: string; key: string; unit: RunUnit<R>; step: number | null }
  | { type: "unplaced"; at: string; key: string; run: R; step: number | null; because: UnplaceableReason }
  | { type: "gate"; at: string; key: string; item: G; step: number | null };

export function stepNumberOf(steps: readonly StreamStepLike[], taskId: string): number | null {
  return steps.find((step) => step.task_id === taskId)?.step ?? null;
}

/**
 * Runs as work units (reviews nested under the run they review — same tree the task
 * view used) and gates, in the order the work began. A run's place is when it was
 * queued; a gate's is when it was raised.
 */
export function streamItemsOf<R extends StreamRunLike, G extends StreamGateLike>(
  runs: readonly R[] | null,
  gates: readonly G[] | null,
  steps: readonly StreamStepLike[],
): StreamItem<R, G>[] {
  const out: StreamItem<R, G>[] = [];
  const tree = runTreeOf(runs);
  for (const unit of tree.units) {
    out.push({ type: "run", at: unit.run.queued_at, key: unit.run.run_id, unit, step: stepNumberOf(steps, unit.run.task_id) });
  }
  for (const unplaced of tree.unplaceable) {
    out.push({ type: "unplaced", at: unplaced.run.queued_at, key: unplaced.run.run_id, run: unplaced.run, step: stepNumberOf(steps, unplaced.run.task_id), because: unplaced.because });
  }
  for (const item of gates ?? []) {
    out.push({ type: "gate", at: item.created_at, key: item.item_id, item, step: stepNumberOf(steps, item.task_id) });
  }
  // Stable: ties keep insertion order (runs before gates raised at the same instant).
  return out
    .map((item, index) => ({ item, index }))
    .sort((a, b) => (a.item.at < b.item.at ? -1 : a.item.at > b.item.at ? 1 : a.index - b.index))
    .map(({ item }) => item);
}

/** What a typed line is for. A prefix decides; there is no second button. */
export type ComposeIntent =
  | { kind: "note"; text: string }
  | { kind: "idea"; text: string }
  | { kind: "dispatch"; text: string };

const NOTE_PREFIXES = ["备注：", "备注:", "note:", "Note:"];
const IDEA_PREFIXES = ["点子：", "点子:", "idea:", "Idea:"];

export function composeIntentOf(raw: string): ComposeIntent | null {
  const text = raw.trim();
  if (text === "") return null;
  for (const prefix of NOTE_PREFIXES) {
    if (text.startsWith(prefix)) {
      const rest = text.slice(prefix.length).trim();
      return rest === "" ? null : { kind: "note", text: rest };
    }
  }
  for (const prefix of IDEA_PREFIXES) {
    if (text.startsWith(prefix)) {
      const rest = text.slice(prefix.length).trim();
      return rest === "" ? null : { kind: "idea", text: rest };
    }
  }
  return { kind: "dispatch", text };
}

/**
 * Which step a dispatched line goes to. The running step, unless it has a run that
 * has not settled (then the line must wait — the control plane refuses a second
 * worker run, and saying so beats a refused request); else the first planned step.
 *
 * "None" has three sentences: every step is done (only over a whole read), nothing is
 * startable (blocked / backlog / design rows exist), or the read was cut and the
 * question cannot be answered.
 */
export type DispatchTarget<S extends StreamStepLike> =
  | { kind: "step"; step: S }
  | { kind: "busy"; step: S }
  | { kind: "none"; reason: "allDone" | "noneStartable" | "unknown" };

export function dispatchTargetOf<S extends StreamStepLike>(
  steps: readonly S[],
  busyTaskIds: ReadonlySet<string>,
  rowsWhole: boolean,
): DispatchTarget<S> {
  const running = steps.find((step) => step.state === "running");
  if (running) return busyTaskIds.has(running.task_id) ? { kind: "busy", step: running } : { kind: "step", step: running };
  const planned = steps.find((step) => step.state === "planned");
  if (planned) return busyTaskIds.has(planned.task_id) ? { kind: "busy", step: planned } : { kind: "step", step: planned };
  if (!rowsWhole) return { kind: "none", reason: "unknown" };
  const allDone = steps.length > 0 && steps.every((step) => step.state === "done");
  return { kind: "none", reason: allDone ? "allDone" : "noneStartable" };
}

/** The one line in the header: what is happening right now. */
export type StreamStatus<R extends StreamRunLike> =
  | { kind: "running"; run: R; step: number | null; also: number }
  | { kind: "waiting"; count: number }
  | { kind: "idle" }
  | { kind: "unknown" };

const LIVE_STATES: readonly string[] = ["queued", "running"];

export function activeRunsOf<R extends StreamRunLike>(runs: readonly R[] | null): R[] {
  return (runs ?? []).filter((run) => LIVE_STATES.includes(run.state));
}

export function streamStatusOf<R extends StreamRunLike>(
  runs: readonly R[] | null,
  runsReadFailed: boolean,
  openGates: number | null,
  steps: readonly StreamStepLike[],
): StreamStatus<R> {
  if (runsReadFailed) return { kind: "unknown" };
  const live = activeRunsOf(runs);
  if (live.length > 0) {
    // The most recently queued one is the one the person is waiting on.
    const run = [...live].sort((a, b) => (a.queued_at < b.queued_at ? 1 : -1))[0];
    return { kind: "running", run, step: stepNumberOf(steps, run.task_id), also: live.length - 1 };
  }
  if (openGates === null) return { kind: "unknown" };
  if (openGates > 0) return { kind: "waiting", count: openGates };
  return { kind: "idle" };
}

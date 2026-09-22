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

/**
 * What a typed line is for. A prefix decides; there is no second button.
 *
 * The default is a **question** (CR-75): the line goes to the agent as a chat run —
 * answered, not reviewed, and it moves no task. Work is what the "start step N" button
 * dispatches, or a line that says so with 做：. The failure this replaces: "还给
 * mockup 页面的链接" was dispatched as step 2's work, reviewed, approved, and step 2 was
 * marked done with nothing built.
 */
export type ComposeIntent =
  | { kind: "note"; text: string }
  | { kind: "idea"; text: string }
  | { kind: "dispatch"; text: string };

const NOTE_PREFIXES = ["备注：", "备注:", "note:", "Note:"];
const IDEA_PREFIXES = ["点子：", "点子:", "idea:", "Idea:"];
const WORK_PREFIXES = ["做：", "做:", "do:", "Do:"];

function stripPrefix(text: string, prefixes: readonly string[]): string | null {
  for (const prefix of prefixes) {
    if (text.startsWith(prefix)) return text.slice(prefix.length).trim();
  }
  return null;
}

export function composeIntentOf(raw: string): ComposeIntent | null {
  const text = raw.trim();
  if (text === "") return null;
  const note = stripPrefix(text, NOTE_PREFIXES);
  if (note !== null) return note === "" ? null : { kind: "note", text: note };
  const idea = stripPrefix(text, IDEA_PREFIXES);
  if (idea !== null) return idea === "" ? null : { kind: "idea", text: idea };
  const work = stripPrefix(text, WORK_PREFIXES);
  if (work !== null) return work === "" ? null : { kind: "dispatch", text: work };
  // CR-99 (2026-09-19): a plain line is sent to the agent as it is, like a line typed
  // into the CLI — it decides whether to answer or to act. The "ask" kind is no longer
  // produced; "做：" is still accepted and stripped so an old habit costs nothing.
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

/**
 * A `·` or `✎` with nothing after it. Before CR-104 that is what an entire thinking
 * block rendered as: its content went to the deltas, which the reader dropped, and
 * the closing copy it did render was empty. Filtered at render time as well as fixed
 * in the reader, because a transcript is rendered once — when it is written — so
 * every one already on disk still holds these.
 */
const MARKER_ONLY = /^[·✎]\s*$/;

/**
 * The transcript lines a turn should show.
 *
 * `holdLastLine` withholds the final one. A growing transcript normally ends mid-line
 * — the file does not end in a newline, and the run-log reader serves whole
 * *characters*, not whole lines — so rendering it made the tail of the window twitch
 * mid-word on every poll (CR-105). The caller decides, because only it can see both
 * halves: the run is still writing AND its text does not end in a newline. Once the
 * run settles, an unterminated last line is all there will ever be of it, so it shows.
 *
 * **A turn is never left with nothing to draw**, and the order below is what keeps
 * that true. Counting raw lines first, a live `["· ", "<half a line>"]` withheld the
 * half line and then filtered away the only line left, blanking the window until the
 * next newline (codex review, 2026-09-22). The promise is about lines the reader can
 * *see*, so it is measured on those.
 */
export function visibleTranscriptLines(body: string, holdLastLine: boolean): string[] {
  const visible = body.split("\n").filter((line) => !MARKER_ONLY.test(line));
  return holdLastLine && visible.length > 1 ? visible.slice(0, -1) : visible;
}

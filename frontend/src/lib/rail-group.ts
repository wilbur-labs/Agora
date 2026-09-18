// Which rail group a task belongs to — the Workspace's primary triage claim.
//
// Why this exists as its own module: the rail derived every task's group from the
// parent's ONE capped, global open-attention page. Past that cap a task's own
// question/approval/blocker can fall off the page, and the rail read that absence
// as "this task has no attention" — filing a running task under "Running" with a
// lifecycle-state subtitle. Select it and the authoritative task-scoped read shows
// the gate and blocks Continue, so one workspace claimed "in progress" in the rail
// and "attention required" in the task view about the same task at the same time.
// Calling the rail display-only stops it affecting execution; it does not stop it
// making the triage claim the rail exists to make.
//
// The shape chosen (see `railGroupOf`): a distinct `unknown` group, NOT dropping
// uncertain tasks from the rail. The rail is also the only navigation surface in
// the Workspace — a task removed from it is unreachable, which is a worse failure
// than an unsorted one — and "I could not tell" is a thing a triage board can say.
// Only the groups that assert the task needs nothing right now are downgraded;
// a `needsYou` claim that rests on lifecycle state alone does not depend on the
// capped read and is kept.
//
// Deliberately free of React and of "@/" path aliases: `node --test` executes the
// companion test file directly (see tests/rail-group.test.mts).

/** The fields the grouping needs from a task manifest; a structural subset. */
export interface RailTaskLike {
  state: string;
}

/**
 * The ball is in the human's court whenever a task awaits a decision; a running
 * task is the agent's; review is its own stage; terminal/verified is done.
 * `unknown` is none of those: it is the read's own limit, shown rather than
 * rounded down to "fine".
 */
export type RailGroup = "needsYou" | "unknown" | "running" | "review" | "done";

/**
 * Display order. `unknown` sits directly under `needsYou`, above every group that
 * means "nothing to do here", because an uncertain task may in fact need a human
 * and must not be buried below the settled work.
 */
export const RAIL_ORDER: readonly RailGroup[] = ["needsYou", "unknown", "running", "review", "done"];

/**
 * The task states whose group asserts "this task needs nothing from you right
 * now". Everything else — backlog / requirements / design / planned / blocked /
 * failed, and any state added later — awaits the human, which is a claim the rail
 * makes from the task's own state and not from the attention page.
 */
const CALM_STATES: ReadonlySet<string> = new Set(["running", "review", "verified", "done", "cancelled"]);

/**
 * What the rail knows when it groups one task.
 *
 * `attentionComplete` is the load-bearing field: false when the global open
 * attention page came back at its cap, or could not be read at all. In that state
 * an empty `kinds` is not evidence of anything — the task's item may simply be
 * off-page — so no "nothing needed" group may be claimed from it.
 */
export interface RailGroupFacts {
  task: RailTaskLike;
  /** Open attention kinds OBSERVED for this task in that page. */
  kinds: ReadonlySet<string>;
  attentionComplete: boolean;
}

export function railGroupOf({ task, kinds, attentionComplete }: RailGroupFacts): RailGroup {
  // Presence is provable from any page, complete or not: an item that is there is
  // there. Only absence is unprovable, which is why these two come first and are
  // unaffected by `attentionComplete`.
  if (kinds.size > 0) return "needsYou";
  if (!CALM_STATES.has(task.state)) return "needsYou";
  // Past here the group would say "nothing is waiting on you for this task", and
  // with no observed item that rests entirely on the page having been complete.
  if (!attentionComplete) return "unknown";
  if (task.state === "running") return "running";
  // Verified still owes a human handoff (mark_done), so it is NOT "done" — it sits
  // in review/acceptance until it actually reaches the done state.
  if (task.state === "review" || task.state === "verified") return "review";
  return "done"; // done / cancelled
}

/**
 * How far the ONE global open-attention read got. Three failure-shaped outcomes
 * are kept apart because they are different facts about different things, and the
 * notice below states which one happened:
 *
 * - `pending`   — no read has settled yet. Nothing is claimed either way.
 * - `complete`  — the page covers every open item, so an absence is an absence.
 * - `capped`    — the page came back at its limit: there was more than was read.
 * - `unreadable`— the read failed: it could not be read at all.
 *
 * `capped` and `unreadable` group identically (see `railGroupOf`) and previously
 * shared one boolean and therefore one message — which told a user whose request
 * had failed the specific and false story that the cap had been reached. Grouping
 * the same is not a reason to say the same: "I could not read it" and "there was
 * more than I read" are the distinction this whole area exists to preserve.
 */
export type AttentionReach = "pending" | "complete" | "capped" | "unreadable";

/**
 * Whether the read supports an "absence means nothing is waiting" claim — the one
 * question `railGroupOf` asks. Only a complete read does.
 */
export function attentionReadIsComplete(reach: AttentionReach): boolean {
  return reach === "complete";
}

/**
 * The rail notice's message key for each outcome that is not a complete read.
 * `complete` is absent by type: there is nothing to disclose, and a record that
 * could answer for it would invite rendering a notice over good data.
 */
export const RAIL_ATTENTION_NOTICE_KEY: Record<Exclude<AttentionReach, "complete">, string> = {
  pending: "workspace.rail.attentionPending",
  capped: "workspace.rail.attentionCapped",
  unreadable: "workspace.rail.attentionUnreadable",
};

/**
 * What the rail row's second line says.
 *
 * Split out because the substitution lived here too: the row rendered
 * `ask ?? localized(task.state)`, so a task whose ask was cut off by the cap got a
 * calm "running" subtitle — the missing evidence replaced by an unrelated fact
 * that reads as reassurance. In the `unknown` group the state may still be shown,
 * but only alongside the admission that the attention read could not cover it.
 */
export type RailSubtitle =
  | { kind: "ask"; text: string }
  | { kind: "stateWithUnknownAttention" }
  | { kind: "state" };

export function railSubtitleOf(group: RailGroup, ask: string | null): RailSubtitle {
  if (ask) return { kind: "ask", text: ask };
  if (group === "unknown") return { kind: "stateWithUnknownAttention" };
  return { kind: "state" };
}

// ---------------------------------------------------------------------------
// The reads the rail's snapshot is built from, each settled on its own.
//
// Why this lives here rather than inline in the page: the rail loaded all four
// reads with one `Promise.all` and one `catch`, and that catch set the attention
// reach to `unreadable`. So a rejected *sibling* — the run list, say — made the
// rail announce "Open attention items could not be read" about a read that had
// succeeded: the cap's defect (a specific, confident, false reason) one origin
// later. A combined promise reports that *something* failed; it cannot say what,
// and `AttentionReach` is a statement about one specific read. Each origin is
// therefore settled and reported separately below, and a sibling's failure says
// what it actually costs the rail — a stale snapshot — in its own words.
// ---------------------------------------------------------------------------

/** The four reads one rail refresh performs. */
export type RailReadOrigin = "tasks" | "projects" | "attention" | "runs";

/**
 * The origins whose failure does NOT license any claim about the attention read.
 * Their cost is staleness: the rail keeps rendering the previous answer (or none
 * at all on a first load), which is worth disclosing but is a different fact from
 * "whether these tasks need you is not known".
 */
export type RailStaleOrigin = Exclude<RailReadOrigin, "attention">;

/** Fixed report order, so the notices and the error text do not reshuffle. */
export const RAIL_STALE_ORDER: readonly RailStaleOrigin[] = ["tasks", "projects", "runs"];

// Whether a run in hand can be presented as current is no longer decided here. The
// helper that did (`railRunIsCurrent`, CR-17) lost its last caller when W9 turned the
// task rail into a project-level column and W11 moved the task list out of it; the
// page now asks the narrow active-runs read (`projectActiveReach`) instead. Removed
// under CR-77 rather than left as an unused export nothing would ever flag.
//
// The timeline's own read-completeness layer lives in `lib/timeline.ts`, not here.
// Its first version was two helpers in this file — a state and a separate "is it
// partial" — and this is the wrong home twice over: the rail is not the timeline, and
// two functions over the same loose booleans left the component to combine them, which
// is what review then found four defects in.

/**
 * The rail notice for each sibling read that failed. Separate messages, not the
 * attention notice: each names its own read and claims nothing about the others.
 */
export const RAIL_STALE_NOTICE_KEY: Record<RailStaleOrigin, string> = {
  tasks: "workspace.rail.tasksUnreadable",
  projects: "workspace.rail.projectsUnreadable",
  runs: "workspace.rail.runsUnreadable",
};

/** The only field of the attention page the reach depends on. */
export interface AttentionPageLike {
  truncated: boolean;
}

/** One settled outcome per read, in the shape `Promise.allSettled` produces. */
export interface RailReads {
  tasks: PromiseSettledResult<unknown>;
  projects: PromiseSettledResult<unknown>;
  attention: PromiseSettledResult<AttentionPageLike>;
  runs: PromiseSettledResult<unknown>;
}

/**
 * Run one read to a settled outcome that belongs to it alone — including a
 * synchronous throw from the call itself, which in an argument list would abort
 * the whole batch before any sibling was even started.
 *
 * The returned promise never rejects, which is the point: there is no combined
 * failure path left for a reach to be inferred from.
 */
export async function settleRead<T>(read: () => T | Promise<T>): Promise<PromiseSettledResult<Awaited<T>>> {
  try {
    return { status: "fulfilled", value: await read() };
  } catch (reason) {
    return { status: "rejected", reason };
  }
}

/**
 * How far the attention read itself got — from the attention read's own outcome
 * and nothing else. `pending` is not reachable from here: a settled read is by
 * definition no longer pending.
 */
export function attentionReachOf(result: PromiseSettledResult<AttentionPageLike>): AttentionReach {
  if (result.status === "rejected") return "unreadable";
  return result.value.truncated ? "capped" : "complete";
}

/** What one refresh may state about itself. */
export interface RailReadReport {
  /** From the attention read alone. */
  attentionReach: AttentionReach;
  /** Non-attention reads that failed, in `RAIL_STALE_ORDER`. */
  staleOrigins: readonly RailStaleOrigin[];
  /** The failure messages, for the page's error banner; empty when all read. */
  errors: readonly string[];
}

function failureMessage(reason: unknown): string {
  if (reason instanceof Error && reason.message) return reason.message;
  return String(reason);
}

export function railReadReport(reads: RailReads): RailReadReport {
  const staleOrigins: RailStaleOrigin[] = [];
  for (const origin of RAIL_STALE_ORDER) {
    if (reads[origin].status === "rejected") staleOrigins.push(origin);
  }
  // Attention first: it is the read whose failure the user is told about most
  // prominently, and its message must not be buried behind a sibling's.
  const ordered: readonly RailReadOrigin[] = ["attention", ...RAIL_STALE_ORDER];
  const errors = ordered
    .map((origin) => reads[origin])
    .filter((result): result is PromiseRejectedResult => result.status === "rejected")
    .map((result) => failureMessage(result.reason));
  return { attentionReach: attentionReachOf(reads.attention), staleOrigins, errors };
}

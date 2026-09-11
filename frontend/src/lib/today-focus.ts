/**
 * Today — the three tiers and everything a card may claim, decided in one place.
 *
 * Mockup v2, screen 1: "今天主推" (at most three big cards), "在推进，但不是今天的重点"
 * (compact rows), "N 个休眠项目" (one folded line). The first tier is a choice the user
 * made and the server stores (`GET /api/projects` → `today_focus`, see
 * docs/W9-mockup-alignment.md D-1); the other two are read off the tasks: the mockup
 * defines advancing as "有未完成任务、近 14 天有活动", and dormant as the rest.
 *
 * Why this is its own module and not JSX. Two claims here are absence claims, and both
 * used to be made by accident on the old Today:
 *
 *   - "dormant" says *no* task of this project moved in fourteen days. That rests on the
 *     task read having covered every task; on a capped or failed read a project with no
 *     observed activity is not dormant, it is unplaced. Presence is provable from any
 *     page, absence is not (lib/portfolio-bucket.ts says the same rule for its columns).
 *   - "3/3 已满" is the server's sentence, not this module's arithmetic. The backend counts
 *     unresolved ids as occupying a seat and says so in `is_full`, and it refuses the
 *     fourth promotion on that same computation. Recomputing fullness here from the
 *     resolvable ids alone is exactly how a screen offers a seat it does not have — the
 *     bug the backend review caught and fixed on its side; this side must not re-open it.
 *
 * Deliberately free of React and of "@/" path aliases: `node --test` executes the
 * companion test file (tests/today-focus.test.mts) directly.
 */
import { NAV_DEFECTS_NO_SOURCE, type NavCapability } from "./nav-capability.ts";
import type { TaskInventoryReach } from "./portfolio-bucket.ts";

/** The fields the tiering needs from a registered project; a structural subset. */
export interface TodayProjectLike {
  project_id: string;
}

/**
 * The fields the tiering needs from a task manifest; a structural subset.
 *
 * `archived_at` is deliberately not among them. Archiving is a display property of the
 * boards, not a lifecycle transition (`TaskStore` keeps the state as it was), so an
 * archived task that is still `running` is still open work: it can hold a blocker that
 * the stuck list below shows, and a screen that called its project dormant while showing
 * that blocker would be saying two things about one task.
 */
export interface TodayTaskLike {
  project_id: string;
  state: string;
  /** ISO 8601. An unparseable value proves nothing — see `taskActivityOf`. */
  updated_at: string;
}

/**
 * The `today_focus` field of `GET /api/projects`, restated with only what this module
 * reads. Every field required, matching the contract — an optional field here would
 * invite a `?? 0`, and the safe reading of a missing number is not zero. Whether the
 * server sent the field at all is the caller's `null`, handled separately below.
 */
export interface TodayFocusLike {
  /** Resolvable project ids, in promotion order. */
  project_ids: readonly string[];
  /** Promoted ids that no longer resolve. They still occupy a seat. */
  unresolved_ids: readonly string[];
  limit: number;
  /** The server's verdict. The only one this module repeats. */
  is_full: boolean;
}

/** The activity window the mockup names: "近 14 天有活动". */
export const ADVANCING_WINDOW_DAYS = 14;
const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * Task states that mean the work is over. Everything else is "未完成".
 *
 * Mirrors `TERMINAL_STATES` in backend/agora/tasks/transitions.py — `failed` has no
 * outgoing transition there, so a failed task cannot be continued and does not make its
 * project "advancing". It still reaches the stuck list below the tiers
 * (lib/portfolio-bucket.ts files it as `blocked`), which is where a dead task belongs on
 * this screen.
 */
const CLOSED_STATES: ReadonlySet<string> = new Set(["done", "cancelled", "failed"]);

export interface TodayTiers {
  /** Whether the server reported a focus tier at all. False on a server older than this UI. */
  focusReported: boolean;
  /** Top tier, in promotion order; only ids that are registered projects. */
  focus: readonly string[];
  /** Promoted ids with no registered project behind them. Shown, because they hold seats. */
  unresolvedFocus: readonly string[];
  /**
   * Every registered project with an *observed* open task updated inside the window,
   * focus included — what the heading's "N 个在推进" counts. The mockup's "9 个项目，4 个在
   * 推进" counts the focus cards too; `advancing` below is the second tier only.
   */
  active: readonly string[];
  /** Registered, not in focus, with an *observed* open task updated inside the window. */
  advancing: readonly string[];
  /** Registered, not in focus, no such task — and the task read covered everything. */
  dormant: readonly string[];
  /**
   * Registered, not in focus, no such task observed — while "dormant" cannot be said,
   * either because the task read did not cover everything or because the project is in
   * `undated`. Never folded into `dormant`.
   */
  unplaced: readonly string[];
  /**
   * Projects with an open task whose `updated_at` could not be parsed. Reported so the
   * row can say *why* it is unplaced: "the read fell short" and "one of its tasks has no
   * readable update time" send a person to different places.
   */
  undated: readonly string[];
}

export interface TodayTierFacts {
  projects: readonly TodayProjectLike[];
  /** `null` when the projects response carried no `today_focus` field. */
  focus: TodayFocusLike | null;
  tasks: readonly TodayTaskLike[];
  taskReach: TaskInventoryReach;
  /** The moment "today" is measured from, in ms since the epoch. */
  nowMs: number;
}

/**
 * What one task says about its project moving now.
 *
 *   recent   open, updated inside the window — the project is advancing
 *   stale    open, updated before the window
 *   closed   in a terminal state; says nothing about activity
 *   unknown  open, but `updated_at` cannot be parsed — it proves neither recent nor
 *            stale, and a project whose only open task carries such a stamp cannot be
 *            called dormant on its account
 *
 * A future `updated_at` is `recent` (a clock skew is not evidence of inactivity).
 */
export type TaskActivity = "recent" | "stale" | "closed" | "unknown";

export function taskActivityOf(task: TodayTaskLike, nowMs: number): TaskActivity {
  if (CLOSED_STATES.has(task.state)) return "closed";
  const updated = Date.parse(task.updated_at);
  if (Number.isNaN(updated)) return "unknown";
  return nowMs - updated <= ADVANCING_WINDOW_DAYS * DAY_MS ? "recent" : "stale";
}

/** Convenience for the one question most callers ask. */
export function taskIsRecentOpenWork(task: TodayTaskLike, nowMs: number): boolean {
  return taskActivityOf(task, nowMs) === "recent";
}

export function todayTiersOf({ projects, focus, tasks, taskReach, nowMs }: TodayTierFacts): TodayTiers {
  const registered = new Set(projects.map((project) => project.project_id));

  const focusIds: string[] = [];
  const unresolvedFocus: string[] = [];
  if (focus !== null) {
    for (const id of focus.project_ids) {
      // The server resolved these against the same registry the list came from, so a
      // miss here should not happen. If it does, the id is reported as unresolved rather
      // than dropped: it still holds a seat, and a seat that vanished from the screen
      // is a count the screen can no longer explain.
      (registered.has(id) ? focusIds : unresolvedFocus).push(id);
    }
    unresolvedFocus.push(...focus.unresolved_ids);
  }
  const inFocus = new Set(focusIds);

  const active = new Set<string>();
  // Projects with an open task whose timestamp could not be read. "Dormant" would be a
  // claim about that task's activity, and the task does not support one.
  const undated = new Set<string>();
  for (const task of tasks) {
    const activity = taskActivityOf(task, nowMs);
    if (activity === "recent") active.add(task.project_id);
    else if (activity === "unknown") undated.add(task.project_id);
  }

  const activeIds: string[] = [];
  const advancing: string[] = [];
  const dormant: string[] = [];
  const unplaced: string[] = [];
  const complete = taskReach === "complete";
  for (const project of projects) {
    const id = project.project_id;
    if (active.has(id)) activeIds.push(id);
    if (inFocus.has(id)) continue;
    if (active.has(id)) {
      advancing.push(id); // observed: provable from any page
    } else if (complete && !undated.has(id)) {
      dormant.push(id); // an absence, the read covered everything, and every stamp was readable
    } else {
      unplaced.push(id); // an absence the read cannot support
    }
  }

  return {
    focusReported: focus !== null,
    focus: focusIds,
    unresolvedFocus,
    active: activeIds,
    advancing,
    dormant,
    unplaced,
    undated: projects.map((project) => project.project_id).filter((id) => undated.has(id)),
  };
}

/**
 * The line under the focus heading. `used` counts unresolved ids because the server
 * does — they occupy seats until demoted. Fullness is the server's word, repeated: this
 * module never derives it from `used >= limit`, so the sentence on screen and the
 * refusal on promote can never disagree.
 */
export type FocusCapacity =
  | { kind: "unreported" }
  | { kind: "full"; used: number; limit: number }
  | { kind: "open"; used: number; limit: number };

export function focusCapacityOf(focus: TodayFocusLike | null): FocusCapacity {
  if (focus === null) return { kind: "unreported" };
  const used = focus.project_ids.length + focus.unresolved_ids.length;
  return focus.is_full
    ? { kind: "full", used, limit: focus.limit }
    : { kind: "open", used, limit: focus.limit };
}

/**
 * What a refused promotion means.
 *
 * `PUT /api/projects/{id}/focus` documents exactly one 409 — the tier is full — and it
 * carries `{error: "today_focus_full", limit}` as its detail. The shared error parser
 * reads a `code` field, not `error`, so the discriminator does not survive the trip;
 * the status is the fact this endpoint's contract lets the client rely on. If a second
 * 409 is ever added there, this mapping is where it has to be told apart.
 */
export type PromoteRefusal = "full" | "unknownProject" | "failed";

export function promoteRefusalOf(
  { status, code }: { status: number | null; code: string | null },
): PromoteRefusal {
  if (code === "today_focus_full") return "full";
  if (status === 409) return "full";
  if (status === 404) return "unknownProject";
  return "failed";
}

/**
 * A count the heading or a row may print.
 *
 * Exact only when the read behind it covered everything: "4 个在推进" over a capped task
 * read is a claim about tasks nobody read. A capped read still supports a floor — the
 * tasks on the page were read. A failed or unsettled read supports nothing: there is no
 * page, so "at least 0" would be true and a falsehood at once. Same contract as
 * `portfolioGroupCountOf` in lib/portfolio-bucket.ts, and separate variants so a caller
 * cannot print the number without having handled which kind it is.
 */
export type TierCount =
  | { kind: "exact"; count: number }
  | { kind: "atLeast"; count: number }
  | { kind: "unknown" };

function tierCountOf(count: number, taskReach: TaskInventoryReach): TierCount {
  if (taskReach === "complete") return { kind: "exact", count };
  if (taskReach === "capped") return { kind: "atLeast", count };
  return { kind: "unknown" };
}

export function advancingCountOf(active: number, taskReach: TaskInventoryReach): TierCount {
  return tierCountOf(active, taskReach);
}

/**
 * How many open tasks a compact row may claim for its project. The same rule: the
 * number of observed open tasks is provable from the page; that it is *all* of them is
 * not, unless the read was complete.
 */
export function openTaskCountOf(
  tasks: readonly TodayTaskLike[],
  projectId: string,
  taskReach: TaskInventoryReach,
): TierCount {
  let count = 0;
  for (const task of tasks) {
    if (task.project_id !== projectId) continue;
    if (CLOSED_STATES.has(task.state)) continue;
    count += 1;
  }
  return tierCountOf(count, taskReach);
}

/**
 * The most recently updated open task of a project — where "继续工作 →" goes. Under
 * adr/0007 a task is a continuing session, so opening the task is continuing the work;
 * this picks which one. `null` when there is none, and then the card offers only
 * "先看看这个项目" — a "continue" button with nothing behind it would start something
 * rather than continue it.
 *
 * "Most recently updated" is a claim about *all* of the project's open tasks, and the
 * global task page is ordered by priority, not by update time — so on a capped read the
 * newest one may be off the page and an older one would be named as "the latest". The
 * pick is therefore made only from a complete read; otherwise `null`, and the card
 * offers the workbench, which does not name a task.
 */
export function continueTaskOf<T extends TodayTaskLike & { task_id: string }>(
  tasks: readonly T[],
  projectId: string,
  taskReach: TaskInventoryReach,
): T | null {
  if (taskReach !== "complete") return null;
  let best: T | null = null;
  let bestMs = Number.NEGATIVE_INFINITY;
  for (const task of tasks) {
    if (task.project_id !== projectId) continue;
    if (CLOSED_STATES.has(task.state)) continue;
    const updated = Date.parse(task.updated_at);
    // One open task with an unreadable stamp and "the most recently updated" cannot be
    // established for this project at all — it might be that one.
    if (Number.isNaN(updated)) return null;
    if (best === null || updated > bestMs) {
      best = task;
      bestMs = updated;
    }
  }
  return best;
}

/** The goal line: the charter's sentence, or the plain fact that there is none yet. */
export type GoalLine = { kind: "goal"; text: string } | { kind: "none" };

export function goalLineOf(goal: string | null | undefined): GoalLine {
  const text = (goal ?? "").trim();
  return text.length > 0 ? { kind: "goal", text } : { kind: "none" };
}

/**
 * The card fields the mockup draws that this product has no read for. Each is a
 * capability slot (lib/nav-capability.ts), never a blank and never a guess: the mockup
 * itself says a project without a snapshot "留空不编数字", and the constitution says an
 * unfinished product may say "unknown" every day but must never say "none" for "could
 * not read". Listed here, once, so the card and its test agree on which fields these are.
 *
 *   milestone    the current milestone — the RESUME names it, no reader extracts it
 *   environment  where the project runs — environments exist (W2), projects do not name one
 *   gitState     branch · commit · pushed? — no git read on the project root
 *   lastTime     "昨天 / 上次" — recaps are per task; nothing rolls one up per project
 *   discovery    "发现" — uncommitted changes, stale worktrees; no read
 */
export const TODAY_CARD_NO_READER = {
  milestone: { kind: "noReader", reason: "reader_unbuilt" },
  environment: { kind: "noReader", reason: "reader_unbuilt" },
  gitState: { kind: "noReader", reason: "reader_unbuilt" },
  lastTime: { kind: "noReader", reason: "reader_unbuilt" },
  discovery: { kind: "noReader", reason: "reader_unbuilt" },
} as const satisfies Record<string, NavCapability>;

export type TodayCardNoReaderField = keyof typeof TODAY_CARD_NO_READER;

/**
 * "在册": the known defects. Real (docs/known-defects.md, D-1..D-6) but not yet in a form
 * this UI reads — W9 D-5 step B. The same slot the project nav shows, so the two surfaces
 * cannot say different things about one fact.
 */
export const TODAY_CARD_DEFECTS: NavCapability = NAV_DEFECTS_NO_SOURCE;

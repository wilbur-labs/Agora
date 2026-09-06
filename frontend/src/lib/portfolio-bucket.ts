import type { Page } from "./paged.ts";

// Which board column a task belongs to — the Portfolio's primary triage claim.
//
// Why this exists as its own module. The Portfolio derived every task's column from
// the parent's ONE capped, global open-attention page, and it kept only that page's
// `items`, discarding the `truncated` flag `listOpenAttention` already returns. Past
// the cap — or on a read that failed outright — a task's own question, approval or
// blocker falls off the page, and an empty kind set was read as "nothing is waiting
// on this task". The task was then filed by lifecycle state alone: a task waiting for
// your answer appeared under Running, or under Shaping, in the product's main triage
// surface. This is the same defect the Workspace rail had, in a second place, which
// is why it was recorded as a follow-up rather than assumed fixed by fixing the rail.
//
// The shape follows `rail-group.ts` deliberately, because the underlying rule is the
// same one: **presence is provable from any page, absence is not.** An item that is
// on the page is on the page, complete or not. Only the claim "there is no item" rests
// on the page having covered everything.
//
// Where this necessarily differs from the rail: the board has nine columns, not five,
// and its columns divide the work differently — `shaping` and `blocked` are separate
// destinations here, while the rail folds every non-calm state into `needsYou`. So the
// set of columns that *assert nothing is waiting* had to be decided for this surface
// rather than borrowed. That set is named once, below, as `ASSERTS_NOTHING_WAITING`.
//
// Deliberately free of React and of "@/" path aliases: `node --test` executes the
// companion test file directly (see tests/portfolio-bucket.test.mts).

/** The fields the bucketing needs from a task manifest; a structural subset. */
export interface PortfolioTaskLike {
  state: string;
}

/**
 * The board's columns, plus `unknown`.
 *
 * `unknown` is not a lifecycle state and not a kind of work: it is this read's own
 * limit, shown rather than rounded down to a calm column. A task is never dropped
 * from the board to express uncertainty — an invisible task is a worse failure than
 * an unsorted one, and "I could not tell" is something a triage board may say.
 */
export type PortfolioBucket =
  | "needsReply"
  | "needsApproval"
  | "blocked"
  | "unknown"
  | "running"
  | "verified"
  | "done"
  | "closed"
  | "shaping";

/** One section of the board, in render order, with the columns it holds. */
export interface PortfolioGroup {
  id: "action" | "unsorted" | "inProgress" | "completed";
  buckets: readonly PortfolioBucket[];
  /** Collapsed behind a disclosure by default; only the settled work is. */
  collapsible?: boolean;
}

/**
 * The board's sections, and the single declaration the page renders from.
 *
 * `unsorted` sits directly below the section that demands action and above every
 * section meaning "nothing to do here", because an uncertain task may in fact need a
 * human and must not be buried under settled work.
 *
 * This lived in the component while a flat `PORTFOLIO_ORDER` lived here and was
 * asserted by the tests — two declarations of the same decision, and they had already
 * drifted apart (this one said done, closed, shaping; the component rendered shaping
 * before the completed section). The test was passing about an order nothing
 * rendered. One declaration, imported by the page.
 */
export const PORTFOLIO_GROUPS: readonly PortfolioGroup[] = [
  { id: "action", buckets: ["needsReply", "needsApproval", "blocked"] },
  { id: "unsorted", buckets: ["unknown"] },
  { id: "inProgress", buckets: ["running", "verified", "shaping"] },
  { id: "completed", buckets: ["done", "closed"], collapsible: true },
];

/** The same thing flattened — derived, so it cannot disagree with what renders. */
export const PORTFOLIO_ORDER: readonly PortfolioBucket[] =
  PORTFOLIO_GROUPS.flatMap((group) => [...group.buckets]);

/**
 * The columns whose meaning includes "nothing is waiting on you for this task".
 *
 * Only these are downgraded when the attention read did not cover everything, and
 * the list is explicit rather than derived so that adding a column forces a decision
 * about it. `verified` and `done` are in it: a task can be finished *and* carry an
 * unanswered question, and filing it as finished is exactly the way that question
 * stops being seen. `shaping` is in it for the same reason — "this is still being
 * shaped" is a claim about there being nothing to answer right now.
 *
 * The three action columns are absent by construction, because reaching them never
 * depends on an absence: each is entered either on an *observed* item, or on a
 * lifecycle state that says so without consulting the page at all.
 */
const ASSERTS_NOTHING_WAITING: ReadonlySet<PortfolioBucket> = new Set<PortfolioBucket>([
  "running",
  "verified",
  "done",
  "closed",
  "shaping",
]);

/** What the board knows when it places one task. */
export interface PortfolioBucketFacts {
  task: PortfolioTaskLike;
  /** Open attention kinds OBSERVED for this task in that page. */
  kinds: ReadonlySet<string>;
  /**
   * Whether the global open-attention read covered every open item. False when the
   * page came back at its cap, when the read failed, and while it has not settled —
   * in all three an empty `kinds` is evidence of nothing.
   */
  attentionComplete: boolean;
}

export function portfolioBucketOf(
  { task, kinds, attentionComplete }: PortfolioBucketFacts,
): PortfolioBucket {
  // Observed items first, and unaffected by `attentionComplete`: an item that is on
  // the page is on the page whether or not the page was truncated.
  if (kinds.has("question")) return "needsReply";
  if (kinds.has("approval")) return "needsApproval";
  if (kinds.has("blocker")) return "blocked";
  // Then the lifecycle states that demand action on their own. These never consulted
  // the attention page, so a capped page cannot take them away.
  if (task.state === "review") return "needsApproval";
  if (task.state === "blocked" || task.state === "failed") return "blocked";

  const settled = bucketFromState(task.state);
  // Past here the column would say "nothing is waiting on you for this task", and
  // with no observed item that claim rests entirely on the page having been complete.
  if (!attentionComplete && ASSERTS_NOTHING_WAITING.has(settled)) return "unknown";
  return settled;
}

function bucketFromState(state: string): PortfolioBucket {
  if (state === "running") return "running";
  if (state === "verified") return "verified";
  if (state === "done") return "done";
  if (state === "cancelled") return "closed";
  return "shaping"; // backlog / requirements / design / planned
}

/**
 * How far the ONE global open-attention read got.
 *
 * The four outcomes are the rail's, for the same reason: `capped` and `unreadable`
 * are treated identically when bucketing, and treating them identically is not a
 * reason to *say* the same thing about them. "The page reached its read limit" and "I
 * could not read it" are different facts, and telling a user whose request failed
 * that a cap was reached is a specific falsehood. Note which fact `capped` is: the
 * page came back full, which a complete list of exactly that size also does, so it is
 * never "there was more than I read" — that is the claim these notices had to be
 * corrected for making.
 */
export type AttentionRead = "pending" | "complete" | "capped" | "unreadable";

/** Whether the read supports an "absence means nothing is waiting" claim. */
export function attentionReadCovers(read: AttentionRead): boolean {
  return read === "complete";
}

/**
 * How far the task read got — from that read's own settled outcome, the way
 * `attentionReachOf` does it for the attention page.
 *
 * `capped` exists because the task list is a page as well, and it is a **fact**: the
 * read asks for one row beyond the page it keeps (`lib/paged.ts`), so `truncated` means
 * a further row actually came back. The limit is not a parameter of this function any
 * more, so a caller cannot pair a page size with a different request size.
 *
 * It used to take the array and its limit and report `length >= limit` as capped, which
 * is one task pessimistic — a list of exactly 500 that is in fact complete — and never
 * optimistic. Safe, but it made a definite claim out of a guess, and the panels
 * downstream said "not fully read" about a read that had missed nothing (CR-28).
 *
 * `pending` is not reachable from this function — a settled read is no longer
 * pending — and exists for the same reason it does on the attention side: before any
 * read the board's emptiness is evidence of nothing, and saying "could not be read"
 * about a read that has not happened yet is a different falsehood. It means *the
 * board has not been given a result yet*, which is a fact about the load rather than
 * about this read: the page settles its four reads and publishes them in one go, so
 * this state ends when the whole first load lands, not when the task read returns.
 */
export type TaskInventoryReach = "pending" | "complete" | "capped" | "unreadable";

export function taskInventoryReachOf(
  result: PromiseSettledResult<Page<unknown>>,
): TaskInventoryReach {
  if (result.status === "rejected") return "unreadable";
  return result.value.truncated ? "capped" : "complete";
}

/**
 * Whether the board may tell the user that nothing needs them right now.
 *
 * Two reads stand behind that sentence, not one. The attention read has to have
 * covered every open item — that is `attentionReadCovers` above. The **task** read
 * has to have covered every task, because an empty action group is only reassuring
 * when the board knows which tasks exist. A failed task read leaves the board with
 * no tasks at all, and a capped one leaves it with a page; either way the tasks it
 * cannot see could be `review`, `blocked` or `failed`, each of which reaches an
 * action column without consulting the attention page at all.
 *
 * The first half of this used to hold by accident. All four reads shared one
 * `Promise.all` and one `catch`, so a failing task read also marked the attention
 * read unreadable and this line disappeared for the wrong reason. Settling the reads
 * independently was right and it removed that accident — and a *fulfilled* task read
 * was then mistaken for a complete one, which the cap makes false at 500 tasks.
 *
 * `projects` and `runs` are deliberately not in it: they decorate cards, and their
 * failure costs a stale detail, not the board's knowledge of what exists. What they
 * do cost is stated where it is rendered, not here.
 */
export function portfolioAllClearSayable(
  { attentionRead, taskInventory }: {
    attentionRead: AttentionRead;
    taskInventory: TaskInventoryReach;
  },
): boolean {
  return attentionReadCovers(attentionRead) && taskInventory === "complete";
}

/**
 * What a group's badge may say about how many cards are in it.
 *
 * `cards.length` is how many tasks *this board is holding*, and the board holds one
 * page: it reads `TASK_PAGE_LIMIT` rows through `readPage`, which asks for one more and
 * so knows whether a further task exists. Rendering that number as the group's size
 * turns "I can see this many" into "there are this many" — the same substitution the
 * attention notice above already exists to prevent, one element to its left. (Before
 * CR-28 the board could not tell the two apart at all: `listTasks` returned a bare
 * array and a full page was indistinguishable from "there is more".)
 *
 * So the count is exact only when **both** the task read and the open-attention read
 * were complete -- the second because which column a card is in depends on it -- and
 * "at least N" only
 * when it was **capped** — a capped page really was read and really did hold that
 * many. A *failed* read supports no lower bound at all: the board keeps its previous
 * snapshot, so those cards are what was true last time. Nor does a load still in
 * flight, where "at least 0" is true and useless. Both of those say nothing.
 *
 * The three outcomes are separate variants rather than a number plus a flag, so a
 * caller cannot render the number without having handled which kind it is.
 */
export type PortfolioGroupCount =
  | { kind: "unknown" }
  | { kind: "exact"; count: number }
  | { kind: "atLeast"; count: number };

export function portfolioGroupCountOf(
  cards: number,
  taskInventory: TaskInventoryReach,
  attentionRead: AttentionRead,
): PortfolioGroupCount {
  // **Two reads, not one.** The task inventory proves which tasks exist. It does not
  // prove which column they belong in: `portfolioBucketOf` sorts on the open-attention
  // page as well, and a task whose question lies past that page's cap is put under
  // "could not be sorted" rather than into a calm column. Counting on the inventory
  // alone therefore printed an exact "Needs you 0" beside a group whose real size the
  // board had no way to know — the same presence/absence boundary the bucketing itself
  // already respects, missed one element to its right.
  const reads = [taskInventory, attentionRead];
  if (reads.every((reach) => reach === "complete")) return { kind: "exact", count: cards };
  // A lower bound needs every read to have at least succeeded. `unreadable` keeps the
  // previous snapshot, so its cards are what was true last time; `pending` has no
  // result at all. And a floor of zero says nothing worth reading.
  if (reads.some((reach) => reach === "unreadable" || reach === "pending") || cards === 0) {
    return { kind: "unknown" };
  }
  return { kind: "atLeast", count: cards };
}

/**
 * The archived control's count, which depends on the task read and nothing else.
 *
 * Deliberately not `portfolioGroupCountOf`: whether a task is archived comes off the
 * task itself, so the attention read has no bearing on it, and passing a literal
 * "complete" to satisfy a parameter that does not apply is how a reach stops meaning
 * anything.
 */
export function archivedCountOf(
  cards: number,
  taskInventory: TaskInventoryReach,
): PortfolioGroupCount {
  if (taskInventory === "complete") return { kind: "exact", count: cards };
  if (taskInventory === "capped" && cards > 0) return { kind: "atLeast", count: cards };
  return { kind: "unknown" };
}

/**
 * What a task card may say about that task's runs.
 *
 * "No runs yet" is a statement about the task. The board arrives at it by looking for
 * the task in one page of runs — `listRuns({ limit })`, a bare array with the same cap
 * as the task list — so a task absent from that page has either no runs or runs the
 * board did not fetch, and those are different sentences. A failed read reaches the
 * same place from the other direction: no runs in hand, and nothing known.
 *
 * A run in hand is showable only when the read that produced it succeeded. A failed
 * refresh leaves the previous page in place, so the run in hand may have been queued
 * and finished since, and rendering it as the current state is a stale fact dressed as
 * a fresh one.
 */
export type TaskRunsLine = { kind: "run" } | { kind: "none" } | { kind: "unknown" };

export function taskRunsLineOf(
  hasLatestRun: boolean,
  runsReach: TaskInventoryReach,
): TaskRunsLine {
  // A failed *refresh* leaves the previous page of runs in place, so a run in hand is
  // not necessarily from the read whose reach this is: the retained run may have been
  // queued and since finished, and rendering it as the current state is a false
  // statement dressed as a fresh one. "A run in hand is always showable" was true only
  // of a read that succeeded.
  if (runsReach === "unreadable") return { kind: "unknown" };
  if (hasLatestRun) return { kind: "run" };
  return runsReach === "complete" ? { kind: "none" } : { kind: "unknown" };
}

/**
 * Whether the project cockpit's weight-derived tiles may be shown at all.
 *
 * `metrics.progress_pct`, `produced_weight/total_weight` and `health` are computed by
 * the backend over the dashboard's **first page of tasks**, and the response says so
 * in `truncated`. The page carried that flag for a footnote under one sentence and
 * rendered the tiles as whole-project facts beside it: on a project whose first five
 * hundred tasks are done, the cockpit could show 100% and a green health while task
 * five hundred and one was blocked.
 *
 * A percentage of an unstated subset is not a percentage of the project, and there is
 * no wording that repairs it — the same reason the middle tile stopped rendering
 * `verified_pct`. So the number is not rendered at all, and the tile says why.
 */
export function projectMetricsSayable(truncated: boolean): boolean {
  return !truncated;
}

/**
 * What the cockpit may say about the project's next action.
 *
 * Three outcomes, because there are three things that can be true, and the first
 * attempt at this only had two. It suppressed the dedicated "nothing needs you"
 * sentence when the read was partial and then rendered the enum label for
 * `action === "none"` in its place — which reads "Nothing to do". The claim survived
 * the guard by moving one line down, in different words. A guard on one sentence is
 * not a guard on the fact.
 *
 * And `partial` disqualifies a *named* action too, not only the empty one. The engine
 * picks the highest-ranked candidate on the page it was given; the backend supplements
 * off-page `blocked` and `failed` tasks but nothing else, so an off-page task ranking
 * above the page's winner — `answer_clarifications` at 80 over `start_requirements` at
 * 52 — leaves the board calling second place "the next action". Presence on a partial
 * page supports "an action found on the page", not "the project's next action";
 * absence supports neither.
 */
export type ProjectNextActionLine =
  | { kind: "allClear" }
  | { kind: "action" }
  | { kind: "unknown" };

export function projectNextActionLineOf(
  action: string,
  partial: boolean,
): ProjectNextActionLine {
  if (partial) return { kind: "unknown" };
  return action === "none" ? { kind: "allClear" } : { kind: "action" };
}

/**
 * What a project dashboard's rollups may be presented as.
 *
 * Three states, because the response and the page between them produce three, and
 * the workspace strip was collapsing all of them into "current":
 *
 *   stale    the refresh failed. The page keeps the dashboard it had -- which is
 *            right, blanking the strip on every failed refresh would be its own
 *            dishonesty -- but every number in it is from before the failure, and the
 *            only guard was an early return that fires when there is no dashboard at
 *            all. A retained one sailed straight past it.
 *   partial  the read succeeded and says so in `truncated`: its `tasks` are a page.
 *            `done/total` was being computed as `tasks.length`, so the denominator was
 *            the size of the page rather than of the project -- a fraction whose
 *            *whole* is wrong is worse than a missing one.
 *   current  neither.
 *
 * `truncated` is checked second on purpose: a stale response's own truncation flag is
 * as old as the rest of it, so "stale" is the honest answer for a failed refresh even
 * when the dashboard it kept had been complete.
 */
export type DashboardClaim = "current" | "partial" | "stale";

export function dashboardClaimOf(
  { readFailed, truncated }: { readFailed: boolean; truncated: boolean },
): DashboardClaim {
  if (readFailed) return "stale";
  return truncated ? "partial" : "current";
}

/**
 * Everything the workspace project strip may show, decided in one place.
 *
 * Written this way after a review pointed out that the previous shape -- a claim
 * value plus four render conditions in JSX -- could not be tested at all. This suite
 * cannot mount a .tsx component (node --test strips types, it does not transform
 * JSX), so a decision left in the markup has no test, and the source scan standing in
 * for one kept proving that calls existed rather than that their results controlled
 * anything. Every regression the reviewer demonstrated passed that scan.
 *
 * So each element gets a variant that carries exactly what may be rendered, and
 * nothing else. A caller cannot print a total that is not there.
 */
export type StripCount =
  | { kind: "exact"; done: number; total: number }
  | { kind: "atLeast"; done: number }
  | { kind: "hidden" };

export type StripAttention =
  | { kind: "exact"; count: number }
  | { kind: "atLeast"; count: number }
  | { kind: "hidden" };

/** The next-action line plus the two things the label needs, so the renderer does not
 *  have to reach back to the response for them. `action` and `title` exist only on the
 *  `action` variant: there is nothing to label on the other two. */
export type StripNext =
  | { kind: "allClear" }
  | { kind: "unknown" }
  | { kind: "action"; action: string; attention: boolean; title: string | null };

export interface WorkspaceProjectStrip {
  claim: DashboardClaim;
  count: StripCount;
  next: StripNext;
  attention: StripAttention;
}

export function workspaceProjectStripOf(
  facts: {
    readFailed: boolean;
    truncated: boolean;
    done: number;
    pageSize: number;
    action: string;
    partial: boolean;
    attentionCount: number;
    attention: boolean;
    title: string | null;
  },
): WorkspaceProjectStrip {
  const claim = dashboardClaimOf(facts);
  // `pageSize` is named for what it is. It was being passed as `total`, and printed
  // as the denominator of "{done}/{total} done" -- so the whole of that fraction was
  // the size of the page rather than of the project. A fraction whose whole is wrong
  // is worse than a missing one.
  const count: StripCount =
    claim === "current" ? { kind: "exact", done: facts.done, total: facts.pageSize }
    : claim === "partial" ? { kind: "atLeast", done: facts.done }
    : { kind: "hidden" };
  // A stale response cannot support either sentence: `action === "none"` is what the
  // engine found on the page it was given, and that page is from before the failure.
  const line = claim === "stale"
    ? ({ kind: "unknown" } as const)
    : projectNextActionLineOf(facts.action, facts.partial);
  const next: StripNext = line.kind === "action"
    ? { kind: "action", action: facts.action, attention: facts.attention, title: facts.title }
    : line;
  const attention: StripAttention =
    facts.attentionCount <= 0 || claim === "stale" ? { kind: "hidden" }
    : claim === "partial" ? { kind: "atLeast", count: facts.attentionCount }
    : { kind: "exact", count: facts.attentionCount };
  return { claim, count, next, attention };
}

/**
 * What the task summary's trust line may say.
 *
 * The failure is asked about first. Giving a retained `dashTask` precedence meant a
 * failed refresh still answered "no approved criteria yet" -- a plain absence claim
 * from a read that established nothing -- or spoke about the current criterion set
 * from a response that predates the failure.
 *
 * `unbound` is where a percentage used to be. `verified_weight / total_weight` marks
 * every criterion of the task's current spec verified from any historical worker
 * success, with no binding to the revision those criteria belong to; it was removed
 * from the portfolio card and the cockpit tile, and this was its third home.
 */
export type TaskTrustLine = "unavailable" | "loading" | "noCriteria" | "unbound";

export function taskTrustLineOf(
  { readFailed, hasTask, hasSpec }: { readFailed: boolean; hasTask: boolean; hasSpec: boolean },
): TaskTrustLine {
  if (readFailed) return "unavailable";
  if (!hasTask) return "loading";
  return hasSpec ? "unbound" : "noCriteria";
}

/**
 * The board notice's message key per outcome. `complete` is absent **by type**:
 * there is nothing to disclose about a complete read, and a key that could answer
 * for it would invite rendering a warning over good data.
 */
export const PORTFOLIO_ATTENTION_NOTICE_KEY: Record<
  Exclude<AttentionRead, "complete">, string
> = {
  pending: "portfolio.attentionRead.pending",
  capped: "portfolio.attentionRead.capped",
  unreadable: "portfolio.attentionRead.unreadable",
};

/**
 * The same, for the task read, and `complete` is absent by type for the same reason.
 *
 * Suppressing the reassurance was only half of that fix. A board that stops saying
 * "Nothing needs you right now" and says nothing in its place has kept the
 * uncertainty internally and dropped it at the display boundary: the user is left
 * with an empty action group and no way to know it is a page rather than an
 * inventory. The capped sentence is the one with no other home — a failed read is
 * also named in the load-error banner, a capped read is named nowhere else.
 */
export const PORTFOLIO_TASK_INVENTORY_NOTICE_KEY: Record<
  Exclude<TaskInventoryReach, "complete">, string
> = {
  pending: "portfolio.taskInventory.pending",
  capped: "portfolio.taskInventory.capped",
  unreadable: "portfolio.taskInventory.unreadable",
};

// There is deliberately no `attentionReadOf` here. This module had its own copy of
// "the reach comes from the attention read's own result", which is the same rule
// `rail-group.ts` already implements as `attentionReachOf` over the same four
// outcomes — and the caller then handed the copy a *combined* failure, so a failing
// tasks read was reported as an unreadable attention read. One rule, one
// implementation: the page computes the reach with `railReadReport`, and what stays
// here is only what is specific to this board — the nine-column mapping above, the
// notice keys, and `attentionReadCovers`.

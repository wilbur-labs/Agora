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
 * `capped` exists because the task list is a page as well. `listTasks` asks for
 * `TASK_PAGE_LIMIT` and gets back a bare array: no total, no `truncated`. A page
 * filled to the cap is therefore indistinguishable from "there is more", so it is
 * reported as capped — which can be one task pessimistic (a list of exactly 500 that
 * is in fact complete) and is never optimistic. The board loses a reassurance it was
 * entitled to; the alternative loses a blocked task.
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
  result: PromiseSettledResult<readonly unknown[]>,
  pageLimit: number,
): TaskInventoryReach {
  if (result.status === "rejected") return "unreadable";
  return result.value.length >= pageLimit ? "capped" : "complete";
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

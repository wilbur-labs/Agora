// One answer to "is a human gate open on this task?", used by everything on the
// page that claims to know.
//
// Why this exists as its own module: the Task Workspace made two claims about the
// same fact from two different sources. The Continue gate read the task-scoped
// list (`taskAttention`), where null means "no answer yet, or the read failed",
// and closed with `attentionUnknown`. The Attention panel, given the same null,
// fell back to the *parent's* capped global list — a projection that is not
// authoritative for one task, and that is empty for most tasks — and rendered
// "nothing needs you on this task". So one page said "nothing is waiting" and
// "I cannot tell whether anything is waiting" at the same time, about one task,
// from one screen.
//
// The fix is not a better fallback. It is that both readers derive from one
// function over one set of facts, so a disagreement is no longer expressible.
//
// Deliberately free of React and of "@/" path aliases: `node --test` executes the
// companion test file directly, and this is where the project's standing rule is
// decided again — an unknown must never render as an absence.

/** The fields this module needs from an attention item; a structural subset. */
export interface AttentionLike {
  item_id: string;
  state: string;
}

/**
 * What the task-scoped attention read has produced so far.
 *
 * `items === null` and `readFailed` are different facts and both are kept: "no
 * answer yet" is a spinner, "the read failed" is an error, and collapsing them
 * would put a permanent spinner where an error belongs.
 *
 * `truncated` means the page came back at the limit, so an item may have been cut
 * off. "Not in a page that may be truncated" is not "not there".
 */
export interface TaskAttentionFacts<T extends AttentionLike> {
  items: readonly T[] | null;
  readFailed: boolean;
  truncated: boolean;
}

/**
 * The one derived answer. `open` carries the items so the panel renders exactly
 * what the gate counted, rather than a second filtering of a second list.
 *
 * `partial` is the awkward one and is why this is not a boolean: the read
 * succeeded and the items are real and worth showing, but the *count* cannot be
 * trusted, so the panel shows them while the gate treats the count as unknown.
 * A single "loaded" state could not express that without lying to one of the two.
 */
export type TaskAttentionView<T extends AttentionLike> =
  | { kind: "loading" }
  | { kind: "unreadable" }
  | { kind: "partial"; items: readonly T[] }
  | { kind: "none" }
  | { kind: "open"; items: readonly T[] };

export function taskAttentionView<T extends AttentionLike>(
  facts: TaskAttentionFacts<T>,
): TaskAttentionView<T> {
  // Checked before `items === null`, because a failed read also leaves items null
  // and "unavailable" is the more specific — and more actionable — thing to say.
  if (facts.readFailed) return { kind: "unreadable" };
  if (facts.items === null) return { kind: "loading" };
  // Only OPEN items are a gate. A responded, cancelled, or expired item is one
  // already dealt with, and the request that produced this list asked for open
  // items — so this filter is a second guard, not the load-bearing one.
  const open = facts.items.filter((item) => item.state === "open");
  if (facts.truncated) return { kind: "partial", items: open };
  return open.length === 0 ? { kind: "none" } : { kind: "open", items: open };
}

/**
 * How many open gates this task has, or null when that is not known.
 *
 * The value `continueEntryGate` consumes as `openAttentionCount`, derived from the
 * same view the panel renders — which is the whole point of this module. Both
 * "still loading" and "the read failed" are null: dispatching a second paid run on
 * a guess is the one outcome a refresh cannot undo.
 */
export function openGateCount<T extends AttentionLike>(
  view: TaskAttentionView<T>,
): number | null {
  switch (view.kind) {
    case "none":
      return 0;
    case "open":
      return view.items.length;
    default:
      // loading / unreadable / partial: unknown. `partial` is included because a
      // page that may have been cut off cannot say how many items exist — and the
      // one it dropped could be the approval that must block Continue.
      return null;
  }
}

/** The panel's message key for the three states that show no list. */
export const TASK_ATTENTION_NOTICE_KEY: Record<"loading" | "unreadable" | "none", string> = {
  loading: "workspace.panels.attention.loading",
  unreadable: "workspace.panels.unavailable",
  none: "workspace.panels.attention.none",
};

/** An attention item as the blocker line needs it: a kind to select on, a title to show. */
export interface BlockerLike extends AttentionLike {
  kind: string;
  title: string;
}

/**
 * What this task's summary may say about a blocker.
 *
 * `loading` and `unknown` are both "no answer", kept apart because they read
 * differently to a person: a spinner-shaped dash while the first read is in
 * flight, "unavailable" once the read has failed or come back possibly cut off.
 * Neither may render as "nothing is blocking this task" — that is the same
 * substitution `taskAttentionView` exists to prevent, and it arrived here as a
 * `blocker` prop derived from the parent's capped *global* attention page: the
 * global read and the task-scoped read disagreed in both directions (a resolved
 * blocker still shown, and a real blocker beyond the global page not shown at
 * all), on one screen, about one task.
 */
export type TaskBlockerClaim =
  | { kind: "loading" }
  | { kind: "unknown" }
  | { kind: "none" }
  | { kind: "blocker"; title: string };

/**
 * The blocker line, derived from the same view the panel and the Continue gate use.
 *
 * The first open blocker in the task-scoped read, by the order the server returned
 * (oldest first), so the line names the item that has been holding the task rather
 * than whichever one sorted first in some other list.
 *
 * `partial` splits: a blocker *found* in a page that may be cut off is a real
 * blocker and is named, but *not finding* one there proves nothing, so that is
 * `unknown`. Same asymmetry as `openGateCount`, for the same reason — a truncated
 * page can confirm presence, never absence.
 */
export function taskBlockerClaim<T extends BlockerLike>(
  view: TaskAttentionView<T>,
): TaskBlockerClaim {
  switch (view.kind) {
    case "loading":
      return { kind: "loading" };
    case "unreadable":
      return { kind: "unknown" };
    case "none":
      return { kind: "none" };
    default: {
      const found = view.items.find((item) => item.kind === "blocker");
      if (found) return { kind: "blocker", title: found.title };
      return view.kind === "partial" ? { kind: "unknown" } : { kind: "none" };
    }
  }
}

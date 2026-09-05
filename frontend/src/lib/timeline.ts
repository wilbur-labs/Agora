// What the task timeline may say, and what it may say it about.
//
// The timeline answers one question that is easy to get wrong: *is this the task's
// history?* It reaches an answer from two reads (events and runs), and every way of
// being wrong about it has now been seen in this file's history:
//
//   - `loadEvents` turned every rejection into an empty array with no error bit, so a
//     failed read rendered as "No activity yet" — a claim about the task made from a
//     read that established nothing — and it also erased notes already on screen, so
//     a transient failure looked like the task losing its past.
//   - The first fix put the state behind a helper and left the raw read facts in the
//     component's hands. Review then wrote four separate defects against the result:
//     the helper took one read's landing and its docstring claimed both; the run
//     page's cap never reached it; and the incompleteness notice rendered beside
//     "could not be read", so the column said both at once.
//
// So the read facts do not come here as loose booleans a caller has to combine
// correctly. Each read arrives as a `TimelineRead` — its reach *and* what it
// returned, in one value — and the answer comes back as a `TimelineVerdict`, a
// discriminated union in which the incompleteness notice exists **only** on the
// variant that carries entries. The combinations review found are not fixed here;
// they are unrepresentable. `TimelineColumn` receives the verdict and never the
// events, the runs, or the error bits, so it cannot compute a second opinion.
//
// The reach vocabulary is the house one, from `AttentionReach` and
// `TaskInventoryReach`, and it is spelled with five variants here. `partial` merges two
// of the house's four, because the timeline needs no distinction between a page that
// hit its cap and a page that lost some of its details: in neither case can the page
// vouch for having all of them, and one sentence serves both. `behind` is the addition
// — a list already on screen that the page *knows* is missing an entry, which
// `complete` would otherwise have covered, claiming the list was whole in the very
// moment the user was looking for a note they had just posted. Note the epistemic wording — a page that
// came back at its cap is *indistinguishable from* a history of exactly that size, so
// "there is more" is a claim this module is never entitled to make.
//
// The single invariant, and the one worth checking every time this file changes:
// **`empty` requires both reads complete.** Nothing else in the union is a claim
// about the task.
//
// Deliberately free of React and of "@/" path aliases: `node --test` executes the
// companion test file (tests/timeline.test.mts) directly.

import type { ColumnRunLike } from "./run-column.ts";
import { runTreeOf, type RunUnit, type UnplacedRun } from "./run-tree.ts";

/** The event fields the timeline orders and keys by. */
export interface TimelineEventLike {
  event_id: string;
  event_type: string;
  created_at: string;
  /** Read only to match an attention event to its item. See `eventsLackKnown`. */
  payload?: Record<string, unknown> | null;
}

/** An attention item as the two reads that return them describe it. */
export interface AttentionItemLike {
  item_id: string;
  state: string;
}

/**
 * The task events that record an attention item in each state. Every transition writes
 * an event carrying the item id in its payload, but *which* event is not one per state.
 *
 * Enumerated from the backend's event **names**, not from its call sites:
 *
 *     grep -rhoE '"attention\.[a-z_]+"' backend/agora --include=*.py | sort -u
 *
 * yields six, five of which record a state. The sixth, `attention.delivery_failed`, is
 * a bridge delivery outcome rather than a transition, so it belongs to no state here —
 * and so does `attention.delivery_delivered`, which **that grep does not find**:
 * `finish_delivery` builds the name as `f"attention.delivery_{state.value}"`
 * (attention/store.py:322), so a literal search sees only the failing half. State the
 * limit rather than the result: enumerating names finds every *literal* one, and a
 * constructed name has to be read out of its writer. Against the full set this map is
 * complete over `AttentionState` (open / responded / cancelled / expired), both
 * delivery outcomes being excluded for the same reason.
 *
 * Counting writers is what got this wrong twice. First `open` was assumed to have one:
 * `create_bridge_event` (attention/store.py:269) opens an item with
 * `attention.bridge_captured` and no `attention.created` at all, so requiring the
 * latter reported a vendor-bridged gate as a missing entry for as long as it stayed
 * open — a caveat that could never clear. Then the correction said `open` has *two*
 * writers, which is also false: `ControlPlaneStore` opens items in two more places
 * (control_plane/store.py:4140, 4224), and cancellation has a writer in
 * attention/run_scope.py:68 as well. Both of those emit names already in the map, so
 * the map survived being justified by a miscount — which is exactly why the
 * justification is now the enumerable thing rather than the tally.
 */
const ATTENTION_STATE_EVENTS: Record<string, readonly string[]> = {
  open: ["attention.created", "attention.bridge_captured"],
  responded: ["attention.responded"],
  cancelled: ["attention.cancelled"],
  expired: ["attention.expired"],
};

/**
 * The sentence each attention event gets in the timeline (CR-27).
 *
 * Every name in `ATTENTION_STATE_EVENTS` plus the two delivery outcomes, because the
 * column renders events and not states: it used to branch on
 * `type === "attention.responded"` alone and render everything else as "Needs you", so
 * a cancelled item, an expired one and a *successfully delivered* answer each told the
 * user they had something to do.
 *
 * `attention.delivery_delivered` is here despite being absent from a literal search of
 * the backend — `finish_delivery` builds it as `f"attention.delivery_{state.value}"`
 * (attention/store.py:322). That is the same trap the state map fell into: enumerate
 * names, then read the writers that construct them.
 */
export const ATTENTION_TIMELINE_EVENT_KEY: Record<string, string> = {
  "attention.created": "workspace.timeline.events.attentionRaised",
  // A bridged item is an open item, so it is raised: same sentence.
  "attention.bridge_captured": "workspace.timeline.events.attentionRaised",
  "attention.responded": "workspace.timeline.events.attentionResponded",
  "attention.cancelled": "workspace.timeline.events.attentionCancelled",
  "attention.expired": "workspace.timeline.events.attentionExpired",
  "attention.delivery_delivered": "workspace.timeline.events.attentionDelivered",
  "attention.delivery_failed": "workspace.timeline.events.attentionDeliveryFailed",
};

/** Exposed for the test that checks every recorded state also has a sentence. */
export const ATTENTION_STATE_EVENTS_FOR_TEST = ATTENTION_STATE_EVENTS;

function attentionItemIdOf(event: TimelineEventLike): string | null {
  const raw = (event.payload ?? {})["item_id"];
  return typeof raw === "string" ? raw : null;
}

/**
 * Whether the page holds an attention item whose event is not in the event list.
 *
 * This is how the timeline learns it is behind, and it is a **derivation, not a flag**.
 * The previous attempt set a flag wherever the page discovered something the list
 * lacked, and review found the discovery paths one round at a time — a note posted, a
 * run settling, a gate raised mid-run, a gate answered elsewhere — because "remember
 * to mark it here" is a rule that has to be obeyed at every call site, and the last
 * version of it also raced with the state update it was compared against. Derived from
 * the two values instead, there is no call site to miss and nothing to clear: the
 * moment the events read lands with the event, the answer becomes false by itself.
 *
 * Only *presence* is ever used as evidence, so a capped or failed read can never make
 * this claim something: absence from any of these lists means nothing here.
 *
 * **What is fed in, exactly** — the wider version of this sentence ("everything the
 * page holds that implies an event") was itself the defect this module exists to
 * prevent, and review found it one round after it was written:
 *
 *   - the three attention lists, all `AttentionItem[]`: the task-scoped read, the
 *     review gate's run-scoped read, and the rail's global read narrowed to this task;
 *   - `knownEventIds` — events the control plane confirmed to this page directly,
 *     which is how a note the user just posted is covered.
 *
 * **What is not**, and it is a real gap rather than a rounding of one: the page also
 * holds `specs`, the recap, and the delivery read, and each is backed by task events
 * (every spec state writes one; `fetchDelivery` performs its own event read). None of
 * them reaches this comparison, so a spec approved or a note posted by *another*
 * client can be named by the recap beside a timeline showing no caveat. `specs` and
 * the delivery read could be fed the same way; the recap could not without a backend
 * change, because it publishes summaries rather than the event ids behind them. Tracked
 * as its own change-request row, not as a sentence here — see CR-26 in the task record.
 *
 * And what no version of this establishes: that nothing has happened which no read has
 * mentioned yet. No snapshot can promise that, and `partial: null` does not claim it.
 */
export function eventsLackKnown(
  events: readonly TimelineEventLike[] | null,
  items: readonly (readonly AttentionItemLike[] | null)[],
  knownEventIds: readonly string[] = [],
): boolean {
  if (events === null) return false;
  if (knownEventIds.length > 0) {
    const present = new Set(events.map((event) => event.event_id));
    if (knownEventIds.some((id) => !present.has(id))) return true;
  }
  const recorded = new Map<string, Set<string>>();
  for (const event of events) {
    const id = attentionItemIdOf(event);
    if (id === null) continue;
    const types = recorded.get(id) ?? new Set<string>();
    types.add(event.event_type);
    recorded.set(id, types);
  }
  return items.some((list) => (list ?? []).some((item) => {
    const expected = ATTENTION_STATE_EVENTS[item.state];
    // An unrecognised state is not evidence of a missing event: a state this build
    // does not know about is a fact about this build, not about the list.
    if (expected === undefined) return false;
    const seen = recorded.get(item.item_id);
    return !expected.some((type) => seen?.has(type) ?? false);
  }));
}

/**
 * Whether the page holds an active run the run history does not.
 *
 * The same derivation for the other list, over every list of this task's runs the page
 * holds: the active-run read is answered from the active set rather than from the
 * capped history page, and the rail's global read is its own snapshot, so either can
 * know of a run before the history read lands. A flag set from inside those reads raced
 * with the history's own state update instead, leaving "still checking" on screen until
 * the next full read.
 *
 * Presence only, again: none of these lists is authoritative about absence, and a
 * history page at its cap is `partial` by then anyway, which outranks this.
 */
export function runsLackKnown(
  runs: readonly { run_id: string }[] | null,
  seen: readonly (readonly { run_id: string }[] | null)[],
): boolean {
  if (runs === null) return false;
  const held = new Set(runs.map((run) => run.run_id));
  return seen.some((list) => (list ?? []).some((run) => !held.has(run.run_id)));
}

/**
 * How far one of the timeline's two reads got, together with what it returned.
 *
 * One value rather than an array plus flags, because the pairs that do not exist
 * should not be constructible: no state carries a list it has no business having, and
 * none that has one lacks it. Note which pairs those are — a *failed refresh* is
 * `partial` **with** the list it retained, and only a read that has produced nothing at
 * all (`pending`, `unreadable`) carries none. `pending` is the state before a read has
 * landed at all: the emptiness of a screen that has not been given an answer yet is
 * evidence of nothing, and "could not be read" about a read still in flight is a
 * different falsehood.
 */
export type TimelineRead<T> =
  /** No answer yet, and nothing from before. */
  | { reach: "pending" }
  | { reach: "unreadable" }
  /**
   * Landed, and known to be missing an entry — the page holds an attention item or an
   * active run whose record is not in this list (`eventsLackKnown` / `runsLackKnown`).
   * Separate from `complete` because the two say different things about the same array,
   * and the difference is exactly what the user sees after posting a note.
   *
   * The boundary is narrower than "a read is in flight", on purpose. What the timeline
   * claims by showing no caveat is that *the read which produced this list left
   * nothing unread* — not that nothing has happened since, which no snapshot can
   * promise. So an ordinary refresh over a complete answer does not reach this state;
   * knowing of an entry the list lacks does.
   */
  | { reach: "behind"; items: readonly T[] }
  /**
   * Landed, without establishing that this is all of it: a page at its cap, dropped
   * details, or a failed refresh whose predecessor is still on screen. Not "there is
   * more" — a page at its cap looks exactly like a history of that size.
   */
  | { reach: "partial"; items: readonly T[] }
  /** Landed whole, with nothing known to be missing. The only vouched-for state. */
  | { reach: "complete"; items: readonly T[] };

/**
 * One read's state, from the facts a caller actually holds about it.
 *
 * Kept here rather than at the call site because this is the one place where those
 * facts still exist separately, and mapping them is exactly what a component was
 * getting wrong. `capped` is a separate input from `failed` for the caller's benefit —
 * the run page's cap and its detail-read failures are tracked separately upstream —
 * but the timeline treats them the same, so both land on `partial`.
 *
 * A standing gap outranks a known-missing entry: `failed || capped` is checked before
 * `behind`, because "this may not be the whole history" is true now, while the read on
 * its way may or may not repair it.
 */
export function timelineReadOf<T>(
  items: readonly T[] | null,
  { failed, capped, behind }:
    { failed: boolean; capped: boolean; behind: boolean },
): TimelineRead<T> {
  if (items === null) return failed ? { reach: "unreadable" } : { reach: "pending" };
  if (failed || capped) return { reach: "partial", items };
  return behind ? { reach: "behind", items } : { reach: "complete", items };
}

/** One entry in the stream. `at` orders it, `key` identifies it. */
export type TimelineItem<E extends TimelineEventLike, R extends ColumnRunLike> =
  | { kind: "event"; at: string; key: string; event: E }
  | { kind: "unit"; at: string; key: string; unit: RunUnit<R> }
  | { kind: "unplaced"; at: string; key: string; unplaced: UnplacedRun<R> };

/**
 * Why the entries on screen may not be the whole history — three reasons, because they
 * are three different sentences, and every time two of them were collapsed the notice
 * said something untrue. "This may not be the whole history" on every ordinary load
 * (entries land before the second read does) trains the reader to ignore the one case
 * that matters; "still checking" about a gap that nothing is fetching is simply false.
 */
export type TimelinePartial =
  /** A read landed without vouching for completeness. The standing one. */
  | "incomplete"
  /**
   * The page holds a record this list does not contain — `eventsLackKnown` /
   * `runsLackKnown`. A specific, closeable gap, so the page fetches it; what it must
   * not claim is that the fetch is what the reader is waiting on, because the same
   * state is reached from a poll that refreshes something else entirely.
   */
  | "missing"
  /**
   * Another read has not landed yet. Note what this does *not* say: not that more will
   * arrive (the read may come back with nothing), and not that it will arrive at all
   * (the loaders carry no timeout). Only that a read is out.
   */
  | "loading";

/**
 * What the timeline column renders.
 *
 * `partial` is a field of the `entries` variant and of no other. That is the point of
 * the union: "this may not be the whole history" is a caveat *on entries that are
 * showing*, and the column used to render it beside "could not be read" and beside
 * "no activity yet" — two notices about the same absence, one of them wrong. It also
 * cannot be forgotten on the branch where it belongs, because the branch carries it.
 */
export type TimelineVerdict<E extends TimelineEventLike, R extends ColumnRunLike> =
  /** Nothing to show yet and nothing has failed: a read is still in flight. */
  | { show: "loading" }
  /** A read did not fully land and there is nothing to show: not "nothing happened". */
  | { show: "unknown" }
  /**
   * Nothing to show, and the page holds a record that is not here — the empty-list
   * counterpart of `partial: "missing"`. Its own variant because the alternative was
   * folding it into `loading`, which review round 11 caught: the gap's fetch can
   * succeed without closing the gap, and the ellipsis then stood for a request that
   * had already finished. Four silences, four sentences, none of them borrowed.
   */
  | { show: "missing" }
  /** Both reads complete, nothing came back. The only variant that claims a fact. */
  | { show: "empty" }
  | {
    show: "entries";
    items: readonly TimelineItem<E, R>[];
    partial: TimelinePartial | null;
  };

function returned<T>(read: TimelineRead<T>): readonly T[] {
  return read.reach === "pending" || read.reach === "unreadable" ? [] : read.items;
}

/** A gap that stands right now, whatever the read in flight comes back with. */
function landedShort(reach: TimelineRead<unknown>["reach"]): boolean {
  return reach === "unreadable" || reach === "partial";
}

/** An answer is on its way: none in hand yet, or one known to be missing an entry. */
function inFlight(reach: TimelineRead<unknown>["reach"]): boolean {
  return reach === "pending" || reach === "behind";
}

export function timelineVerdictOf<E extends TimelineEventLike, R extends ColumnRunLike>(
  events: TimelineRead<E>,
  runs: TimelineRead<R>,
): TimelineVerdict<E, R> {
  const items = timelineItemsOf(returned(events), returned(runs));

  // A gap that stands, and a read still on its way, are both reasons the list may be
  // short — but only the first is a standing one, so they are told apart everywhere
  // below. A list known to be missing an entry counts as on its way even though it is
  // on screen: that is exactly the state in which the user has just posted a note and
  // cannot find it.
  const short = landedShort(events.reach) || landedShort(runs.reach);
  const known = events.reach === "behind" || runs.reach === "behind";
  const waiting = inFlight(events.reach) || inFlight(runs.reach);

  // Entries that have landed are shown, whatever the other read is doing. Withholding
  // them until every read landed was the previous behaviour here, and it hid readable
  // notes behind an ellipsis for as long as the run read took — which resolves a
  // detail request per run, so: a while.
  if (items.length > 0) {
    return {
      show: "entries",
      items,
      partial: short ? "incomplete" : known ? "missing" : waiting ? "loading" : null,
    };
  }

  // Nothing to show, and four different silences, in the same precedence as the
  // caveats above. A read that landed short outranks the rest: its answer is already
  // in, and `loading` would promise a resolution that is not coming for it. A known
  // gap outranks a read merely being out, for the reason review round 11 gave — the
  // fetch for it can succeed without closing it, and then nothing is in flight at all,
  // so an ellipsis would be standing for a finished request.
  if (short) return { show: "unknown" };
  if (known) return { show: "missing" };
  if (waiting) return { show: "loading" };
  return { show: "empty" };
}

// The run.* lifecycle events are noise once the actual run cards are shown, so they
// are dropped, and one work unit per worker run is interleaved by its queue time, plus
// any run the tree could not place.
//
// Runs appear as **work units**, not as process launches. A worker run is a top-level
// entry and the review run(s) recorded against it are nested inside it — no connector
// lines, which is the recorded decision and its reason (the relation is already in the
// data, and drawn lines become spaghetti). Ordering is by work unit: a unit sits at
// its worker run's queue time, and a review does not get its own slot in the stream.
//
// The third entry kind is the one that keeps this honest. `runTreeOf` returns runs it
// could not place — a review whose reviewed run is past the run cap or lost to a
// failed detail read, or one that records no reviewed run at all — and they are
// rendered, with the reason. Dropping them would turn a partial read into a
// disappearance.
export function timelineItemsOf<E extends TimelineEventLike, R extends ColumnRunLike>(
  events: readonly E[],
  runs: readonly R[],
): readonly TimelineItem<E, R>[] {
  const out: TimelineItem<E, R>[] = [];
  for (const e of events) {
    if (e.event_type.startsWith("run.")) continue;
    out.push({ kind: "event", at: e.created_at, key: e.event_id, event: e });
  }
  const tree = runTreeOf(runs);
  for (const unit of tree.units) {
    out.push({ kind: "unit", at: unit.run.queued_at, key: unit.run.run_id, unit });
  }
  for (const unplaced of tree.unplaceable) {
    out.push({ kind: "unplaced", at: unplaced.run.queued_at, key: unplaced.run.run_id, unplaced });
  }
  // One sort over the whole stream, and the run tie-break has to be applied here to be
  // the global rule the tree documents: units and unplaceable runs are pushed as two
  // groups, so on a shared queue time a timestamp-only sort would order a top-level
  // run against an unplaceable review by which group was appended first, not by run
  // id. Events are not runs and keep their arrival order on a tie
  // (Array.prototype.sort is stable).
  return out.sort((a, b) => {
    const byTime = a.at.localeCompare(b.at);
    if (byTime !== 0) return byTime;
    // `key` is the run id for both run-bearing kinds.
    return a.kind === "event" || b.kind === "event" ? 0 : a.key.localeCompare(b.key);
  });
}

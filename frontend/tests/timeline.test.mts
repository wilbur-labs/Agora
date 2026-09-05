import assert from "node:assert/strict";
import test from "node:test";

import {
  RUN_REVIEWS_KEY,
  RUN_ROLE_KEY,
  ROLE_REVIEW,
  type ColumnRunLike,
} from "../src/lib/run-column.ts";
import {
  ATTENTION_TIMELINE_EVENT_KEY,
  ATTENTION_STATE_EVENTS_FOR_TEST,
  eventsLackKnown,
  runsLackKnown,
  timelineItemsOf,
  timelineReadOf,
  timelineVerdictOf,
  type TimelineEventLike,
  type TimelineRead,
  type TimelineVerdict,
} from "../src/lib/timeline.ts";
import { workspace as workspaceDictionary } from "../src/lib/i18n/dictionaries/workspace.ts";

type Locale = "en" | "zh";

function lookup(locale: Locale, key: string): string | undefined {
  const parts = key.split(".");
  assert.equal(parts.shift(), "workspace", `key must be in the workspace namespace: ${key}`);
  let node: unknown = workspaceDictionary[locale];
  for (const part of parts) {
    if (!node || typeof node !== "object" || !(part in (node as Record<string, unknown>))) {
      return undefined;
    }
    node = (node as Record<string, unknown>)[part];
  }
  return typeof node === "string" ? node : undefined;
}

let seq = 0;

function ev(over: Partial<TimelineEventLike> = {}): TimelineEventLike {
  seq += 1;
  return {
    event_id: over.event_id ?? `ev-${seq}`,
    event_type: over.event_type ?? "user_message",
    created_at: over.created_at ?? `2026-09-05T00:00:${String(seq).padStart(2, "0")}Z`,
  };
}

function run(over: Partial<ColumnRunLike> = {}): ColumnRunLike {
  seq += 1;
  const at = over.queued_at ?? `2026-09-05T00:00:${String(seq).padStart(2, "0")}Z`;
  return {
    run_id: over.run_id ?? `run-${seq}`,
    state: over.state ?? "succeeded",
    adapter: over.adapter ?? "codex",
    queued_at: at,
    started_at: over.started_at ?? at,
    finished_at: over.finished_at ?? at,
    result_metadata: over.result_metadata,
  };
}

const REACHES = ["pending", "unreadable", "behind", "partial", "complete"] as const;

function reading<T>(reach: (typeof REACHES)[number], items: readonly T[]): TimelineRead<T> {
  return reach === "pending" || reach === "unreadable" ? { reach } : { reach, items };
}

// --- CR-22: what the timeline may say -----------------------------------------

test("a read's state comes from the three facts the caller holds, in one place", () => {
  const at = (items: readonly number[] | null, over: Partial<Parameters<typeof timelineReadOf>[1]> = {}) =>
    timelineReadOf(items, { failed: false, capped: false, behind: false, ...over });

  assert.deepEqual(at(null), { reach: "pending" });
  assert.deepEqual(at(null, { failed: true }), { reach: "unreadable" });
  assert.deepEqual(at([]), { reach: "complete", items: [] });
  // Both flavours of "landed without vouching for completeness" reach the same state:
  // a page at its cap, and a page whose refresh failed or whose details dropped.
  assert.equal(at([1], { capped: true }).reach, "partial");
  assert.equal(at([1], { failed: true }).reach, "partial");
  assert.equal(at([1]).reach, "complete");
  // A list the page knows is missing an entry is its own state, not `complete` --
  // that is what the screen looks like right after a note is posted.
  assert.equal(at([1], { behind: true }).reach, "behind");
  // …and nothing in hand at all is still just `pending`.
  assert.deepEqual(at(null, { behind: true }), { reach: "pending" });
  // A standing gap outranks a known-missing entry: the caveat true now wins.
  assert.equal(at([1], { failed: true, behind: true }).reach, "partial");
  assert.equal(at([1], { capped: true, behind: true }).reach, "partial");
});

test("a list known to be missing an entry is never presented as the whole history", () => {
  // Review round 5. The concrete trigger: posting a note succeeds, the draft clears,
  // `loadEvents` starts — and until it lands the timeline was rendering the old
  // entries with no caveat at all, i.e. asserting that a list provably missing the
  // note the user just wrote was the current history. `partial: null` is a claim, and
  // this change is the one that introduced it, so it owns being right.
  //
  // What this test does NOT establish, per review round 6: that the page marks every
  // state where it learns of a missing entry. It constructs the `behind` read itself.
  // Which call sites set it is a property of the component, and the component cannot
  // be mounted in a test here at all ([[CR-18]]); round 6 found two unmarked
  // discovery paths while this test was green.
  const verdict = timelineVerdictOf(
    { reach: "behind", items: [ev()] }, { reach: "complete", items: [run()] },
  );
  assert.equal(verdict.show, "entries");
  assert.equal(verdict.show === "entries" && verdict.partial, "missing");
});

// With nothing to show there are four different silences, and which one is right is
// decided by precedence between the two reads. Written out as a literal table rather
// than as a rule, because a rule here would just be the implementation typed twice:
// round 3 of review found that reordering those checks let an established failure sit
// behind an ellipsis, and no test had noticed. Any reordering turns a cell red.
const SILENCE: Record<
  (typeof REACHES)[number], Record<(typeof REACHES)[number], TimelineVerdict<never, never>["show"]>
> = {
  pending:    { pending: "loading", unreadable: "unknown", behind: "missing", partial: "unknown", complete: "loading" },
  unreadable: { pending: "unknown", unreadable: "unknown", behind: "unknown", partial: "unknown", complete: "unknown" },
  behind:     { pending: "missing", unreadable: "unknown", behind: "missing", partial: "unknown", complete: "missing" },
  partial:    { pending: "unknown", unreadable: "unknown", behind: "unknown", partial: "unknown", complete: "unknown" },
  complete:   { pending: "loading", unreadable: "unknown", behind: "missing", partial: "unknown", complete: "empty" },
};

test("with nothing to show, each pairing of the two reads has one right silence", () => {
  for (const events of REACHES) {
    for (const runs of REACHES) {
      const verdict = timelineVerdictOf<TimelineEventLike, ColumnRunLike>(
        reading(events, []), reading(runs, []),
      );
      assert.equal(verdict.show, SILENCE[events][runs], `events=${events} runs=${runs}`);
    }
  }
});

test("\"no activity yet\" requires both reads complete, in every combination", () => {
  // The invariant this module exists for. `empty` is the only variant that states a
  // fact about the task, so it may appear only when nothing was left unread — and the
  // defect being fixed was exactly this sentence, printed after a failed read.
  for (const events of REACHES) {
    for (const runs of REACHES) {
      const verdict = timelineVerdictOf<TimelineEventLike, ColumnRunLike>(
        reading(events, []), reading(runs, []),
      );
      const bothComplete = events === "complete" && runs === "complete";
      assert.equal(
        verdict.show === "empty", bothComplete,
        `events=${events} runs=${runs} said ${verdict.show}`,
      );
      // With nothing in hand there is nothing to render as entries either.
      assert.notEqual(verdict.show, "entries");
    }
  }
});

test("entries that have landed are shown while the other read is in flight", () => {
  // Round 2 of review, as a behaviour loss rather than a false claim: holding the
  // entries back until every read landed meant a task's notes sat behind an ellipsis
  // for as long as the run read took — and that read resolves one detail request per
  // run. Show them, labelled, rather than withholding them.
  const verdict = timelineVerdictOf(
    { reach: "complete", items: [ev()] }, { reach: "pending" },
  );
  assert.equal(verdict.show, "entries");
  // …and labelled as the in-flight case, not the standing one and not a known gap.
  // Collapsing any two of the three made the notice say something untrue: the standing
  // caveat on every ordinary load is how a warning stops being read, and "still
  // checking" about a gap nothing is fetching is simply false.
  assert.equal(verdict.show === "entries" && verdict.partial, "loading");
});

test("with nothing to show, a known gap is not an ellipsis", () => {
  // Round 11: `behind` was grouped with `pending` on the empty-list path, so a gap
  // whose fetch succeeded without closing it left "…" on screen standing for a request
  // that had already finished. The 25-cell table above pins every pairing; this names
  // the one that was wrong.
  const verdict = timelineVerdictOf<TimelineEventLike, ColumnRunLike>(
    { reach: "behind", items: [] }, { reach: "complete", items: [] },
  );
  assert.equal(verdict.show, "missing");
});

test("a known gap is its own sentence, ranked between the other two", () => {
  // Round 10: `partial` had two values, so a list known to be missing a record was
  // labelled "still checking" — an activity claim, while during a run the poll that
  // notices the gap refreshes attention and not events. Three states, three sentences.
  // Only the discriminant is checked here; which string each discriminant renders is
  // in the locale test below, and that the page passes these reads at all is a property
  // of the component, which this project cannot mount in a test ([[CR-18]]).
  const known = timelineVerdictOf(
    { reach: "behind", items: [ev()] }, { reach: "complete", items: [] },
  );
  assert.equal(known.show === "entries" && known.partial, "missing");
  // A read that landed short still outranks it: that caveat is true now, whatever the
  // fetch for the known gap comes back with.
  const both = timelineVerdictOf(
    { reach: "behind", items: [ev()] }, { reach: "partial", items: [] },
  );
  assert.equal(both.show === "entries" && both.partial, "incomplete");
  // …and a known gap outranks a read merely being out, because it is the specific one.
  const overWaiting = timelineVerdictOf(
    { reach: "behind", items: [ev()] }, { reach: "pending" },
  );
  assert.equal(overWaiting.show === "entries" && overWaiting.partial, "missing");
});

test("a read that landed short outranks one still in flight, with nothing to show", () => {
  // Review round 3: every run detail read failing leaves `runs` as [] with a partial
  // reach. While the sibling read is still pending, calling that "loading" hides an
  // answer that is already in — and `loading` is documented as "nothing has failed".
  const verdict = timelineVerdictOf<TimelineEventLike, ColumnRunLike>(
    { reach: "pending" }, { reach: "partial", items: [] },
  );
  assert.equal(verdict.show, "unknown");
});

test("a failed read outranks a read still in flight", () => {
  // A read that failed has landed: its answer is in, and the answer is that this page
  // cannot vouch for the history. Calling that "loading" would promise a resolution
  // that is not coming.
  const verdict = timelineVerdictOf<TimelineEventLike, ColumnRunLike>(
    { reach: "unreadable" }, { reach: "pending" },
  );
  assert.equal(verdict.show, "unknown");
});

test("entries survive a failed read, and are disclosed as unvouched-for", () => {
  // The half nobody looks at: a list with something in it reads as the history.
  const verdict = timelineVerdictOf(
    { reach: "unreadable" }, { reach: "partial", items: [run()] },
  );
  assert.equal(verdict.show, "entries");
  assert.equal(verdict.show === "entries" && verdict.partial, "incomplete");
  assert.equal(verdict.show === "entries" && verdict.items.length, 1);
});

test("a capped run read makes the verdict disclose incompleteness", () => {
  // Review's M2 on the first attempt: the cap never reached the timeline, because the
  // helper had no parameter for it. "A list only shows fewer, it does not assert"
  // does not hold for a column that presents itself as the task's history.
  //
  // Scope, per review round 10: this constructs the capped read itself. That
  // `page.tsx` actually passes `runsTruncated` into it is a property of the component,
  // which cannot be mounted in a test in this project at all ([[CR-18]]).
  const capped = timelineVerdictOf(
    { reach: "complete", items: [ev()] }, { reach: "partial", items: [run()] },
  );
  assert.equal(capped.show === "entries" && capped.partial, "incomplete");

  const whole = timelineVerdictOf(
    { reach: "complete", items: [ev()] }, { reach: "complete", items: [run()] },
  );
  assert.equal(whole.show === "entries" && whole.partial, null);
});

test("an incomplete read with nothing to show is unknown, never empty", () => {
  // The path a plain boolean missed: a failed *refresh* leaves an empty array in hand
  // together with the failure, so "reach is not unreadable" is not the same as "this
  // read supports a claim of emptiness".
  const verdict = timelineVerdictOf<TimelineEventLike, ColumnRunLike>(
    { reach: "partial", items: [] }, { reach: "complete", items: [] },
  );
  assert.equal(verdict.show, "unknown");
});

test("the incompleteness notice cannot leave the branch that carries entries", () => {
  // Review's M4: the notice rendered before the state branch, so "some of this could
  // not be read" printed beside "could not be read" — two notices about one absence.
  // This is a type test, and the gate runs `tsc --noEmit` over tests/**/*.mts: if the
  // union is ever flattened back into a state plus a loose boolean, the directive
  // below stops erroring and tsc fails on the unused directive.
  const verdict: TimelineVerdict<TimelineEventLike, ColumnRunLike> =
    timelineVerdictOf<TimelineEventLike, ColumnRunLike>({ reach: "pending" }, { reach: "pending" });
  if (verdict.show !== "entries") {
    // @ts-expect-error — `partial` exists only on the entries variant.
    assert.equal(verdict.partial, undefined);
  }
  // …and it cannot be forgotten on the branch where it belongs.
  // @ts-expect-error — an entries verdict without the notice is not constructible.
  const missing: TimelineVerdict<TimelineEventLike, ColumnRunLike> = { show: "entries", items: [] };
  assert.equal(missing.show, "entries");
});

test("every timeline notice key resolves in both locales", () => {
  // Presence, not wording: a missing key renders as the raw key, which this catches.
  // What the sentences claim is not checkable here — the two wordings review rejected
  // were both non-empty strings in both locales.
  // Every key the column can render. Round 11 found `notShownYet` missing from this
  // list one round after it was added — a coverage list is itself a claim, and this one
  // was wider than its contents.
  for (const key of ["workspace.timeline.empty", "workspace.timeline.unknown",
                     "workspace.timeline.partial", "workspace.timeline.stillChecking",
                     "workspace.timeline.notShownYet"]) {
    for (const locale of ["en", "zh"] as const) {
      const value = lookup(locale, key);
      assert.ok(value && value.length > 0, `${locale} is missing ${key}`);
    }
  }
});

// --- how the timeline learns it is behind (derived, not flagged) ----------------

const attn = (item_id: string, event_type: string): TimelineEventLike =>
  ({ event_id: `e-${item_id}-${event_type}`, event_type, created_at: "2026-09-05T00:00:00Z",
     payload: { item_id } });

test("an attention item the page holds but the event list does not is a known gap", () => {
  // Every transition records one event carrying the item id. Which event, per state, is
  // enumerated from the backend's event names rather than from its writers -- see the
  // note on ATTENTION_STATE_EVENTS, where a tally of writers was wrong twice while the
  // name set was right both times.
  const open = [{ item_id: "attn_1", state: "open" }];

  assert.equal(eventsLackKnown([attn("attn_1", "attention.created")], [open]), false);
  assert.equal(eventsLackKnown([], [open]), true);
  // Its *current* state is what must be recorded, so an item answered elsewhere is a
  // gap even while its creation event is on screen — that is the case a set-comparison
  // of open ids could not see, because the item had left the open set.
  assert.equal(eventsLackKnown(
    [attn("attn_1", "attention.created")], [[{ item_id: "attn_1", state: "responded" }]]), true);
  assert.equal(eventsLackKnown(
    [attn("attn_1", "attention.created"), attn("attn_1", "attention.responded")],
    [[{ item_id: "attn_1", state: "responded" }]]), false);
  for (const state of ["cancelled", "expired"]) {
    assert.equal(eventsLackKnown([], [[{ item_id: "attn_1", state }]]), true, state);
    assert.equal(eventsLackKnown(
      [attn("attn_1", `attention.${state}`)], [[{ item_id: "attn_1", state }]]), false, state);
  }
});

test("what the page does not hold is never reported as a gap", () => {
  // A read that has not landed establishes nothing, and neither does a state this
  // build does not recognise — that is a fact about the build, not about the list.
  assert.equal(eventsLackKnown(null, [[{ item_id: "attn_1", state: "open" }]]), false);
  assert.equal(eventsLackKnown([], [null, null]), false);
  assert.equal(eventsLackKnown([], [[{ item_id: "attn_1", state: "invented" }]]), false);
  // An event with no item id in its payload is not evidence about any item.
  assert.equal(eventsLackKnown(
    [{ event_id: "e1", event_type: "attention.created", created_at: "2026-09-05T00:00:00Z" }],
    [[{ item_id: "attn_1", state: "open" }]]), true);
});

test("more than one attention list is counted, not just the first", () => {
  // The review gate is a second, run-scoped read of the same items, and review round 6
  // found it was its own unmarked discovery path. This establishes that a later list
  // counts on its own; *which* lists the page passes is a property of the component,
  // and CR-26 records the ones it still does not.
  const gate = [{ item_id: "attn_9", state: "responded" }];
  assert.equal(eventsLackKnown([attn("attn_9", "attention.created")], [null, gate]), true);
  assert.equal(eventsLackKnown(
    [attn("attn_9", "attention.responded")], [null, gate]), false);
});

test("a run the history does not hold is a known gap, whichever list saw it", () => {
  // The active-run read is answered from the active set rather than the capped history
  // page, and the rail's global read is its own snapshot: either can see a run first.
  assert.equal(runsLackKnown([{ run_id: "r1" }], [[{ run_id: "r1" }]]), false);
  assert.equal(runsLackKnown([{ run_id: "r1" }], [[{ run_id: "r2" }]]), true);
  assert.equal(runsLackKnown([], [[{ run_id: "r2" }]]), true);
  // The second list is the rail's, and it counts on its own — review round 8 called the
  // parent's snapshot a correctness gap, not documentation debt.
  assert.equal(runsLackKnown([{ run_id: "r1" }], [null, [{ run_id: "r3" }]]), true);
  // No history in hand establishes nothing (the read is `pending` anyway), and neither
  // does a list that has not landed. Absence is never evidence here.
  assert.equal(runsLackKnown(null, [[{ run_id: "r2" }]]), false);
  assert.equal(runsLackKnown([{ run_id: "r1" }], [null, null]), false);
});

test("an event the control plane confirmed to this page must be in the list", () => {
  // The note case, and the one deleting a call-site marker reopened in round 8:
  // `appendTaskEvent` returns the record it wrote, so its id is knowledge the page
  // holds directly — no attention item and no run is involved, which is why comparing
  // only those two could not see it.
  const posted = ev({ event_id: "ev-posted" });
  assert.equal(eventsLackKnown([], [], ["ev-posted"]), true);
  assert.equal(eventsLackKnown([posted], [], ["ev-posted"]), false);
  // …and an unlanded read still establishes nothing.
  assert.equal(eventsLackKnown(null, [], ["ev-posted"]), false);
});

test("an open item created by the vendor bridge is not reported as missing forever", () => {
  // Round 8 found this one in the backend: `create_bridge_event` opens an item writing
  // only `attention.bridge_captured` and no `attention.created`, so requiring the
  // latter for every open item left a bridged gate reported as a gap for as long as it
  // stayed open — a caveat with no way to clear. The rule is per state, not per event.
  const open = [{ item_id: "attn_b", state: "open" }];
  assert.equal(eventsLackKnown([attn("attn_b", "attention.bridge_captured")], [open]), false);
  assert.equal(eventsLackKnown([attn("attn_b", "attention.created")], [open]), false);
  assert.equal(eventsLackKnown([attn("attn_b", "attention.responded")], [open]), true);
});

// CR-26: the derivation is not fed everything on this page that implies an event.
// `specs` and the delivery read are event-backed and do not reach it, so a spec
// approved or a note posted by another client can be named elsewhere on the page
// beside a timeline showing no caveat. Skipped rather than written: `specs` needs a
// state->event map, and this file's existing one was wrong twice, so it is built by
// enumerating the backend's event names rather than guessed at here. The recap cannot
// be fed at all without a backend change — it publishes summaries, not event ids.
//
// This skip is the artifact, per the workspace rule that a hazard left unfixed must
// produce something that fails, blocks or reappears on its own: the suite reports it
// on every run, and deleting it to make the output tidy is a visible edit rather than
// a silent one.
test.skip("CR-26: a spec whose state event is missing is a known gap too", () => {});

test("every attention event has its own sentence, and none borrows \"Needs you\"", () => {
  // CR-27: the column branched on `attention.responded` alone, so a cancelled item, an
  // expired one and a *successfully delivered* answer all told the user they had
  // something to do. One key per event name -- and the map has to cover every name in
  // the state map plus both delivery outcomes, one of which no literal search finds.
  // The map is keyed by *state*; its values are the event names, which is what the
  // timeline renders. Iterating the keys here would have compared states to event
  // names and passed by looking up undefined against undefined.
  for (const type of Object.values(ATTENTION_STATE_EVENTS_FOR_TEST).flat()) {
    assert.ok(ATTENTION_TIMELINE_EVENT_KEY[type] !== undefined,
      `${type} records a state but has no timeline sentence`);
  }
  for (const type of ["attention.delivery_delivered", "attention.delivery_failed"]) {
    assert.ok(ATTENTION_TIMELINE_EVENT_KEY[type] !== undefined, type);
  }
  // Only the two raising events may say "Needs you".
  const raised = Object.entries(ATTENTION_TIMELINE_EVENT_KEY)
    .filter(([, key]) => key.endsWith("attentionRaised")).map(([type]) => type).sort();
  assert.deepEqual(raised, ["attention.bridge_captured", "attention.created"]);
  // …and every sentence resolves in both locales.
  for (const key of Object.values(ATTENTION_TIMELINE_EVENT_KEY)) {
    for (const locale of ["en", "zh"] as const) {
      assert.ok(lookup(locale, key), `${locale} is missing ${key}`);
    }
  }
});

// --- the stream itself ---------------------------------------------------------

test("run lifecycle events are dropped and work units interleave by queue time", () => {
  const items = timelineItemsOf(
    [
      ev({ event_id: "note", created_at: "2026-09-05T00:00:02Z" }),
      ev({ event_id: "noise", event_type: "run.started", created_at: "2026-09-05T00:00:01Z" }),
    ],
    [run({ run_id: "r1", queued_at: "2026-09-05T00:00:01Z" })],
  );
  assert.deepEqual(items.map((i) => i.key), ["r1", "note"]);
  assert.deepEqual(items.map((i) => i.kind), ["unit", "event"]);
});

test("on a shared queue time runs order by run id, across both run kinds", () => {
  // The tie-break has to be one rule over the whole stream: units and unplaceable
  // runs are appended as two groups, so a timestamp-only sort would order them by
  // which group was pushed first rather than by run id.
  const at = "2026-09-05T00:00:01Z";
  const items = timelineItemsOf<TimelineEventLike, ColumnRunLike>(
    [],
    [
      run({ run_id: "r-b", queued_at: at }),
      run({
        run_id: "r-a",
        queued_at: at,
        result_metadata: { [RUN_ROLE_KEY]: ROLE_REVIEW, [RUN_REVIEWS_KEY]: "not-loaded" },
      }),
    ],
  );
  assert.deepEqual(items.map((i) => i.key), ["r-a", "r-b"]);
  // …and the review whose parent is not in the page is rendered, not dropped.
  assert.deepEqual(items.map((i) => i.kind), ["unplaced", "unit"]);
});

// --- what a source scan can and cannot hold ------------------------------------

test("a failed events read is not written down as an empty list", async () => {
  // Honest about what this is: a scan of one literal in one file. It catches the
  // exact regression (`setEvents([])` in the catch, which erased notes already on
  // screen) and nothing semantically equivalent to it — review demonstrated with 11
  // mutations that source scans do not hold, which is CR-18. The *false claim* half
  // of that defect is now closed by the union above instead, and closing this half
  // structurally needs the loader guard that CR-24 carries.
  const { readFile } = await import("node:fs/promises");
  const file = await readFile(new URL("../src/app/workspace/page.tsx", import.meta.url), "utf8");
  const source = file.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");
  assert.ok(!source.includes("setEvents([])"),
    "a failed events read must not be recorded as a confirmed empty history");
});

import assert from "node:assert/strict";
import test from "node:test";

import {
  TASK_ATTENTION_NOTICE_KEY,
  openGateCount,
  taskAttentionView,
  taskBlockerClaim,
  type AttentionLike,
  type BlockerLike,
} from "../src/lib/task-attention.ts";
import { continueEntryGate } from "../src/lib/continue-run.ts";
import { workspace as workspaceDictionary } from "../src/lib/i18n/dictionaries/workspace.ts";

type Locale = "en" | "zh";

/** Resolve a "workspace.…" message key against one locale, or undefined. */
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

const item = (id: string, state = "open"): AttentionLike => ({ item_id: id, state });

test("no answer yet is loading, not an absence", () => {
  const view = taskAttentionView({ items: null, readFailed: false, truncated: false });
  assert.equal(view.kind, "loading");
  assert.equal(openGateCount(view), null);
});

test("a failed read is unreadable, and outranks the null it also produces", () => {
  // The read failure leaves `items` null too, so the order of these two checks is
  // the whole content of the state: an error must not render as a spinner.
  const view = taskAttentionView({ items: null, readFailed: true, truncated: false });
  assert.equal(view.kind, "unreadable");
  assert.equal(openGateCount(view), null);
});

test("an empty successful read is the only 'nothing is waiting'", () => {
  const view = taskAttentionView({ items: [], readFailed: false, truncated: false });
  assert.equal(view.kind, "none");
  assert.equal(openGateCount(view), 0);
});

test("open items are counted, and only the open ones", () => {
  const view = taskAttentionView({
    items: [item("a"), item("b", "responded"), item("c", "expired"), item("d")],
    readFailed: false,
    truncated: false,
  });
  assert.equal(view.kind, "open");
  assert.deepEqual(view.kind === "open" ? view.items.map((i) => i.item_id) : [], ["a", "d"]);
  assert.equal(openGateCount(view), 2);
});

test("a truncated page is partial: the items show, the count stays unknown", () => {
  // Both halves matter. Hiding the items would throw away real gates a human can
  // act on; reporting a count would assert a total the read cannot support, and the
  // item it dropped could be the approval that must block Continue.
  const view = taskAttentionView({
    items: [item("a"), item("b")],
    readFailed: false,
    truncated: true,
  });
  assert.equal(view.kind, "partial");
  assert.deepEqual(view.kind === "partial" ? view.items.map((i) => i.item_id) : [], ["a", "b"]);
  assert.equal(openGateCount(view), null);
});

test("a truncated page whose items are all settled is still unknown, not none", () => {
  const view = taskAttentionView({
    items: [item("a", "responded")],
    readFailed: false,
    truncated: true,
  });
  assert.equal(view.kind, "partial");
  assert.equal(view.kind === "partial" ? view.items.length : -1, 0);
  assert.equal(openGateCount(view), null);
});

test("the panel and the Continue gate can never contradict each other", () => {
  // The finding this module exists for: the panel rendered "nothing needs you"
  // from the parent's capped global list while the gate on the same page blocked
  // with attentionUnknown. Both now derive from one view, so for every possible
  // set of facts, "the panel shows the none state" and "the gate says unknown"
  // cannot both be true.
  const everyFact = [null, [], [item("a")], [item("a", "cancelled")]].flatMap((items) =>
    [false, true].flatMap((readFailed) =>
      [false, true].map((truncated) => ({ items, readFailed, truncated })),
    ),
  );
  for (const facts of everyFact) {
    const view = taskAttentionView(facts);
    const count = openGateCount(view);
    const gate = continueEntryGate({
      taskState: "running",
      activeRunCount: 0,
      openAttentionCount: count,
      followUpArmed: false,
      dispatching: false,
    });
    const panelSaysNothingWaiting = view.kind === "none";
    const gateSaysUnknown = gate.block === "attentionUnknown";
    assert.ok(
      !(panelSaysNothingWaiting && gateSaysUnknown),
      `contradiction for ${JSON.stringify(facts)}: panel=none, gate=attentionUnknown`,
    );
    // And the converse claim: whenever the panel asserts "nothing is waiting", the
    // gate must actually be open on that fact rather than blocking on it.
    if (panelSaysNothingWaiting) assert.equal(count, 0);
    // Whenever the gate treats the fact as unknown, the panel must be saying
    // something other than "nothing is waiting".
    if (gateSaysUnknown) assert.notEqual(view.kind, "none");
  }
});

test("every panel notice key exists in every locale", () => {
  for (const key of Object.values(TASK_ATTENTION_NOTICE_KEY)) {
    for (const locale of ["en", "zh"] as const) {
      assert.ok(lookup(locale, key) !== undefined, `missing ${locale} message: ${key}`);
    }
  }
  // The two the partial state renders are not in the map above (they accompany a
  // list rather than replace it), so they are pinned separately. So are the two the
  // Outcome Summary's blocker line renders for "no answer" and "blocked, no detail".
  for (const key of [
    "workspace.panels.attention.partial",
    "workspace.panels.attention.partialEmpty",
    "workspace.header.unavailable",
    "workspace.header.blockedNoDetail",
  ]) {
    for (const locale of ["en", "zh"] as const) {
      assert.ok(lookup(locale, key) !== undefined, `missing ${locale} message: ${key}`);
    }
  }
});

const gate = (id: string, kind: string, state = "open"): BlockerLike =>
  ({ item_id: id, state, kind, title: `title ${id}` });

test("no answer about a blocker never renders as 'nothing is blocking'", () => {
  // The finding: the Outcome Summary's blocker line came from the parent's capped
  // GLOBAL attention page, so it disagreed with the task-scoped panel in both
  // directions — a resolved blocker stayed on screen until the next global refresh,
  // and a blocker beyond the global page never appeared at all.
  assert.deepEqual(
    taskBlockerClaim(taskAttentionView<BlockerLike>({ items: null, readFailed: false, truncated: false })),
    { kind: "loading" },
  );
  assert.deepEqual(
    taskBlockerClaim(taskAttentionView<BlockerLike>({ items: null, readFailed: true, truncated: false })),
    { kind: "unknown" },
  );
});

test("the blocker line names the first open blocker the task-scoped read returned", () => {
  const view = taskAttentionView({
    items: [gate("a", "approval"), gate("b", "blocker"), gate("c", "blocker")],
    readFailed: false,
    truncated: false,
  });
  assert.deepEqual(taskBlockerClaim(view), { kind: "blocker", title: "title b" });
});

test("open items with no blocker among them is the only 'none'", () => {
  const answered = taskAttentionView({
    items: [gate("a", "approval"), gate("b", "blocker", "responded")],
    readFailed: false,
    truncated: false,
  });
  assert.deepEqual(taskBlockerClaim(answered), { kind: "none" });
  assert.deepEqual(
    taskBlockerClaim(taskAttentionView({ items: [], readFailed: false, truncated: false })),
    { kind: "none" },
  );
});

test("a truncated page can confirm a blocker but never rule one out", () => {
  const found = taskAttentionView({
    items: [gate("a", "blocker")],
    readFailed: false,
    truncated: true,
  });
  assert.deepEqual(taskBlockerClaim(found), { kind: "blocker", title: "title a" });
  const missing = taskAttentionView({
    items: [gate("a", "approval")],
    readFailed: false,
    truncated: true,
  });
  assert.deepEqual(taskBlockerClaim(missing), { kind: "unknown" });
});

test("the blocker line and the attention panel cannot disagree", () => {
  // Same exhaustive shape as the panel/gate check above, over the fact sets that
  // can carry a blocker. The claim is derived from the view, so the two can only
  // agree — this pins that they are still derived from one answer, which is the
  // thing that regressed once already.
  const sets = [null, [], [gate("a", "blocker")], [gate("a", "approval")],
                [gate("a", "blocker", "cancelled")], [gate("a", "approval"), gate("b", "blocker")]];
  for (const items of sets) {
    for (const readFailed of [false, true]) {
      for (const truncated of [false, true]) {
        const view = taskAttentionView({ items, readFailed, truncated });
        const claim = taskBlockerClaim(view);
        const shown = view.kind === "open" || view.kind === "partial" ? view.items : [];
        if (claim.kind === "blocker") {
          // Named, so the panel is showing it: the line can never report a blocker
          // the panel does not list.
          assert.ok(shown.some((entry) => entry.title === claim.title));
        }
        if (claim.kind === "none") {
          // Asserted absence, so the read must have been complete and blocker-free.
          assert.ok(view.kind === "none" || view.kind === "open");
          assert.ok(!shown.some((entry) => entry.kind === "blocker"));
        }
        // An unreadable or still-loading answer is never an absence.
        if (view.kind === "loading" || view.kind === "unreadable") {
          assert.ok(claim.kind === "loading" || claim.kind === "unknown");
        }
      }
    }
  }
});


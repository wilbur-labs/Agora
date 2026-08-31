import assert from "node:assert/strict";
import test from "node:test";

import {
  CONVENTION_CHECKBOXES,
  CONVENTION_MARKERS,
  CONVENTION_NONE,
  PROGRESS_BAR_KEY,
  SNAPSHOT_ABSENT_REASON,
  SNAPSHOT_MIXED_CONVENTIONS_REASON,
  SNAPSHOT_UNCOUNTED_MARKERS_REASON,
  progressBarOf,
  type ProgressBar,
  type ProgressSnapshotLike,
} from "../src/lib/progress-snapshot.ts";
import { common as commonDictionary } from "../src/lib/i18n/dictionaries/common.ts";

type Locale = "en" | "zh";

/** Resolve a "common.…" message key against one locale, or undefined. */
function lookup(locale: Locale, key: string): string | undefined {
  const parts = key.split(".");
  assert.equal(parts.shift(), "common", `key must be in the common namespace: ${key}`);
  let node: unknown = commonDictionary[locale];
  for (const part of parts) {
    if (!node || typeof node !== "object" || !(part in (node as Record<string, unknown>))) {
      return undefined;
    }
    node = (node as Record<string, unknown>)[part];
  }
  return typeof node === "string" ? node : undefined;
}

const loaded = (snapshot: ProgressSnapshotLike | null | undefined) =>
  progressBarOf({ snapshot, dashboardLoaded: true, dashboardReadFailed: false });

const available = (
  done: number, inProgress: number, todo: number,
): ProgressSnapshotLike => ({
  available: true, reason: null, done, in_progress: inProgress, todo,
  convention: CONVENTION_MARKERS,
});

/** A checkbox snapshot as the backend reports one: done, not-done, and no split. */
const checkboxes = (done: number, notDone: number): ProgressSnapshotLike => ({
  available: true, reason: null, done, in_progress: null, todo: notDone,
  convention: CONVENTION_CHECKBOXES,
});

/** No variant except the two count ones may carry a number — what makes "no 0%" hold. */
function assertHasNoNumber(bar: ProgressBar) {
  assert.notEqual(bar.kind, "counts");
  assert.notEqual(bar.kind, "countsDoneOnly");
  assert.ok(!("donePct" in bar), `${bar.kind} must not carry a percentage`);
  assert.ok(!("done" in bar), `${bar.kind} must not carry a count`);
}

test("counts drive the percentage from done only", () => {
  const bar = loaded(available(5, 3, 2));
  assert.equal(bar.kind, "counts");
  if (bar.kind !== "counts") return;
  assert.equal(bar.total, 10);
  // 5 of 10 done. The 3 in progress are NOT half credit — that would report 65%.
  assert.equal(bar.donePct, 50);
  assert.equal(bar.inProgressPct, 30);
  assert.ok(bar.donePct + bar.inProgressPct <= 100);
});

test("in progress never widens the bar past full", () => {
  const bar = loaded(available(50, 50, 0));
  if (bar.kind !== "counts") throw new Error("expected counts");
  assert.equal(bar.donePct, 50);
  assert.equal(bar.inProgressPct, 50);
});

test("everything done is 100 percent and nothing done is zero", () => {
  const all = loaded(available(4, 0, 0));
  if (all.kind !== "counts") throw new Error("expected counts");
  assert.equal(all.donePct, 100);
  const none = loaded(available(0, 2, 3));
  if (none.kind !== "counts") throw new Error("expected counts");
  // Zero done *is* 0% here, and that is a counted zero: the snapshot says so. It is
  // the uncounted zeros below that must never render.
  assert.equal(none.donePct, 0);
  assert.equal(none.inProgressPct, 40);
});

test("a finished item is never rounded away, and an unfinished one never rounds to full", () => {
  const barely = loaded(available(1, 0, 299));
  if (barely.kind !== "counts") throw new Error("expected counts");
  // 0.33% rounds to 0, and a 0% beside a done item reads as "nothing is done".
  assert.equal(barely.donePct, 1);
  const nearly = loaded(available(299, 0, 1));
  if (nearly.kind !== "counts") throw new Error("expected counts");
  assert.equal(nearly.donePct, 99);
});

test("a project with no snapshot renders no number", () => {
  const bar = loaded({ available: false, reason: SNAPSHOT_ABSENT_REASON, done: null, in_progress: null, todo: null });
  assert.deepEqual(bar, { kind: "empty", because: "noSnapshot" });
  assertHasNoNumber(bar);
});

test("a snapshot that tracks nothing is empty, not zero percent", () => {
  const bar = loaded(available(0, 0, 0));
  // Distinguishable from "no snapshot": the file exists and lists no items. Neither
  // has a percentage, and the two say different things to a human.
  assert.deepEqual(bar, { kind: "empty", because: "noItems" });
  assertHasNoNumber(bar);
});

test("unreadable counts do not render as 0%", () => {
  for (const reason of ["unreadable", "too_large", "a reason this UI has never heard of"]) {
    const bar = loaded({ available: false, reason, done: null, in_progress: null, todo: null });
    assert.deepEqual(bar, { kind: "unknown", because: "snapshotUnread" });
    assertHasNoNumber(bar);
  }
});

test("a file whose markers this reader does not count says that, not 'no items'", () => {
  // A file whose boxes hold marks the backend has no meaning for (`[/]`, `[✓]`).
  // "Tracks no items yet" would be a false statement about the project, and "could
  // not be read" would be false about the file: it is its own outcome with its own
  // sentence. (Plain `- [x]` lines are counted now; this outcome is what is left.)
  const bar = loaded({
    available: false, reason: SNAPSHOT_UNCOUNTED_MARKERS_REASON, done: null, in_progress: null, todo: null,
  });
  assert.deepEqual(bar, { kind: "unknown", because: "markersNotCounted" });
  assert.notDeepEqual(bar, { kind: "empty", because: "noItems" });
  assertHasNoNumber(bar);
});

test("available with unusable counts is unknown rather than zero", () => {
  // The `?? 0` this replaces would have reported 0% from each of these.
  // Every shape names its convention, so what is under test is the unusable count and
  // not the missing-convention rule that would otherwise answer first.
  const shapes: ProgressSnapshotLike[] = [
    { available: true, done: null, in_progress: 1, todo: 1, convention: CONVENTION_MARKERS },
    { available: true, done: 1, in_progress: undefined, todo: 1, convention: CONVENTION_MARKERS },
    { available: true, done: -1, in_progress: 0, todo: 0, convention: CONVENTION_MARKERS },
    { available: true, done: 1.5, in_progress: 0, todo: 0, convention: CONVENTION_MARKERS },
    { available: true, done: Number.NaN, in_progress: 0, todo: 0, convention: CONVENTION_MARKERS },
    {
      available: true, done: "3" as unknown as number, in_progress: 0, todo: 0,
      convention: CONVENTION_MARKERS,
    },
    // And the checkbox shape's own contradictions.
    { available: true, done: null, in_progress: null, todo: 2, convention: CONVENTION_CHECKBOXES },
    { available: true, done: 2, in_progress: null, todo: null, convention: CONVENTION_CHECKBOXES },
  ];
  for (const snapshot of shapes) {
    const bar = loaded(snapshot);
    assert.deepEqual(bar, { kind: "unknown", because: "countsUnusable" });
    assertHasNoNumber(bar);
  }
});

test("a checkbox snapshot reports an exact percentage with no in-progress segment", () => {
  // The objection to counting checkboxes is that they cannot express "in progress".
  // True, and it does not cost the percentage: an item in flight is written as an
  // unchecked box, so done + not-done is the whole checklist and done / total is
  // exact. What the file cannot support is the split, so there is no field for it —
  // not a zero, no field at all, so no renderer can draw a segment from it.
  const bar = loaded(checkboxes(3, 1));
  assert.equal(bar.kind, "countsDoneOnly");
  if (bar.kind !== "countsDoneOnly") return;
  assert.equal(bar.total, 4);
  assert.equal(bar.donePct, 75);
  assert.equal(bar.notDone, 1);
  assert.ok(!("inProgress" in bar), "a checkbox snapshot must not carry an in-progress count");
  assert.ok(!("inProgressPct" in bar), "a checkbox snapshot must not carry an in-progress width");
});

test("a checkbox snapshot rounds by the same rules as a marker one", () => {
  const barely = loaded(checkboxes(1, 299));
  if (barely.kind !== "countsDoneOnly") throw new Error("expected countsDoneOnly");
  assert.equal(barely.donePct, 1);
  const nearly = loaded(checkboxes(299, 1));
  if (nearly.kind !== "countsDoneOnly") throw new Error("expected countsDoneOnly");
  assert.equal(nearly.donePct, 99);
  const all = loaded(checkboxes(4, 0));
  if (all.kind !== "countsDoneOnly") throw new Error("expected countsDoneOnly");
  assert.equal(all.donePct, 100);
  // Zero done is a counted zero here too: the boxes say so.
  const none = loaded(checkboxes(0, 4));
  if (none.kind !== "countsDoneOnly") throw new Error("expected countsDoneOnly");
  assert.equal(none.donePct, 0);
});

test("an empty checkbox snapshot is empty, not zero percent", () => {
  assert.deepEqual(loaded(checkboxes(0, 0)), { kind: "empty", because: "noItems" });
});

test("a checkbox snapshot carrying an in-progress count is a contradiction, not a bucket", () => {
  // The convention has no form for work in flight, so a number in that field did not
  // come from the file. Rendering it would put a segment on the bar that nothing
  // counted — the failure this whole module is built against, arriving as a surplus
  // instead of as a missing value.
  const bar = loaded({ ...checkboxes(3, 1), in_progress: 2 });
  assert.deepEqual(bar, { kind: "unknown", because: "countsUnusable" });
  assertHasNoNumber(bar);
});

test("a convention this build has never heard of is unknown, not read as markers", () => {
  // A newer backend counting some third convention. Its fields may not mean what
  // this build's rule assumes, so it says unknown rather than reporting a number
  // read by the wrong rule.
  const bar = loaded({ ...available(3, 1, 1), convention: "something this build predates" });
  assert.deepEqual(bar, { kind: "unknown", because: "countsUnusable" });
  assertHasNoNumber(bar);
});

test("a payload with no convention field is unknown, not counted as markers", () => {
  // This test asserted the opposite until review found what it was protecting: a
  // server older than the field preferred markers in a file that also had checkboxes
  // and returned the marker count alone. `- ✅ done` + `- [ ] pending` reached this
  // module as done 1 of 1 — and reading the missing field as "markers" rendered a
  // half-finished checklist as 100%. The exact payload that server sends is below.
  const fromOldServer: ProgressSnapshotLike = {
    available: true, reason: null, done: 1, in_progress: 0, todo: 0,
  };
  const bar = loaded(fromOldServer);
  assert.deepEqual(bar, { kind: "unknown", because: "conventionNotReported" });
  assertHasNoNumber(bar);
  // Explicit null is the same case: the field carries no convention either way.
  assert.deepEqual(
    loaded({ ...fromOldServer, convention: null }),
    { kind: "unknown", because: "conventionNotReported" },
  );
  // And the cost is stated rather than hidden: an old server showing pure markers
  // also loses its bar. That is the intended side to be wrong on — a sentence is
  // recoverable by upgrading, a confident 100% is not.
  assertHasNoNumber(loaded({ available: true, reason: null, done: 5, in_progress: 3, todo: 2 }));
});

test("a file that lists nothing is empty, and is not the old-server case", () => {
  // The backend names this rather than sending no convention, so that "no convention"
  // keeps proving "old server". Both render without a number, and they say different
  // things: one is about the file, the other about the server.
  const nothingTracked = loaded({
    available: true, reason: null, done: 0, in_progress: 0, todo: 0, convention: CONVENTION_NONE,
  });
  assert.deepEqual(nothingTracked, { kind: "empty", because: "noItems" });
  assert.notDeepEqual(nothingTracked, { kind: "unknown", because: "conventionNotReported" });
});

test("'no convention' arriving with items counted is a contradiction, not a count", () => {
  const bar = loaded({
    available: true, reason: null, done: 3, in_progress: 0, todo: 1, convention: CONVENTION_NONE,
  });
  assert.deepEqual(bar, { kind: "unknown", because: "countsUnusable" });
  assertHasNoNumber(bar);
});

test("a file mixing both conventions says so instead of reporting a floor", () => {
  // Counting either convention alone would drop the other's items and report a
  // percentage that is too low — the same wrong number the size bound exists to
  // prevent, and not something a sentence about "unreadable" would explain.
  const bar = loaded({
    available: false, reason: SNAPSHOT_MIXED_CONVENTIONS_REASON,
    done: null, in_progress: null, todo: null,
  });
  assert.deepEqual(bar, { kind: "unknown", because: "mixedConventions" });
  assertHasNoNumber(bar);
});

test("a server that reports no snapshot field is unknown, not a project without one", () => {
  // A fact about the server is not a fact about the project, and the two render
  // different sentences.
  assert.deepEqual(loaded(undefined), { kind: "unknown", because: "notReported" });
  assert.deepEqual(loaded(null), { kind: "unknown", because: "notReported" });
  assert.notDeepEqual(loaded(undefined), { kind: "empty", because: "noSnapshot" });
});

test("a dashboard that has not answered is loading; one that failed is unknown", () => {
  assert.deepEqual(
    progressBarOf({ snapshot: undefined, dashboardLoaded: false, dashboardReadFailed: false }),
    { kind: "loading" },
  );
  const failed = progressBarOf({ snapshot: undefined, dashboardLoaded: false, dashboardReadFailed: true });
  assert.deepEqual(failed, { kind: "unknown", because: "dashboardUnread" });
  assertHasNoNumber(failed);
});

test("a failed dashboard read is never answered from a stale snapshot", () => {
  // The read failed; whatever snapshot object happens to be in hand says nothing
  // about now, and must not become a percentage.
  const bar = progressBarOf({
    snapshot: available(9, 0, 1), dashboardLoaded: false, dashboardReadFailed: true,
  });
  assertHasNoNumber(bar);
});

test("every wordless outcome has a sentence in both locales", () => {
  const outcomes: Array<Extract<ProgressBar, { kind: "empty" | "unknown" }>["because"]> = [
    "noSnapshot", "noItems", "dashboardUnread", "snapshotUnread", "notReported", "countsUnusable",
    "markersNotCounted", "mixedConventions", "conventionNotReported",
  ];
  for (const because of outcomes) {
    const key = PROGRESS_BAR_KEY[because];
    for (const locale of ["en", "zh"] as const) {
      const text = lookup(locale, key);
      assert.ok(text, `${locale} is missing ${key}`);
      // None of these may report a percentage: a sentence that ends in a figure is
      // a figure, whatever the words around it say.
      assert.ok(!/\d\s*%/.test(text), `${key} (${locale}) must not state a percentage`);
    }
  }
  // The counted line and the label are shared by both surfaces.
  for (const locale of ["en", "zh"] as const) {
    assert.ok(lookup(locale, "common.progress.counts"), `${locale} is missing common.progress.counts`);
    assert.ok(lookup(locale, "common.progress.label"), `${locale} is missing common.progress.label`);
    const doneOnly = lookup(locale, "common.progress.countsDoneOnly");
    assert.ok(doneOnly, `${locale} is missing common.progress.countsDoneOnly`);
    // The remainder under this convention may hold work in flight, so the sentence
    // must not call it "not started" / "待办". Naming it that way is how an unknown
    // becomes a claim in the one place the type system cannot stop it.
    assert.ok(
      !/to do|not started|待办|未开始/.test(doneOnly),
      `${locale}: countsDoneOnly must say "not done", not "to do": ${doneOnly}`,
    );
    // And it must not print an in-progress figure it does not have.
    assert.ok(
      !/\{inProgress\}/.test(doneOnly),
      `${locale}: countsDoneOnly must not interpolate an in-progress count`,
    );
  }
});

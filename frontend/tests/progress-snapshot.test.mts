import assert from "node:assert/strict";
import test from "node:test";

import {
  PROGRESS_BAR_KEY,
  SNAPSHOT_ABSENT_REASON,
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
): ProgressSnapshotLike => ({ available: true, reason: null, done, in_progress: inProgress, todo });

/** No variant except `counts` may carry a number, which is what makes "no 0%" hold. */
function assertHasNoNumber(bar: ProgressBar) {
  assert.notEqual(bar.kind, "counts");
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
  // Agora's own snapshot is over a thousand `- [x]` lines, which the backend reader
  // does not count. "Tracks no items yet" would be a false statement about the
  // project, and "could not be read" would be false about the file: it is its own
  // outcome with its own sentence.
  const bar = loaded({
    available: false, reason: SNAPSHOT_UNCOUNTED_MARKERS_REASON, done: null, in_progress: null, todo: null,
  });
  assert.deepEqual(bar, { kind: "unknown", because: "markersNotCounted" });
  assert.notDeepEqual(bar, { kind: "empty", because: "noItems" });
  assertHasNoNumber(bar);
});

test("available with unusable counts is unknown rather than zero", () => {
  // The `?? 0` this replaces would have reported 0% from each of these.
  const shapes: ProgressSnapshotLike[] = [
    { available: true, done: null, in_progress: 1, todo: 1 },
    { available: true, done: 1, in_progress: undefined, todo: 1 },
    { available: true, done: -1, in_progress: 0, todo: 0 },
    { available: true, done: 1.5, in_progress: 0, todo: 0 },
    { available: true, done: Number.NaN, in_progress: 0, todo: 0 },
    { available: true, done: "3" as unknown as number, in_progress: 0, todo: 0 },
  ];
  for (const snapshot of shapes) {
    const bar = loaded(snapshot);
    assert.deepEqual(bar, { kind: "unknown", because: "countsUnusable" });
    assertHasNoNumber(bar);
  }
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
    "markersNotCounted",
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
  }
});

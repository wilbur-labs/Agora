import assert from "node:assert/strict";
import test from "node:test";

import { navFromDefects, type DefectsLike } from "../src/lib/nav-capability.ts";

/** A payload with the shape the backend sends, overridable per case. */
function defects(over: Partial<DefectsLike>): DefectsLike {
  return {
    available: true,
    reason: null,
    records: [],
    open_count: 0,
    unrecognised_count: 0,
    unreadable_count: 0,
    ...over,
  };
}

test("absent is a settled nothing, not a failed read", () => {
  assert.deepEqual(
    navFromDefects(defects({ available: false, reason: "absent", records: null, open_count: null, unrecognised_count: null })),
    { kind: "none" },
  );
});

test("unavailable without 'absent' is a failed read, never 'none'", () => {
  for (const reason of ["unreadable", "too_large", "malformed", "duplicate_key", null]) {
    const cap = navFromDefects(
      defects({ available: false, reason, records: null, open_count: null, unrecognised_count: null, unreadable_count: null }),
    );
    assert.equal(cap.kind, "unreadable", `reason=${reason}`);
  }
});

test("the count is the open count, not the record total", () => {
  // three records, one open — the badge says 1, not 3.
  assert.deepEqual(
    navFromDefects(defects({ records: [{}, {}, {}], open_count: 1, unrecognised_count: 0 })),
    { kind: "count", value: 1, caveat: null },
  );
});

test("no open and none unclassified is 'none', even with fixed records present", () => {
  // A file of only-fixed defects: records is non-empty, but nothing is owed.
  assert.deepEqual(
    navFromDefects(defects({ records: [{}, {}], open_count: 0, unrecognised_count: 0 })),
    { kind: "none" },
  );
});

test("unclassified entries ride along as a caveat, not counted as open", () => {
  const cap = navFromDefects(defects({ records: [{}, {}], open_count: 1, unrecognised_count: 1 }));
  assert.deepEqual(cap, { kind: "count", value: 1, caveat: { kind: "unclassified", count: 1 } });
});

test("zero open but some unclassified is a count of 0 with a caveat, not 'none'", () => {
  // Nothing positively open, but an entry nobody could classify: not a settled nothing.
  const cap = navFromDefects(defects({ records: [{}], open_count: 0, unrecognised_count: 1 }));
  assert.deepEqual(cap, { kind: "count", value: 0, caveat: { kind: "unclassified", count: 1 } });
});

test("a contradictory payload (available but null numbers) is a failed read, not zero", () => {
  // The safe reading of a missing number is not zero.
  assert.equal(navFromDefects(defects({ records: [{}], open_count: null, unrecognised_count: 0 })).kind, "unreadable");
  assert.equal(navFromDefects(defects({ records: [{}], open_count: 1, unrecognised_count: null })).kind, "unreadable");
  assert.equal(navFromDefects(defects({ records: null, open_count: 1, unrecognised_count: 0 })).kind, "unreadable");
  // A null unreadable_count is just as contradictory as the others.
  assert.equal(navFromDefects(defects({ records: [{}], open_count: 1, unrecognised_count: 0, unreadable_count: null })).kind, "unreadable");
});

test("a file of only-unreadable entries is not a settled 'none'", () => {
  // open=0, unrecognised=0, but entries the reader kept because it could not read them.
  // Reporting `none` here would be the product claiming there are no defects when the
  // truth is it could not read the ones that are there.
  const cap = navFromDefects(defects({ records: [{}, {}], open_count: 0, unrecognised_count: 0, unreadable_count: 2 }));
  assert.deepEqual(cap, { kind: "count", value: 0, caveat: { kind: "unopenable", count: 2 } });
});

test("unreadable outranks unclassified in the single caveat slot", () => {
  // Both qualifiers present: surface the more severe one (an entry not read at all).
  const cap = navFromDefects(defects({ records: [{}, {}, {}], open_count: 1, unrecognised_count: 1, unreadable_count: 1 }));
  assert.deepEqual(cap, { kind: "count", value: 1, caveat: { kind: "unopenable", count: 1 } });
});

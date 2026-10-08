import assert from "node:assert/strict";
import test from "node:test";

import {
  NAV_DEFECTS_NO_SOURCE,
  NAV_FILES_NO_READER,
  NAV_SKILLS_UNBUILT,
  NAV_UNREPORTED,
  countSayable,
  isProjectDerived,
  navCapabilityKey,
  navFromCRLog,
  navFromDecisions,
  navReadFailed,
  supportsAbsenceClaim,
  type DecisionsLike,
} from "../src/lib/nav-capability.ts";
import { crLogViewOf, type CRLogView } from "../src/lib/cr-log.ts";

/** A decisions payload with every field present, as the contract requires. Overrides are
 *  spelled out per case so each test says which field it is about. */
function decisions(over: Partial<DecisionsLike>): DecisionsLike {
  return { available: true, reason: null, records: [], unreadable_count: 0, ...over };
}

// --- the distinction the whole module exists for -----------------------------

test("nothing-to-report and could-not-report are never the same capability", () => {
  // A project that keeps no CR Log, versus a server that did not mention CR Logs at
  // all. Merging these is what makes a UI state a fact about the project on the
  // strength of a fact about the server.
  const keepsNone = navFromCRLog(crLogViewOf({ available: false, reason: "absent" }));
  const serverSilent = navFromCRLog(crLogViewOf(undefined));

  assert.equal(keepsNone.kind, "none");
  assert.equal(serverSilent.kind, "unreported");
  assert.notEqual(navCapabilityKey(keepsNone), navCapabilityKey(serverSilent));
});

test("not built, cannot read, and read failed are three sentences", () => {
  const keys = new Set([
    navCapabilityKey(NAV_SKILLS_UNBUILT),
    navCapabilityKey(NAV_FILES_NO_READER),
    navCapabilityKey(navReadFailed()),
  ]);
  assert.equal(keys.size, 3, "each must have its own sentence, not a shared 'unavailable'");
});

test("two things unreadable for different reasons do not collapse into one sentence", () => {
  // Files have no reader; the known defects have no structured source in this repo. Same
  // variant, different reasons — and the reason has to survive as far as the key, or a
  // generic renderer merges them again.
  assert.equal(NAV_FILES_NO_READER.kind, "noReader");
  assert.equal(NAV_DEFECTS_NO_SOURCE.kind, "noReader");
  assert.notEqual(
    navCapabilityKey(NAV_FILES_NO_READER),
    navCapabilityKey(NAV_DEFECTS_NO_SOURCE),
  );
});

test("only a real count may be drawn as a number", () => {
  // Built as a view rather than as a payload: crLogViewOf counts the rows itself and
  // cross-checks them against the payload's numbers, so a payload with counts but no
  // matching rows is (correctly) unreadable. What is under test here is the mapping,
  // not that classification — which has its own tests.
  const counted: CRLogView = { kind: "rows", rows: [], open: 2, unrecognised: 0 };
  assert.equal(countSayable(navFromCRLog(counted)), true);

  for (const cap of [
    navFromCRLog(crLogViewOf({ available: false, reason: "absent" })),
    NAV_SKILLS_UNBUILT,
    NAV_FILES_NO_READER,
    NAV_DEFECTS_NO_SOURCE,
    navReadFailed("too_large"),
    NAV_UNREPORTED,
  ]) {
    assert.equal(countSayable(cap), false, `${cap.kind} must not render as a number`);
  }
});

test("only count and none were derived from the project", () => {
  assert.equal(isProjectDerived({ kind: "count", value: 0, caveat: null }), true);
  assert.equal(isProjectDerived({ kind: "none" }), true);
  for (const cap of [NAV_SKILLS_UNBUILT, NAV_FILES_NO_READER, navReadFailed(), NAV_UNREPORTED]) {
    assert.equal(isProjectDerived(cap), false, `${cap.kind} came from our limits, not the project`);
  }
});

test("a zero with a caveat is not an absence", () => {
  // The stricter question. A count of 0 beside records nobody could open means the
  // decisions may well exist; saying "there are none" there is the substitution the
  // constitution forbids.
  assert.equal(supportsAbsenceClaim({ kind: "none" }), true);
  assert.equal(supportsAbsenceClaim({ kind: "count", value: 0, caveat: null }), true);
  assert.equal(
    supportsAbsenceClaim({ kind: "count", value: 0, caveat: { kind: "unopenable", count: 2 } }),
    false,
    "a zero qualified by unopenable records cannot support 'there are none'",
  );
  assert.equal(supportsAbsenceClaim({ kind: "count", value: 3, caveat: null }), false);
  // And nothing about our own limits licenses the claim either.
  for (const cap of [NAV_SKILLS_UNBUILT, NAV_FILES_NO_READER, navReadFailed(), NAV_UNREPORTED]) {
    assert.equal(supportsAbsenceClaim(cap), false);
  }
});

// --- CR Log ------------------------------------------------------------------

test("the CR count is open rows, and unclassifiable rows qualify it rather than join it", () => {
  const view: CRLogView = { kind: "rows", rows: [], open: 4, unrecognised: 2 };
  const cap = navFromCRLog(view);

  assert.deepEqual(cap, {
    kind: "count",
    value: 4,
    caveat: { kind: "unclassified", count: 2 },
  });
  // The caveat must not have been added to the number: 4 open stays 4 open, and the
  // two rows nobody could classify are the reason 4 may not be the whole story.
  assert.equal(navCapabilityKey(cap), "workspace.nav.capability.countWithCaveat.unclassified");
});

test("a clean CR Log carries no caveat", () => {
  const cap = navFromCRLog({ kind: "rows", rows: [], open: 1, unrecognised: 0 });
  assert.deepEqual(cap, { kind: "count", value: 1, caveat: null });
  assert.equal(navCapabilityKey(cap), "workspace.nav.capability.count");
});

test("each way a CR Log read can fail keeps its own reason", () => {
  for (const [reason, expected] of [
    ["too_large", "workspace.nav.capability.unreadable.too_large"],
    ["unreadable", "workspace.nav.capability.unreadable.unreadable"],
    ["malformed", "workspace.nav.capability.unreadable.malformed"],
  ] as const) {
    const cap = navFromCRLog(crLogViewOf({ available: false, reason }));
    assert.equal(cap.kind, "unreadable", `${reason} is a failed read`);
    assert.equal(navCapabilityKey(cap), expected);
  }
});

// --- decisions ---------------------------------------------------------------

test("decisions absent is a settled nothing", () => {
  assert.deepEqual(
    navFromDecisions(decisions({ available: false, reason: "absent", records: null, unreadable_count: null })),
    { kind: "none" },
  );
});

test("unavailable without a stated reason is a failed read, never a settled nothing", () => {
  // Nobody said why. Reading that as "the project keeps no decisions" would turn our
  // ignorance into a fact about the project.
  for (const reason of [null, "unreadable", "something_new"]) {
    const cap = navFromDecisions(
      decisions({ available: false, reason, records: null, unreadable_count: null }),
    );
    assert.equal(cap.kind, "unreadable", `reason=${String(reason)} must not become 'none'`);
    assert.equal(supportsAbsenceClaim(cap), false);
  }
});

test("a decisions count never appears without the records that could not be opened", () => {
  assert.deepEqual(navFromDecisions(decisions({ records: [{}, {}, {}], unreadable_count: 1 })), {
    kind: "count",
    value: 3,
    caveat: { kind: "unopenable", count: 1 },
  });
});

test("an empty decisions directory is none, but one with only unopenable records is not", () => {
  assert.deepEqual(navFromDecisions(decisions({ records: [], unreadable_count: 0 })), {
    kind: "none",
  });
  // Nothing could be opened, yet records are there. Reporting "none" here would be
  // claiming the project has no decisions on the strength of a failure to read them.
  const cap = navFromDecisions(decisions({ records: [], unreadable_count: 2 }));
  assert.deepEqual(cap, { kind: "count", value: 0, caveat: { kind: "unopenable", count: 2 } });
  assert.equal(supportsAbsenceClaim(cap), false);
});

test("an unreported completeness count is a failed read, not a confirmed zero", () => {
  // `unreadable_count: null` on an available payload contradicts the contract. Reading it
  // as 0 would present the count as if its completeness had been confirmed.
  const cap = navFromDecisions(decisions({ records: [{}, {}], unreadable_count: null }));
  assert.equal(cap.kind, "unreadable");
  assert.equal(countSayable(cap), false);
});

test("available with no records array is treated as a failed read, not as zero", () => {
  const cap = navFromDecisions(decisions({ records: null, unreadable_count: null }));
  assert.equal(cap.kind, "unreadable");
});

// --- the three unbacked items ------------------------------------------------

test("skills says not-built and names the milestone", () => {
  assert.deepEqual(NAV_SKILLS_UNBUILT, { kind: "unbuilt", milestone: "W5" });
  assert.equal(
    navCapabilityKey(NAV_SKILLS_UNBUILT),
    "workspace.nav.capability.unbuiltWithMilestone",
  );
  // "not built yet" is a different sentence from "exists but unreadable".
  assert.notEqual(navCapabilityKey(NAV_SKILLS_UNBUILT), navCapabilityKey(NAV_DEFECTS_NO_SOURCE));
});

test("CR-38: a feature that does not exist yet is not drawn; a fact that cannot be read still is", async () => {
  const { featureAbsent } = await import("../src/lib/nav-capability.ts");
  assert.equal(featureAbsent({ kind: "unbuilt", milestone: "W5" }), true);
  assert.equal(featureAbsent({ kind: "noReader", reason: "reader_unbuilt" }), true);
  assert.equal(featureAbsent({ kind: "noReader", reason: "no_structured_source" }), false);
  assert.equal(featureAbsent({ kind: "unreadable", reason: "too_large" } as never), false);
  assert.equal(featureAbsent({ kind: "none" }), false);
  assert.equal(featureAbsent(undefined), false);
});

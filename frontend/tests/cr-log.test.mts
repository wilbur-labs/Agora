import assert from "node:assert/strict";
import test from "node:test";

import {
  allClearSayable,
  attentionRowsOf,
  crLogHeadlineOf,
  crLogViewOf,
  statusClassOf,
  type CRLogLike,
  type ChangeRequestLike,
} from "../src/lib/cr-log.ts";

function row(over: Partial<ChangeRequestLike> = {}): ChangeRequestLike {
  return {
    id: "CR-1", date: "2026-09-05", source: "agent", description: "d",
    kind: "refine", status: "OPEN", decision: "accept", propagated_to: "",
    status_token: "open", kind_token: "refine",
    ...over,
  };
}

function read(rows: ChangeRequestLike[]): CRLogLike {
  return {
    available: true,
    reason: null,
    rows,
    open_count: rows.filter((r) => r.status_token === "open").length,
    unrecognised_status_count: rows.filter((r) => r.status_token === "unrecognised").length,
    malformed_ids: [],
    source: ".agora/development/CR-LOG.md",
  };
}

const unavailable = (reason: string, malformed: string[] = []): CRLogLike => ({
  available: false, reason, rows: null, open_count: null,
  unrecognised_status_count: null, malformed_ids: malformed,
});

test("a server that predates the field is unknown, not a project without a CR Log", () => {
  for (const missing of [undefined, null]) {
    const view = crLogViewOf(missing);
    assert.equal(view.kind, "unknown");
    assert.equal(view.kind === "unknown" && view.reason, "unreported");
  }
});

test("absent is its own answer and carries no count", () => {
  const view = crLogViewOf(unavailable("absent"));
  assert.equal(view.kind, "none");
  // The type is what stops a count being rendered here: "none" has no fields.
  assert.equal("open" in view, false);
});

test("unreadable and too_large are unknown, never zero", () => {
  for (const reason of ["unreadable", "too_large"]) {
    const view = crLogViewOf(unavailable(reason));
    assert.equal(view.kind, "unknown");
    assert.equal(view.kind === "unknown" && view.reason, reason);
  }
});

test("a malformed log names the rows to fix", () => {
  const view = crLogViewOf(unavailable("malformed", ["CR-7"]));
  assert.equal(view.kind, "unknown");
  assert.deepEqual(view.kind === "unknown" ? view.malformedIds : null, ["CR-7"]);
});

test("an unrecognised unavailable reason is unknown rather than silently absent", () => {
  // A future backend reason this UI has never heard of must not degrade into "this
  // project keeps no CR Log", which is a claim about the project.
  const view = crLogViewOf(unavailable("some_new_reason"));
  assert.equal(view.kind, "unknown");
  assert.equal(view.kind === "unknown" && view.reason, "unreadable");
});

test("a read log carries its rows and both counts", () => {
  const view = crLogViewOf(read([
    row({ id: "CR-1" }),
    row({ id: "CR-2", status: "DONE", status_token: "done" }),
    row({ id: "CR-3", status: "tbd", status_token: "unrecognised" }),
  ]));
  assert.equal(view.kind, "rows");
  if (view.kind !== "rows") return;
  assert.equal(view.open, 1);
  assert.equal(view.unrecognised, 1);
  assert.equal(view.rows.length, 3);
});

test("an available payload missing its counts is unknown, not zero", () => {
  // A server disagreeing with its own contract. The safe reading of a missing number
  // is never 0 — that is the exact shape this layer exists to refuse.
  const view = crLogViewOf({ available: true, reason: null, rows: [], open_count: null,
                             unrecognised_status_count: null });
  assert.equal(view.kind, "unknown");
});

test("a present but empty log may say zero, because it was read", () => {
  const view = crLogViewOf(read([]));
  assert.equal(view.kind, "rows");
  assert.equal(view.kind === "rows" && view.open, 0);
  assert.equal(allClearSayable(view), true);
});

// --- the sentence this module exists to guard -------------------------------

test("all clear is sayable only when every row was classified", () => {
  assert.equal(allClearSayable(crLogViewOf(read([
    row({ id: "CR-1", status: "DONE", status_token: "done" }),
  ]))), true);

  // One row nobody classified: `open` is a floor, so "nothing is open" is a claim
  // wider than the check behind it.
  assert.equal(allClearSayable(crLogViewOf(read([
    row({ id: "CR-1", status: "DONE", status_token: "done" }),
    row({ id: "CR-2", status: "still thinking", status_token: "unrecognised" }),
  ]))), false);
});

test("all clear is never sayable about a log that was not read", () => {
  for (const log of [undefined, unavailable("absent"), unavailable("unreadable"),
                     unavailable("too_large"), unavailable("malformed", ["CR-1"])]) {
    assert.equal(allClearSayable(crLogViewOf(log)), false);
  }
});

test("an open row keeps all clear unsayable", () => {
  assert.equal(allClearSayable(crLogViewOf(read([row({ id: "CR-1" })]))), false);
});

// --- what gets put in front of someone --------------------------------------

test("open rows come first and unclassified ones are not buried under closed rows", () => {
  const view = crLogViewOf(read([
    row({ id: "CR-1", status: "DONE", status_token: "done" }),
    row({ id: "CR-2", status: "tbd", status_token: "unrecognised" }),
    row({ id: "CR-3" }),
  ]));
  assert.deepEqual(attentionRowsOf(view).map((r) => r.id), ["CR-3", "CR-2"]);
});

test("nothing is put forward from a log that was not read", () => {
  assert.deepEqual(attentionRowsOf(crLogViewOf(unavailable("unreadable"))), []);
  assert.deepEqual(attentionRowsOf(crLogViewOf(undefined)), []);
});

// --- regressions from review round 1 ----------------------------------------

test("counts that disagree with the rows are unknown, not a reassurance", () => {
  // The guard used to check only that the counts were numbers, which let
  // {rows: [], open_count: 1} through as a `rows` view. The caller then took its
  // "no rows" branch and said the project had raised nothing, while the same payload
  // reported one open request. A server contradicting itself is not one whose
  // reassuring half should be believed.
  const view = crLogViewOf({
    available: true, reason: null, rows: [], open_count: 1,
    unrecognised_status_count: 0, malformed_ids: [],
  });
  assert.equal(view.kind, "unknown");
  assert.equal(allClearSayable(view), false);
});

test("counts larger than the rows they summarise are unknown", () => {
  const view = crLogViewOf({
    available: true, reason: null, rows: [row()], open_count: 1,
    unrecognised_status_count: 1, malformed_ids: [],
  });
  assert.equal(view.kind, "unknown");
});

test("a negative count is unknown", () => {
  const view = crLogViewOf({
    available: true, reason: null, rows: [row()], open_count: -1,
    unrecognised_status_count: 0, malformed_ids: [],
  });
  assert.equal(view.kind, "unknown");
});

test("counts that agree with the rows still pass", () => {
  const view = crLogViewOf(read([row(), row({ id: "CR-2", status_token: "unrecognised" })]));
  assert.equal(view.kind, "rows");
  assert.equal(view.kind === "rows" && view.open, 1);
});

// --- regressions from review round 2 ----------------------------------------

test("counts are taken from the rows, so a row that says open cannot be summarised as clear", () => {
  // The previous guard only bounded the counts: one row with status_token "open"
  // beside open_count 0 passed, because 0 + 0 <= 1, and the screen showed an OPEN row
  // under the words "no open change requests". A bound on a number is not agreement.
  const view = crLogViewOf({
    available: true, reason: null, rows: [row({ status_token: "open" })],
    open_count: 0, unrecognised_status_count: 0, malformed_ids: [],
  });
  assert.equal(view.kind, "unknown");
  assert.equal(allClearSayable(view), false);
});

test("a server whose counts disagree with its rows is not summarised at all", () => {
  const view = crLogViewOf({
    available: true, reason: null,
    rows: [row({ status_token: "done" }), row({ id: "CR-2", status_token: "unrecognised" })],
    open_count: 0, unrecognised_status_count: 0, malformed_ids: [],
  });
  assert.equal(view.kind, "unknown");
});

// --- the headline, which the component now only renders ---------------------

test("both reassuring sentences sit behind the same gate", () => {
  const empty = crLogHeadlineOf(crLogViewOf(read([])), "p");
  assert.equal(empty.key, "empty");
  assert.equal(empty.tone, "clear");

  const closed = crLogHeadlineOf(
    crLogViewOf(read([row({ status_token: "done" })])), "p");
  assert.equal(closed.key, "allClear");
  assert.equal(closed.tone, "clear");
});

test("an unclassified row denies both reassuring sentences", () => {
  const view = crLogViewOf(read([row({ status_token: "unrecognised" })]));
  const headline = crLogHeadlineOf(view, "p");
  assert.notEqual(headline.key, "allClear");
  assert.notEqual(headline.key, "empty");
  assert.equal(headline.tone, "unknown");
  assert.equal(headline.params.count, "0");
});

test("an open row is announced with its count and an attention tone", () => {
  const headline = crLogHeadlineOf(crLogViewOf(read([row(), row({ id: "CR-2" })])), "p");
  assert.equal(headline.key, "open");
  assert.equal(headline.params.count, "2");
  assert.equal(headline.tone, "attention");
});

test("no unreadable view produces a reassuring sentence", () => {
  for (const log of [undefined, unavailable("absent"), unavailable("unreadable"),
                     unavailable("too_large"), unavailable("malformed", ["CR-1"])]) {
    const headline = crLogHeadlineOf(crLogViewOf(log), "the/path.md");
    assert.notEqual(headline.key, "allClear");
    assert.notEqual(headline.key, "empty");
    assert.notEqual(headline.key, "open");
    assert.equal(headline.tone, "unknown");
  }
});

test("the malformed headline carries the ids to fix, and absence carries the path", () => {
  const bad = crLogHeadlineOf(crLogViewOf(unavailable("malformed", ["CR-7", "CR-9"])), "p");
  assert.equal(bad.key, "malformed");
  assert.equal(bad.params.ids, "CR-7, CR-9");

  const none = crLogHeadlineOf(crLogViewOf(unavailable("absent")), ".agora/x.md");
  assert.equal(none.key, "none");
  assert.equal(none.params.path, ".agora/x.md");
});

// --- regressions from review round 3 ----------------------------------------

test("a status token this UI does not know is unrecognised, never closed", () => {
  // Counting `=== "open"` and `=== "unrecognised"` and calling the remainder closed
  // was the same mistake a third time: one row with status_token "pending" fell into
  // neither bucket, and the strip said "no open change requests" above it. A closed
  // row has to be positively recognised as closed.
  const view = crLogViewOf({
    available: true, reason: null, rows: [row({ status_token: "pending" })],
    open_count: 0, unrecognised_status_count: 1, malformed_ids: [],
  });
  assert.equal(view.kind, "rows");
  assert.equal(view.kind === "rows" && view.unrecognised, 1);
  assert.equal(allClearSayable(view), false);
  assert.notEqual(crLogHeadlineOf(view, "p").key, "allClear");
});

test("a backend that classifies an unknown token differently fails closed", () => {
  // A later backend emitting a token this UI has never seen will disagree with the
  // recomputed count, and the disagreement is resolved as unknown rather than as the
  // reassuring half of it.
  const view = crLogViewOf({
    available: true, reason: null, rows: [row({ status_token: "withdrawn" })],
    open_count: 0, unrecognised_status_count: 0, malformed_ids: [],
  });
  assert.equal(view.kind, "unknown");
});

test("the cockpit strip renders the headline rather than deciding it", async () => {
  // Structural, and honestly so: `node --test` cannot render a React component here,
  // and without this the wording branching could be reintroduced inline while every
  // test above stayed green — which is exactly how it was written the first time.
  // Two conditions, because either alone is easy to satisfy while defeating the
  // other: the component must call the helper, AND it must not name the reassuring
  // message keys itself.
  const { readFile } = await import("node:fs/promises");
  const source = await readFile(
    new URL("../src/app/portfolio/page.tsx", import.meta.url), "utf8");
  const strip = source.slice(source.indexOf("function ProjectChangeLog"));
  assert.ok(strip.includes("crLogHeadlineOf(view, path)"),
    "ProjectChangeLog must take its wording from crLogHeadlineOf");
  for (const key of ["crLog.allClear", "crLog.empty", "crLog.unreadable", "crLog.tooLarge"]) {
    assert.ok(!strip.includes(key),
      `ProjectChangeLog must not choose ${key} itself — that decision belongs to crLogHeadlineOf`);
  }
});

// --- regressions from review round 4 ----------------------------------------

test("an unclassified row is shown, not just counted", () => {
  // The counting was fixed to recognise closed positively; the display filter was
  // not. A row with an unknown token was counted as unclassified and left out of the
  // list, so the strip reported a number with nothing to account for it. One rule,
  // one implementation: both now go through statusClassOf.
  const view = crLogViewOf({
    available: true, reason: null, rows: [row({ id: "CR-9", status_token: "pending" })],
    open_count: 0, unrecognised_status_count: 1, malformed_ids: [],
  });
  assert.equal(view.kind === "rows" && view.unrecognised, 1);
  assert.deepEqual(attentionRowsOf(view).map((r) => r.id), ["CR-9"]);
});

test("the count and the list never describe different rows", () => {
  const rows = [
    row({ id: "CR-1", status_token: "open" }),
    row({ id: "CR-2", status_token: "done" }),
    row({ id: "CR-3", status_token: "decided" }),
    row({ id: "CR-4", status_token: "pending" }),
    row({ id: "CR-5", status_token: "" }),
  ];
  const view = crLogViewOf({
    available: true, reason: null, rows,
    open_count: 1, unrecognised_status_count: 2, malformed_ids: [],
  });
  assert.equal(view.kind, "rows");
  if (view.kind !== "rows") return;
  // Everything not positively closed is listed, and the numbers agree with the list.
  assert.deepEqual(attentionRowsOf(view).map((r) => r.id), ["CR-1", "CR-4", "CR-5"]);
  assert.equal(view.open + view.unrecognised, attentionRowsOf(view).length);
});

test("only decided and done are closed, and an unknown token is not", () => {
  // The earlier version of this test used only decided and done, so it passed with
  // positive recognition reverted. The third row is what makes "only" testable.
  const clear = crLogViewOf({
    available: true, reason: null,
    rows: [row({ status_token: "decided" }), row({ id: "CR-2", status_token: "done" })],
    open_count: 0, unrecognised_status_count: 0, malformed_ids: [],
  });
  assert.equal(allClearSayable(clear), true);

  const notClear = crLogViewOf({
    available: true, reason: null,
    rows: [row({ status_token: "done" }), row({ id: "CR-2", status_token: "closed-ish" })],
    open_count: 0, unrecognised_status_count: 1, malformed_ids: [],
  });
  assert.equal(allClearSayable(notClear), false);
});

// --- regressions from review round 5 ----------------------------------------

test("statusClassOf earns closed and defaults to unrecognised", () => {
  // Asserted directly rather than through the counts. The previous version of this
  // test went through crLogViewOf, where a mutated classifier still produced a count
  // mismatch and therefore an unknown view — so the test passed for the wrong reason
  // and could not detect the mutation at all.
  assert.equal(statusClassOf(row({ status_token: "open" })), "open");
  assert.equal(statusClassOf(row({ status_token: "decided" })), "closed");
  assert.equal(statusClassOf(row({ status_token: "done" })), "closed");
  for (const unknown of ["unrecognised", "pending", "closed", "OPEN", "", "withdrawn"]) {
    assert.equal(statusClassOf(row({ status_token: unknown })), "unrecognised",
      `${unknown} must not be treated as closed`);
  }
});

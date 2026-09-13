import assert from "node:assert/strict";
import test from "node:test";

import {
  MOCKUP_TASK_KIND,
  adoptBlockedBy,
  kickoffStepOf,
  proposalCaveatsOf,
  type KickoffRecordLike,
} from "../src/lib/kickoff.ts";

function record(overrides: Partial<KickoffRecordLike> = {}): KickoffRecordLike {
  return {
    status: "questions",
    round: "r1",
    revision: "abc123",
    brief: "b",
    charter_draft: { goal: "g", non_goals: [], success_criteria: [] },
    questions: [
      { id: "a", question: "A?", why: "", suggestions: [] },
      { id: "b", question: "B?", why: "", suggestions: [] },
    ],
    answers: {},
    needs_mockup: true,
    mockup_note: "",
    read_notes: [],
    proposal: null,
    context: { readme: false, tree_entries: 0, git_log_entries: 0, unread: [], text: "(none)" },
    ...overrides,
  };
}

test("the three non-steps are their own sentences, and only a definite absence offers to start", () => {
  assert.deepEqual(kickoffStepOf(null, false), { kind: "loading" });
  assert.deepEqual(kickoffStepOf(null, true), { kind: "readFailed" });
  assert.deepEqual(kickoffStepOf({ available: false, reason: "absent", record: null }, false), { kind: "start" });
  assert.deepEqual(kickoffStepOf({ available: false, reason: "unreadable", record: null }, false), { kind: "unreadable", reason: "unreadable" });
  assert.deepEqual(kickoffStepOf({ available: false, reason: "malformed", record: null }, false), { kind: "unreadable", reason: "malformed" });
  assert.deepEqual(kickoffStepOf({ available: false, reason: "foreign", record: null }, false), { kind: "unreadable", reason: "foreign" });
  assert.deepEqual(kickoffStepOf({ available: false, reason: "linked", record: null }, false), { kind: "unreadable", reason: "linked" });
  assert.deepEqual(kickoffStepOf({ available: false, reason: "root_missing", record: null }, false), { kind: "unreadable", reason: "root_missing" });
  // A reason this build does not know is not "start here" either.
  assert.deepEqual(kickoffStepOf({ available: false, reason: "future", record: null }, false), { kind: "unreadable", reason: "unreadable" });
});

test("the steps follow the record's status and count only non-blank answers", () => {
  const q = kickoffStepOf({ available: true, reason: null, record: record({ answers: { a: "  " } }) }, false);
  assert.equal(q.kind, "questions");
  if (q.kind === "questions") assert.deepEqual([q.answered, q.total], [0, 2]);
  const answered = kickoffStepOf({ available: true, reason: null, record: record({ status: "answered", answers: { a: "yes" } }) }, false);
  assert.equal(answered.kind, "answered");
  if (answered.kind === "answered") assert.deepEqual([answered.answered, answered.total], [1, 2]);
  const proposal = { charter: { goal: "g", non_goals: [], success_criteria: [] }, tasks: [{ title: "m", description: "", kind: MOCKUP_TASK_KIND }], needs_mockup: true, mockup_task_missing: false };
  const proposed = kickoffStepOf({ available: true, reason: null, record: record({ status: "proposed", proposal }) }, false);
  assert.equal(proposed.kind, "proposed");
  // "proposed" with nothing proposed falls back to answered — there is nothing to adopt.
  assert.equal(kickoffStepOf({ available: true, reason: null, record: record({ status: "proposed", proposal: null }) }, false).kind, "answered");
  const sealedCharter = { goal: "g", non_goals: [], success_criteria: [] };
  const adopted = kickoffStepOf({ available: true, reason: null, record: record({ status: "adopted", adopted: { charter: sealedCharter, approved: [{ key: "k1", title: "one", description: "", kind: "mockup" }], tasks: [{ key: "k1", task_id: "t1", title: "one", kind: "mockup" }] } }) }, false);
  assert.equal(adopted.kind, "adopted");
  // The count is what was created, not what the proposal listed.
  if (adopted.kind === "adopted") {
    assert.equal(adopted.created, 1);
    assert.equal(adopted.charterKept, false);
    // The backend did not say what this control plane holds: no claim is made.
    assert.equal(adopted.here, null);
  }
  const notHere = kickoffStepOf({
    available: true, reason: null,
    here: { applied_here: false, charter: "absent", tasks_recorded: 2, tasks_present: 0, receipt_complete: true, complete: false },
    record: record({ status: "adopted", adopted: { charter: sealedCharter, approved: [], tasks: [] } }),
  }, false);
  assert.equal(notHere.kind === "adopted" && notHere.here?.complete, false);
  // A failed scan travels through untouched, so the panel can say it instead of the counts.
  const scanFailed = kickoffStepOf({
    available: true, reason: null,
    here: { applied_here: false, charter: "absent", tasks_recorded: 3, tasks_present: 0, receipt_complete: false, complete: false, scan_failed: true },
    record: record({ status: "adopted", adopted: { charter: sealedCharter, approved: [], tasks: [] } }),
  }, false);
  assert.equal(scanFailed.kind === "adopted" && scanFailed.here?.scan_failed, true);
  const kept = kickoffStepOf({ available: true, reason: null, record: record({ status: "adopted", adopted: { charter: sealedCharter, approved: [], tasks: [], charter_kept_edit: true } }) }, false);
  assert.equal(kept.kind === "adopted" && kept.charterKept, true);
  const adopting = kickoffStepOf({ available: true, reason: null, record: record({ status: "adopting", proposal, adopted: { charter: sealedCharter, approved: [{ key: "k1", title: "one", description: "", kind: "mockup" }, { key: "k2", title: "two", description: "", kind: "feature" }], tasks: [{ key: "k1", task_id: "t1", title: "one", kind: "mockup" }] } }) }, false);
  // When the backend counted the store, that count wins over the record's own.
  const adoptingCounted = kickoffStepOf({ available: true, reason: null, here: { applied_here: false, charter: "absent", tasks_recorded: 2, tasks_present: 2, receipt_complete: false, complete: false }, record: record({ status: "adopting", proposal, adopted: { charter: sealedCharter, approved: [{ key: "k1", title: "one", description: "", kind: "mockup" }, { key: "k2", title: "two", description: "", kind: "feature" }], tasks: [] } }) }, false);
  assert.equal(adoptingCounted.kind === "adopting" && adoptingCounted.created, 2);
  assert.equal(adoptingCounted.kind === "adopting" && adoptingCounted.scanFailed, false);
  assert.equal(adoptingCounted.kind === "adopting" && adoptingCounted.keyConflict, false);
  assert.equal(adopting.kind, "adopting");
  // Resuming reports what was sealed against what exists — not the proposal's count.
  if (adopting.kind === "adopting") assert.deepEqual([adopting.approved, adopting.created], [2, 1]);
});

test("the caveats are judged on the ticked tasks: unticking the mockup is the case they exist for", () => {
  const ui = { needs_mockup: true };
  assert.deepEqual(proposalCaveatsOf(ui, []), ["noTasks", "mockupTaskMissing"]);
  assert.deepEqual(proposalCaveatsOf(ui, [{ kind: "mockup" }, { kind: "feature" }]), []);
  // The proposal had the mockup first; the person unticked it.
  assert.deepEqual(proposalCaveatsOf(ui, [{ kind: "feature" }]), ["mockupTaskMissing"]);
  // Mockup present but not first is not first.
  assert.deepEqual(proposalCaveatsOf(ui, [{ kind: "feature" }, { kind: "mockup" }]), ["mockupTaskMissing"]);
  assert.deepEqual(proposalCaveatsOf({ needs_mockup: false }, [{ kind: "feature" }]), []);
});

test("the conversation decides its step through this module and never offers 'start' by itself", async () => {
  const { readFile } = await import("node:fs/promises");
  // The panel became the workbench's project conversation (CR-62); the check follows it.
  const panel = await readFile(new URL("../src/components/project-chat.tsx", import.meta.url), "utf8");
  const source = panel.replace(/\/\*[\s\S]*?\*\//g, " ").replace(/\/\/[^\n]*/g, " ");
  assert.ok(source.includes("kickoffStepOf("), "the conversation must derive its step from lib/kickoff.ts");
  assert.ok(source.includes("proposalCaveatsOf("), "the conversation must show the proposal's caveats before adopt");
  assert.ok(!/reason\s*===\s*"absent"/.test(source), "the conversation must not re-decide what an absent record means");
});


test("the adopt button is off, and says why, when the goal or the success criteria are missing", () => {
  assert.equal(adoptBlockedBy("  ", ["c"]), "goal");
  assert.equal(adoptBlockedBy("g", []), "criteria");
  assert.equal(adoptBlockedBy("g", ["  "]), "criteria");
  assert.equal(adoptBlockedBy("g", ["c"]), null);
});

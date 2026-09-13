import assert from "node:assert/strict";
import test from "node:test";

import { kickoffStepOf, type KickoffRecordLike } from "../src/lib/kickoff.ts";
import {
  DRAFT_BRIEF_KEY,
  YOU_DECIDE_ANSWER,
  answerChipsOf,
  composeModeOf,
  currentQuestionOf,
  draftFieldsOf,
  landingNoteOf,
  positionChipsOf,
  projectGateOf,
} from "../src/lib/project-chat.ts";

function record(overrides: Partial<KickoffRecordLike> = {}): KickoffRecordLike {
  return {
    status: "questions",
    round: "r1",
    revision: "abc123",
    brief: "一个给在日华人的日语职场助手",
    charter_draft: { goal: "g", non_goals: [], success_criteria: [] },
    questions: [
      { id: "who", question: "先给谁用？", why: "决定要不要账号", suggestions: ["先给我自己", "小圈子内测"] },
      { id: "first", question: "第一版只做哪一件？", why: "决定首批任务", suggestions: ["邮件润色"] },
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

test("the candidates always end with 'write your own' and 'you decide'", () => {
  const chips = answerChipsOf({ suggestions: ["先给我自己", "小圈子内测"] });
  assert.deepEqual(chips.map((c) => c.kind), ["suggestion", "suggestion", "own", "youDecide"]);
  assert.deepEqual(chips.map((c) => c.value), ["先给我自己", "小圈子内测", null, null]);
  // A question whose suggestions came back empty is still answerable.
  assert.deepEqual(answerChipsOf({ suggestions: [] }).map((c) => c.kind), ["own", "youDecide"]);
  // Blank suggestions are not candidates.
  assert.deepEqual(answerChipsOf({ suggestions: ["  ", "真的"] }).map((c) => c.value), ["真的", null, null]);
});

test("the question on the table is the first unanswered one, in the agent's order", () => {
  assert.equal(currentQuestionOf(record())?.question.id, "who");
  // An answered first question moves to the second.
  assert.equal(currentQuestionOf(record({ answers: { who: "先给我自己" } }))?.question.id, "first");
  // "You decide" is an answer: the question does not come back on a reload.
  assert.equal(currentQuestionOf(record({ answers: { who: YOU_DECIDE_ANSWER } }))?.question.id, "first");
  // Whitespace is not an answer.
  assert.equal(currentQuestionOf(record({ answers: { who: "   " } }))?.question.id, "who");
  assert.equal(currentQuestionOf(record({ answers: { who: "a", first: "b" } })), null);
  // Out of order: the one that was skipped is still the one on the table.
  assert.equal(currentQuestionOf(record({ answers: { first: "b" } }))?.question.id, "who");
});

test("the draft holds a line per question, and an unsettled one is not an empty one", () => {
  const fields = draftFieldsOf(record({ answers: { who: "先给我自己" } }), "who");
  assert.deepEqual(fields.map((f) => f.key), [DRAFT_BRIEF_KEY, "who", "first"]);
  assert.equal(fields[0].value, "一个给在日华人的日语职场助手");
  assert.equal(fields[1].value, "先给我自己");
  assert.equal(fields[1].fresh, true);
  // Not yet answered is null — the panel says "还没聊到" rather than showing a blank.
  assert.equal(fields[2].value, null);
  assert.equal(fields[2].fresh, false);
  // No brief written yet is the same kind of nothing.
  assert.equal(draftFieldsOf(record({ brief: "  " }), null)[0].value, null);
});

test("the compose box is for a different thing at every step, and shut when it must be", () => {
  assert.deepEqual(composeModeOf(kickoffStepOf(null, false)), { kind: "closed", reason: "loading" });
  assert.deepEqual(composeModeOf(kickoffStepOf(null, true)), { kind: "closed", reason: "readFailed" });
  assert.deepEqual(
    composeModeOf(kickoffStepOf({ available: false, reason: "malformed", record: null }, false)),
    { kind: "closed", reason: "unreadable" },
  );
  assert.deepEqual(composeModeOf(kickoffStepOf({ available: false, reason: "absent", record: null }, false)), { kind: "brief" });
  const asking = kickoffStepOf({ available: true, reason: null, record: record() }, false);
  assert.deepEqual(composeModeOf(asking), { kind: "answer", questionId: "who" });
  const allAnswered = kickoffStepOf(
    { available: true, reason: null, record: record({ status: "answered", answers: { who: "a", first: "b" } }) },
    false,
  );
  assert.deepEqual(composeModeOf(allAnswered), { kind: "askForProposal" });
  const proposed = kickoffStepOf({
    available: true, reason: null,
    record: record({
      status: "proposed", answers: { who: "a", first: "b" },
      proposal: { charter: { goal: "g", non_goals: [], success_criteria: ["c"] }, tasks: [], needs_mockup: false, mockup_task_missing: false },
    }),
  }, false);
  assert.deepEqual(composeModeOf(proposed), { kind: "reply" });
  const adopting = kickoffStepOf({
    available: true, reason: null,
    record: record({ status: "adopting", answers: { who: "a", first: "b" }, adopted: { charter: { goal: "g", non_goals: [], success_criteria: [] }, approved: [], tasks: [] } }),
  }, false);
  assert.deepEqual(composeModeOf(adopting), { kind: "closed", reason: "adopting" });
  const adoptedRecord = record({
    status: "adopted", answers: { who: "a", first: "b" },
    adopted: { charter: { goal: "g", non_goals: [], success_criteria: [] }, approved: [], tasks: [] },
  });
  const adopted = kickoffStepOf({ available: true, reason: null, here: null, record: adoptedRecord }, false);
  assert.deepEqual(composeModeOf(adopted), { kind: "idea" });
  // The record travels with the repository; the charter and the tasks do not. On a clone,
  // or a database that was not restored, proposing a card against a charter that is not
  // here would be proposing it against nothing — the re-apply comes first.
  const here = { applied_here: false, charter: "absent" as const, tasks_recorded: 2, tasks_present: 0, receipt_complete: true, complete: false };
  assert.deepEqual(
    composeModeOf(kickoffStepOf({ available: true, reason: null, here, record: adoptedRecord }, false)),
    { kind: "closed", reason: "notAppliedHere" },
  );
  assert.deepEqual(
    composeModeOf(kickoffStepOf({ available: true, reason: null, here: { ...here, applied_here: true, charter: "matches", tasks_present: 2, complete: true }, record: adoptedRecord }, false)),
    { kind: "idea" },
  );
});

test("'this project has no goal' is only said from a read that landed", () => {
  assert.deepEqual(projectGateOf("做一个邮件助手", "landed"), { kind: "open" });
  assert.deepEqual(projectGateOf("", "landed"), { kind: "noCharter" });
  assert.deepEqual(projectGateOf("   ", "landed"), { kind: "noCharter" });
  assert.deepEqual(projectGateOf(null, "landed"), { kind: "noCharter" });
  // A read that failed or has not landed cannot support the claim either way.
  assert.deepEqual(projectGateOf("", "failed"), { kind: "unknown" });
  assert.deepEqual(projectGateOf("做一个邮件助手", "failed"), { kind: "unknown" });
  assert.deepEqual(projectGateOf(null, "pending"), { kind: "unknown" });
});

test("the position chips never offer the same outcome twice", () => {
  assert.deepEqual(positionChipsOf(2, 3).map((c) => [c.kind, c.position]), [
    ["suggested", 2], ["last", 4], ["later", null], ["no", null],
  ]);
  // The agent suggested the end: "at 4" and "last" would be one outcome under two labels.
  assert.deepEqual(positionChipsOf(4, 3).map((c) => c.kind), ["last", "later", "no"]);
  // No suggestion (or a nonsensical one) still leaves a way to place it.
  assert.deepEqual(positionChipsOf(null, 0).map((c) => [c.kind, c.position]), [
    ["last", 1], ["later", null], ["no", null],
  ]);
  assert.deepEqual(positionChipsOf(0, 2).map((c) => c.kind), ["last", "later", "no"]);
});

test("where the card landed is read back, not assumed from what was asked", () => {
  assert.deepEqual(landingNoteOf({ asked_position: 2, landed_at: 2, order: [1, 2, 3, 4] }), { kind: "exact", position: 2 });
  // Asked for the end of a shorter order than the one it was shown: still the end.
  assert.deepEqual(landingNoteOf({ asked_position: 9, landed_at: 2, order: [1, 2] }), { kind: "exact", position: 2 });
  // Neighbours that share a priority cannot be split — the panel says where it went.
  assert.deepEqual(landingNoteOf({ asked_position: 3, landed_at: 2, order: [1, 2, 3] }), { kind: "moved", asked: 3, position: 2 });
  // Past the ceiling the board is read to, nothing is claimed at all.
  assert.deepEqual(landingNoteOf({ asked_position: 3, landed_at: null, order: [] }), { kind: "unknown" });
});

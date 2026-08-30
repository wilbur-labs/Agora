import assert from "node:assert/strict";
import test from "node:test";

import {
  CONTINUE_BLOCK_KEY,
  CONTINUE_PROMPT_LIMIT,
  CONTINUE_PROMPT_TAIL_BUDGET,
  DISPATCH_FAILURE_KEY,
  RUN_GATE_CODES,
  classifyDispatchFailure,
  clampPrompt,
  confirmsNothingRunning,
  continueEntryGate,
  continueGate,
  dispatchOutcomeNeedsReread,
  mergeRunUpdates,
  promptIsSendable,
  provisionNoteKey,
  provisionResultGate,
  seedContinuePrompt,
  workspaceReadiness,
  worktreeNotice,
  type ContinueSeedFacts,
  type WorkspaceReadiness,
} from "../src/lib/continue-run.ts";
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

/** A translate function backed by the real dictionary, so a missing key fails. */
function translator(locale: Locale) {
  return (key: string, vars?: Record<string, string | number>): string => {
    const template = lookup(locale, key);
    assert.ok(template !== undefined, `missing ${locale} message: ${key}`);
    return template.replace(/\{(\w+)\}/g, (match, name: string) =>
      vars && name in vars ? String(vars[name]) : match,
    );
  };
}

const facts = (over: Partial<ContinueSeedFacts> = {}): ContinueSeedFacts => ({
  title: "Add email login",
  description: "",
  acceptance: [],
  recapHeading: "Yesterday",
  recapLines: ["codex delivered 3 file(s)"],
  recapMore: 0,
  recapPartial: false,
  recapUnavailable: false,
  nextAction: null,
  nextActionUnavailable: false,
  ...over,
});

test("an unreadable worktree status is unknown, never a missing worktree", () => {
  assert.deepEqual(
    workspaceReadiness({ loading: false, readFailed: "network down", status: null }),
    { kind: "unreadable", detail: "network down" },
  );
  // Even with a stale status still in hand, the failed read wins: acting on the
  // old answer is what would silently create or overwrite a worktree.
  assert.equal(
    workspaceReadiness({
      loading: false,
      readFailed: "network down",
      status: { state: "missing", source_is_git: true },
    }).kind,
    "unreadable",
  );
});

test("worktree readiness separates provisionable, foreign, non-git, and unknown states", () => {
  const git = { source_is_git: true };
  assert.deepEqual(
    workspaceReadiness({ loading: false, readFailed: null, status: { state: "ready", branch: "agora/x", ...git } }),
    { kind: "ready", branch: "agora/x" },
  );
  assert.deepEqual(
    workspaceReadiness({ loading: false, readFailed: null, status: { state: "missing", ...git } }),
    { kind: "provisionable", detail: null },
  );
  assert.equal(
    workspaceReadiness({ loading: false, readFailed: null, status: { state: "missing", source_is_git: false } }).kind,
    "notGit",
  );
  assert.equal(
    workspaceReadiness({ loading: false, readFailed: null, status: { state: "foreign", ...git } }).kind,
    "foreign",
  );
  assert.equal(
    workspaceReadiness({ loading: true, readFailed: null, status: null }).kind,
    "checking",
  );
  // A state this UI does not know is a newer backend, not a green light.
  assert.deepEqual(
    workspaceReadiness({ loading: false, readFailed: null, status: { state: "quarantined", ...git } }),
    { kind: "unprovisionable", detail: "quarantined" },
  );
});

test("Continue is unavailable while a run for the task is active", () => {
  assert.deepEqual(
    continueEntryGate({ taskState: "running", activeRunCount: 1, openAttentionCount: 0, followUpArmed: false, dispatching: false }),
    { ready: false, needsProvision: false, block: "runActive" },
  );
  assert.deepEqual(
    continueEntryGate({ taskState: "running", activeRunCount: 0, openAttentionCount: 0, followUpArmed: false, dispatching: false }),
    { ready: true, needsProvision: false, block: null },
  );
});

test("the auto-review handoff window blocks Continue before any review run exists", () => {
  // The exact hole: with auto_review on, the worker run is marked terminal and
  // only then is the review run built and queued. In that gap nothing is active,
  // no gate has been raised, and the backend would accept a second worker run.
  assert.deepEqual(
    continueEntryGate({ taskState: "running", activeRunCount: 0, openAttentionCount: 0, followUpArmed: true, dispatching: false }),
    { ready: false, needsProvision: false, block: "followUpPending" },
  );
  // A ready worktree does not unblock it either.
  assert.equal(
    continueGate({
      taskState: "running", activeRunCount: 0, openAttentionCount: 0, followUpArmed: true,
      worktree: { kind: "ready", branch: "agora/x" }, dispatching: false,
    }).ready,
    false,
  );
  // An open gate is the more useful thing to say once one actually exists.
  assert.equal(
    continueEntryGate({ taskState: "running", activeRunCount: 0, openAttentionCount: 1, followUpArmed: true, dispatching: false }).block,
    "attentionOpen",
  );
});

test("an unreadable run list blocks Continue instead of reading as 'nothing running'", () => {
  assert.deepEqual(
    continueEntryGate({ taskState: "planned", activeRunCount: null, openAttentionCount: 0, followUpArmed: false, dispatching: false }),
    { ready: false, needsProvision: false, block: "runsUnknown" },
  );
});

test("an open human gate blocks Continue even though the task still reads 'running'", () => {
  // The exact hole: the auto-review flow settles the worker run and raises an
  // approval while the task stays 'running', so state + runs alone say "idle".
  assert.deepEqual(
    continueEntryGate({ taskState: "running", activeRunCount: 0, openAttentionCount: 1, followUpArmed: false, dispatching: false }),
    { ready: false, needsProvision: false, block: "attentionOpen" },
  );
  // And a ready worktree cannot unblock it either.
  assert.equal(
    continueGate({
      taskState: "running", activeRunCount: 0, openAttentionCount: 2, followUpArmed: false,
      worktree: { kind: "ready", branch: "agora/x" }, dispatching: false,
    }).block,
    "attentionOpen",
  );
});

test("unreadable attention blocks Continue instead of reading as 'nothing waiting'", () => {
  assert.deepEqual(
    continueEntryGate({ taskState: "running", activeRunCount: 0, openAttentionCount: null, followUpArmed: false, dispatching: false }),
    { ready: false, needsProvision: false, block: "attentionUnknown" },
  );
  // An active run is the more immediate fact, so it is what gets said.
  assert.equal(
    continueEntryGate({ taskState: "running", activeRunCount: 1, openAttentionCount: null, followUpArmed: false, dispatching: false }).block,
    "runActive",
  );
});

test("only a planned or running task can be continued", () => {
  for (const state of ["backlog", "requirements", "design", "review", "verified", "done", "blocked", "failed", "cancelled"]) {
    assert.equal(
      continueEntryGate({ taskState: state, activeRunCount: 0, openAttentionCount: 0, followUpArmed: false, dispatching: false }).block,
      "taskNotRunnable",
      state,
    );
  }
  for (const state of ["planned", "running"]) {
    assert.equal(
      continueEntryGate({ taskState: state, activeRunCount: 0, openAttentionCount: 0, followUpArmed: false, dispatching: false }).ready,
      true,
      state,
    );
  }
});

test("a missing worktree is provisioned by the dispatch, an unknown one is not", () => {
  const base = { taskState: "running", activeRunCount: 0, openAttentionCount: 0, followUpArmed: false, dispatching: false };
  assert.deepEqual(
    continueGate({ ...base, worktree: { kind: "provisionable", detail: null } }),
    { ready: true, needsProvision: true, block: null },
  );
  assert.deepEqual(
    continueGate({ ...base, worktree: { kind: "ready", branch: "agora/x" } }),
    { ready: true, needsProvision: false, block: null },
  );
  assert.deepEqual(
    continueGate({ ...base, worktree: { kind: "unreadable", detail: "boom" } }),
    { ready: false, needsProvision: false, block: "worktreeUnreadable" },
  );
  assert.equal(
    continueGate({ ...base, worktree: { kind: "provisioning" } }).ready,
    false,
  );
  // The task/run facts outrank the worktree: a ready worktree cannot unblock a
  // task that already has a run in flight.
  assert.equal(
    continueGate({ ...base, activeRunCount: 2, worktree: { kind: "ready", branch: null } }).block,
    "runActive",
  );
});

test("a 409 reads as 'already running' only when a run really is active", () => {
  assert.deepEqual(
    classifyDispatchFailure({ status: 409, detail: "…", activeRunCount: 1 }),
    { kind: "alreadyRunning" },
  );
  assert.deepEqual(
    classifyDispatchFailure({ status: 409, detail: "…", activeRunCount: 0 }),
    { kind: "taskChanged" },
  );
});

test("an uncoded 409 whose re-read failed is its own unknown, not 'the task changed'", () => {
  // The hole: activeRunCount === null (the re-read failed) was classified
  // taskChanged, and confirmsNothingRunning treats taskChanged as a confirmed
  // absence — so the panel could say "nothing is running now" while the very run
  // that caused the 409 was executing. The count being absent is two facts, not
  // one: "no runs" and "could not find out".
  const failed = classifyDispatchFailure({
    status: 409,
    detail: "Task already has an unfinished worker run (run_9)",
    activeRunCount: null,
    rereadFailed: true,
  });
  assert.deepEqual(failed, {
    kind: "conflictUnknown",
    detail: "Task already has an unfinished worker run (run_9)",
  });
  // …and it may not claim the absence.
  assert.equal(confirmsNothingRunning(failed), false);
  assert.equal(provisionNoteKey(failed), "workspace.continue.errors.afterProvisionUnsure");

  // A caller that does not pass the flag still fails closed on a null count.
  assert.deepEqual(
    classifyDispatchFailure({ status: 409, detail: "…", activeRunCount: null }),
    { kind: "conflictUnknown", detail: "…" },
  );
  // A re-read that succeeded and found zero keeps taskChanged, which is the only
  // case that has ever established it.
  assert.deepEqual(
    classifyDispatchFailure({ status: 409, detail: "…", activeRunCount: 0, rereadFailed: false }),
    { kind: "taskChanged" },
  );
  // A coded refusal names its own reason, so it stands even without the re-read.
  assert.deepEqual(
    classifyDispatchFailure({
      status: 409, detail: "gate", activeRunCount: null, rereadFailed: true,
      conflictCode: RUN_GATE_CODES.attentionOpen,
    }),
    { kind: "gateOpen", detail: "gate" },
  );
  assert.deepEqual(
    classifyDispatchFailure({
      status: 409, detail: "review", activeRunCount: null, rereadFailed: true,
      conflictCode: RUN_GATE_CODES.reviewActive,
    }),
    { kind: "reviewActive", detail: "review" },
  );
  // The same distinction one level up: a lost POST whose re-read failed is
  // startUnknown, and the flag alone is enough to reach it.
  assert.deepEqual(
    classifyDispatchFailure({
      status: null, detail: "Failed to fetch", activeRunCount: 0, rereadFailed: true,
    }),
    { kind: "startUnknown", detail: "Failed to fetch" },
  );
});

test("a coded 409 is a gate refusal, not 'the task changed'", () => {
  // The control plane now enforces Run -> Gate ordering in the create
  // transaction, and labels the refusal so the panel can say how to clear it.
  // Deriving it from the run count would be wrong twice over: a gate is not a
  // run, and "the task changed underneath you" is not the action to take.
  assert.deepEqual(
    classifyDispatchFailure({
      status: 409,
      detail: "Task has 1 open gate item(s) waiting for you: approval attn_1 …",
      activeRunCount: 0,
      conflictCode: RUN_GATE_CODES.attentionOpen,
    }),
    { kind: "gateOpen", detail: "Task has 1 open gate item(s) waiting for you: approval attn_1 …" },
  );
  assert.deepEqual(
    classifyDispatchFailure({
      status: 409,
      detail: "Task is under review: review run run_1 is running.",
      activeRunCount: 1,
      conflictCode: RUN_GATE_CODES.reviewActive,
    }),
    { kind: "reviewActive", detail: "Task is under review: review run run_1 is running." },
  );
  // An unrecognised code (a newer backend) falls back to the run-count reading
  // rather than being reported as a gate nobody can name.
  assert.deepEqual(
    classifyDispatchFailure({ status: 409, detail: "…", activeRunCount: 2, conflictCode: "future_code" }),
    { kind: "alreadyRunning" },
  );
});

test("the review-owed refusal is its own answer, not 'the task changed'", () => {
  // The control plane now also refuses inside the handoff window: the previous
  // worker run succeeded and its independent review has not been created yet. The
  // run count is zero there — nothing is active — so deriving the reason from it
  // would report "the task changed underneath this page", which is neither true
  // nor actionable. The refusal releases itself when the review appears, so the
  // message is "wait and retry", and that is only sayable from the code.
  assert.deepEqual(
    classifyDispatchFailure({
      status: 409,
      detail: "Worker run run_1 succeeded and its independent cross-review has not been created yet…",
      activeRunCount: 0,
      conflictCode: RUN_GATE_CODES.reviewOwed,
    }),
    {
      kind: "reviewOwed",
      detail: "Worker run run_1 succeeded and its independent cross-review has not been created yet…",
    },
  );
  // It stands without the re-read, like the other two coded refusals.
  assert.deepEqual(
    classifyDispatchFailure({
      status: 409, detail: "owed", activeRunCount: null, rereadFailed: true,
      conflictCode: RUN_GATE_CODES.reviewOwed,
    }),
    { kind: "reviewOwed", detail: "owed" },
  );
  // And it establishes that nothing is running: the create transaction refused
  // before inserting anything, and what is owed is a run that does not exist.
  assert.equal(confirmsNothingRunning({ kind: "reviewOwed", detail: "owed" }), true);
  assert.equal(
    provisionNoteKey({ kind: "reviewOwed", detail: "owed" }),
    "workspace.continue.errors.afterProvision",
  );
  // Every refusal kind has a message key; a new kind without one renders blank.
  assert.equal(DISPATCH_FAILURE_KEY.reviewOwed, "workspace.continue.errors.reviewOwed");
});

test("an uncertain POST is answered by re-reading the runs, not by assuming absence", () => {
  // The hole: the server created and scheduled the run, and the reply was lost.
  // Only 409 used to trigger a re-read, so the page kept its stale zero count and
  // could say "nothing is running now" over a live run.
  assert.equal(dispatchOutcomeNeedsReread(null), true);
  assert.equal(dispatchOutcomeNeedsReread(500), true);
  assert.equal(dispatchOutcomeNeedsReread(503), true);
  assert.equal(dispatchOutcomeNeedsReread(409), true);
  // These are the control plane deciding NOT to create a run; the outcome is known.
  assert.equal(dispatchOutcomeNeedsReread(404), false);
  assert.equal(dispatchOutcomeNeedsReread(422), false);
  assert.equal(dispatchOutcomeNeedsReread(400), false);

  // A run is active: as far as the human is concerned the dispatch landed.
  assert.deepEqual(
    classifyDispatchFailure({ status: null, detail: "Failed to fetch", activeRunCount: 1 }),
    { kind: "startedUnconfirmed", detail: "Failed to fetch" },
  );
  // The re-read failed too: unknown, which is not absence.
  assert.deepEqual(
    classifyDispatchFailure({ status: null, detail: "Failed to fetch", activeRunCount: null }),
    { kind: "startUnknown", detail: "Failed to fetch" },
  );
  // Nothing active — still kept apart from a confirmed "no run was created",
  // because a run may have been created and already settled.
  assert.deepEqual(
    classifyDispatchFailure({ status: null, detail: "Failed to fetch", activeRunCount: 0 }),
    { kind: "transportFailed", detail: "Failed to fetch" },
  );
  // A 5xx is the same uncertainty: the server may already have created the run.
  assert.deepEqual(
    classifyDispatchFailure({ status: 500, detail: "   ", activeRunCount: 1 }),
    { kind: "startedUnconfirmed", detail: "no detail was returned" },
  );
  assert.deepEqual(
    classifyDispatchFailure({ status: 500, detail: "   ", activeRunCount: null }),
    { kind: "startUnknown", detail: "no detail was returned" },
  );
});

test("only an outcome that establishes it may claim nothing is running", () => {
  // The exact sentence at issue: "the worktree was created and is ready — only
  // the run failed to start, and nothing is running now."
  for (const failure of [
    { kind: "taskChanged" },
    { kind: "taskGone", detail: "x" },
    { kind: "rejected", detail: "x" },
    { kind: "provisionFailed", detail: "x" },
    { kind: "worktreeNotReady", detail: "x" },
    { kind: "gateOpen", detail: "x" },
  ] as const) {
    assert.equal(confirmsNothingRunning(failure), true, failure.kind);
    assert.equal(provisionNoteKey(failure), "workspace.continue.errors.afterProvision", failure.kind);
  }
  for (const failure of [
    { kind: "alreadyRunning" },
    { kind: "reviewActive", detail: "x" },
    { kind: "conflictUnknown", detail: "x" },
    { kind: "startedUnconfirmed", detail: "x" },
    { kind: "startUnknown", detail: "x" },
    { kind: "transportFailed", detail: "x" },
    { kind: "unknown", detail: "x" },
  ] as const) {
    assert.equal(confirmsNothingRunning(failure), false, failure.kind);
    assert.equal(
      provisionNoteKey(failure),
      "workspace.continue.errors.afterProvisionUnsure",
      failure.kind,
    );
  }
});

test("provisioning that did not end 'ready' stops the dispatch, and says which state", () => {
  const describe = (state: string) => `state:${state}`;
  assert.deepEqual(provisionResultGate({ state: "ready" }, describe), { dispatch: true });
  // Still being created: not an error, and still not a directory to dispatch into.
  assert.deepEqual(
    provisionResultGate({ state: "provisioning" }, describe),
    { dispatch: false, failure: { kind: "worktreeNotReady", detail: "state:provisioning" } },
  );
  // The status' own error is preferred over the generic state wording.
  assert.deepEqual(
    provisionResultGate({ state: "error", error: "worktree add failed" }, describe),
    { dispatch: false, failure: { kind: "worktreeNotReady", detail: "worktree add failed" } },
  );
  for (const state of ["missing", "foreign", "quarantined", ""]) {
    assert.equal(provisionResultGate({ state }, describe).dispatch, false, state);
  }
});

test("404, 422, and everything else stay distinct failures", () => {
  assert.deepEqual(
    classifyDispatchFailure({ status: 404, detail: "Task not found: t1", activeRunCount: 0 }),
    { kind: "taskGone", detail: "Task not found: t1" },
  );
  assert.deepEqual(
    classifyDispatchFailure({ status: 422, detail: "prompt may not be blank", activeRunCount: 0 }),
    { kind: "rejected", detail: "prompt may not be blank" },
  );
  // A 4xx that is not one of the known refusals: the server answered and did not
  // create a run, so this is a plain failure rather than an uncertain outcome.
  assert.deepEqual(
    classifyDispatchFailure({ status: 400, detail: "Bad Request", activeRunCount: 0 }),
    { kind: "unknown", detail: "Bad Request" },
  );
});

test("the seeded prompt carries the recap, the goal, and the recorded next step", () => {
  const t = translator("en");
  const prompt = seedContinuePrompt(
    facts({
      description: "Email only, no social login.",
      acceptance: ["A user can sign in with email", ""],
      nextAction: "Start the build",
    }),
    t,
  );
  assert.match(prompt, /Add email login/);
  assert.match(prompt, /Email only, no social login\./);
  assert.match(prompt, /- A user can sign in with email/);
  assert.match(prompt, /Yesterday/);
  assert.match(prompt, /- codex delivered 3 file\(s\)/);
  assert.match(prompt, /Start the build/);
  assert.match(prompt, /Continue this task from that point/);
  // No blank bullet from the empty acceptance line.
  assert.doesNotMatch(prompt, /-\s*$/m);
});

test("an unreadable recap is said out loud in the seed, not silently dropped", () => {
  const t = translator("en");
  const unreadable = seedContinuePrompt(
    facts({ recapUnavailable: true, recapLines: [], recapHeading: null }),
    t,
  );
  assert.match(unreadable, /could not be read/);
  assert.doesNotMatch(unreadable, /Nothing has been recorded/);

  const never = seedContinuePrompt(facts({ recapHeading: null, recapLines: [] }), t);
  assert.match(never, /Nothing has been recorded on this task yet\./);

  const quietDay = seedContinuePrompt(facts({ recapLines: [] }), t);
  assert.match(quietDay, /nothing that could be summarized/);

  const noNextStep = seedContinuePrompt(facts({ nextActionUnavailable: true }), t);
  assert.match(noNextStep, /next step could not be read/);
});

test("a truncated recap is said out loud in the seed, items and all", () => {
  const t = translator("en");
  // The hole: the Resume Brief tells the human when recap.more > 0 or
  // recap.partial is set, but the seed mapped only `items` — so the history handed
  // to the agent looked complete.
  const trimmed = seedContinuePrompt(facts({ recapMore: 4 }), t);
  assert.match(trimmed, /- codex delivered 3 file\(s\)/);
  assert.match(trimmed, /incomplete: 4 more recorded item\(s\)/);

  const partial = seedContinuePrompt(facts({ recapPartial: true }), t);
  assert.match(partial, /treat the summary above as partial/);

  // Both facts travel together when both are set.
  const both = seedContinuePrompt(facts({ recapMore: 2, recapPartial: true }), t);
  assert.match(both, /2 more recorded item\(s\)/);
  assert.match(both, /treat the summary above as partial/);

  // The case that was an outright false claim: zero loaded items AND a trimmed
  // read. "That day had activity, but nothing that could be summarized" asserts
  // something the records do not support, so it must not be said.
  const withheld = seedContinuePrompt(facts({ recapLines: [], recapMore: 6 }), t);
  assert.doesNotMatch(withheld, /nothing that could be summarized/);
  assert.match(withheld, /6 more recorded item\(s\)/);
  assert.match(withheld, /Yesterday/);

  const partialAndEmpty = seedContinuePrompt(
    facts({ recapLines: [], recapMore: 0, recapPartial: true }),
    t,
  );
  assert.doesNotMatch(partialAndEmpty, /nothing that could be summarized/);
  assert.match(partialAndEmpty, /partial/);

  // A genuinely quiet day with nothing withheld keeps its own answer.
  const quiet = seedContinuePrompt(facts({ recapLines: [] }), t);
  assert.match(quiet, /nothing that could be summarized/);

  // An unreadable recap still wins over both: there is no day to describe.
  const unreadable = seedContinuePrompt(
    facts({ recapUnavailable: true, recapHeading: null, recapLines: [], recapMore: 9, recapPartial: true }),
    t,
  );
  assert.match(unreadable, /could not be read/);
  assert.doesNotMatch(unreadable, /9 more/);
});

test("an oversized seed is cut inside the prompt limit and says it was cut", () => {
  const t = translator("en");
  const long = seedContinuePrompt(facts({ description: "x".repeat(CONTINUE_PROMPT_LIMIT * 2) }), t);
  assert.equal(long.length <= CONTINUE_PROMPT_LIMIT, true);
  assert.match(long, /cut to fit the prompt limit/);
  // A prompt that already fits is returned untouched.
  assert.equal(clampPrompt("short", t), "short");
});

test("truncation keeps the closing instruction and the next step, and says what it cut", () => {
  const t = translator("en");
  // Reachable with valid input: a task description may legitimately run past the
  // prompt limit on its own. The old clamp kept only the prefix, so the recorded
  // next step and the closing "stop and ask rather than guess" instruction — the
  // one sentence that keeps the run bounded — were the first things dropped.
  const oversized = seedContinuePrompt(
    facts({
      description: "x".repeat(20_000),
      acceptance: ["A user can sign in with email"],
      nextAction: "Start the build",
    }),
    t,
  );
  assert.equal(oversized.length <= CONTINUE_PROMPT_LIMIT, true);
  // The tail survived, in order, at the end.
  assert.match(oversized, /Recorded next step:\nStart the build/);
  assert.match(oversized, /stop and say what you need instead of guessing\.$/);
  // The cut is stated at the cut point, not left to be inferred.
  assert.match(oversized, /Part of this seed was cut to fit the prompt limit/);
  // The body up to the cut is still there, and the acceptance criteria that fell
  // past it are gone — which is what the marker announces.
  assert.match(oversized, /Task goal:\nAdd email login/);
  assert.doesNotMatch(oversized, /A user can sign in with email/);
  // The marker sits between the trimmed body and the preserved tail.
  const markerAt = oversized.indexOf("Part of this seed was cut");
  assert.ok(markerAt > 0);
  assert.ok(oversized.indexOf("Recorded next step:") > markerAt);

  // An unreadable next step is part of the tail too: "could not be read" is a fact
  // the agent needs, and dropping it silently asserts there was nothing to read.
  const unknownNext = seedContinuePrompt(
    facts({ description: "y".repeat(20_000), nextActionUnavailable: true }),
    t,
  );
  assert.match(unknownNext, /next step could not be read/);
  assert.match(unknownNext, /instead of guessing\.$/);

  // Both locales, since both markers and both tail blocks are localized.
  for (const locale of ["en", "zh"] as const) {
    const cut = seedContinuePrompt(
      facts({ description: "z".repeat(20_000), nextAction: "n" }),
      translator(locale),
    );
    assert.equal(cut.length <= CONTINUE_PROMPT_LIMIT, true, locale);
    assert.doesNotMatch(cut, /workspace\.continue\./, locale);
  }
});

test("clampPrompt reserves the tail budget and cuts a huge tail from its front", () => {
  const t = translator("en");
  // No tail: the original single-marker behaviour, unchanged.
  const bodyOnly = clampPrompt("x".repeat(CONTINUE_PROMPT_LIMIT + 50), t);
  assert.equal(bodyOnly.length <= CONTINUE_PROMPT_LIMIT, true);
  assert.match(bodyOnly, /\[Seed cut to fit the prompt limit\.\]$/);

  // A tail short enough to fit survives whole, and the body pays for it.
  const tail = "\n\nTAIL-START middle TAIL-END";
  const withTail = clampPrompt("x".repeat(CONTINUE_PROMPT_LIMIT), t, tail);
  assert.equal(withTail.length <= CONTINUE_PROMPT_LIMIT, true);
  assert.equal(withTail.endsWith(tail), true);

  // A tail bigger than its reservation is cut from the FRONT, so the last block —
  // the closing instruction — is the last thing to go.
  const hugeTail = `HEAD${"t".repeat(CONTINUE_PROMPT_TAIL_BUDGET)}KEEP-ME`;
  const clipped = clampPrompt("x".repeat(CONTINUE_PROMPT_LIMIT), t, hugeTail);
  assert.equal(clipped.length <= CONTINUE_PROMPT_LIMIT, true);
  assert.equal(clipped.endsWith("KEEP-ME"), true);
  assert.equal(clipped.includes("HEAD"), false);

  // Nothing is cut when the whole thing already fits, tail included.
  assert.equal(clampPrompt("body", t, "-tail"), "body-tail");
});

test("an empty or oversized prompt is not sendable", () => {
  assert.equal(promptIsSendable("   \n "), false);
  assert.equal(promptIsSendable("do the thing"), true);
  assert.equal(promptIsSendable("x".repeat(CONTINUE_PROMPT_LIMIT + 1)), false);
});

test("every Continue message key exists in both locales", () => {
  const readiness: WorkspaceReadiness[] = [
    { kind: "checking" },
    { kind: "ready", branch: "agora/x" },
    { kind: "provisionable", detail: null },
    { kind: "provisioning" },
    { kind: "unreadable", detail: "boom" },
    { kind: "foreign" },
    { kind: "notGit" },
    { kind: "unprovisionable", detail: "boom" },
  ];
  const keys = [
    ...Object.values(CONTINUE_BLOCK_KEY),
    ...Object.values(DISPATCH_FAILURE_KEY),
    ...readiness.map((state) => worktreeNotice(state).key),
    "workspace.continue.action",
    "workspace.continue.panelTitle",
    "workspace.continue.panelHint",
    "workspace.continue.start",
    "workspace.continue.starting",
    "workspace.continue.provisioning",
    "workspace.continue.cancel",
    "workspace.continue.reseed",
    "workspace.continue.count",
    "workspace.continue.adapterNote",
    "workspace.continue.errors.afterProvision",
    "workspace.continue.errors.afterProvisionUnsure",
    // Both truncation markers, so a locale missing one is caught here rather than
    // only by whichever test happens to build an oversized prompt.
    "workspace.continue.seed.truncated",
    "workspace.continue.seed.truncatedMiddle",
  ];
  for (const locale of ["en", "zh"] as const) {
    for (const key of keys) {
      assert.ok(lookup(locale, key), `missing ${locale} message: ${key}`);
    }
    // The seed's own keys are exercised by building a prompt with a translator
    // that asserts on any key the dictionary does not have. Both truncation facts
    // are set so their messages are covered in both locales too.
    const prompt = seedContinuePrompt(
      facts({
        description: "d", acceptance: ["a"], nextAction: "n",
        recapMore: 3, recapPartial: true,
      }),
      translator(locale),
    );
    assert.doesNotMatch(prompt, /workspace\.continue\./, locale);
    // …and the quiet-day wording, which the branch above skips.
    assert.doesNotMatch(
      seedContinuePrompt(facts({ recapLines: [] }), translator(locale)),
      /workspace\.continue\./,
      locale,
    );
  }
});

test("a stale poll answer never overwrites a newer run record", () => {
  const first = { run_id: "run_1", version: 3, state: "running" };
  const second = { run_id: "run_2", version: 1, state: "queued" };
  const current = [first, second];

  assert.deepEqual(
    mergeRunUpdates(current, [{ run_id: "run_1", version: 4, state: "succeeded" }]),
    [{ run_id: "run_1", version: 4, state: "succeeded" }, second],
  );
  assert.deepEqual(
    mergeRunUpdates(current, [{ run_id: "run_1", version: 2, state: "queued" }]),
    current,
  );
  // A run this view has not seen is added, not dropped.
  assert.deepEqual(
    mergeRunUpdates(current, [{ run_id: "run_3", version: 1, state: "queued" }]),
    [...current, { run_id: "run_3", version: 1, state: "queued" }],
  );
  assert.deepEqual(mergeRunUpdates(null, [second]), [second]);
});

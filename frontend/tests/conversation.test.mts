import assert from "node:assert/strict";

import { MAX_PAGE } from "../src/lib/paged.ts";
import { test } from "node:test";

import {
  EMPTY_TRANSCRIPT,
  WORKER_ROLE,
  conversationOrder,
  mergeTranscript,
  nextPollDelay,
  orphanSession,
  sendWouldBeRefused,
  sessionVerdict,
  willContinue,
  willStartFresh,
  formatDuration,
  nestedSummaryOf,
  streamEventsOf,
  turnDurationMs,
  type AgentSessionLike,
  type OutputSliceLike,
  type TurnLike,
} from "../src/lib/conversation.ts";

function session(overrides: Partial<AgentSessionLike> = {}): AgentSessionLike {
  return {
    task_id: "t1",
    adapter: "codex",
    state: "active",
    session_id: "sid-1",
    workspace: "D:/wt/codex",
    reason: null,
    turns: 3,
    ...overrides,
  };
}

function slice(overrides: Partial<OutputSliceLike> = {}): OutputSliceLike {
  return { text: "", offset: 0, next_offset: 0, reachable: true, ...overrides };
}

test("a session list that was never read is unknown, not empty", () => {
  // The whole point: "I could not ask" must not render as "you have no
  // conversation here", which would tell the user their next message starts
  // fresh when it may well continue three turns of context.
  assert.equal(sessionVerdict(null, "codex").kind, "unknown");
  assert.equal(sessionVerdict([], "codex").kind, "none");
});

test("an active session reports its id and turn count", () => {
  const verdict = sessionVerdict([session()], "codex");
  assert.deepEqual(verdict, { kind: "active", sessionId: "sid-1", turns: 3 });
  assert.equal(willContinue(verdict), true);
  assert.equal(sendWouldBeRefused(verdict), false);
});

test("another adapter's session is not this adapter's", () => {
  assert.equal(sessionVerdict([session({ adapter: "claude" })], "codex").kind, "none");
});

test("an unreadable session keeps its id and its reason", () => {
  const verdict = sessionVerdict(
    [session({ state: "unreadable", reason: "工作目录变了" })],
    "codex",
  );
  assert.deepEqual(verdict, {
    kind: "unreadable",
    sessionId: "sid-1",
    reason: "工作目录变了",
  });
  assert.equal(willContinue(verdict), false);
});

test("an unsupported link is said, not silently treated as a new chat", () => {
  const verdict = sessionVerdict(
    [session({ state: "unsupported", session_id: null, reason: "app-server 不续接" })],
    "codex",
  );
  assert.equal(verdict.kind, "unsupported");
  assert.equal(willContinue(verdict), false);
});

test("a state this build does not know is unreadable, never none", () => {
  const verdict = sessionVerdict([session({ state: "quantum" })], "codex");
  assert.equal(verdict.kind, "unreadable");
});

test("an active row with no id is not presented as a live session", () => {
  const verdict = sessionVerdict([session({ session_id: null })], "codex");
  assert.equal(verdict.kind, "unreadable");
});

test("slices append in order", () => {
  let transcript = mergeTranscript(EMPTY_TRANSCRIPT, slice({ text: "› hi\n", next_offset: 6 }));
  assert.equal(transcript.text, "› hi\n");
  assert.equal(transcript.offset, 6);
  transcript = mergeTranscript(transcript, slice({ text: "ok\n", offset: 6, next_offset: 9 }));
  assert.equal(transcript.text, "› hi\nok\n");
  assert.equal(transcript.offset, 9);
});

test("a duplicate or stale slice is dropped, not appended twice", () => {
  const first = mergeTranscript(EMPTY_TRANSCRIPT, slice({ text: "ok\n", next_offset: 3 }));
  const again = mergeTranscript(first, slice({ text: "ok\n", offset: 0, next_offset: 3 }));
  assert.equal(again.text, "ok\n", "the same slice printed twice");
  assert.equal(again.offset, 3);
});

test("an unreachable read is shown, never completes the turn, and clears when a read lands", () => {
  const started = mergeTranscript(EMPTY_TRANSCRIPT, slice({ text: "ok\n", next_offset: 3 }));
  const failed = mergeTranscript(
    started,
    slice({ offset: 3, next_offset: 3, reachable: false, unreachable_reason: "PermissionError", complete: true }),
  );
  assert.equal(failed.unreadable, true);
  assert.equal(failed.unreadableReason, "PermissionError");
  // The transcript behind it did not come back, so the turn must not be drawn
  // as a whole one — even though that same response carried the run's settled
  // state.
  assert.equal(failed.complete, false);
  assert.equal(failed.text, "ok\n");
  assert.equal(failed.offset, 3, "an unreachable read must not advance the offset");

  // And it clears on the next read that lands. It used to be sticky, under the
  // name `gap`, justified by "the offset moved on without them" — which is not
  // what happens: the offset stays, so those bytes come back in this very
  // slice. One transient ACL error made the window claim, for the rest of the
  // session, that text was missing from a transcript it had since read whole.
  const recovered = mergeTranscript(failed, slice({ text: "more\n", offset: 3, next_offset: 8 }));
  assert.equal(recovered.unreadable, false, "a read that landed proves nothing was missed");
  assert.equal(recovered.unreadableReason, null);
  assert.equal(recovered.text, "ok\nmore\n");
  // What is genuinely unrecoverable is a different flag, and that one is sticky.
  const dropped = mergeTranscript(recovered, slice({ text: "", offset: 8, next_offset: 8, lossy: true }));
  assert.equal(dropped.lossy, true);
  assert.equal(mergeTranscript(dropped, slice({ text: "x\n", offset: 8, next_offset: 10 })).lossy, true);
});

test("a request that did not come back is a stall, not a permanent hole", () => {
  // The offset has not moved, so the next successful read returns everything
  // that was missed. Recording it as a gap made the window claim an incomplete
  // transcript for the rest of the session over one dropped request.
  const started = mergeTranscript(EMPTY_TRANSCRIPT, slice({ text: "ok\n", next_offset: 3 }));
  const dropped = mergeTranscript(started, slice({ offset: 3, next_offset: 3, failed: true }));
  assert.equal(dropped.stalled, true);
  assert.equal(dropped.unreadable, false, "a dropped request is not a failed read");
  assert.equal(dropped.offset, 3);

  const recovered = mergeTranscript(dropped, slice({ text: "more\n", offset: 3, next_offset: 8 }));
  assert.equal(recovered.stalled, false, "the stall must clear on the next success");
  assert.equal(recovered.unreadable, false);
  assert.equal(recovered.text, "ok\nmore\n");
});

test("a settled turn with more on disk keeps paging", () => {
  // The rest of a long transcript was unreachable: the poll was driven by run
  // liveness, so a finished turn with a second page had no timer and no button.
  const paging = { ...EMPTY_TRANSCRIPT, complete: true, more: true };
  assert.equal(nextPollDelay(paging, false), 0);
  const done = { ...EMPTY_TRANSCRIPT, complete: true, more: false };
  assert.equal(nextPollDelay(done, false), null);
});

test("polling stops when the turn is over and hurries when more is on disk", () => {
  const active = { ...EMPTY_TRANSCRIPT, complete: false };
  assert.equal(nextPollDelay(active, true), 900);
  assert.equal(nextPollDelay({ ...active, more: true }, true), 0);
  // Settled state not yet confirmed by a read: keep asking, slowly.
  assert.equal(nextPollDelay(active, false), 2_500);
  assert.equal(nextPollDelay({ ...active, complete: true }, false), null);
});

test("turns read oldest first with a stable tie-break", () => {
  const turns = [
    { run_id: "b", state: "succeeded", adapter: "codex", queued_at: "2026-09-08T01:00:00Z", started_at: null, finished_at: null, exit_code: 0 },
    { run_id: "a", state: "succeeded", adapter: "codex", queued_at: "2026-09-08T01:00:00Z", started_at: null, finished_at: null, exit_code: 0 },
    { run_id: "c", state: "running", adapter: "codex", queued_at: "2026-09-08T00:00:00Z", started_at: null, finished_at: null, exit_code: null },
  ];
  assert.deepEqual(conversationOrder(turns).map((turn) => turn.run_id), ["c", "a", "b"]);
});

test("a session row is picked by adapter and role, not adapter alone", () => {
  // A task can hold a worker conversation and a review conversation on the same
  // adapter. Matching on the adapter alone showed one while acting on the other:
  // the badge read from the review's row and "start a new session" cleared the
  // worker's.
  const rows: AgentSessionLike[] = [
    session({ role: "review", session_id: "review-sid", turns: 1 }),
    session({ role: "worker", session_id: "worker-sid", turns: 5 }),
  ];
  const verdict = sessionVerdict(rows, "codex", WORKER_ROLE);
  assert.deepEqual(verdict, { kind: "active", sessionId: "worker-sid", turns: 5 });
  // And a task with only a review session has no worker conversation yet.
  assert.equal(sessionVerdict([rows[0]], "codex", WORKER_ROLE).kind, "none");
});

test("a lost session blocks sending; a forked one does not", () => {
  const lost = sessionVerdict([session({ state: "lost", reason: "no id announced" })], "codex");
  assert.equal(lost.kind, "lost");
  assert.equal(sendWouldBeRefused(lost), true);

  // Forked means the conversation moved, not that it is unusable: the control
  // plane will continue the session it moved to, and blocking would strand the
  // person in a task they can still work in.
  const forked = sessionVerdict(
    [session({ state: "forked", session_id: "new", previous_session_id: "old" })],
    "codex",
  );
  assert.equal(forked.kind, "forked");
  assert.equal(sendWouldBeRefused(forked), false);
});

test("lost output is sticky, because what was lost is not coming back", () => {
  const lossy = mergeTranscript(EMPTY_TRANSCRIPT, slice({ text: "a\n", next_offset: 2, lossy: true }));
  assert.equal(lossy.lossy, true);
  const later = mergeTranscript(lossy, slice({ text: "b\n", offset: 2, next_offset: 4 }));
  assert.equal(later.lossy, true, "a clean read does not make the transcript whole");
});

test("an out-of-order slice contributes nothing at all, not even its flags", () => {
  // Two reads of one offset can be in flight. Taking `complete` off the response
  // whose text was dropped stopped the polling while the longer response's text
  // had gone nowhere, and the turn ended mid-sentence.
  const held = mergeTranscript(EMPTY_TRANSCRIPT, slice({ text: "ok\n", next_offset: 3 }));
  const stale = mergeTranscript(held, slice({ text: "ok and more\n", offset: 0, next_offset: 12, complete: true }));
  assert.equal(stale.text, "ok\n");
  assert.equal(stale.offset, 3);
  assert.equal(stale.complete, false, "a dropped slice must not end the turn");
});

test("a pending session is offered a fresh start, not a block", () => {
  // The first turn ended before the agent named its session, so nothing was
  // established. Folded into the default this became `unreadable` and disabled
  // sending — on the one state that is designed to be recovered from.
  const verdict = sessionVerdict([session({ state: "pending", session_id: "chosen" })], "codex");
  assert.equal(verdict.kind, "pending");
  assert.equal(sendWouldBeRefused(verdict), false);
  assert.equal(willContinue(verdict), false);
});

test("an unreadable session does not block sending, because only a turn can heal it", () => {
  // The dispatcher re-derives the workspace and resume conditions every turn, so
  // putting the worktree back makes the conversation continue — and nothing but a
  // turn triggers that recheck. Blocking here sealed the state in: the only way
  // out was to clear the session and lose the context.
  const verdict = sessionVerdict([session({ state: "unreadable", reason: "worktree moved" })], "codex");
  assert.equal(verdict.kind, "unreadable");
  assert.equal(sendWouldBeRefused(verdict), false);
  assert.equal(willContinue(verdict), false);
});

test("an unsupported link is not promised a fresh start, because its id is kept", () => {
  // `mark_unsupported` keeps the recorded id — another configuration may yet
  // reach that conversation — so pointing the task back at a link that can
  // resume continues it. Saying "the next message opens a new session" here made
  // the old instructions come back under a promise that they would not.
  const verdict = sessionVerdict([session({ state: "unsupported", session_id: "sid-1" })], "codex");
  assert.equal(verdict.kind, "unsupported");
  assert.equal(willStartFresh(verdict), false);
  assert.equal(willContinue(verdict), false);
});

test("a session recorded before roles existed is seen, and clearing it is offered", () => {
  // The control plane refuses every turn while one is on record. Reading only
  // the worker row left the page saying "the next message starts a new session"
  // while each send came back refused — and the clear button, addressed to the
  // worker role, would not have cleared the row doing the refusing.
  const rows = [session({ role: "unknown", session_id: "legacy-1" })];
  assert.equal(sessionVerdict(rows, "codex").kind, "none");
  const orphan = orphanSession(rows, "codex");
  assert.notEqual(orphan, null);
  assert.equal(orphan?.session_id, "legacy-1");
  assert.equal(orphan?.role, "unknown");
  // A cleared legacy row (no id) is not one: there is nothing left to refuse over.
  assert.equal(orphanSession([session({ role: "unknown", session_id: null })], "codex"), null);
  // And an unread list is not "no orphan" either.
  assert.equal(orphanSession(null, "codex"), null);
});

test("a transcript rebuilt from the run's record is applied once, not twice", () => {
  // The control plane serves it with `next_offset: 0` — it is a whole
  // substitute, not a stream — so the offset never advances and the
  // duplicate check cannot see a second copy. A prefetch racing the person
  // pressing "load this turn" appended the same prompt and output twice.
  const record = { text: "> ask\nanswer\n", offset: 0, next_offset: 0, reachable: true, from_record: true, complete: true };
  const once = mergeTranscript(EMPTY_TRANSCRIPT, slice(record));
  assert.equal(once.text, "> ask\nanswer\n");
  assert.equal(once.fromRecord, true);
  const twice = mergeTranscript(once, slice(record));
  assert.equal(twice.text, "> ask\nanswer\n", "the same record must not be appended again");
  assert.equal(twice.complete, true);
});

test("a page of a settled transcript that did not come back is retried", () => {
  // Both a dropped request and an unreadable answer clear `more` — neither
  // carries an answer about what is left — so with `complete` already true
  // the polling stopped for good and the rest of a long transcript needed a
  // full page reload to reach.
  const first = mergeTranscript(EMPTY_TRANSCRIPT, slice({ text: "page one\n", next_offset: 9, complete: true, more_available: true }));
  assert.equal(nextPollDelay(first, false), 0);
  const dropped = mergeTranscript(first, slice({ offset: 9, next_offset: 9, failed: true }));
  assert.equal(dropped.more, false);
  assert.equal(dropped.complete, true);
  assert.equal(nextPollDelay(dropped, false), 2_500, "a dropped page must keep being asked for");
  const unreadable = mergeTranscript(first, slice({ offset: 9, next_offset: 9, reachable: false }));
  assert.equal(nextPollDelay(unreadable, false), 2_500, "an unreadable page must keep being asked for");
  // And once it lands, the poll follows the answer again.
  const landed = mergeTranscript(dropped, slice({ text: "page two\n", offset: 9, next_offset: 18, complete: true }));
  assert.equal(nextPollDelay(landed, false), null);
  assert.equal(landed.text, "page one\npage two\n");
});

test("the turn probe never asks for more than the server allows", () => {
  // The window asks for one more turn than it shows, to tell "there are
  // older turns" from "there are exactly this many". At the ceiling that
  // probe asked for 501 of a maximum 500: the request came back 422 and the
  // catch emptied the window, so every turn already loaded vanished because
  // the extra one could not be fetched.
  const probeFor = (shown: number) => Math.min(shown + 1, MAX_PAGE);
  assert.equal(probeFor(20), 21);
  assert.equal(probeFor(MAX_PAGE - 1), MAX_PAGE);
  assert.equal(probeFor(MAX_PAGE), MAX_PAGE, "the probe must not exceed the ceiling");
  assert.ok(probeFor(MAX_PAGE) <= MAX_PAGE);
});

// --- the stream --------------------------------------------------------------------------

function turn(run_id: string, queued_at: string, overrides: Partial<TurnLike> = {}): TurnLike {
  return {
    run_id, state: "succeeded", adapter: "codex", queued_at,
    started_at: null, finished_at: null, exit_code: 0, ...overrides,
  };
}

test("a turn's duration needs both ends, and both readable", () => {
  assert.equal(turnDurationMs(turn("r", "2026-09-12T09:00:00Z", {
    started_at: "2026-09-12T09:41:00Z", finished_at: "2026-09-12T09:44:12Z",
  })), 192_000);
  // Live: started, not finished — no number, not "0s so far".
  assert.equal(turnDurationMs(turn("r", "x", { started_at: "2026-09-12T09:41:00Z" })), null);
  assert.equal(turnDurationMs(turn("r", "x", { finished_at: "2026-09-12T09:41:00Z" })), null);
  assert.equal(turnDurationMs(turn("r", "x", { started_at: "?", finished_at: "2026-09-12T09:41:00Z" })), null);
  // A clock that went backwards measures nothing — not "0s", which would say instantaneous.
  assert.equal(turnDurationMs(turn("r", "x", {
    started_at: "2026-09-12T09:44:00Z", finished_at: "2026-09-12T09:41:00Z",
  })), null);
});

test("durations take the mockup's shapes: seconds, minutes and seconds, hours and minutes", () => {
  assert.equal(formatDuration(42_000), "42s");
  assert.equal(formatDuration(192_000), "3m 12s");
  assert.equal(formatDuration(65 * 60_000), "1h 05m");
  assert.equal(formatDuration(0), "0s");
});

test("the stream drops run.* events, and holds back events older than the oldest shown turn only when older turns exist", () => {
  const events = [
    { event_id: "e0", event_type: "task_created", created_at: "2026-09-10T08:00:00Z" },
    { event_id: "e1", event_type: "user_message", created_at: "2026-09-11T08:00:00Z" },
    { event_id: "e2", event_type: "run.queued", created_at: "2026-09-12T09:00:00Z" },
    { event_id: "e3", event_type: "spec.approved", created_at: "2026-09-12T10:00:00Z" },
  ];
  const turns = [turn("b", "2026-09-12T09:30:00Z"), turn("a", "2026-09-12T09:00:00Z")];
  // Older turns exist: the two entries before the oldest shown turn are folded with them.
  const shown = streamEventsOf(events, turns, true);
  assert.deepEqual(shown.events.map((e) => e.event_id), ["e3"]);
  assert.equal(shown.heldBack, 2);
  // A complete page: nothing is older than history itself; only run.* goes.
  const whole = streamEventsOf(events, turns, false);
  assert.deepEqual(whole.events.map((e) => e.event_id), ["e0", "e1", "e3"]);
  assert.equal(whole.heldBack, 0);
  // No turns on the page at all: nothing to be older than, even if older turns exist.
  assert.equal(streamEventsOf(events, [], true).heldBack, 0);
});

test("the footnote counts reviews by role and calls everything else nested 'other' — never 'retry'", () => {
  assert.deepEqual(nestedSummaryOf([]), { total: 0, reviews: 0, others: 0 });
  assert.deepEqual(
    nestedSummaryOf([{ role: "review" }, { role: "review" }, { role: "worker" }]),
    { total: 3, reviews: 2, others: 1 },
  );
});

test("run activity counts silence from the last read and never claims it from a failed one (CR-12 ①)", async () => {
  const { runActivityOf, STUCK_AFTER_MINUTES } = await import("../src/lib/conversation.ts");
  const read = (quiet: number | null, extra: Partial<OutputSliceLike> = {}) => mergeTranscript(EMPTY_TRANSCRIPT, {
    text: "x", offset: 0, next_offset: 1, reachable: true, quiet_seconds: quiet, read_at: 1_000_000, ...extra,
  });
  assert.deepEqual(runActivityOf(read(30), 1_000_000), { kind: "quiet", minutes: 0, stuck: false });
  // Keeps counting between reads.
  assert.equal((runActivityOf(read(30), 1_000_000 + 5 * 60_000) as { minutes: number }).minutes, 5);
  const late = runActivityOf(read(STUCK_AFTER_MINUTES * 60), 1_000_000);
  assert.equal(late.kind === "quiet" && late.stuck, true);
  // Cannot say: no figure, an unreadable transcript, a read that did not come back.
  assert.deepEqual(runActivityOf(read(null), 1_000_000), { kind: "none" });
  assert.deepEqual(runActivityOf(undefined), { kind: "none" });
  const unreadable = mergeTranscript(read(30), { text: "", offset: 1, next_offset: 1, reachable: false });
  assert.deepEqual(runActivityOf(unreadable, 1_000_000), { kind: "none" });
  const stalled = mergeTranscript(read(30), { text: "", offset: 1, next_offset: 1, reachable: true, failed: true });
  assert.deepEqual(runActivityOf(stalled, 1_000_000), { kind: "none" });
  // A late answer to an older poll of the same offset does not pull the figure back.
  const newer = mergeTranscript(EMPTY_TRANSCRIPT, {
    text: "", offset: 0, next_offset: 0, reachable: true, quiet_seconds: 660, quiet_measured_at: 2000, read_at: 1_000_000,
  });
  const lateRead = mergeTranscript(newer, {
    text: "", offset: 0, next_offset: 0, reachable: true, quiet_seconds: 60, quiet_measured_at: 1400, read_at: 1_000_500,
  });
  assert.equal((runActivityOf(lateRead, 1_000_500) as { minutes: number }).minutes, 11);
  // A read with no figure in between hides the note but keeps the order mark.
  const blank = mergeTranscript(newer, { text: "", offset: 0, next_offset: 0, reachable: true, quiet_seconds: null, read_at: 1_000_200 });
  assert.deepEqual(runActivityOf(blank, 1_000_200), { kind: "none" });
  const afterBlank = mergeTranscript(blank, {
    text: "", offset: 0, next_offset: 0, reachable: true, quiet_seconds: 60, quiet_measured_at: 1400, read_at: 1_000_600,
  });
  assert.deepEqual(runActivityOf(afterBlank, 1_000_600), { kind: "none" });
  // A capped transcript cannot tell.
  assert.deepEqual(runActivityOf(read(900, { log_capped: true }), 1_000_000), { kind: "capped" });
});

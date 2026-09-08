/**
 * What the conversation window is allowed to claim.
 *
 * The window shows two things the user acts on: **is this still the same
 * conversation**, and **what has the agent said so far**. Both are read over a
 * network from a control plane that can fail, and both have a wrong answer that
 * looks exactly like a right one:
 *
 * - a session that could not be read, drawn as a fresh conversation; and
 * - a transcript that could not be fetched, drawn as an agent that has gone quiet.
 *
 * So neither is a boolean here. `sessionVerdict` and `mergeTranscript` return
 * "could not read" as its own case, and the UI has to render it.
 *
 * Plain TypeScript, no React and no `@/` aliases, so `node --test` can mount it:
 * the components that use it cannot be mounted in this project's test runner
 * (see CR-18), which is exactly why the decisions live out here.
 */

export type SessionState =
  | "none"
  | "active"
  | "pending"
  | "unidentified"
  | "unreadable"
  | "lost"
  | "forked"
  | "unsupported";

/** The role whose conversation the message box drives. */
export const WORKER_ROLE = "worker";
/**
 * The role of a session recorded before roles existed — see `ROLE_UNKNOWN` in
 * the control plane. Named here because clearing such a row is the only way to
 * unblock a task that holds one, and the clear is addressed by role.
 */
export const UNKNOWN_ROLE = "unknown";
/**
 * The role an automatic review's own session is filed under. Named here because
 * a review conversation that gets stuck refuses every later review of the task,
 * and the window has to be able to say so and act on it.
 */
export const REVIEW_ROLE = "review";

export interface AgentSessionLike {
  task_id: string;
  adapter: string;
  /**
   * Part of a session's identity. A task can hold a worker conversation and a
   * review conversation on the same adapter, and picking a row by adapter alone
   * showed one while acting on the other — the badge read from the review's row
   * and "start a new session" cleared the worker's.
   */
  role?: string;
  state: string;
  session_id: string | null;
  workspace: string | null;
  reason: string | null;
  turns: number;
  previous_session_id?: string | null;
}

/**
 * Whether this task holds a session recorded before roles existed.
 *
 * The control plane refuses every turn while one is on record — it cannot tell
 * whether the conversation was the user's or a review's — so the window has to
 * show it and offer to clear it. Reading only the worker row made the page say
 * "the next message starts a new session" while every send was refused, with no
 * button anywhere to unblock it.
 */
export function orphanSession(
  sessions: readonly AgentSessionLike[] | null,
  adapter: string,
): AgentSessionLike | null {
  if (sessions === null) return null;
  return sessions.find(
    (item) => item.adapter === adapter
      && (item.role ?? WORKER_ROLE) === UNKNOWN_ROLE
      && item.session_id !== null,
  ) ?? null;
}

/** What a session badge may say. `unknown` is the read having failed. */
export type SessionVerdict =
  | { kind: "unknown" }
  | { kind: "none" }
  /** An unconfirmed id: the next message starts a session rather than continuing. */
  | { kind: "pending" }
  /** A turn ran whose session could not be named, so its context is not carried. */
  | { kind: "unidentified"; reason: string | null }
  | { kind: "active"; sessionId: string; turns: number }
  | { kind: "unreadable"; sessionId: string | null; reason: string | null }
  | { kind: "lost"; sessionId: string | null; reason: string | null }
  | { kind: "forked"; sessionId: string | null; previousSessionId: string | null; reason: string | null }
  | { kind: "unsupported"; reason: string | null };

/**
 * The session for one adapter, from a list that may not have been read at all.
 *
 * `sessions === null` means the read failed or has not returned. It is
 * deliberately **not** folded into "no session yet": the first is "I do not know
 * whether you have a conversation here", the second is "you do not", and only the
 * second one makes "this will start a new conversation" a true thing to show.
 */
export function sessionVerdict(
  sessions: readonly AgentSessionLike[] | null,
  adapter: string,
  role: string = WORKER_ROLE,
): SessionVerdict {
  if (sessions === null) return { kind: "unknown" };
  // Matched on adapter **and** role. A row whose role this build does not know
  // is not treated as the worker's: it belongs to some other conversation, and
  // borrowing it would put another turn's session behind this message box.
  const row = sessions.find(
    (item) => item.adapter === adapter && (item.role ?? WORKER_ROLE) === role,
  );
  if (!row) return { kind: "none" };
  switch (row.state) {
    case "active":
      // An `active` row without an id cannot happen from the control plane, but
      // the type allows it, and inventing an empty id would put a badge on screen
      // claiming a session nobody can name.
      return row.session_id
        ? { kind: "active", sessionId: row.session_id, turns: row.turns }
        : { kind: "unreadable", sessionId: null, reason: row.reason };
    case "unreadable":
      return { kind: "unreadable", sessionId: row.session_id, reason: row.reason };
    case "lost":
      return { kind: "lost", sessionId: row.session_id, reason: row.reason };
    case "forked":
      return {
        kind: "forked",
        sessionId: row.session_id,
        previousSessionId: row.previous_session_id ?? null,
        reason: row.reason,
      };
    case "unsupported":
      return { kind: "unsupported", reason: row.reason };
    case "none":
      return { kind: "none" };
    case "unidentified":
      return { kind: "unidentified", reason: row.reason };
    case "pending":
      // An id Agora chose that no provider event has confirmed. The dispatcher
      // starts cleanly from here — nothing was established, so nothing is lost —
      // and the window says the next message opens a session rather than
      // continuing one. Folded into the default before, it became `unreadable`
      // and blocked sending on a state that is *designed* to be recovered from.
      return { kind: "pending" };
    default:
      // A state this build does not know. Not "none": a control plane newer than
      // this page has said something about the session, and guessing would be
      // the silent substitution this whole module exists to refuse.
      return { kind: "unreadable", sessionId: row.session_id, reason: row.reason };
  }
}

/** Whether the next turn continues an existing conversation. */
export function willContinue(verdict: SessionVerdict): boolean {
  return verdict.kind === "active";
}

/**
 * Whether the window may tell the person their next message *starts* a
 * conversation.
 *
 * Only where that is certain. Saying it for everything that was not `active`
 * was wrong in three states the control plane can still resume from: `unknown`
 * (the list read failed, and a live session may well be on record), `unreadable`
 * (the dispatcher re-derives that condition and a restored worktree continues),
 * and `forked` (the conversation moved, and the session it moved to continues).
 * The person acted on "this starts fresh" and the control plane resumed.
 */
export function willStartFresh(verdict: SessionVerdict): boolean {
  // `unsupported` is deliberately **not** here. That row can still hold an id —
  // `mark_unsupported` keeps it, because another configuration may yet reach
  // that conversation — so switching the adapter back makes the control plane
  // resume it. The window promised a fresh start and the old instructions came
  // back with it.
  return verdict.kind === "none"
    || verdict.kind === "pending"
    || verdict.kind === "unidentified";
}

/**
 * Whether sending is expected to be refused by the control plane.
 *
 * The dispatcher refuses a turn whose recorded session cannot be continued, so
 * the window says so *before* the user types instead of taking the message and
 * settling a failed run. `unknown` is not blocked: a failed read of the session
 * list is no reason to stop someone working, and the dispatcher is the authority.
 */
export function sendWouldBeRefused(verdict: SessionVerdict): boolean {
  // Only `lost` blocks, because only `lost` is a conclusion the control plane
  // will not revisit.
  //
  // `unreadable` deliberately does **not**: the dispatcher re-derives that
  // condition on every turn, so a worktree put back makes the conversation
  // continue again — and nothing but a turn triggers the recheck. Blocking here
  // sealed the recorded state in: the only way out was to clear the session and
  // throw the context away, which is the opposite of what the state means.
  // Sending while it holds costs one refused turn, and its reason is shown.
  //
  // `forked` does not block either: the conversation moved, and the control
  // plane will continue the session it moved to. Saying so is the window's job.
  return verdict.kind === "lost";
}

export interface OutputSliceLike {
  text: string;
  offset: number;
  next_offset: number;
  reachable: boolean;
  unreachable_reason?: string | null;
  more_available?: boolean;
  complete?: boolean;
  /** Synthesised by the caller when the request itself did not come back. */
  failed?: boolean;
  /** The control plane rebuilt this text from the run's record, not a transcript. */
  from_record?: boolean;
  /** Output the agent produced that could not be saved. */
  lossy?: boolean;
}

export interface Transcript {
  /** Text accumulated so far. Never rewritten backwards — only appended. */
  text: string;
  /** Byte offset to ask for next. */
  offset: number;
  /**
   * The control plane could not read the transcript on the **last** read — a
   * permission error on the file, a lock, a delete. Not sticky, and not a hole:
   * the offset does not advance on an unreachable read, so the bytes are still
   * there to fetch and the next successful read returns every one of them.
   *
   * It was sticky, called `gap`, and its own docstring justified that with "the
   * offset moved on without them" — which is not what happens. One transient
   * ACL error made the window claim, permanently, that text was missing from the
   * middle of a transcript that in fact came back whole. What is genuinely
   * unrecoverable has its own flag: `lossy`, set by the writer that dropped it.
   */
  unreadable: boolean;
  unreadableReason: string | null;
  /** The last read did not come back. Cleared by the next one that does. */
  stalled: boolean;
  /** True when the run has settled, so the end of the text is the end of the turn. */
  complete: boolean;
  /** True when the last read hit its size cap and more is already on disk. */
  more: boolean;
  /** True when this text came from the run's record rather than a transcript. */
  fromRecord: boolean;
  /**
   * Output was produced and could not be saved. Sticky, like `gap` and for the
   * same reason: what was lost is not coming back, so a later successful read
   * does not make the transcript whole again.
   */
  lossy: boolean;
}

export const EMPTY_TRANSCRIPT: Transcript = {
  text: "",
  offset: 0,
  unreadable: false,
  unreadableReason: null,
  stalled: false,
  complete: false,
  more: false,
  fromRecord: false,
  lossy: false,
};

/**
 * Fold one polled slice into the transcript.
 *
 * Out-of-order and duplicate slices are dropped rather than appended: a poll
 * that lands twice (React strict mode, a retried fetch) would otherwise print
 * the same paragraph twice, and a stale slice would print an old one after a new
 * one. The test for "this slice is the next one" is that it starts exactly where
 * the transcript ends.
 */
export function mergeTranscript(prev: Transcript, slice: OutputSliceLike): Transcript {
  if (slice.failed) {
    // The request did not land. Nothing is known to be missing — the offset has
    // not moved — so this is a stall to show, not a hole to record.
    return { ...prev, stalled: true, more: false };
  }
  if (!slice.reachable) {
    return {
      ...prev,
      stalled: false,
      unreadable: true,
      unreadableReason: slice.unreachable_reason ?? null,
      // `complete` is not taken from an unreachable read: the run's state came
      // back in the same response, but the transcript behind it did not, and
      // marking the turn finished here would present a truncated conversation as
      // a whole one.
      more: false,
    };
  }
  if (slice.offset !== prev.offset) {
    // Not the slice we asked for, so **nothing** in it is used — not even its
    // `complete`. Two reads of the same offset can be in flight at once; taking
    // the flag off the one whose text was dropped stopped the polling while the
    // longer response's text had gone nowhere, and the turn ended mid-sentence.
    // The stall clears, because a response did arrive.
    return { ...prev, stalled: false };
  }
  if (slice.from_record && prev.fromRecord) {
    // A transcript rebuilt from the run's own record is a whole substitute, not
    // a stream: the control plane serves it with `next_offset: 0`, so the offset
    // never advances and the duplicate check above cannot see a second copy. Two
    // reads in flight — a prefetch and the person pressing "load this turn" —
    // therefore appended the same prompt and output twice. Applied once, and the
    // repeat is dropped rather than concatenated.
    return { ...prev, stalled: false, complete: slice.complete ?? prev.complete };
  }
  return {
    text: prev.text + slice.text,
    offset: slice.next_offset,
    // Cleared by a read that came back: the bytes this one was worried about are
    // in `slice.text`. Carrying it forward was the bug — a single transient
    // failure kept saying "text is missing here" over a transcript that had
    // since been read in full.
    unreadable: false,
    unreadableReason: null,
    stalled: false,
    complete: slice.complete ?? false,
    more: slice.more_available ?? false,
    fromRecord: slice.from_record ?? false,
    lossy: prev.lossy || (slice.lossy ?? false),
  };
}

/**
 * How soon to poll again, in ms, or null to stop.
 *
 * `more` is checked before anything else, and **before** the run's state: a
 * settled turn whose log is larger than one slice still has pages waiting, and
 * driving the poll from liveness alone left the rest of a long transcript
 * permanently unreachable — no timer, and no button either.
 */
export function nextPollDelay(transcript: Transcript, runIsActive: boolean): number | null {
  if (transcript.more) return 0; // more is already on disk; do not wait for it
  if (runIsActive) return 900;
  // A page that did not come back, or could not be read, is retried even on a
  // settled turn. Both of those clear `more` — they carry no answer about what
  // is left — so with `complete` already true the polling stopped for good, and
  // the rest of a long transcript needed a full page reload to reach. The offset
  // has not moved, so the retry asks for exactly the bytes that are missing.
  if (transcript.stalled || transcript.unreadable) return 2_500;
  if (!transcript.complete) return 2_500; // settled state not yet confirmed
  return null; // the turn is over and its transcript is fully read
}

/**
 * One turn of the conversation, as the window draws it.
 *
 * A turn is a run, not a message: the run is what has a workspace, an exit code,
 * a file list and a review. adr/0007 keeps the run as the unit of work inside the
 * conversation for exactly this reason — the honesty machinery of W0–W7 hangs off
 * it — and only the *primitive the user handles* became the session.
 */
export interface TurnLike {
  run_id: string;
  state: string;
  adapter: string;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
}

export function turnIsActive(turn: TurnLike): boolean {
  return turn.state === "queued" || turn.state === "running";
}

/**
 * Turns oldest-first, which is the reading order of a conversation.
 *
 * Ordered by `queued_at` with the run id as the tie-break, so two turns queued in
 * the same clock tick still have a stable order instead of swapping places
 * between polls.
 */
export function conversationOrder<T extends TurnLike>(turns: readonly T[]): T[] {
  return [...turns].sort((left, right) => {
    if (left.queued_at !== right.queued_at) {
      return left.queued_at < right.queued_at ? -1 : 1;
    }
    return left.run_id < right.run_id ? -1 : 1;
  });
}

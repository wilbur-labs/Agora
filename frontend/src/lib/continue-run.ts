// The decision layer behind the Task Workspace's "Continue" action: whether a
// run may be dispatched at all, what the seeded prompt says, and how a refusal
// reads.
//
// Deliberately free of React and of "@/" path aliases: `node --test` executes
// the companion test file (tests/continue-run.test.mts) directly, and this is
// where the project's standing rule is actually decided — an unknown must never
// render as an absence. "Couldn't read the worktree status" is not "there is no
// worktree", and "couldn't read the runs" is not "nothing is running", so both
// are separate answers here rather than a falsy default.

export type TranslateFn = (key: string, vars?: Record<string, string | number>) => string;

/**
 * The only adapter this action dispatches. Executor and environment being chosen
 * at dispatch time is a later slice; pinning the value here keeps that decision
 * explicit and greppable instead of implied by a default somewhere.
 */
export const CONTINUE_ADAPTER = "claude";

/**
 * Mirrors CreateRunRequest.prompt (max_length=16_000) in execution/models.py.
 *
 * Nothing at runtime keeps this in step with the backend, so the backend owns the
 * alarm: `test_run_contract_constants_mirrored_by_the_frontend` in
 * backend/tests/test_execution.py asserts these three values and names this file.
 * If it fails, this is what has to change.
 */
export const CONTINUE_PROMPT_LIMIT = 16_000;

/** Mirrors CreateRunRequest.timeout_seconds' default in execution/models.py. */
export const CONTINUE_TIMEOUT_SECONDS = 600;

/**
 * Task states ExecutionStore.create accepts a run for. Anything else is refused
 * server-side with a 409, so the button must say why rather than fire a request
 * that cannot succeed. Pinned by the backend test named above.
 */
export const CONTINUABLE_TASK_STATES: readonly string[] = ["planned", "running"];

// --- Worktree readiness ------------------------------------------------------

/**
 * What we actually know about the (project, codex) worktree. `unreadable` exists
 * to keep a failed status read apart from a confirmed absence: treating the two
 * alike would offer to "create" a worktree that may already hold work.
 */
export type WorkspaceReadiness =
  | { kind: "checking" }
  | { kind: "ready"; branch: string | null }
  | { kind: "provisionable"; detail: string | null }
  | { kind: "provisioning" }
  | { kind: "unreadable"; detail: string }
  | { kind: "foreign" }
  | { kind: "notGit" }
  | { kind: "unprovisionable"; detail: string };

export interface WorkspaceFacts {
  /** A status read is in flight and no answer has arrived yet. */
  loading: boolean;
  /** The status read itself failed. Non-null means "unknown", never "missing". */
  readFailed: string | null;
  status: {
    state: string;
    branch?: string | null;
    error?: string | null;
    source_is_git: boolean;
  } | null;
}

export function workspaceReadiness(facts: WorkspaceFacts): WorkspaceReadiness {
  if (facts.readFailed !== null) return { kind: "unreadable", detail: facts.readFailed };
  if (facts.loading || facts.status === null) return { kind: "checking" };
  const status = facts.status;
  switch (status.state) {
    case "ready":
      return { kind: "ready", branch: status.branch ?? null };
    case "provisioning":
      return { kind: "provisioning" };
    case "foreign":
      return { kind: "foreign" };
    case "missing":
    case "error":
      // Same rule the Run Center's create panel uses: a worktree can only be
      // provisioned when the project root is a git repository.
      if (!status.source_is_git) return { kind: "notGit" };
      return { kind: "provisionable", detail: status.error ?? null };
    default:
      // A state this UI does not know is a newer backend, not a green light.
      return { kind: "unprovisionable", detail: status.error ?? status.state };
  }
}

// --- Gating ------------------------------------------------------------------

export type ContinueBlock =
  | "noCharter"
  | "dispatching"
  | "taskNotRunnable"
  | "runsUnknown"
  | "runActive"
  | "followUpPending"
  | "attentionUnknown"
  | "attentionOpen"
  | "worktreeChecking"
  | "worktreeUnreadable"
  | "worktreeForeign"
  | "worktreeNotGit"
  | "worktreeProvisioning"
  | "worktreeUnprovisionable";

export interface ContinueDecision {
  ready: boolean;
  /** The dispatch must provision the worktree before creating the run. */
  needsProvision: boolean;
  block: ContinueBlock | null;
}

/**
 * The facts that decide whether the action may be *offered*, known without any
 * extra request: the task's own state, this task's runs, and the attention items
 * raised on it — all three already in hand in the workspace view.
 *
 * `activeRunCount === null` and `openAttentionCount === null` both mean the fact
 * could not be read. Unknown blocks: firing a second paid run on a guess is the
 * one outcome that cannot be undone by a refresh.
 *
 * `followUpArmed` is the handoff window: with auto-review on, the dispatcher
 * publishes the worker run's terminal state and only *then* builds and queues the
 * review run. In that gap no run is active and no gate has been raised yet, so
 * every other fact here says "idle, go ahead" — and the backend would accept the
 * dispatch, because the worker run has settled and no same-role run exists. An
 * armed window therefore blocks with its own reason: we are waiting to see
 * whether a review starts, which is how Run -> Gate ordering survives the
 * handoff.
 *
 * Why open attention blocks at all: a task stays `running` while the auto-review
 * flow settles the worker run and raises an approval, so state and run list alone
 * say "idle, go ahead" at exactly the moment a human gate is waiting. Continuing
 * there would build the next run on top of unapproved work, which is the
 * Run -> Artifact/Evidence -> Gate -> Handoff line running backwards.
 *
 * All three kinds block, and each for its own reason:
 * - `approval`: the gate itself. Nothing may advance before the human answers.
 * - `question`: the agent asked and is owed an answer; a new run would not be
 *   that answer, and would race whatever the reply changes.
 * - `blocker`: the task is stuck on something a human has to clear. Starting
 *   another run does not clear it, it just spends tokens on the same wall.
 * Only OPEN items are counted — a responded, cancelled, or expired item is a
 * gate that has already been dealt with, and the caller filters on that.
 */
export function continueEntryGate(input: {
  taskState: string;
  /** Whether the project may be given work at all (CR-61). "unknown" does NOT block: a
   *  dashboard read that failed is not evidence the project has no goal, and refusing on
   *  it would stop work over a request that timed out. */
  projectGate?: "open" | "noCharter" | "unknown";
  activeRunCount: number | null;
  openAttentionCount: number | null;
  /** A run settled moments ago and the review it may trigger has not appeared yet. */
  followUpArmed: boolean;
  dispatching: boolean;
}): ContinueDecision {
  if (input.dispatching) return { ready: false, needsProvision: false, block: "dispatching" };
  // First, because it is the most fundamental refusal: a project whose goal was never
  // settled has nothing to judge a run against, and dispatching into it is how work
  // starts without anyone having said what it is for.
  if (input.projectGate === "noCharter") {
    return { ready: false, needsProvision: false, block: "noCharter" };
  }
  if (!CONTINUABLE_TASK_STATES.includes(input.taskState)) {
    return { ready: false, needsProvision: false, block: "taskNotRunnable" };
  }
  if (input.activeRunCount === null) {
    return { ready: false, needsProvision: false, block: "runsUnknown" };
  }
  if (input.activeRunCount > 0) return { ready: false, needsProvision: false, block: "runActive" };
  if (input.openAttentionCount === null) {
    return { ready: false, needsProvision: false, block: "attentionUnknown" };
  }
  if (input.openAttentionCount > 0) {
    return { ready: false, needsProvision: false, block: "attentionOpen" };
  }
  // Last of the three, because a gate that already exists is the more useful
  // thing to say; this covers the window in which one may be about to.
  if (input.followUpArmed) {
    return { ready: false, needsProvision: false, block: "followUpPending" };
  }
  return { ready: true, needsProvision: false, block: null };
}

/** The full decision: the entry facts plus the worktree the run would write to. */
export function continueGate(input: {
  taskState: string;
  projectGate?: "open" | "noCharter" | "unknown";
  activeRunCount: number | null;
  openAttentionCount: number | null;
  followUpArmed: boolean;
  worktree: WorkspaceReadiness;
  dispatching: boolean;
}): ContinueDecision {
  const entry = continueEntryGate(input);
  if (!entry.ready) return entry;
  switch (input.worktree.kind) {
    case "ready":
      return { ready: true, needsProvision: false, block: null };
    case "provisionable":
      return { ready: true, needsProvision: true, block: null };
    case "checking":
      return { ready: false, needsProvision: false, block: "worktreeChecking" };
    case "provisioning":
      return { ready: false, needsProvision: false, block: "worktreeProvisioning" };
    case "unreadable":
      return { ready: false, needsProvision: false, block: "worktreeUnreadable" };
    case "foreign":
      return { ready: false, needsProvision: false, block: "worktreeForeign" };
    case "notGit":
      return { ready: false, needsProvision: false, block: "worktreeNotGit" };
    default:
      return { ready: false, needsProvision: false, block: "worktreeUnprovisionable" };
  }
}

export const CONTINUE_BLOCK_KEY: Record<ContinueBlock, string> = {
  noCharter: "projectChat.gate.noCharter",
  dispatching: "workspace.continue.blocked.dispatching",
  taskNotRunnable: "workspace.continue.blocked.taskNotRunnable",
  runsUnknown: "workspace.continue.blocked.runsUnknown",
  runActive: "workspace.continue.blocked.runActive",
  followUpPending: "workspace.continue.blocked.followUpPending",
  attentionUnknown: "workspace.continue.blocked.attentionUnknown",
  attentionOpen: "workspace.continue.blocked.attentionOpen",
  worktreeChecking: "workspace.continue.worktree.checking",
  worktreeUnreadable: "workspace.continue.worktree.unreadable",
  worktreeForeign: "workspace.continue.worktree.foreign",
  worktreeNotGit: "workspace.continue.worktree.notGit",
  worktreeProvisioning: "workspace.continue.worktree.provisioning",
  worktreeUnprovisionable: "workspace.continue.worktree.unprovisionable",
};

/**
 * What to say about the worktree the run would write to — including the two
 * states that are not refusals ("ready" and "will be created"), which is why
 * this is not just a lookup in CONTINUE_BLOCK_KEY.
 */
export function worktreeNotice(
  readiness: WorkspaceReadiness,
): { key: string; vars: Record<string, string | number> } {
  switch (readiness.kind) {
    case "ready":
      return {
        key: "workspace.continue.worktree.ready",
        vars: { branch: readiness.branch ?? "" },
      };
    case "provisionable":
      return { key: "workspace.continue.worktree.willProvision", vars: {} };
    case "provisioning":
      return { key: "workspace.continue.worktree.provisioning", vars: {} };
    case "unreadable":
      return {
        key: "workspace.continue.worktree.unreadable",
        vars: { detail: readiness.detail },
      };
    case "foreign":
      return { key: "workspace.continue.worktree.foreign", vars: {} };
    case "notGit":
      return { key: "workspace.continue.worktree.notGit", vars: {} };
    case "unprovisionable":
      return {
        key: "workspace.continue.worktree.unprovisionable",
        vars: { detail: readiness.detail },
      };
    default:
      return { key: "workspace.continue.worktree.checking", vars: {} };
  }
}

// --- Failure classification --------------------------------------------------

/**
 * Why a dispatch did not start a run — or, for the last three, why we cannot say
 * that it didn't. Each kind is a different thing to tell the human, which is the
 * whole point: a stale task version, a task that no longer exists, a rejected
 * request, an open gate, and a lost response are not one "couldn't start" bucket.
 */
export type DispatchFailure =
  | { kind: "alreadyRunning" }
  | { kind: "gateOpen"; detail: string }
  | { kind: "reviewActive"; detail: string }
  /**
   * No independent review accounts for the previous worker run: none was created,
   * or one was and it ended without reviewing anything, or it finished and left no
   * approval to decide on.
   */
  | { kind: "reviewOwed"; detail: string }
  | { kind: "taskChanged" }
  /** A 409 whose re-read failed: something refused it, and we cannot say what. */
  | { kind: "conflictUnknown"; detail: string }
  | { kind: "taskGone"; detail: string }
  | { kind: "rejected"; detail: string }
  | { kind: "provisionFailed"; detail: string }
  | { kind: "worktreeNotReady"; detail: string }
  /** The POST failed without an answer, and a run for this task IS now active. */
  | { kind: "startedUnconfirmed"; detail: string }
  /** The POST failed without an answer, and the re-read failed too: unknown. */
  | { kind: "startUnknown"; detail: string }
  /** The POST failed without an answer, and no run is active now. */
  | { kind: "transportFailed"; detail: string }
  | { kind: "unknown"; detail: string };

export const DISPATCH_FAILURE_KEY: Record<DispatchFailure["kind"], string> = {
  alreadyRunning: "workspace.continue.errors.alreadyRunning",
  gateOpen: "workspace.continue.errors.gateOpen",
  reviewActive: "workspace.continue.errors.reviewActive",
  reviewOwed: "workspace.continue.errors.reviewOwed",
  taskChanged: "workspace.continue.errors.taskChanged",
  conflictUnknown: "workspace.continue.errors.conflictUnknown",
  taskGone: "workspace.continue.errors.taskGone",
  rejected: "workspace.continue.errors.rejected",
  provisionFailed: "workspace.continue.errors.provisionFailed",
  worktreeNotReady: "workspace.continue.errors.worktreeNotReady",
  startedUnconfirmed: "workspace.continue.errors.startedUnconfirmed",
  startUnknown: "workspace.continue.errors.startUnknown",
  transportFailed: "workspace.continue.errors.transportFailed",
  unknown: "workspace.continue.errors.unknown",
};

/**
 * The control plane's 409 discriminators for a Run -> Gate ordering refusal.
 *
 * Mirrors GATE_CODE_ATTENTION_OPEN / GATE_CODE_REVIEW_ACTIVE / GATE_CODE_REVIEW_OWED
 * in backend/agora/execution/store.py. Nothing at runtime keeps the two in step, so
 * the backend owns the alarm: `test_run_gate_conflict_codes_mirrored_by_the_frontend`
 * in backend/tests/test_execution.py asserts these strings and names this file.
 */
export const RUN_GATE_CODES = {
  attentionOpen: "attention_gate_open",
  reviewActive: "review_run_active",
  reviewOwed: "review_owed",
} as const;

/**
 * Whether a POST whose outcome we do not know should be answered by re-reading
 * the task's active runs.
 *
 * Two cases, and they are not the same fact:
 * - `status === null`: no HTTP answer at all. The request may have been created,
 *   scheduled, and started while the response was lost on the way back. Reporting
 *   that as "nothing is running" is the one wrong answer available.
 * - `status >= 500`: the server answered, but after the point where it may
 *   already have created the run.
 * A 409/404/422 is the control plane deciding *not* to create a run, so its
 * outcome is known — but 409 still triggers a re-read, because which of the
 * several 409s it was decides the wording.
 */
export function dispatchOutcomeNeedsReread(status: number | null): boolean {
  return status === null || status === 409 || status >= 500;
}

/**
 * Map a failed POST /api/runs onto one of those answers.
 *
 * The 409 the control plane returns covers a stale `expected_task_version`, a
 * task that left planned/running, a lost race, a task that already holds an
 * unfinished run of this role, an open human gate, and a review still in flight.
 * The last two carry a `conflictCode`; the rest are prose, so this does not parse
 * the message. Instead the caller re-reads the task's active runs first and
 * passes what it found: an active run makes "already running" a fact, and
 * anything else means the task moved underneath the page.
 *
 * `rereadFailed` is why the re-read produced nothing, and it is a separate fact
 * from the count. An uncoded 409 whose re-read *failed* is `conflictUnknown`, not
 * `taskChanged`: something refused the dispatch and the page cannot say what, so
 * it must not go on to claim nothing is running — the run that caused the 409 may
 * be executing right now. `taskChanged` is reserved for a re-read that succeeded
 * and found zero. This is the module's own unknown-is-not-absence rule, one level
 * deeper: the count being absent is itself two different facts.
 *
 * `activeRunCount === null` is treated as an unknown re-read too, so a caller that
 * does not pass `rereadFailed` still fails closed rather than open.
 */
export function classifyDispatchFailure(input: {
  status: number | null;
  detail: string;
  activeRunCount: number | null;
  /** The re-read of the task's active runs failed, so the count is not evidence. */
  rereadFailed?: boolean;
  /** The control plane's structured 409 code, when it sent one. */
  conflictCode?: string | null;
}): DispatchFailure {
  const detail = input.detail.trim() || "no detail was returned";
  const countUnknown = input.activeRunCount === null || input.rereadFailed === true;
  if (input.status === 409) {
    // A coded refusal names its own reason and how to clear it; it must not be
    // re-derived from the run count, which says nothing about a waiting gate — and
    // it stands whether or not the re-read worked.
    if (input.conflictCode === RUN_GATE_CODES.attentionOpen) return { kind: "gateOpen", detail };
    if (input.conflictCode === RUN_GATE_CODES.reviewActive) return { kind: "reviewActive", detail };
    // The handoff window the client's own `followUpArmed` timer only guesses at,
    // now answered by the control plane: the last worker run succeeded and no
    // independent review accounts for it — the review may not exist yet, or one may
    // have ended without reviewing anything, or without leaving a gate to settle.
    if (input.conflictCode === RUN_GATE_CODES.reviewOwed) return { kind: "reviewOwed", detail };
    if (countUnknown) return { kind: "conflictUnknown", detail };
    return input.activeRunCount !== null && input.activeRunCount > 0
      ? { kind: "alreadyRunning" }
      : { kind: "taskChanged" };
  }
  if (input.status === 404) return { kind: "taskGone", detail };
  if (input.status === 422) return { kind: "rejected", detail };
  if (dispatchOutcomeNeedsReread(input.status)) {
    // Uncertain outcome. The re-read is the only evidence available, and its
    // three answers are three different things to say.
    if (countUnknown) return { kind: "startUnknown", detail };
    if (input.activeRunCount !== null && input.activeRunCount > 0) {
      return { kind: "startedUnconfirmed", detail };
    }
    return { kind: "transportFailed", detail };
  }
  return { kind: "unknown", detail };
}

/**
 * Whether this outcome establishes that no run of this task is running now.
 *
 * Only used to decide what may be *claimed* alongside a failure — chiefly the
 * "the worktree was created, and nothing is running" note. An uncertain outcome
 * must not borrow that sentence: it is exactly the case where a run may be
 * executing while the page says the opposite.
 */
export function confirmsNothingRunning(failure: DispatchFailure): boolean {
  switch (failure.kind) {
    case "taskChanged":
    case "taskGone":
    case "rejected":
    case "provisionFailed":
    case "worktreeNotReady":
    case "gateOpen":
      // The create transaction refused before inserting a run, and a gate is not
      // a run. The same-role case is `alreadyRunning`, which is excluded below.
      return true;
    case "reviewOwed":
      // Same: refused inside the create transaction, before any insert. Not because
      // the owed review "does not exist" — a review run that failed before it could
      // review anything does exist — but because an *unsettled* review run is a
      // different refusal (`reviewActive`), so this code is only sent when no run of
      // this task is executing.
      return true;
    default:
      // alreadyRunning / reviewActive: a run IS active. conflictUnknown: something
      // refused it and the re-read failed, so the refusal may well have been the
      // live run that caused it. startedUnconfirmed / startUnknown /
      // transportFailed / unknown: not established either way.
      return false;
  }
}

/** Which note to add when this click did create the worktree but no run followed. */
export function provisionNoteKey(failure: DispatchFailure): string {
  return confirmsNothingRunning(failure)
    ? "workspace.continue.errors.afterProvision"
    : "workspace.continue.errors.afterProvisionUnsure";
}

/**
 * Whether a dispatch may proceed after provisioning returned.
 *
 * Provisioning answers with a status, and "not an error" is not "ready": a
 * worktree still being created, or one that came back foreign or errored, is a
 * directory of unknown shape, and dispatching into it is how a run ends up
 * writing somewhere nobody chose. So only an explicit `ready` continues.
 *
 * `describeState` localizes a state that carries no error text of its own, and is
 * passed in so this stays free of React and of the i18n module.
 */
export function provisionResultGate(
  status: { state: string; error?: string | null },
  describeState: (state: string) => string,
): { dispatch: true } | { dispatch: false; failure: DispatchFailure } {
  if (status.state === "ready") return { dispatch: true };
  return {
    dispatch: false,
    failure: {
      kind: "worktreeNotReady",
      detail: status.error ?? describeState(status.state),
    },
  };
}

// --- Prompt seeding ----------------------------------------------------------

export interface ContinueSeedFacts {
  title: string;
  description: string;
  acceptance: readonly string[];
  /**
   * The Resume Brief's own heading and lines, already localized by the caller so
   * this module stays free of the recap's slug vocabulary. `recapHeading === null`
   * means the task has no recorded day at all.
   */
  recapHeading: string | null;
  recapLines: readonly string[];
  /**
   * Recap items the endpoint did not return (`recap.more`). The Resume Brief
   * shows this to the human, so the seed must carry it too: a prompt built from
   * `items` alone presents a trimmed day as the whole day.
   */
  recapMore: number;
  /** The recap read less of the day than the day holds (`recap.partial`). */
  recapPartial: boolean;
  /** The recap read failed: say so instead of implying nothing has happened. */
  recapUnavailable: boolean;
  /** The task's recorded next step, already localized. */
  nextAction: string | null;
  nextActionUnavailable: boolean;
}

/**
 * The prompt the Continue action proposes. It is a *seed*: the caller shows it
 * and lets the human edit it before anything is dispatched, so this function
 * never has to be right — only honest about what the records say, including
 * where they could not be read.
 */
export function seedContinuePrompt(facts: ContinueSeedFacts, t: TranslateFn): string {
  const blocks: string[] = [];
  blocks.push(`${t("workspace.continue.seed.goalLabel")}\n${facts.title.trim()}`);

  const description = facts.description.trim();
  if (description) {
    blocks.push(`${t("workspace.continue.seed.descriptionLabel")}\n${description}`);
  }

  const acceptance = facts.acceptance.map((line) => line.trim()).filter(Boolean);
  if (acceptance.length > 0) {
    blocks.push(
      `${t("workspace.continue.seed.acceptanceLabel")}\n${acceptance.map((line) => `- ${line}`).join("\n")}`,
    );
  }

  if (facts.recapUnavailable) {
    blocks.push(t("workspace.continue.seed.recapUnavailable"));
  } else if (facts.recapHeading === null) {
    blocks.push(t("workspace.continue.seed.recapNever"));
  } else {
    const lines = facts.recapLines.map((line) => line.trim()).filter(Boolean);
    // What the recap did NOT include travels with what it did. The Resume Brief
    // tells the human when a day was trimmed; a seed that drops that hands the
    // agent a history that looks complete, and the agent has no other way to find
    // out. Stated as prose in the prompt, not as a count the model has to infer.
    const withheld: string[] = [];
    if (facts.recapMore > 0) {
      withheld.push(t("workspace.continue.seed.recapMore", { n: facts.recapMore }));
    }
    if (facts.recapPartial) {
      withheld.push(t("workspace.continue.seed.recapPartial"));
    }
    const parts = [facts.recapHeading];
    if (lines.length > 0) {
      parts.push(lines.map((line) => `- ${line}`).join("\n"));
    } else if (withheld.length === 0) {
      // Zero items AND nothing withheld is a real answer: the day had activity
      // that does not summarize. With something withheld it would be a claim the
      // records do not support, so it is left unsaid and the withheld lines speak.
      parts.push(t("workspace.continue.seed.recapNoItems"));
    }
    parts.push(...withheld);
    blocks.push(parts.join("\n"));
  }

  // The tail: the two blocks that must reach the agent whatever else is cut. A
  // task description may legitimately run past the whole prompt limit on its own,
  // and clamping a single joined string keeps only its prefix — which drops the
  // recorded next step and, worse, the closing "stop and ask rather than guess"
  // instruction, turning a bounded ask into an unbounded one. So they are kept
  // apart from the clampable body and reattached after the cut.
  const tail: string[] = [];
  if (facts.nextActionUnavailable) {
    tail.push(t("workspace.continue.seed.nextUnknown"));
  } else if (facts.nextAction && facts.nextAction.trim()) {
    tail.push(`${t("workspace.continue.seed.nextLabel")}\n${facts.nextAction.trim()}`);
  }
  tail.push(t("workspace.continue.seed.instruction"));

  return clampPrompt(blocks.join("\n\n"), t, `\n\n${tail.join("\n\n")}`);
}

/**
 * How much of the prompt limit is reserved for the must-survive tail.
 *
 * Chosen deliberately rather than fitted to today's strings. The tail is a
 * localized label plus the task's recorded next step (itself a fixed dictionary
 * phrase) plus the closing instruction — around 300 characters in English and
 * fewer in Chinese. 2,000 leaves roughly six times that headroom, which absorbs a
 * much longer translation or a third tail block later without anyone having to
 * revisit this number, while still leaving 14,000 of the 16,000-character limit
 * for the body. The reservation only ever costs anything when the prompt is
 * already over the limit and something has to go.
 *
 * A tail longer than the reservation is cut from its *front*, so the closing
 * instruction — the last block, and the one that keeps the run bounded — is the
 * last thing to go.
 */
export const CONTINUE_PROMPT_TAIL_BUDGET = 2_000;

/**
 * Keep a seed inside the control plane's prompt limit. Cutting silently would
 * hand the agent a sentence that stops mid-word with no sign anything was lost,
 * so the cut is marked.
 *
 * `tail` is text that must survive the cut. When it is present the marker sits at
 * the cut point, between the trimmed body and the preserved tail, so the prompt
 * says out loud that its middle is missing rather than letting the agent read a
 * body that stops mid-sentence and a closing instruction as if they were
 * continuous. What was dropped is not reproducible — that is the nature of a
 * length cut — but its absence is stated.
 */
export function clampPrompt(prompt: string, t: TranslateFn, tail: string = ""): string {
  const whole = `${prompt}${tail}`;
  if (whole.length <= CONTINUE_PROMPT_LIMIT) return whole;
  if (!tail) {
    const marker = t("workspace.continue.seed.truncated");
    const room = Math.max(0, CONTINUE_PROMPT_LIMIT - marker.length);
    return `${prompt.slice(0, room)}${marker}`.slice(0, CONTINUE_PROMPT_LIMIT);
  }
  const marker = t("workspace.continue.seed.truncatedMiddle");
  const kept = tail.length <= CONTINUE_PROMPT_TAIL_BUDGET
    ? tail
    : tail.slice(-CONTINUE_PROMPT_TAIL_BUDGET);
  const room = Math.max(0, CONTINUE_PROMPT_LIMIT - marker.length - kept.length);
  return `${prompt.slice(0, room)}${marker}${kept}`.slice(0, CONTINUE_PROMPT_LIMIT);
}

/** Whether an edited prompt can be sent at all (mirrors the backend's rules). */
export function promptIsSendable(prompt: string): boolean {
  return prompt.trim().length > 0 && prompt.length <= CONTINUE_PROMPT_LIMIT;
}

// --- Live run merging --------------------------------------------------------

/**
 * Fold freshly read run records into the list the timeline renders.
 *
 * A dispatched run is queued with no output yet, so the view polls it; a poll
 * answer that lost a race must not overwrite a newer record, hence the version
 * check. Order is preserved (the timeline sorts by its own timestamps) and runs
 * we have not seen before are appended rather than dropped.
 */
export function mergeRunUpdates<T extends { run_id: string; version: number }>(
  current: readonly T[] | null,
  updates: readonly T[],
): T[] {
  const merged = [...(current ?? [])];
  for (const update of updates) {
    const index = merged.findIndex((run) => run.run_id === update.run_id);
    if (index < 0) {
      merged.push(update);
      continue;
    }
    if (update.version >= merged[index].version) merged[index] = update;
  }
  return merged;
}

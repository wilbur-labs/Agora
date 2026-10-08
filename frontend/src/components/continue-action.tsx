"use client";

// The Task Workspace's "Continue" action: start a real Codex run in this task's
// provisioned git worktree, seeded from what the task recorded.
//
// It sits with the Resume Brief because it acts on exactly what that panel says,
// and it never dispatches an invented prompt — the seed is shown and editable,
// and only the button below it sends anything. Adapter choice is deliberately
// absent: this slice runs Codex, and picking executor/environment at dispatch
// time is later work.
//
// The decisions (may this be offered, what does a refusal mean) live in
// lib/continue-run.ts so they can be tested without a browser.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Play, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ApiError, handoffTask, transitionTask, type TaskManifest } from "@/lib/control-plane";
import {
  CONTINUE_ADAPTER,
  CONTINUE_BLOCK_KEY,
  CONTINUE_PROMPT_LIMIT,
  CONTINUE_TIMEOUT_SECONDS,
  DISPATCH_FAILURE_KEY,
  classifyDispatchFailure,
  continueEntryGate,
  continueGate,
  dispatchOutcomeNeedsReread,
  promptIsSendable,
  provisionNoteKey,
  provisionResultGate,
  seedContinuePrompt,
  workspaceReadiness,
  worktreeNotice,
  type ContinueSeedFacts,
  type DispatchFailure,
} from "@/lib/continue-run";
import {
  createRun,
  listActiveRuns,
  type ExecutionAdapter,
  type ExecutionRun,
} from "@/lib/execution";
import { getWorkspaceStatus, provisionWorkspace, type WorkspaceStatus } from "@/lib/workspaces";
import { useI18n } from "@/lib/i18n";

const PROVISION_POLL_MS = 1_500;

export function ContinueAction({
  task,
  projectGate,
  seed,
  activeRunCount,
  openAttentionCount,
  followUpArmed,
  onCreated,
  onTaskMayHaveChanged,
  initiallyOpen = false,
  adapter = CONTINUE_ADAPTER,
}: {
  task: TaskManifest;
  /**
   * The executor this task's project names as its worker. The constant is only the
   * fallback for a project whose record could not be read: a project that says
   * `codex` keeps dispatching to codex whatever the product default is.
   */
  adapter?: ExecutionAdapter;
  /**
   * Open with the seed showing. Set when the page arrived here on its own after the
   * previous step was approved (CR-70): the prompt is in view, one click starts it.
   */
  initiallyOpen?: boolean;
  /** Whether this task's project may be given work at all (CR-61); "unknown" does not
   *  block, because a charter read that failed says nothing about the project. */
  projectGate: "open" | "noCharter" | "unknown";
  seed: ContinueSeedFacts;
  /** Runs for this task that have not settled. null = the fact couldn't be read. */
  activeRunCount: number | null;
  /** Open attention items on this task — a human gate waiting. null = unknown. */
  openAttentionCount: number | null;
  /** A run settled moments ago and the review it may trigger has not appeared yet. */
  followUpArmed: boolean;
  /**
   * A run exists now. `null` means the POST's answer was lost and the re-read
   * found an active run: we know one exists but not its record, so the view has
   * to fetch it rather than being handed an invented one.
   */
  onCreated: (run: ExecutionRun | null) => void;
  /** Creating a run bumps the task's version (planned → running), and a conflict
   *  means it moved underneath us; either way the page's copy is stale. */
  onTaskMayHaveChanged: () => void;
}) {
  const { t, te } = useI18n();
  const [open, setOpen] = useState(initiallyOpen);
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState<"provisioning" | "dispatching" | null>(null);
  const [failure, setFailure] = useState<DispatchFailure | null>(null);
  const [provisionedNow, setProvisionedNow] = useState(false);
  const [status, setStatus] = useState<WorkspaceStatus | null>(null);
  // A failed status read is tracked separately from a missing worktree: the two
  // are different facts, and conflating them would offer to create a worktree
  // that may already exist and hold work. `status === null` with no error is the
  // third state — no answer yet.
  const [statusError, setStatusError] = useState<string | null>(null);
  // Lifting a block: `blocked -> planned`, which the control plane's transition table
  // allows and which no page offered. A rejected cross-review puts the task here, and
  // without this the conversation refuses every message with no way on.
  const [unblocking, setUnblocking] = useState(false);
  const [unblockError, setUnblockError] = useState<string | null>(null);
  const canUnblock = task.state === "blocked" && activeRunCount === 0;
  const unblock = async () => {
    setUnblocking(true);
    setUnblockError(null);
    try {
      await transitionTask(task, "planned");
      if (!mounted.current) return;
      onTaskMayHaveChanged();
    } catch (err) {
      if (!mounted.current) return;
      setUnblockError(err instanceof Error ? err.message : String(err));
    } finally {
      if (mounted.current) setUnblocking(false);
    }
  };
  // Marking done: `verified -> done` through the handoff, which commits whatever the
  // worker's worktree still holds and binds the commit to the task. The dashboard names
  // this as the task's next action ("mark_done"); until now only the Projects page had
  // the button — the same kind of gap as the block above.
  const [markingDone, setMarkingDone] = useState(false);
  const [markDoneError, setMarkDoneError] = useState<string | null>(null);
  const [markDoneNote, setMarkDoneNote] = useState<string | null>(null);
  const canMarkDone = task.state === "verified" && activeRunCount === 0;
  const markDone = async () => {
    setMarkingDone(true);
    setMarkDoneError(null);
    setMarkDoneNote(null);
    try {
      const result = await handoffTask(task);
      if (!mounted.current) return;
      // Say what the handoff actually did to the tree — a commit with N files, or
      // nothing to commit — rather than a bare "done".
      setMarkDoneNote(result.committed && result.commit
        ? t("workspace.continue.markDoneCommitted", { commit: result.commit.slice(0, 7), count: result.files.length })
        : t("workspace.continue.markDoneNothingToCommit"));
      onTaskMayHaveChanged();
    } catch (err) {
      if (!mounted.current) return;
      setMarkDoneError(err instanceof Error ? err.message : String(err));
    } finally {
      if (mounted.current) setMarkingDone(false);
    }
  };
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => { mounted.current = false; };
  }, []);

  const seededPrompt = useMemo(() => seedContinuePrompt(seed, t), [seed, t]);

  // Read the worktree's status only while the panel is open: the collapsed action
  // is gated by facts the view already holds, so switching tasks costs nothing.
  useEffect(() => {
    if (!open) return;
    const controller = new AbortController();
    let timer: number | undefined;
    const read = () => {
      // task_id: the server checks the place this task's work is on (its next run
      // inherits it), not necessarily this machine.
      getWorkspaceStatus(task.project_id, adapter, controller.signal, null, task.task_id)
        .then((value) => {
          if (controller.signal.aborted) return;
          setStatus(value);
          setStatusError(null);
          if (value.state === "provisioning") timer = window.setTimeout(read, PROVISION_POLL_MS);
        })
        .catch((err) => {
          if (controller.signal.aborted || (err as Error).name === "AbortError") return;
          setStatusError((err as Error).message);
        });
    };
    read();
    return () => { controller.abort(); if (timer !== undefined) window.clearTimeout(timer); };
  }, [open, task.project_id, task.task_id, adapter]);

  const readiness = workspaceReadiness({
    // "No answer yet" is carried by `status === null`; the panel resets both
    // fields when it opens, so a stale answer is never what is gated on.
    loading: false,
    readFailed: statusError,
    status,
  });
  const entry = continueEntryGate({
    taskState: task.state,
    projectGate,
    activeRunCount,
    openAttentionCount,
    followUpArmed,
    dispatching: busy !== null,
  });
  const decision = continueGate({
    taskState: task.state,
    projectGate,
    activeRunCount,
    openAttentionCount,
    followUpArmed,
    worktree: readiness,
    dispatching: busy !== null,
  });
  const notice = worktreeNotice(readiness);
  const sendable = decision.ready && promptIsSendable(prompt);

  const openPanel = useCallback(() => {
    setPrompt(seededPrompt);
    setFailure(null);
    setProvisionedNow(false);
    // Drop the previous answer about the worktree rather than gating on it: the
    // panel shows "checking" until this open's own read lands.
    setStatus(null);
    setStatusError(null);
    setOpen(true);
  }, [seededPrompt]);

  const dispatch = async () => {
    if (!sendable || busy !== null) return;
    setFailure(null);
    setProvisionedNow(false);
    if (decision.needsProvision) {
      setBusy("provisioning");
      try {
        const result = await provisionWorkspace(task.project_id, adapter, undefined, null, task.task_id);
        if (!mounted.current) return;
        setStatus(result.status);
        setStatusError(null);
        setProvisionedNow(result.created);
        // "Not an error" is not "ready" — the decision lives in continue-run.ts
        // so it is tested without a browser.
        const gate = provisionResultGate(result.status, (state) => te("workspaceState", state));
        if (!gate.dispatch) {
          setFailure(gate.failure);
          setBusy(null);
          return;
        }
      } catch (err) {
        if (mounted.current) {
          setFailure({ kind: "provisionFailed", detail: (err as Error).message });
          setBusy(null);
        }
        return;
      }
    }
    setBusy("dispatching");
    try {
      const run = await createRun({
        task_id: task.task_id,
        adapter,
        prompt: prompt.trim(),
        timeout_seconds: CONTINUE_TIMEOUT_SECONDS,
        // From the manifest actually in hand, so a task that moved underneath
        // this page is refused by the control plane instead of overwritten.
        expected_task_version: task.version,
        actor: "user",
      });
      if (!mounted.current) return;
      setBusy(null);
      setOpen(false);
      onCreated(run);
      onTaskMayHaveChanged();
    } catch (err) {
      const httpStatus = err instanceof ApiError ? err.status : null;
      const conflictCode = err instanceof ApiError ? err.code : null;
      let observed = activeRunCount;
      // Whether the re-read below actually answered. Kept apart from the count,
      // because "no active runs" and "could not find out" are the same `null` and
      // are not the same fact: on an uncoded 409, the second must not be reported
      // as "the task changed and nothing is running" while the very run that
      // caused the conflict executes.
      let rereadFailed = false;
      if (dispatchOutcomeNeedsReread(httpStatus)) {
        // Any outcome we cannot read off the status. Two families land here:
        //
        // - 409: the control plane decided not to create a run, but which of its
        //   several 409s it was decides the wording, and its message is prose.
        // - no answer at all (network drop, timeout, abort) or a 5xx: the server
        //   may have created, scheduled and STARTED the run while the response was
        //   lost. Keeping the page's stale zero count here is how it comes to say
        //   "nothing is running now" over a live run.
        //
        // Asked as its own question, not filtered out of a page of recent history
        // that a busy task can fill with terminal runs. A failed re-read stays
        // null: that is "unknown", and classifyDispatchFailure says so rather than
        // reading it as an absence.
        observed = await listActiveRuns(task.task_id)
          .then((runs) => runs.length)
          .catch(() => null);
        rereadFailed = observed === null;
        onTaskMayHaveChanged();
      }
      if (!mounted.current) return;
      const outcome = classifyDispatchFailure({
        status: httpStatus,
        detail: (err as Error).message,
        activeRunCount: observed,
        rereadFailed,
        conflictCode,
      });
      setFailure(outcome);
      if (outcome.kind === "startedUnconfirmed") {
        // A run really is active, so as far as the human is concerned the dispatch
        // landed. Close the panel and let the timeline own it — leaving the panel
        // open invites a second dispatch on top of a live run.
        setOpen(false);
        onCreated(null);
      }
      setBusy(null);
    }
  };

  if (!open) {
    return (
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 px-5 pb-2.5 pt-1.5">
        <Button size="sm" onClick={openPanel} disabled={!entry.ready}>
          <Play className="size-3.5" /> {t("workspace.continue.action")}
        </Button>
        {entry.block && (
          <span className="text-xs text-muted-foreground">
            {t(CONTINUE_BLOCK_KEY[entry.block], { state: te("taskState", task.state) })}
          </span>
        )}
        {entry.block === "taskNotRunnable" && canUnblock && (
          <>
            <Button size="sm" variant="outline" onClick={() => void unblock()} disabled={unblocking}>
              {unblocking ? <RefreshCw className="size-3.5 animate-spin" /> : null}
              {t("workspace.continue.unblock")}
            </Button>
            <span className="text-xs text-muted-foreground">{t("workspace.continue.unblockHint")}</span>
          </>
        )}
        {unblockError && (
          <span className="text-xs text-destructive" role="alert">
            {t("workspace.continue.unblockFailed", { reason: unblockError })}
          </span>
        )}
        {entry.block === "taskNotRunnable" && canMarkDone && (
          <>
            <Button size="sm" variant="outline" onClick={() => void markDone()} disabled={markingDone}>
              {markingDone ? <RefreshCw className="size-3.5 animate-spin" /> : null}
              {t("workspace.continue.markDone")}
            </Button>
            <span className="text-xs text-muted-foreground">{t("workspace.continue.markDoneHint")}</span>
          </>
        )}
        {markDoneNote && <span className="text-xs text-muted-foreground" aria-live="polite">{markDoneNote}</span>}
        {markDoneError && (
          <span className="text-xs text-destructive" role="alert">
            {t("workspace.continue.markDoneFailed", { reason: markDoneError })}
          </span>
        )}
        {failure && (
          <span className="text-xs text-destructive" role="alert">
            {t(DISPATCH_FAILURE_KEY[failure.kind], {
              detail: "detail" in failure ? failure.detail : "",
            })}
            {/* Also said here: an uncertain outcome closes the panel, so this is
                where its wording has to survive. */}
            {provisionedNow && ` ${t(provisionNoteKey(failure))}`}
          </span>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-2 px-5 pb-3 pt-1.5">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <p className="text-xs font-semibold">{t("workspace.continue.panelTitle")}</p>
        <span className="text-[0.75rem] tabular-nums text-muted-foreground">
          {t("workspace.continue.count", { n: prompt.length, limit: CONTINUE_PROMPT_LIMIT })}
        </span>
      </div>
      <p className="text-[0.75rem] leading-relaxed text-muted-foreground">{t("workspace.continue.panelHint")}</p>
      <textarea
        value={prompt}
        onChange={(event) => setPrompt(event.target.value)}
        maxLength={CONTINUE_PROMPT_LIMIT}
        aria-label={t("workspace.continue.panelTitle")}
        className="min-h-44 w-full resize-y rounded-lg border bg-background p-2 font-mono text-xs leading-relaxed outline-none focus:border-ring"
      />
      <p className="text-[0.75rem] text-muted-foreground" aria-live="polite">{t(notice.key, notice.vars)}</p>
      <p className="text-[0.75rem] text-muted-foreground">{t("workspace.continue.adapterNote")}</p>
      {!decision.ready && entry.block && entry.block !== "dispatching" && (
        <p className="text-[0.75rem] text-muted-foreground">
          {t(CONTINUE_BLOCK_KEY[entry.block], { state: te("taskState", task.state) })}
        </p>
      )}
      {failure && (
        <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-2 text-xs text-destructive" role="alert">
          {t(DISPATCH_FAILURE_KEY[failure.kind], { detail: "detail" in failure ? failure.detail : "" })}
          {/* The worktree change stuck even though the run did not: say which
              state things are actually in — and only claim "nothing is running"
              for an outcome that establishes it. */}
          {provisionedNow && ` ${t(provisionNoteKey(failure))}`}
        </div>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Button size="sm" onClick={() => void dispatch()} disabled={!sendable}>
          {busy === null ? (
            <>
              <Play className="size-3.5" /> {t("workspace.continue.start")}
            </>
          ) : (
            <>
              <RefreshCw className="size-3.5 animate-spin" />
              {busy === "provisioning" ? t("workspace.continue.provisioning") : t("workspace.continue.starting")}
            </>
          )}
        </Button>
        <Button size="sm" variant="ghost" onClick={() => setOpen(false)} disabled={busy !== null}>
          {t("workspace.continue.cancel")}
        </Button>
        {prompt !== seededPrompt && (
          <button
            type="button"
            onClick={() => setPrompt(seededPrompt)}
            disabled={busy !== null}
            className="text-[0.75rem] font-medium text-primary hover:underline disabled:opacity-50"
          >
            {t("workspace.continue.reseed")}
          </button>
        )}
      </div>
    </div>
  );
}

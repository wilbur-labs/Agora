"use client";

/**
 * One task's conversation with an agent — and, since W9, the whole middle column.
 *
 * The primitive the user handles is a continuing session (tasks/agora/adr/0007):
 * type a message, it continues; type another, the agent remembers the last one.
 * Underneath, each turn is still an auditable Run — with a workspace, an exit
 * code, files it changed, and a review that may hang off it — because that is
 * what every honesty guard from W0 through W7 is attached to. Runs did not
 * disappear; they moved *inside* the conversation.
 *
 * Mockup v2, screen 2, draws the middle column as one stream of run cards: executor,
 * run id, exit, time and duration, the prompt, the terminal, the files changed with a
 * "view" on each, review runs indented under the worker run they review, a footnote
 * (worktree · nested runs), and a message box at the bottom that either notes
 * something into the task's history or dispatches it to an agent. The task's other
 * history — notes, stage changes, specs, gates — is interleaved between the cards by
 * time; it used to be a second tab, which is where notes went to not be seen.
 *
 * Every decision this component makes about what it is allowed to claim lives in
 * `lib/conversation.ts` and `lib/timeline.ts`, which `node --test` can mount. What is
 * left here is rendering. Two things the mockup draws that the data does not carry are
 * not drawn: a retry nested under the run it retries (no record says "retries" —
 * lib/run-tree.ts), and an environment picker (runs execute on this machine until
 * W3-a; the label says so).
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Loader2, PenLine, RotateCcw, Send, Square } from "lucide-react";

import { DeliverablesBlock, type DeliverablesRead } from "@/components/run-deliverables";
import { TimelineEntry, relTime, specForEvent } from "@/components/timeline-entry";
import { useI18n } from "@/lib/i18n";
import {
  appendTaskEvent,
  getTask,
  listProjects,
  type RequirementSpec,
  type TaskEvent,
} from "@/lib/control-plane";
import { cn } from "@/lib/utils";
import {
  EMPTY_TRANSCRIPT,
  conversationOrder,
  formatDuration,
  mergeTranscript,
  nestedSummaryOf,
  nextPollDelay,
  WORKER_ROLE,
  orphanSession,
  REVIEW_ROLE,
  streamEventsOf,
  turnDurationMs,
  UNKNOWN_ROLE,
  sendWouldBeRefused,
  sessionVerdict,
  turnIsActive,
  willStartFresh,
  type SessionVerdict,
  type Transcript,
} from "@/lib/conversation";
import type { RunUnit, UnplacedRun } from "@/lib/run-tree";
import { runsLackKnown, timelineReadOf, timelineVerdictOf } from "@/lib/timeline";
import { CONTINUE_TIMEOUT_SECONDS } from "@/lib/continue-run";
import { MAX_PAGE } from "@/lib/paged";
import {
  cancelRun,
  createRun,
  forgetTaskSession,
  retryTaskSession,
  getRun,
  getRunDeliverables,
  getRunOutput,
  listExecutionAdapters,
  listRuns,
  listTaskSessions,
  type AdapterCapability,
  type AgentSession,
  type RunOutputSlice,
  type ExecutionAdapter,
  type RunSummary,
} from "@/lib/execution";

/** Turns shown without asking. Older ones are reachable by raising the limit. */
const TURNS_SHOWN = 12;
/**
 * As far as "load earlier" can go. The endpoint refuses a limit above
 * SERVER_PAGE_MAX with a 422, and this component drops its list on a failed
 * read — so without the clamp a long enough conversation reached a click that
 * emptied the window and hid everything already loaded.
 */
const TURNS_CEILING = MAX_PAGE;
/** How many turns' transcripts — and settled turns' file lists — are fetched up front. */
const TRANSCRIPTS_PREFETCHED = 4;

/**
 * The sentence for each reason a turn could not be nested; one per reason.
 *
 * Reuses the timeline column's wording rather than restating it: the reasons come
 * from one `runTreeOf`, and two sets of words for one verdict is two things to
 * keep true.
 */
const UNPLACED_TURN_KEY: Record<UnplacedRun<RunSummary>["because"], string> = {
  parentNotLoaded: "workspace.timeline.nested.unplacedParentNotLoaded",
  namesNoRun: "workspace.timeline.nested.unplacedNamesNoRun",
  namesItself: "workspace.timeline.nested.unplacedNamesItself",
  chainNotRooted: "workspace.timeline.nested.unplacedChainNotRooted",
};

interface TaskLike {
  task_id: string;
  title: string;
  state: string;
  version: number;
  project_id?: string;
}

export function Conversation({
  task,
  events,
  eventsReadFailed,
  eventsBehind,
  specs,
  knownActiveRuns = null,
  onHistoryMayHaveChanged,
  onPosted,
  onTurnSettled,
  onTaskMayHaveChanged,
}: {
  task: TaskLike;
  /**
   * The task's whole event history, read by the page (the endpoint takes no limit, so
   * the read is never capped). `null` until it lands or when it failed — told apart by
   * `eventsReadFailed`.
   */
  events: TaskEvent[] | null;
  eventsReadFailed: boolean;
  /** The page holds a record this list lacks (a note just posted, a gate raised mid-run). */
  eventsBehind: boolean;
  specs: RequirementSpec[] | null;
  /**
   * The task's unsettled runs as the page's own authoritative read knows them. A run
   * the page knows and this list of turns does not — a review queued after its worker
   * settled, a turn dispatched from another window — is a gap this component can see
   * and close by re-reading, instead of waiting for something local to bump it.
   * `null` when the page has no such read yet.
   */
  knownActiveRuns?: readonly RunSummary[] | null;
  /**
   * Told when the task's history may have changed without an id to wait for — a note
   * whose answer was lost may still be on record, and the only honest thing to do is
   * read again before the person decides whether to write it twice.
   */
  onHistoryMayHaveChanged?: () => void;
  /**
   * Told the id of a note just recorded. The page re-reads its events and, until that
   * read carries the id, the stream says a recorded entry is not shown yet (`eventsBehind`)
   * — it does not promise the read will land.
   */
  onPosted?: (eventId: string) => void;
  /** Told when a turn settles, so the page's other panels can re-read. */
  onTurnSettled?: () => void;
  /** Told when a turn is dispatched: creating a run moves the task's version. */
  onTaskMayHaveChanged?: () => void;
}) {
  const { t, te } = useI18n();
  const [adapters, setAdapters] = useState<AdapterCapability[] | null>(null);
  const [adapter, setAdapter] = useState<ExecutionAdapter>("codex");
  const [sessions, setSessions] = useState<AgentSession[] | null>(null);
  const [turns, setTurns] = useState<RunSummary[] | null>(null);
  const [turnsUnreadable, setTurnsUnreadable] = useState(false);
  const [olderExist, setOlderExist] = useState(false);
  // Raised by the person, not by a poll: older turns exist and are worth
  // reaching, but fetching every turn of a long-lived task on open would
  // make the window slower the more it has been used.
  const [shown, setShown] = useState(TURNS_SHOWN);
  const [transcripts, setTranscripts] = useState<Record<string, Transcript>>({});
  // The files each settled turn delivered, keyed by run id; absent = not asked yet.
  const [deliverables, setDeliverables] = useState<Record<string, DeliverablesRead>>({});
  const [draft, setDraft] = useState("");
  // Bumped on every edit of the box. A send or a note captures the value it saw and
  // clears the box only if nothing was typed meanwhile — compared by edit count, not by
  // text, because the next message can legitimately be the same words as the last.
  const draftEdits = useRef(0);
  const [sending, setSending] = useState(false);
  const [noting, setNoting] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  const [nonce, setNonce] = useState(0);

  // The message box drives the *worker* conversation. A review turn has its
  // own session under the same adapter, and reading the first row that
  // matched the adapter showed one while acting on the other.
  const verdict = sessionVerdict(sessions, adapter, WORKER_ROLE);
  // A session recorded before roles existed blocks every turn on this adapter,
  // whatever the worker row says. Reading only the worker row left the page
  // promising a new session while each send came back refused, with no button
  // anywhere to clear the row that was doing the refusing.
  const orphan = orphanSession(sessions, adapter);
  const refuseSend = sendWouldBeRefused(verdict) || orphan !== null;

  useEffect(() => {
    const ctrl = new AbortController();
    listExecutionAdapters(ctrl.signal)
      .then((list) => {
        setAdapters(list);
        // Keep whatever the user chose; only seed the first time.
        setAdapter((current) => (list.some((item) => item.name === current) ? current : list[0]?.name ?? current));
      })
      .catch(() => setAdapters(null));
    return () => ctrl.abort();
  }, []);

  // The project's configured worker, not a hardcoded "codex". A project set up
  // for claude or kiro had its messages sent to codex — which runs in codex's
  // own worktree, so the agent could not see the work already done, and that
  // turn's files, review and approvals were bound to the wrong workspace. This
  // only *seeds* the choice: once the person picks an adapter here, that is what
  // they meant, so it does not run again.
  // Which project the picker was seeded for — not a boolean. A flag that only
  // said "seeded" stayed true across a switch to another project, so a workbench
  // moved from a Codex project to a Claude one kept sending to Codex: the run
  // landed in Codex's worktree, where the work already done is not visible.
  const [seededFor, setSeededFor] = useState<string | null>(null);
  // **Which project** the person's own choice was made for, not a boolean. A
  // late `listProjects` answer used to overwrite their choice; a boolean then
  // carried that choice into the *next* project, where a Kiro pick made in
  // project A quietly overrode project B's Claude.
  const [chosenFor, setChosenFor] = useState<string | null>(null);
  const [projectUnread, setProjectUnread] = useState(false);
  // The project's *reviewer*, which is routinely a different agent from its
  // worker — Codex writing and Claude reviewing is the ordinary setup. Looking
  // for the review session under the worker's adapter found nothing, so a stuck
  // review conversation was invisible while every later review was refused.
  const [reviewAdapter, setReviewAdapter] = useState<ExecutionAdapter | null>(null);
  // The review conversation runs on the same adapter under its own role. When
  // *it* gets stuck, every later automatic review of this task is refused — and
  // the window showed only the worker's session, so there was nothing to press:
  // the retry button sent WORKER_ROLE and no button cleared the review row.
  const reviewVerdict = sessionVerdict(sessions, reviewAdapter ?? adapter, REVIEW_ROLE);
  // Only `lost` refuses every later review. `unreadable` is re-derived each
  // turn and continues by itself once the condition goes away, and `forked`
  // resumes the session the provider actually answered in — telling the person
  // those were stuck, and offering only "start a new session", pushed them to
  // throw away review context that was still reachable.
  const reviewStuck = reviewVerdict.kind === "lost";
  const reviewMoved =
    reviewVerdict.kind === "unreadable" || reviewVerdict.kind === "forked";
  useEffect(() => {
    const project_id = task.project_id;
    if (!project_id || seededFor === project_id) return;
    let cancelled = false;
    listProjects()
      .then((list) => {
        if (cancelled) return;
        const project = list.items.find((item) => item.project_id === project_id);
        if (project?.worker_agent && chosenFor !== project_id) {
          setAdapter(project.worker_agent);
        }
        setReviewAdapter(project?.reviewer_agent ?? null);
        setProjectUnread(false);
        setSeededFor(project_id);
      })
      // A failed read does **not** unblock sending. Which agent this project
      // works with is exactly what could not be read, and defaulting to the
      // first adapter would put the turn in another agent's worktree. The
      // person can still send — by picking an adapter themselves, which says
      // they meant that one.
      .catch(() => !cancelled && setProjectUnread(true));
    return () => {
      cancelled = true;
    };
  }, [task.project_id, seededFor, chosenFor]);
  // Until that answer arrives, or the person picks for themselves, the window
  // does not know which agent this project works with — and sending would put
  // the turn in another agent's worktree, where the work already done is not
  // visible. A second's wait, not a silent mistake.
  const seeding =
    Boolean(task.project_id)
    && seededFor !== task.project_id
    && chosenFor !== task.project_id;

  // Turns and sessions, re-read together after every send and settle so the
  // badge and the stream cannot disagree about which turn is the latest.
  useEffect(() => {
    const ctrl = new AbortController();
    let cancelled = false;
    // One more than is shown, so "there are older turns" is evidence rather
    // than the guess `list.length === TURNS_SHOWN` would make — that guess calls
    // a conversation of exactly TURNS_SHOWN turns truncated. **Clamped to the
    // server's own maximum**: at the ceiling the probe asked for 501 of a
    // maximum 500, the request came back 422, and the catch below emptied the
    // window — every turn already loaded vanished because the *extra* one could
    // not be fetched.
    const probe = Math.min(shown + 1, TURNS_CEILING);
    listRuns({ task_id: task.task_id, limit: probe }, ctrl.signal)
      .then((list) => {
        if (cancelled) return;
        // At the ceiling there is no probe left to spend, so "older exist" is
        // not asserted from a full page — the notice is shown from the ceiling
        // itself instead, because otherwise a task with more than TURNS_CEILING
        // turns showed nothing at all and read as a complete history.
        setOlderExist(probe > shown && list.length > shown);
        setTurns(list.slice(0, shown));
        setTurnsUnreadable(false);
      })
      .catch(() => {
        if (cancelled) return;
        // The list is dropped rather than kept: a stale list drawn as the
        // conversation would show an old turn as the newest thing said.
        setTurns(null);
        setTurnsUnreadable(true);
        // And the page size goes back to what did work, so a failure at a larger
        // page is not a state the window can get stuck in.
        setShown((value) => (value > TURNS_SHOWN ? TURNS_SHOWN : value));
      });
    listTaskSessions(task.task_id, ctrl.signal)
      .then((list) => !cancelled && setSessions(list))
      .catch(() => !cancelled && setSessions(null));
    return () => {
      cancelled = true;
      ctrl.abort();
    };
  }, [task.task_id, nonce, shown]);

  const ordered = useMemo(() => conversationOrder(turns ?? []), [turns]);

  // A run the page knows to be unsettled that this list does not hold. Derived, not
  // tracked, the way the page derives its own gaps: presence in the authoritative
  // active-run read is evidence, absence from it is not. Closed by re-reading — once per
  // time the gap appears; a boolean dependency cannot re-fire while it stays true, so a
  // run that never lands in the page cannot make this loop. Only the unsettled set is
  // compared: an unsettled run is by construction among the newest turns, while the
  // page's full history and the rail's global list both hold runs older than this
  // page shows, and comparing against those would call a complete page short forever.
  const turnsBehind = runsLackKnown(turns, [knownActiveRuns]);
  useEffect(() => {
    if (turnsBehind) setNonce((value) => value + 1);
  }, [turnsBehind]);

  // The stream: the task's history interleaved with its turns by time, and what may be
  // said about its completeness. Both decided in lib/timeline.ts — the interleave, the
  // nesting of reviews under the run they review (via runTreeOf), the runs that cannot
  // be placed, and the four silences an empty list can mean. The turns read is capped by
  // the page being shown; the events read is never capped (the endpoint takes no limit)
  // but can be behind a record the page already holds.
  const atCeiling = shown >= TURNS_CEILING;
  // At the ceiling there is no probe left, so "older turns exist" cannot be shown — but
  // it can be *ruled out*: a page that came back smaller than the ceiling is the whole
  // history. Only a full page at the ceiling leaves the history unvouched for.
  const ceilingUnvouched = atCeiling && turns !== null && turns.length >= shown;
  // Folded behind *proven* older turns only: folding the early history behind turns
  // that may not exist would hide readable records under a sentence about them.
  const stream = useMemo(
    () => streamEventsOf(events ?? [], turns ?? [], olderExist),
    [events, turns, olderExist],
  );
  const streamVerdict = useMemo(
    () => timelineVerdictOf(
      timelineReadOf(events === null ? null : stream.events, { failed: eventsReadFailed, capped: false, behind: eventsBehind }),
      timelineReadOf(turns, { failed: turnsUnreadable, capped: olderExist || ceilingUnvouched, behind: turnsBehind }),
    ),
    [events, stream.events, eventsReadFailed, eventsBehind, turns, turnsUnreadable, olderExist, ceilingUnvouched, turnsBehind],
  );

  // Keep the newest entry in view as the stream grows — on open, and when a turn or a
  // note is appended. Keyed on the entry count, as the timeline column was: a poll that
  // changes nothing but a transcript must not yank a reader who scrolled up.
  const scrollRef = useRef<HTMLDivElement>(null);
  const entryCount = streamVerdict.show === "entries" ? streamVerdict.items.length : 0;
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entryCount]);
  const specsById = useMemo(() => {
    const map = new Map<string, RequirementSpec>();
    for (const spec of specs ?? []) map.set(spec.spec_id, spec);
    return map;
  }, [specs]);

  // Every unsettled turn, not just the first. A worker turn and the review it
  // triggers can be live at once, and polling only the first left the other one's
  // card silent — which reads as an agent that has stopped rather than one whose
  // output nobody is fetching.
  const actives = useMemo(() => ordered.filter(turnIsActive), [ordered]);
  const activeKey = actives.map((turn) => turn.run_id).join(",");

  const pull = useCallback(async (runId: string): Promise<RunOutputSlice | null> => {
    const current = transcripts[runId] ?? EMPTY_TRANSCRIPT;
    try {
      const slice = await getRunOutput(runId, current.offset);
      setTranscripts((prev) => ({
        ...prev,
        [runId]: mergeTranscript(prev[runId] ?? EMPTY_TRANSCRIPT, slice),
      }));
      if (slice.complete) onTurnSettled?.();
      return slice;
    } catch {
      // The request did not come back. Marked as a stall, not as a gap: the
      // offset has not moved, so the next successful read returns everything
      // that was missed. Treating it as a permanent hole made the window claim
      // an incomplete transcript for the rest of the session over one dropped
      // request — while still saying nothing about the drop itself.
      setTranscripts((prev) => ({
        ...prev,
        [runId]: mergeTranscript(prev[runId] ?? EMPTY_TRANSCRIPT, {
          text: "",
          offset: (prev[runId] ?? EMPTY_TRANSCRIPT).offset,
          next_offset: (prev[runId] ?? EMPTY_TRANSCRIPT).offset,
          reachable: true,
          failed: true,
        }),
      }));
      return null;
    }
  }, [transcripts, onTurnSettled]);

  // The files a turn delivered. Asked once per run; a failure is recorded as a failure
  // ("could not load"), never as an empty list.
  const loadFiles = useCallback((runId: string) => {
    setDeliverables((prev) => ({ ...prev, [runId]: "loading" }));
    getRunDeliverables(runId)
      .then((value) => setDeliverables((prev) => ({ ...prev, [runId]: value })))
      .catch(() => setDeliverables((prev) => ({ ...prev, [runId]: "failed" })));
  }, []);

  // Prefetch the newest few turns once, so reopening the page shows the
  // conversation rather than a list of collapsed run ids. Their file lists too, for
  // the settled worker turns among them: the mockup's card carries its files, and a
  // card that hides them behind a click is a card the reader scrolls past. A live
  // turn's files are not asked for here — its list would be the moving worktree, and
  // it is asked for once the turn has settled (the list re-derives on each settle).
  const prefetched = useRef<Set<string>>(new Set());
  const filesRequested = useRef<Set<string>>(new Set());
  useEffect(() => {
    const newest = ordered.slice(-TRANSCRIPTS_PREFETCHED);
    for (const turn of newest) {
      if (!prefetched.current.has(turn.run_id)) {
        prefetched.current.add(turn.run_id);
        void pull(turn.run_id);
      }
      if (!turnIsActive(turn) && turn.role !== REVIEW_ROLE && !filesRequested.current.has(turn.run_id)) {
        filesRequested.current.add(turn.run_id);
        loadFiles(turn.run_id);
      }
    }
    // `pull` is intentionally not a dependency: it changes on every transcript
    // update, and re-running this effect for that would re-enter the prefetch
    // loop on its own output.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ordered]);

  // Live poll: one timer per tick, over every turn that still has something to
  // fetch — the unsettled ones, and any settled turn whose last slice reported
  // more on disk. Driving this from liveness alone left the rest of a long
  // transcript unreachable: no timer, and no button either.
  const pending = useMemo(() => {
    // Any turn with reading left to do, not only the live ones. A cancelled run
    // is terminal in the database before its stdout has drained, so its
    // transcript reports `complete: false` for a moment — and dropping it from
    // this set the instant the run state changed meant the tail a killed agent
    // produced was never fetched. A stalled read is here for the same reason:
    // the fetch that failed still has to be retried by something.
    const unfinished = ordered.filter((turn) => {
      const transcript = transcripts[turn.run_id];
      if (!transcript) return false;
      return transcript.more || transcript.stalled || !transcript.complete;
    });
    const seen = new Set(actives.map((turn) => turn.run_id));
    return [...actives, ...unfinished.filter((turn) => !seen.has(turn.run_id))];
  }, [actives, ordered, transcripts]);

  useEffect(() => {
    if (pending.length === 0) return;
    const delays = pending
      .map((turn) => nextPollDelay(
        transcripts[turn.run_id] ?? EMPTY_TRANSCRIPT, turnIsActive(turn),
      ))
      .filter((delay): delay is number => delay !== null);
    if (delays.length === 0) return;
    const timer = window.setTimeout(() => {
      void Promise.all(pending.map((turn) => pull(turn.run_id))).then((slices) => {
        // Only re-read the turn list when a turn *finished*. Bumping the nonce
        // on every tick re-fetched the runs and the session two or three times a
        // second to learn nothing: the transcript is what changes while a turn
        // is live, and it is already in hand.
        if (slices.some((slice) => slice?.complete)) setNonce((value) => value + 1);
      });
    }, Math.min(...delays));
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeKey, pending.length, transcripts, nonce]);

  async function send() {
    const prompt = draft.trim();
    const edits = draftEdits.current;
    // `refuseSend` guards the action, not just the notice above the box. It used
    // to only render a warning, so the button and Ctrl+Enter still submitted a
    // message the dispatcher was certain to refuse — a failed turn in the
    // conversation, produced by the window that had already worked out it would
    // fail.
    // `seeding`: the project's own worker is not known yet, and sending now
    // would put this turn in whichever agent's worktree the picker happens to
    // show.
    if (!prompt || sending || noting || refuseSend || seeding) return;
    setSending(true);
    setSendError(null);
    try {
      // Re-read the task's version at send time. The first turn carries the task
      // planned -> running, which bumps it, and `create` refuses a stale one —
      // so a conversation's *second* message would be rejected while the window
      // waited for the page around it to refresh. A failed re-read falls back to
      // the version in hand rather than blocking: the control plane is the
      // authority and will say so if it is stale.
      const version = await getTask(task.task_id)
        .then((fresh) => fresh.version)
        .catch(() => task.version);
      const run = await createRun({
        task_id: task.task_id,
        adapter,
        prompt,
        // The same per-turn limit Continue has always used, mirrored from the
        // backend default by lib/continue-run.ts (and pinned by a backend test).
        timeout_seconds: CONTINUE_TIMEOUT_SECONDS,
        expected_task_version: version,
      });
      // Clear what was sent, not whatever is in the box now: the textarea stays
      // editable while the request is out, and a line typed meanwhile is the next
      // message, not part of this one.
      if (draftEdits.current === edits) setDraft("");
      prefetched.current.add(run.run_id);
      setNonce((value) => value + 1);
      onTaskMayHaveChanged?.();
      void pull(run.run_id);
    } catch (error) {
      setSendError(error instanceof Error ? error.message : String(error));
    } finally {
      setSending(false);
    }
  }

  // The other thing the box does: record a note in the task's history without
  // dispatching anything. The written record's id goes up to the page, which is what
  // lets the stream say it is missing an entry while the read that would fetch it has
  // not carried it (`eventsBehind`), instead of the note silently not appearing.
  async function note() {
    const text = draft.trim();
    const edits = draftEdits.current;
    if (!text || sending || noting) return;
    setNoting(true);
    setSendError(null);
    try {
      const written = await appendTaskEvent(task.task_id, "user_message", { text });
      // Same rule as send(): only the text that was recorded leaves the box.
      if (draftEdits.current === edits) setDraft("");
      onPosted?.(written.event_id);
    } catch (error) {
      // A lost answer is not a refusal: the note may well be on record. The endpoint has
      // no idempotency key, so this side cannot make a retry safe — it can only say so,
      // ask the page to read the history again so a recorded note shows up, and point at
      // the stream before the person decides whether to write it twice.
      setSendError(t("conversation.stream.noteFailed", { reason: error instanceof Error ? error.message : String(error) }));
      onHistoryMayHaveChanged?.();
    } finally {
      setNoting(false);
    }
  }

  async function stop(runId: string) {
    // Read the run's current version first. The list is a snapshot, and a run's
    // version moves the moment its process is attached, so cancelling with the
    // version in hand produced a 409 that this only swallowed — the first click
    // on Stop did nothing at all, visibly.
    setSendError(null);
    try {
      const current = await getRun(runId);
      await cancelRun(runId, { expected_version: current.version });
    } catch (error) {
      setSendError(error instanceof Error ? error.message : String(error));
    } finally {
      setNonce((value) => value + 1);
    }
  }

  async function retry(role: string = WORKER_ROLE, on: ExecutionAdapter = adapter) {
    setSendError(null);
    try {
      await retryTaskSession(task.task_id, on, role);
    } catch (error) {
      setSendError(error instanceof Error ? error.message : String(error));
    } finally {
      setNonce((value) => value + 1);
    }
  }

  async function forget(role: string = WORKER_ROLE, on: ExecutionAdapter = adapter) {
    if (!window.confirm(t("conversation.session.forgetConfirm"))) return;
    setSendError(null);
    try {
      await forgetTaskSession(task.task_id, on, role);
    } catch (error) {
      // Shown, not swallowed. The control plane refuses this while a run of the
      // same adapter is still going — a 409 with a reason — and swallowing it
      // left the person believing the conversation had been reset while the next
      // message went straight on resuming the context they meant to drop.
      setSendError(error instanceof Error ? error.message : String(error));
    }
    setNonce((value) => value + 1);
  }

  const cardProps: CardProps = {
    transcriptOf: (runId: string) => transcripts[runId] ?? null,
    deliverablesOf: (runId: string) => deliverables[runId] ?? null,
    onLoadTranscript: (runId: string) => void pull(runId),
    // Also the retry after a failed read: the block offers it, and asking again is the
    // only way a transient failure stops being the answer for the rest of the session.
    onLoadFiles: (runId: string) => {
      filesRequested.current.add(runId);
      loadFiles(runId);
    },
    onStop: (runId: string) => void stop(runId),
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <SessionBadge
        verdict={verdict}
        orphaned={orphan !== null}
        onForget={forget}
        onRetry={retry}
        onForgetOrphan={() => void forget(UNKNOWN_ROLE)}
      />

      {(reviewStuck || reviewMoved) && (
        <div className="border-b bg-amber-500/5 px-5 py-2 text-xs">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-semibold text-amber-700 dark:text-amber-400">
              {reviewStuck
                ? t("conversation.session.reviewStuck")
                : t("conversation.session.reviewMoved")}
            </span>
            {reviewVerdict.kind === "lost" && (
              <button
                className="rounded-md border px-2 py-1 text-muted-foreground hover:text-foreground"
                onClick={() => void retry(REVIEW_ROLE, reviewAdapter ?? adapter)}
              >
                {t("conversation.session.retry")}
              </button>
            )}
            {/* Offered on `lost` only. On the other two the conversation is
                still reachable, and the one button on screen would have been the
                one that throws it away. */}
            {reviewStuck && (
              <button
                className="rounded-md border px-2 py-1 text-muted-foreground hover:text-foreground"
                onClick={() => void forget(REVIEW_ROLE, reviewAdapter ?? adapter)}
              >
                {t("conversation.session.forget")}
              </button>
            )}
          </div>
          <p className="mt-1.5 leading-relaxed text-muted-foreground">
            {("reason" in reviewVerdict && reviewVerdict.reason)
              || (reviewStuck
                ? t("conversation.session.reviewStuckDetail")
                : t("conversation.session.reviewMovedDetail"))}
          </p>
        </div>
      )}

      <div ref={scrollRef} className="min-h-0 flex-1 overflow-y-auto px-5 py-4">
        {turnsUnreadable && <Notice tone="warn">{t("conversation.errors.turnsUnreadable")}</Notice>}
        {/* Two different failures of the events read: nothing ever landed (only the turns
            are here), or a refresh failed and the entries between the cards are from
            before it. The page keeps the old list on a failed refresh by design; what the
            stream must not do is call that list current, or call it absent. */}
        {eventsReadFailed && (
          <Notice tone="warn">
            {events === null ? t("conversation.stream.eventsUnreadable") : t("conversation.stream.eventsStale")}
          </Notice>
        )}
        {/* "Earlier turns exist" only when the probe proved it. At the ceiling the probe
            is gone: a full page says where this view stops and no more; a short page is
            the whole history and says nothing. */}
        {(olderExist || ceilingUnvouched) && (
          <p className="mb-3 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            {olderExist ? (
              <>
                {t("conversation.errors.olderTurns", { shown })}
                <button
                  className="text-primary hover:underline"
                  onClick={() => setShown((value) => Math.min(TURNS_CEILING, value + TURNS_SHOWN))}
                >
                  {t("conversation.errors.loadOlder")}
                </button>
              </>
            ) : (
              <span>{t("conversation.errors.olderCeiling", { limit: TURNS_CEILING })}</span>
            )}
            {stream.heldBack > 0 && <span>{t("conversation.stream.heldBack", { count: stream.heldBack })}</span>}
          </p>
        )}

        {/* One branch per thing the stream may say. The "may not be the whole
            history" caveat lives inside the branch that has entries — the only branch it
            can apply to — and above them, because a caveat under a list nobody scrolls is
            a caveat nobody reads. The four silences are four sentences, none borrowed:
            a read in flight, a read that fell short, a record known to be missing, and
            a history that is genuinely empty. */}
        {streamVerdict.show !== "entries" ? (
          <p className="py-10 text-center text-sm text-muted-foreground">
            {streamVerdict.show === "loading" ? "…"
              : streamVerdict.show === "unknown" ? t("workspace.timeline.unknown")
              : streamVerdict.show === "missing" ? t("workspace.timeline.notShownYet")
              : t("conversation.firstTurn")}
          </p>
        ) : (
          <>
            {streamVerdict.partial !== null && (
              <p className="pb-2 text-center text-xs text-muted-foreground">
                {t(streamVerdict.partial === "loading" ? "workspace.timeline.stillChecking"
                  : streamVerdict.partial === "missing" ? "workspace.timeline.notShownYet"
                  : "workspace.timeline.partial")}
              </p>
            )}
            <div className="space-y-4">
              {streamVerdict.items.map((item) => item.kind === "event" ? (
                <TimelineEntry key={item.key} event={item.event} spec={specForEvent(item.event, specsById)} t={t} te={te} />
              ) : item.kind === "unit" ? (
                <TurnCard key={item.key} unit={item.unit} {...cardProps} />
              ) : (
                /* Turns the tree could not nest, shown with the reason they stand
                   alone. The page of turns is capped, so "the turn this reviews is not
                   in this page" is ordinary — and it means the placement is unknown,
                   not that the turn is a top-level one. Dropping them would hide work
                   that happened; promoting them silently would file a review as a turn
                   of its own. */
                <div key={item.key} className="rounded-lg border border-dashed p-2">
                  <p className="mb-1 text-[11px] leading-snug text-muted-foreground">
                    {t(UNPLACED_TURN_KEY[item.unplaced.because], { limit: shown })}
                  </p>
                  <TurnCard unit={{ run: item.unplaced.run, nested: [] }} {...cardProps} />
                </div>
              ))}
            </div>
          </>
        )}
      </div>

      <div className="border-t bg-muted/30 px-5 py-3">
        {sendError && <Notice tone="error">{t("conversation.errors.sendFailed", { reason: sendError })}</Notice>}
        {projectUnread && seeding && (
          <Notice tone="warn">{t("conversation.errors.projectUnread")}</Notice>
        )}
        {refuseSend && (
          <Notice tone="warn">
            {orphan !== null
              ? t("conversation.session.orphanDetail")
              : t("conversation.session.lostDetail")}
          </Notice>
        )}
        <div className="flex items-end gap-3">
          <textarea
            className="min-h-[44px] flex-1 resize-y rounded-lg border bg-background px-3 py-2 text-sm outline-none focus:ring-2 focus:ring-primary/40"
            rows={2}
            value={draft}
            placeholder={
              // The orphan row is checked before either promise: with one on
              // record every send is refused, so neither "this continues" nor
              // "this starts a new one" is true of the message being typed.
              orphan !== null ? t("conversation.placeholderUnsure")
              : verdict.kind === "active" ? t("conversation.placeholder")
              : willStartFresh(verdict) ? t("conversation.placeholderNew")
              // Neither promise holds: the control plane may resume or may not,
              // and the window does not know which.
              : t("conversation.placeholderUnsure")
            }
            onChange={(event) => {
              draftEdits.current += 1;
              setDraft(event.target.value);
            }}
            onKeyDown={(event) => {
              if (event.key === "Enter" && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                void send();
              }
            }}
          />
          <div className="flex flex-col items-stretch gap-2">
            {/* Executor and environment are chosen here, at the moment of dispatch —
                not in the navigation and not on Today (the mockup's recorded rule).
                The environment is a label, not a picker: runs execute on this machine
                until W3-a lands, and a dropdown with one honest option is a promise. */}
            <div className="flex items-center gap-1.5">
              <select
                className="rounded-md border bg-background px-2 py-1 text-xs"
                value={adapter}
                aria-label={t("conversation.pickers.adapter")}
                onChange={(event) => {
                  setChosenFor(task.project_id ?? null);
                  setAdapter(event.target.value as ExecutionAdapter);
                }}
              >
                {(adapters ?? [{ name: adapter } as AdapterCapability]).map((item) => (
                  <option key={item.name} value={item.name}>{item.name}</option>
                ))}
              </select>
              <span
                className="rounded-md border border-dashed px-2 py-1 text-xs text-muted-foreground"
                title={t("conversation.pickers.environmentTitle")}
              >
                {t("conversation.pickers.environment")} · {t("conversation.pickers.environmentLocal")}
              </span>
            </div>
            <div className="flex items-stretch gap-2">
              <button
                className="flex items-center justify-center gap-1.5 rounded-md border px-3 py-2 text-sm text-muted-foreground hover:text-foreground disabled:opacity-50"
                onClick={() => void note()}
                disabled={sending || noting || !draft.trim()}
                title={t("conversation.stream.noteTitle")}
              >
                {noting ? <Loader2 className="size-4 animate-spin" /> : <PenLine className="size-4" />}
                {t("conversation.stream.note")}
              </button>
              <button
                className="flex flex-1 items-center justify-center gap-2 rounded-md bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground disabled:opacity-50"
                onClick={() => void send()}
                disabled={sending || noting || refuseSend || seeding || !draft.trim()}
              >
                {sending ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
                {sending ? t("conversation.sending") : t("conversation.send")}
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function SessionBadge({
  verdict,
  orphaned,
  onForget,
  onRetry,
  onForgetOrphan,
}: {
  verdict: SessionVerdict;
  orphaned: boolean;
  onForget: () => void;
  onRetry: () => void;
  onForgetOrphan: () => void;
}) {
  const { t } = useI18n();
  const tone =
    orphaned ? "bg-amber-500/10 text-amber-700 dark:text-amber-400"
    : verdict.kind === "active" ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
    : verdict.kind === "none" || verdict.kind === "pending" ? "bg-muted text-muted-foreground"
    : "bg-amber-500/10 text-amber-700 dark:text-amber-400";
  const label = orphaned ? t("conversation.session.orphan") :
    verdict.kind === "active" ? t("conversation.session.active", { turns: verdict.turns })
    : verdict.kind === "none" ? t("conversation.session.none")
    : verdict.kind === "pending" ? t("conversation.session.pending")
    : verdict.kind === "unidentified" ? t("conversation.session.unidentified")
    : verdict.kind === "unreadable" ? t("conversation.session.unreadable")
    : verdict.kind === "lost" ? t("conversation.session.lost")
    : verdict.kind === "forked" ? t("conversation.session.forked")
    : verdict.kind === "unsupported" ? t("conversation.session.unsupported")
    : t("conversation.session.unknown");
  // The reason the control plane gave, verbatim, because "fix the worktree" and
  // "start a new one" are different actions and only that text distinguishes them.
  const detail = orphaned ? t("conversation.session.orphanDetail")
    : verdict.kind === "pending" ? t("conversation.session.pendingDetail")
    : verdict.kind === "unidentified" ? (verdict.reason ?? t("conversation.session.unidentifiedDetail"))
    : verdict.kind === "unreadable" ? (verdict.reason ?? t("conversation.session.unreadableDetail"))
    : verdict.kind === "lost" ? (verdict.reason ?? t("conversation.session.lostDetail"))
    : verdict.kind === "forked" ? (verdict.reason ?? t("conversation.session.forkedDetail"))
    : verdict.kind === "unsupported" ? (verdict.reason ?? t("conversation.session.unsupportedDetail"))
    : verdict.kind === "unknown" ? t("conversation.session.unknownDetail")
    : null;

  return (
    <div className="border-b px-5 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn("rounded-full px-2.5 py-0.5 text-xs font-semibold", tone)}>{label}</span>
        {verdict.kind === "active" && (
          <span className="font-mono text-[11px] text-muted-foreground">
            {t("conversation.session.idLabel")} {verdict.sessionId.slice(0, 8)}
          </span>
        )}
        {/* The way out of `lost` that keeps the conversation. That state
            refuses every turn on a real observation, and its own reason says to
            fix the cause and carry on — which named an action the window did not
            offer: the only button was the one that throws the context away. */}
        {verdict.kind === "lost" && !orphaned && (
          <button
            className="ml-auto flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs text-muted-foreground hover:text-foreground"
            onClick={() => onRetry()}
          >
            <RotateCcw className="size-3" />
            {t("conversation.session.retry")}
          </button>
        )}
        {/* `unsupported` is here too: that row keeps its id, so there is
            something to clear — and without the button the only state the page
            said was unresumable was also the only one it gave no way out of. */}
        {(orphaned || verdict.kind === "active" || verdict.kind === "unreadable"
          || verdict.kind === "lost" || verdict.kind === "forked"
          || verdict.kind === "unsupported") && (
          <button
            className={cn(
              "flex items-center gap-1.5 rounded-md border px-2 py-1 text-xs text-muted-foreground hover:text-foreground",
              // The retry button already took the right-hand slot when both are
              // shown, so this one only claims it when it stands alone.
              verdict.kind === "lost" && !orphaned ? "" : "ml-auto",
            )}
            // Wrapped, not passed: `onClick={onForget}` handed React's mouse
            // event to `forget(role)`, which encoded it as
            // `role=[object Object]` — a 422 from the control plane, so the one
            // button offered on a `lost` session did nothing at all.
            onClick={() => (orphaned ? onForgetOrphan() : onForget())}
          >
            <RotateCcw className="size-3" />
            {t("conversation.session.forget")}
          </button>
        )}
      </div>
      {detail && <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">{detail}</p>}
    </div>
  );
}

interface CardProps {
  transcriptOf: (runId: string) => Transcript | null;
  deliverablesOf: (runId: string) => DeliverablesRead | null;
  onLoadTranscript: (runId: string) => void;
  onLoadFiles: (runId: string) => void;
  onStop: (runId: string) => void;
}

/**
 * One work unit: the worker turn as the mockup's card, with the runs recorded against
 * it indented underneath.
 *
 * Header: executor · run id · state (with exit code) · when · how long. The duration is
 * printed only for a settled turn with both stamps (`turnDurationMs`); a live turn
 * shows its state instead of a number ticking off a clock this component does not own.
 * Body: the prompt and the terminal. Then the files the turn delivered, through the
 * same block the right column uses. Footnote: the worktree the run ran in (from the
 * files record, which names it) and how many runs are nested here — reviews by role,
 * and "other" for anything else, because nothing in the records says "retry".
 */
function TurnCard({ unit, transcriptOf, deliverablesOf, onLoadTranscript, onLoadFiles, onStop }: { unit: RunUnit<RunSummary> } & CardProps) {
  const { t } = useI18n();
  const run = unit.run;
  const live = turnIsActive(run);
  const transcript = transcriptOf(run.run_id);
  const delivered = deliverablesOf(run.run_id);
  const nested = nestedSummaryOf(unit.nested);
  const worktree = delivered !== null && delivered !== "loading" && delivered !== "failed" && delivered.workspace
    ? delivered.workspace.split(/[\\/]/).filter(Boolean).slice(-2).join("/")
    : null;

  return (
    <article className="overflow-hidden rounded-lg border bg-card">
      <header className="flex flex-wrap items-center gap-2 border-b bg-muted/40 px-3 py-2 text-xs">
        <span className="font-semibold">{run.adapter}</span>
        <span className="font-mono text-muted-foreground">{run.run_id.slice(0, 8)}</span>
        <StatePill state={run.state} exitCode={run.exit_code} />
        <TurnClock run={run} />
        {live && (
          <button className="flex items-center gap-1 rounded border px-2 py-0.5 text-muted-foreground hover:text-foreground" onClick={() => onStop(run.run_id)}>
            <Square className="size-3" /> {t("conversation.stop")}
          </button>
        )}
      </header>

      <TranscriptBody transcript={transcript} live={live} onLoad={() => onLoadTranscript(run.run_id)} />

      {/* The files this turn delivered. Not asked for while the turn is live — that
          list would be the moving worktree — and never guessed: "none" is its own
          sentence in the block. */}
      {!live && (
        <div className="border-t px-3 py-2">
          <p className="mb-1 text-[11px] font-medium text-muted-foreground">{t("conversation.turn.files")}</p>
          <DeliverablesBlock runId={run.run_id} delivered={delivered} onLoad={() => onLoadFiles(run.run_id)} t={t} compact />
        </div>
      )}

      {unit.nested.length > 0 && (
        <div className="space-y-2 border-t px-3 py-3">
          {unit.nested.map((child) => (
            <NestedTurn
              key={child.run_id}
              run={child}
              transcript={transcriptOf(child.run_id)}
              delivered={deliverablesOf(child.run_id)}
              onLoad={() => onLoadTranscript(child.run_id)}
              onLoadFiles={() => onLoadFiles(child.run_id)}
              onStop={() => onStop(child.run_id)}
            />
          ))}
        </div>
      )}

      {(worktree !== null || nested.total > 0) && (
        <footer className="flex flex-wrap gap-x-3 gap-y-1 border-t bg-muted/20 px-3 py-1.5 text-[11px] text-muted-foreground">
          {worktree !== null && <span className="font-mono">{t("conversation.turn.worktree", { name: worktree })}</span>}
          {nested.total > 0 && (
            <span>{t("conversation.turn.nested", { count: nested.total, reviews: nested.reviews, others: nested.others })}</span>
          )}
        </footer>
      )}
    </article>
  );
}

/** When the turn happened and, once settled, how long it took. */
function TurnClock({ run }: { run: RunSummary }) {
  const { t } = useI18n();
  const duration = turnDurationMs(run);
  return (
    <span className="ml-auto font-mono text-muted-foreground">
      {relTime(run.started_at ?? run.queued_at, t)}
      {duration !== null && <> · {formatDuration(duration)}</>}
      {duration === null && turnIsActive(run) && <> · {t("conversation.turn.liveNoDuration")}</>}
    </span>
  );
}

/**
 * A run recorded against the worker turn — a review, today — indented under it, with
 * its own state, its own transcript and its own files on demand. Indentation
 * de-emphasises, so the state pill stays: a failed review under a succeeded turn is the
 * case a person most needs to see. Its files are offered too: a reviewer that wrote a
 * report, or touched source by mistake, delivered something, and a card that hid it
 * would let that be approved unseen.
 */
function NestedTurn({ run, transcript, delivered, onLoad, onLoadFiles, onStop }: {
  run: RunSummary;
  transcript: Transcript | null;
  delivered: DeliverablesRead | null;
  onLoad: () => void;
  onLoadFiles: () => void;
  onStop: () => void;
}) {
  const { t } = useI18n();
  const live = turnIsActive(run);
  const [open, setOpen] = useState(false);
  return (
    <div className="rounded border-l-2 border-l-primary/50 bg-muted/30 text-xs">
      <div className="flex flex-wrap items-center gap-2 px-3 py-2">
        <span className="font-semibold">{run.adapter}</span>
        {/* Its own id first, then the run it reviews: two reviews of one turn by the
            same executor are told apart by nothing else on the card. */}
        <span className="font-mono text-muted-foreground">{run.run_id.slice(0, 8)}</span>
        <span className="text-muted-foreground">
          {run.role === REVIEW_ROLE ? t("conversation.turn.review") : run.role}
        </span>
        {run.reviews_run_id && (
          <span className="font-mono text-muted-foreground">{t("conversation.turn.reviews", { id: run.reviews_run_id.slice(0, 8) })}</span>
        )}
        <StatePill state={run.state} exitCode={run.exit_code} />
        <TurnClock run={run} />
        {live && (
          <button className="flex items-center gap-1 rounded border px-2 py-0.5 text-muted-foreground hover:text-foreground" onClick={onStop}>
            <Square className="size-3" /> {t("conversation.stop")}
          </button>
        )}
        <button
          className="text-primary hover:underline"
          onClick={() => { setOpen((value) => !value); if (!open && transcript === null) onLoad(); }}
        >
          {open ? t("workspace.timeline.run.collapse") : t("workspace.timeline.run.output")}
        </button>
      </div>
      {open && <TranscriptBody transcript={transcript} live={live} onLoad={onLoad} />}
      {open && !live && (
        <div className="border-t border-border/50 px-3 py-2">
          <p className="mb-1 text-[11px] font-medium text-muted-foreground">{t("conversation.turn.files")}</p>
          <DeliverablesBlock runId={run.run_id} delivered={delivered} onLoad={onLoadFiles} t={t} compact />
        </div>
      )}
    </div>
  );
}

function TranscriptBody({ transcript, live, onLoad }: { transcript: Transcript | null; live: boolean; onLoad: () => void }) {
  const { t } = useI18n();
  if (transcript === null) {
    return (
      <div className="px-3 py-2">
        <button className="text-xs text-primary hover:underline" onClick={onLoad}>
          {t("conversation.transcript.load")}
        </button>
      </div>
    );
  }
  const [first, ...rest] = splitPrompt(transcript.text);
  return (
    <div className="px-3 py-2">
      {first && <p className="mb-2 border-l-2 border-l-primary pl-2 text-sm">{first}</p>}
      {transcript.unreadable && (
        <Notice tone="warn">
          {transcript.unreadableReason
            ? t("conversation.transcript.unreadable", { reason: transcript.unreadableReason })
            : t("conversation.transcript.unreadableNoReason")}
        </Notice>
      )}
      {transcript.stalled && (
        <Notice tone="warn">{t("conversation.transcript.stalled")}</Notice>
      )}
      {transcript.lossy && (
        <Notice tone="warn">{t("conversation.transcript.lossy")}</Notice>
      )}
      {transcript.fromRecord && (
        <p className="mb-2 text-xs italic text-muted-foreground">
          {t("conversation.transcript.fromRecord")}
        </p>
      )}
      {rest.join("\n").trim() ? (
        <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded bg-zinc-950 px-3 py-2 font-mono text-[11.5px] leading-relaxed text-zinc-200">
          {rest.join("\n")}
          {live && <span className="animate-pulse">▌</span>}
        </pre>
      ) : (
        <p className="text-xs italic text-muted-foreground">
          {live
            ? t("conversation.transcript.waiting")
            : transcript.complete
              ? t("conversation.transcript.settledEmpty")
              : t("conversation.transcript.unreachable")}
        </p>
      )}
    </div>
  );
}

/**
 * Split the user's own line off the top of a transcript.
 *
 * The dispatcher opens every transcript with `› prompt` (or `›› prompt` for a
 * continued session), so the window can draw the two halves of the conversation
 * differently without a second source for the prompt text.
 */
function splitPrompt(text: string): string[] {
  const lines = text.split("\n");
  // The leading *run* of marked lines, not just the first one: the message box is
  // a textarea, and taking one line left the rest of a multi-line instruction in
  // the terminal pane, reading as something the agent had said.
  let end = 0;
  while (end < lines.length && /^››? /.test(lines[end])) end += 1;
  if (end === 0) return ["", ...lines];
  const said = lines.slice(0, end).map((line) => line.replace(/^››? /, "")).join("\n");
  return [said, ...lines.slice(end)];
}

function StatePill({ state, exitCode }: { state: string; exitCode: number | null }) {
  const { t } = useI18n();
  const tone =
    state === "succeeded" ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
    : state === "running" ? "bg-blue-500/10 text-blue-700 dark:text-blue-400"
    : state === "queued" ? "bg-muted text-muted-foreground"
    : "bg-red-500/10 text-red-700 dark:text-red-400";
  const label =
    state === "running" ? t("conversation.turn.running")
    : state === "queued" ? t("conversation.turn.queued")
    : exitCode !== null ? t("conversation.turn.exit", { code: exitCode })
    : state;
  return <span className={cn("rounded-full px-2 py-0.5 text-[11px] font-semibold", tone)}>{label}</span>;
}

function Notice({ tone, children }: { tone: "warn" | "error"; children: React.ReactNode }) {
  return (
    <p
      className={cn(
        "mb-2 rounded-md border px-3 py-2 text-xs leading-relaxed",
        tone === "error"
          ? "border-red-500/30 bg-red-500/5 text-red-700 dark:text-red-400"
          : "border-amber-500/30 bg-amber-500/5 text-amber-800 dark:text-amber-300",
      )}
    >
      {children}
    </p>
  );
}

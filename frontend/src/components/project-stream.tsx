"use client";

/**
 * The project conversation (W12, mockup v4): one stream for the whole project.
 *
 * Every run of every step, every gate a reviewer raised, and what the person typed —
 * in the order the work began, in one column, with one box at the bottom. A step is
 * never a place you go; it is the number on a card and a row in the right-hand
 * column. What a typed line means is decided by lib/project-stream.ts (a note, an
 * idea, or work for the current step), and what happens after a review is answered
 * by lib/auto-advance.ts (approve → the step is handed off and the next one is laid
 * out; reject → the reviewer's words go back to the worker). The person's rule for
 * all of it: what can be automatic is; show more, click less.
 *
 * Reads are facts with their own failure sentences: a failed runs read is "cannot
 * read", not "no runs"; a full page is "the last N", not "all".
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ChevronDown, RefreshCw, Send } from "lucide-react";
import { Button } from "@/components/ui/button";
import { InlineGateBlock, type GateOutcome } from "@/components/inline-gate";
import { DeliverablesBlock, type DeliverablesRead } from "@/components/run-deliverables";
import { relTime } from "@/components/timeline-entry";
import { listAttention, type AttentionItem } from "@/lib/attention";
import {
  ApiError, appendTaskEvent, getTask, handoffTask, placeIdeaTask, proposeFromIdea, transitionTask,
  type IdeaProposal, type TaskManifest,
} from "@/lib/control-plane";
import {
  createRun, getRun, getRunDeliverables, listActiveProjectRuns, listExecutionAdapters, listRuns,
  remoteTargetLabel,
  type AdapterCapability, type ExecutionAdapter, type ExecutionRun, type RunSummary,
} from "@/lib/execution";
import { CONTINUE_ADAPTER, CONTINUE_TIMEOUT_SECONDS, provisionResultGate, seedContinuePrompt } from "@/lib/continue-run";
import { getWorkspaceStatus, provisionWorkspace } from "@/lib/workspaces";
import { listTaskEvents } from "@/lib/control-plane";
import { nextStepAfter, noNextStepIsCertain, reviewFixPrompt } from "@/lib/auto-advance";
import {
  activeRunsOf, composeIntentOf, dispatchTargetOf, streamItemsOf, streamStatusOf,
  type StreamItem,
} from "@/lib/project-stream";
import { REVIEW_REQUESTER } from "@/lib/run-column";
import { formatDuration, turnDurationMs } from "@/lib/conversation";
import type { PlanRow } from "@/lib/task-plan";
import { useI18n } from "@/lib/i18n";
import { workspace as workspaceDictionary } from "@/lib/i18n/dictionaries/workspace";
import { cn } from "@/lib/utils";

/** The runs page. Full means "the last N", and the stream says so. */
const RUNS_LIMIT = 200;
const GATES_LIMIT = 200;
/** The project's unsettled runs are their own read: a history page cannot say what is
 *  running now once the project has more runs than the page holds. */
const ACTIVE_LIMIT = 100;
const POLL_MS = 4000;
/** How many recent worker runs are read in full so what they said shows in the stream. */
const RECENT_REPLIES = 8;
/** A reply longer than this is shown from its end; the whole is under "expand". */
const REPLY_FOLD = 1600;
/** The read-only preamble a question carries, in every language it was ever sent in. */
const ASK_PREAMBLES: readonly string[] = [
  workspaceDictionary.zh.stream.compose.askPreamble,
  workspaceDictionary.en.stream.compose.askPreamble,
];

type LocalItem =
  /** `runId`: the run this echo announced — once that run is on record the echo goes. */
  | { type: "user"; key: string; at: string; text: string; runId?: string }
  /** A note on record (a `user_message` task event) — survives a reload. */
  | { type: "note"; key: string; at: string; text: string; step: number | null }
  | { type: "auto"; key: string; at: string; text: string; tone?: "ok" | "warn"; runId?: string }
  | { type: "idea"; key: string; at: string; idea: string; proposal: IdeaProposal | null; error: string | null; placed: string | null; busy: boolean }
  | {
      type: "advance"; key: string; at: string;
      doneStep: number | null; doneTitle: string; commit: string | null; committed: boolean;
      next: PlanRow | null; nextCertain: boolean; seed: string; started: boolean; editing: boolean; error: string | null;
    };

interface Expanded {
  run: ExecutionRun | "loading" | "failed" | null;
  deliverables: DeliverablesRead | null;
}

/**
 * Agent output as it comes: markdown-ish text with fenced code. Fences become code
 * blocks that scroll sideways on their own; everything else wraps, including paths
 * and URLs with no spaces in them — the bubble is the box, and nothing runs out of it.
 * Not a markdown renderer: tables and emphasis stay as typed, which is honest about
 * what the agent actually wrote.
 */
function Prose({ text, className }: { text: string; className?: string }) {
  const parts = text.split(/```[^\n]*\n?/);
  return (
    <div className={cn("min-w-0 space-y-2", className)}>
      {parts.map((part, index) => {
        if (part.trim() === "") return null;
        return index % 2 === 1
          ? <pre key={index} className="max-w-full overflow-x-auto rounded bg-zinc-950 px-2.5 py-2 font-mono text-[11.5px] leading-relaxed text-zinc-200">{part.replace(/\n$/, "")}</pre>
          : <p key={index} className="whitespace-pre-wrap break-words [overflow-wrap:anywhere]">{part.trim()}</p>;
      })}
    </div>
  );
}

export function ProjectStream({
  projectId, projectName, tasks, rows, rowsWhole, workerAdapter, onChanged,
}: {
  projectId: string;
  projectName: string;
  /** This project's tasks — for versions, descriptions and acceptance lines. */
  tasks: readonly TaskManifest[];
  /** The order, as the right-hand column shows it. */
  rows: readonly PlanRow[];
  rowsWhole: boolean;
  /** The project's worker agent from the control plane; null when that read failed. */
  workerAdapter: string | null;
  /** Told when something here moved a task, so the page's own reads follow. */
  onChanged: () => void;
}) {
  const { t, te } = useI18n();
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [runsFailed, setRunsFailed] = useState(false);
  const [gates, setGates] = useState<AttentionItem[] | null>(null);
  const [gatesFailed, setGatesFailed] = useState(false);
  const [gatesCapped, setGatesCapped] = useState(false);
  const [active, setActive] = useState<RunSummary[] | null>(null);
  const [activeFailed, setActiveFailed] = useState(false);
  const [notes, setNotes] = useState<Extract<LocalItem, { type: "note" }>[]>([]);
  const [local, setLocal] = useState<LocalItem[]>([]);
  // Full records of the most recent worker runs, read on their own: the summary has
  // neither the line that started the run nor what the agent said back, and both are
  // the conversation — the person asked a question and saw only a folded card.
  const [details, setDetails] = useState<Record<string, ExecutionRun | "failed">>({});
  const [expanded, setExpanded] = useState<Record<string, Expanded>>({});
  const [adapters, setAdapters] = useState<AdapterCapability[] | null>(null);
  const [adapter, setAdapter] = useState<ExecutionAdapter>((workerAdapter as ExecutionAdapter | null) ?? CONTINUE_ADAPTER);
  const [adapterChosen, setAdapterChosen] = useState(false);
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [notice, setNotice] = useState<{ text: string; tone: "ok" | "warn" | "bad" } | null>(null);
  const streamRef = useRef<HTMLDivElement | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);

  // The project's worker is the default executor until the person picks another.
  useEffect(() => {
    if (!adapterChosen && workerAdapter) setAdapter(workerAdapter as ExecutionAdapter);
  }, [workerAdapter, adapterChosen]);

  const ticket = useRef(0);
  const load = useCallback(async (signal?: AbortSignal) => {
    const mine = ++ticket.current;
    const [runsRead, gatesRead, activeRead] = await Promise.allSettled([
      listRuns({ project_id: projectId, limit: RUNS_LIMIT }, signal),
      // One more than shown: whether the page was cut is measured, not assumed.
      listAttention({ project_id: projectId, limit: GATES_LIMIT + 1 }, signal),
      listActiveProjectRuns(projectId, ACTIVE_LIMIT, signal),
    ]);
    if (signal?.aborted || mine !== ticket.current || !alive.current) return;
    if (runsRead.status === "fulfilled") { setRuns([...runsRead.value]); setRunsFailed(false); } else setRunsFailed(true);
    if (gatesRead.status === "fulfilled") {
      setGatesCapped(gatesRead.value.length > GATES_LIMIT);
      setGates(gatesRead.value.slice(0, GATES_LIMIT));
      setGatesFailed(false);
    } else setGatesFailed(true);
    if (activeRead.status === "fulfilled") { setActive([...activeRead.value]); setActiveFailed(false); } else setActiveFailed(true);
  }, [projectId]);

  // Notes on record: `user_message` events on each step. Read once per project and
  // again after a note is posted — not on every poll; the runs and gates are what move.
  const loadNotes = useCallback(async (signal?: AbortSignal) => {
    const reads = await Promise.allSettled(rows.map((row) => listTaskEvents(row.task_id, signal)));
    if (signal?.aborted || !alive.current) return;
    const found: Extract<LocalItem, { type: "note" }>[] = [];
    reads.forEach((read, index) => {
      if (read.status !== "fulfilled") return;
      for (const event of read.value) {
        if (event.event_type !== "user_message") continue;
        const text = typeof event.payload.text === "string" ? event.payload.text : "";
        if (text === "") continue;
        found.push({ type: "note", key: event.event_id, at: event.created_at, text, step: rows[index].step });
      }
    });
    setNotes(found);
  }, [rows]);
  const notesLoadedFor = useRef<string | null>(null);
  useEffect(() => {
    if (rows.length === 0 || notesLoadedFor.current === projectId) return;
    notesLoadedFor.current = projectId;
    const ctrl = new AbortController();
    void loadNotes(ctrl.signal);
    return () => ctrl.abort();
  }, [projectId, rows.length, loadNotes]);

  // The recent worker runs' full records: the line that started each and what came
  // back. The last few settled ones, fetched once; older ones open on demand. A
  // running worker run is fetched too — its prompt is fixed from the start and is
  // the person's own line, which must not vanish for as long as the run takes — and
  // fetched again once it settles, since its output only then stops changing.
  const fetchedLive = useRef(new Set<string>());
  useEffect(() => {
    if (runs === null) return;
    const isActive = (run: RunSummary) => run.state === "queued" || run.state === "running";
    const settled = runs.filter((run) => !isActive(run));
    const recentWorkers = settled
      .filter((run) => run.role !== "review")
      .sort((a, b) => (a.queued_at < b.queued_at ? 1 : -1))
      .slice(0, RECENT_REPLIES);
    const recentIds = new Set(recentWorkers.map((run) => run.run_id));
    // The reviews of those runs too: the reviewer's verdict is the result the person
    // asked to see, and it must not live only behind "expand".
    const reviews = settled.filter((run) => run.role === "review" && run.reviews_run_id !== null && recentIds.has(run.reviews_run_id));
    const liveWorkers = runs.filter((run) => isActive(run) && run.role !== "review");
    // A record read while the run was live is not its final record: whatever settled
    // run was ever fetched live is fetched again, whether or not it is still among the
    // recent few — and a live read that failed is tried again on the next poll, so a
    // transient error does not leave the reply missing for good (review, 2026-09-19).
    const fetchedLiveSettled = settled.filter((run) => fetchedLive.current.has(run.run_id));
    const seen = new Set<string>();
    const wanted = [
      ...[...recentWorkers, ...reviews].filter((run) => details[run.run_id] === undefined),
      ...fetchedLiveSettled,
      ...liveWorkers.filter((run) => details[run.run_id] === undefined || details[run.run_id] === "failed"),
    ].filter((run) => !seen.has(run.run_id) && seen.add(run.run_id));
    if (wanted.length === 0) return;
    const ctrl = new AbortController();
    for (const run of wanted) {
      const live = isActive(run);
      // Marked before the read, not after: a live read that fails must still be
      // followed by the settled read.
      if (live) fetchedLive.current.add(run.run_id);
      getRun(run.run_id, ctrl.signal)
        .then((full) => {
          if (ctrl.signal.aborted || !alive.current) return;
          if (!live) fetchedLive.current.delete(run.run_id);
          setDetails((all) => ({ ...all, [run.run_id]: full }));
        })
        .catch(() => { if (!ctrl.signal.aborted && alive.current) setDetails((all) => ({ ...all, [run.run_id]: "failed" })); });
      if (live) continue; // its files and reply come once it has settled
      if (run.role !== "review") {
        // What it changed, listed under the reply without a click.
        getRunDeliverables(run.run_id, ctrl.signal)
          .then((files) => { if (!ctrl.signal.aborted && alive.current) setExpanded((all) => ({ ...all, [run.run_id]: { run: all[run.run_id]?.run ?? null, deliverables: files } })); })
          .catch(() => { if (!ctrl.signal.aborted && alive.current) setExpanded((all) => ({ ...all, [run.run_id]: { run: all[run.run_id]?.run ?? null, deliverables: "failed" } })); });
      }
    }
    return () => ctrl.abort();
    // `details` is read to skip what is already held; adding it would refetch on each answer.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [runs]);

  useEffect(() => {
    const ctrl = new AbortController();
    void load(ctrl.signal);
    listExecutionAdapters(ctrl.signal).then((list) => { if (!ctrl.signal.aborted) setAdapters(list); }).catch(() => {});
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") void load(); }, POLL_MS);
    return () => { ctrl.abort(); window.clearInterval(timer); };
  }, [load]);

  const steps = useMemo(() => rows.map((row) => ({ task_id: row.task_id, step: row.step, title: row.title, state: row.state })), [rows]);
  const items = useMemo(() => streamItemsOf(runs, gates, steps), [runs, gates, steps]);
  const merged = useMemo(() => {
    // A note already on record is not repeated by the local echo of the send that made it.
    const noteKeys = new Set(notes.map((note) => note.at.slice(0, 16) + note.text));
    // Nor is the line that started a run repeated once that run's record is in hand.
    // The "sent to…" line goes the moment the run is on record (the card is drawn in
    // its place); the person's own line stays until the run's *full* record is here,
    // because only that record carries the words to draw — dropping the echo on the
    // summary alone left the message invisible for as long as the run took (user,
    // 2026-09-19: "我说的内容不显示").
    const onRecord = new Set((runs ?? []).map((run) => run.run_id));
    const inHand = (runId: string) => details[runId] !== undefined && details[runId] !== "failed";
    const echoes = local.filter((item) => {
      if (item.type === "auto" && item.runId !== undefined && onRecord.has(item.runId)) return false;
      if (item.type === "user" && item.runId !== undefined && onRecord.has(item.runId) && inHand(item.runId)) return false;
      return !(item.type === "user" && noteKeys.has(item.at.slice(0, 16) + item.text));
    });
    const all: (StreamItem<RunSummary, AttentionItem> | LocalItem)[] = [...items, ...notes, ...echoes];
    return all.sort((a, b) => (a.at < b.at ? -1 : a.at > b.at ? 1 : 0));
  }, [items, notes, local, runs, details]);
  const openGates = gates === null ? null : gates.filter((item) => item.state === "open").length;
  // What is running now comes from the dedicated active-run read, never inferred from
  // the history page: an old run still running can be pushed out of that page.
  const status = activeFailed ? { kind: "unknown" as const } : streamStatusOf(active, false, gatesFailed || gatesCapped ? null : openGates, steps);
  const busyTasks = useMemo(() => new Set(activeRunsOf(active).map((run) => run.task_id)), [active]);
  const runsCapped = runs !== null && runs.length >= RUNS_LIMIT;

  useEffect(() => {
    const el = streamRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [merged.length, status.kind]);

  const now = () => new Date().toISOString();
  const push = (item: LocalItem) => setLocal((current) => [...current, item]);
  const drop = (key: string) => setLocal((current) => current.filter((item) => item.key !== key));
  const patch = (key: string, change: Partial<LocalItem>) =>
    setLocal((current) => current.map((item) => (item.key === key ? ({ ...item, ...change } as LocalItem) : item)));

  const taskOf = (taskId: string) => tasks.find((task) => task.task_id === taskId) ?? null;
  /** The line the person typed, when the run carried a preamble or a drafted seed; else the prompt. */
  const displayPromptOf = (run: ExecutionRun): string => {
    const shown = run.result_metadata?.display_prompt;
    if (typeof shown === "string" && shown.trim() !== "") return shown.trim();
    // A question recorded before `display_prompt` existed: the read-only preamble is
    // the agent's instruction, not the person's words — peel it off, in either language.
    let text = run.prompt.trim();
    if (run.role === "chat") {
      for (const preamble of ASK_PREAMBLES) {
        if (text.startsWith(preamble)) { text = text.slice(preamble.length).trim(); break; }
      }
    }
    return text;
  };
  const seedFor = (row: PlanRow): string => {
    const task = taskOf(row.task_id);
    const seed = seedContinuePrompt({
      title: row.title, description: task?.description ?? "", acceptance: task?.acceptance ?? [],
      recapHeading: null, recapLines: [], recapMore: 0, recapPartial: false, recapUnavailable: false,
      nextAction: null, nextActionUnavailable: false,
    }, t);
    // The person verifies from the conversation, so the agent is asked to end with what
    // there is to verify and how — the answer lands in the reply bubble, not in a log.
    return `${seed}\n\n${t("workspace.stream.seedVerify")}`;
  };

  // The worktree the run would execute in, checked and — when it does not exist yet —
  // created, before anything is dispatched (the same preflight the task page's
  // Continue has always done). Returns the sentence that stops the send, or null.
  const ensureWorkspace = async (who: ExecutionAdapter): Promise<string | null> => {
    let status = await getWorkspaceStatus(projectId, who);
    if (status.state === "missing") status = (await provisionWorkspace(projectId, who)).status;
    const gate = provisionResultGate(status, (state) => te("workspaceState", state));
    return gate.dispatch ? null : t("workspace.stream.compose.worktreeNotReady", { detail: "detail" in gate.failure ? gate.failure.detail : te("workspaceState", status.state) });
  };

  // --- what a review answer sets in motion -----------------------------------------
  const handleGate = async (outcome: GateOutcome) => {
    void load();
    onChanged();
    if (outcome.item.requester !== REVIEW_REQUESTER) return;
    const task = taskOf(outcome.item.task_id);
    const step = steps.find((candidate) => candidate.task_id === outcome.item.task_id) ?? null;
    if (outcome.action === "approve") {
      if (task === null) { push({ type: "auto", key: `k${Date.now()}`, at: now(), tone: "warn", text: t("workspace.stream.advance.taskUnknown") }); return; }
      try {
        const result = await handoffTask(task);
        if (!alive.current) return;
        const next = nextStepAfter(rows, task.task_id);
        push({
          type: "advance", key: `adv${Date.now()}`, at: now(),
          doneStep: step?.step ?? null, doneTitle: task.title, commit: result.commit, committed: result.committed,
          next, nextCertain: noNextStepIsCertain(rowsWhole, next), seed: next ? seedFor(next) : "", started: false, editing: false, error: null,
        });
      } catch (err) {
        if (!alive.current) return;
        push({ type: "auto", key: `k${Date.now()}`, at: now(), tone: "warn", text: t("workspace.continue.advance.completeFailed", { reason: err instanceof Error ? err.message : String(err) }) });
      }
      void load(); onChanged();
      return;
    }
    if (outcome.action === "reject") {
      if (task === null) return;
      try {
        const fresh = await getTask(task.task_id);
        const planned = fresh.state === "planned" ? fresh : await transitionTask(fresh, "planned");
        const reviewed = runs?.find((run) => run.run_id === outcome.item.run_id);
        const who = reviewed?.adapter ?? adapter;
        const stop = await ensureWorkspace(who);
        if (stop !== null) throw new Error(stop);
        await createRun({
          task_id: task.task_id,
          adapter: who,
          prompt: reviewFixPrompt({ reviewTitle: outcome.item.title, reviewBody: outcome.item.body, reason: outcome.response }),
          timeout_seconds: CONTINUE_TIMEOUT_SECONDS,
          expected_task_version: planned.version,
          actor: "user",
        });
        if (!alive.current) return;
        push({ type: "auto", key: `k${Date.now()}`, at: now(), tone: "ok", text: t("workspace.continue.advance.fixSent") });
      } catch (err) {
        if (!alive.current) return;
        push({ type: "auto", key: `k${Date.now()}`, at: now(), tone: "warn", text: t("workspace.continue.advance.fixFailed", { reason: err instanceof Error ? err.message : String(err) }) });
      }
      void load(); onChanged();
    }
  };

  const startNext = async (key: string, row: PlanRow, seed: string) => {
    patch(key, { error: null });
    try {
      const stop = await ensureWorkspace(adapter);
      if (stop !== null) throw new Error(stop);
      const fresh = await getTask(row.task_id);
      const at = now();
      const run = await createRun({ task_id: row.task_id, adapter, kind: "work", prompt: seed, metadata: { display_prompt: t("workspace.stream.promptSummary", { step: row.step }) }, timeout_seconds: CONTINUE_TIMEOUT_SECONDS, expected_task_version: fresh.version, actor: "user" });
      if (!alive.current) return;
      patch(key, { started: true, editing: false });
      push({ type: "auto", key: `k${Date.now()}`, at, text: t("workspace.stream.compose.sent", { adapter: adapterLabel(adapter), step: row.step }), runId: run.run_id });
    } catch (err) {
      if (!alive.current) return;
      patch(key, { error: err instanceof Error ? err.message : String(err) });
    }
    void load(); onChanged();
  };

  // --- the box ---------------------------------------------------------------------
  const adapterLabel = (name: string) => te("adapter", name);
  /** Where an adapter runs (W3-a U5): the host from its configuration, or this machine. */
  const envLabel = (name: string): string => {
    const target = remoteTargetLabel(adapters?.find((item) => item.name === name)?.remote);
    return target ? t("workspace.stream.compose.envRemote", { target }) : t("workspace.stream.compose.envLocal");
  };
  const target = dispatchTargetOf(steps, busyTasks, rowsWhole);
  const send = async () => {
    const intent = composeIntentOf(draft);
    if (intent === null || sending) return;
    setSending(true);
    setNotice(null);
    const echo = draft.trim();
    // For a question or work, the line goes on screen the moment it is sent — before
    // the worktree check, the task read and the run's creation, which together take
    // seconds (user, 2026-09-19: "为什么没有马上显示"). If the send then fails, the
    // line comes off the screen and back into the box, with the reason beside it.
    const undo: { current: (() => void) | null } = { current: null };
    const show = () => {
      const key = `u${Date.now()}`;
      push({ type: "user", key, at: now(), text: echo });
      setDraft("");
      undo.current = () => { drop(key); setDraft(echo); undo.current = null; };
      return key;
    };
    try {
      if (intent.kind === "note") {
        const taskId = target.kind === "none" ? (rows[0]?.task_id ?? null) : target.step.task_id;
        if (taskId === null) { setNotice({ text: t("workspace.stream.compose.noteNoTask"), tone: "warn" }); return; }
        await appendTaskEvent(taskId, "user_message", { text: intent.text });
        if (!alive.current) return;
        push({ type: "auto", key: `k${Date.now()}`, at: now(), text: t("workspace.stream.compose.noted") });
        setDraft("");
        void loadNotes();
        return;
      }
      if (intent.kind === "idea") {
        const key = `idea${Date.now()}`;
        push({ type: "user", key: `u${Date.now()}`, at: now(), text: echo });
        push({ type: "idea", key, at: now(), idea: intent.text, proposal: null, error: null, placed: null, busy: true });
        setDraft("");
        try {
          const proposal = await proposeFromIdea(projectId, intent.text);
          if (!alive.current) return;
          patch(key, { proposal, busy: false });
        } catch (err) {
          if (!alive.current) return;
          patch(key, { error: err instanceof Error ? err.message : String(err), busy: false });
        }
        return;
      }
      if (intent.kind === "ask") {
        // A question, not work (CR-75): a chat run on the current step — answered in the
        // step's worktree, never reviewed, the task untouched. Allowed while a worker run
        // is going, because asking is how the person follows what it is doing.
        if (target.kind === "none") { setNotice({ text: t(`workspace.stream.compose.${target.reason}`), tone: "warn" }); return; }
        const echoKey = show();
        const stop = await ensureWorkspace(adapter);
        if (stop !== null) { undo.current?.(); setNotice({ text: stop, tone: "bad" }); return; }
        const fresh = await getTask(target.step.task_id);
        const run = await createRun({
          task_id: target.step.task_id, adapter, kind: "chat",
          // The preamble is for the agent; what the person typed is what the stream shows.
          prompt: `${t("workspace.stream.compose.askPreamble")}\n\n${intent.text}`,
          metadata: { display_prompt: intent.text },
          timeout_seconds: CONTINUE_TIMEOUT_SECONDS, expected_task_version: fresh.version, actor: "user",
        });
        if (!alive.current) return;
        undo.current = null;
        patch(echoKey, { runId: run.run_id });
        push({ type: "auto", key: `k${Date.now()}`, at: now(), text: t("workspace.stream.compose.asked", { adapter: adapterLabel(adapter) }), runId: run.run_id });
        void load();
        return;
      }
      if (target.kind === "busy") {
        setNotice({ text: t("workspace.stream.compose.busy", { adapter: adapterLabel(activeRunsOf(runs).find((run) => run.task_id === target.step.task_id)?.adapter ?? adapter), step: target.step.step }), tone: "warn" });
        return;
      }
      if (target.kind === "none") {
        setNotice({ text: t(`workspace.stream.compose.${target.reason}`), tone: "warn" });
        return;
      }
      const echoKey = show();
      const stop = await ensureWorkspace(adapter);
      if (stop !== null) { undo.current?.(); setNotice({ text: stop, tone: "bad" }); return; }
      const fresh = await getTask(target.step.task_id);
      const run = await createRun({ task_id: target.step.task_id, adapter, kind: "work", prompt: intent.text, timeout_seconds: CONTINUE_TIMEOUT_SECONDS, expected_task_version: fresh.version, actor: "user" });
      if (!alive.current) return;
      undo.current = null;
      patch(echoKey, { runId: run.run_id });
      push({ type: "auto", key: `k${Date.now()}`, at: now(), text: t("workspace.stream.compose.sent", { adapter: adapterLabel(adapter), step: target.step.step }), runId: run.run_id });
      void load(); onChanged();
    } catch (err) {
      if (!alive.current) return;
      undo.current?.();
      const reason = err instanceof ApiError && err.status === 409 ? t("workspace.stream.compose.conflict", { detail: err.message }) : (err instanceof Error ? err.message : String(err));
      setNotice({ text: t("workspace.stream.compose.sendFailed", { reason }), tone: "bad" });
      // A failure is not proof that nothing was created: a response lost on the way
      // back leaves a run the server did start. Reading the list now draws it as its
      // record if it exists, rather than leaving it to the next poll to surface (and
      // for work, the busy gate refuses a second dispatch onto that step meanwhile).
      void load();
    } finally {
      if (alive.current) setSending(false);
    }
  };

  const placeIdea = async (key: string, proposal: IdeaProposal, position: number | null) => {
    if (proposal.task === null) return;
    patch(key, { busy: true, error: null });
    try {
      const at = position ?? (proposal.order.length + 1);
      const placed = await placeIdeaTask(projectId, proposal.task, at);
      if (!alive.current) return;
      patch(key, { busy: false, placed: placed.landed_at === null ? t("workspace.stream.idea.placedUnknown") : t("workspace.stream.idea.placed", { position: placed.landed_at }) });
      onChanged();
    } catch (err) {
      if (!alive.current) return;
      patch(key, { busy: false, error: err instanceof Error ? err.message : String(err) });
    }
  };

  // --- a run card, folded to a line --------------------------------------------------
  const toggleRun = (run: RunSummary) => {
    const current = expanded[run.run_id];
    if (current) { setExpanded((all) => { const next = { ...all }; delete next[run.run_id]; return next; }); return; }
    setExpanded((all) => ({ ...all, [run.run_id]: { run: "loading", deliverables: null } }));
    getRun(run.run_id).then((full) => { if (alive.current) setExpanded((all) => (all[run.run_id] ? { ...all, [run.run_id]: { ...all[run.run_id], run: full } } : all)); })
      .catch(() => { if (alive.current) setExpanded((all) => (all[run.run_id] ? { ...all, [run.run_id]: { ...all[run.run_id], run: "failed" } } : all)); });
  };
  const loadFiles = (runId: string) => {
    setExpanded((all) => (all[runId] ? { ...all, [runId]: { ...all[runId], deliverables: "loading" } } : all));
    getRunDeliverables(runId).then((files) => { if (alive.current) setExpanded((all) => (all[runId] ? { ...all, [runId]: { ...all[runId], deliverables: files } } : all)); })
      .catch(() => { if (alive.current) setExpanded((all) => (all[runId] ? { ...all, [runId]: { ...all[runId], deliverables: "failed" } } : all)); });
  };

  const statePill = (run: RunSummary) => {
    const live = run.state === "running" || run.state === "queued";
    const tone = live ? "border-primary/50 bg-primary/10 text-primary" : run.state === "succeeded" ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400" : "border-destructive/40 bg-destructive/10 text-destructive";
    const ms = turnDurationMs(run);
    return (
      <span className={cn("inline-flex items-center gap-1.5 rounded-full border px-2 py-0.5 font-mono text-[10px]", tone)}>
        {live && <span className="size-1.5 animate-pulse rounded-full bg-current" />}
        {te("runState", run.state)}{run.exit_code !== null && run.state !== "running" ? ` · ${t("workspace.stream.run.exit", { code: run.exit_code })}` : ""}{ms !== null ? ` · ${formatDuration(ms)}` : ""}
      </span>
    );
  };

  const runCard = (run: RunSummary, step: number | null, nested: readonly RunSummary[], because?: string) => {
    const open = expanded[run.run_id];
    return (
      <div key={run.run_id} className="max-w-[64ch] overflow-hidden rounded-lg border bg-muted/30">
        <button type="button" onClick={() => toggleRun(run)} className="flex w-full flex-wrap items-center gap-x-2.5 gap-y-1 px-3 py-2 text-left text-[13px] text-muted-foreground hover:bg-accent/50" aria-expanded={open !== undefined}>
          <b className="font-medium text-foreground">{adapterLabel(run.adapter)}</b>
          <span className="font-mono text-[11px]">{run.run_id.slice(0, 12)}</span>
          <span>{step === null ? t("workspace.stream.run.stepUnknown") : t("workspace.stream.run.step", { step })}</span>
          {run.role === "review" && <span className="rounded border px-1 text-[10px] uppercase tracking-wide">{t("workspace.stream.run.review")}</span>}
          {run.role === "chat" && <span className="rounded border px-1 text-[10px] uppercase tracking-wide">{t("workspace.stream.run.chat")}</span>}
          {/* Where THIS run ran, from its record — not where its adapter points today. */}
          {remoteTargetLabel(run.remote) && <span className="rounded border px-1 font-mono text-[10px]" title={run.remote?.workspace ?? undefined}>{remoteTargetLabel(run.remote)}</span>}
          {statePill(run)}
          {nested.length > 0 && <span className="text-[11px]">{t("workspace.stream.run.nested", { count: nested.length })}</span>}
          {because && <span className="text-[11px] italic">{t(`workspace.stream.run.unplaced.${because}`)}</span>}
          <span className="ml-auto inline-flex items-center gap-1 font-mono text-[11px]"><ChevronDown className={cn("size-3 transition-transform", open && "rotate-180")} />{open ? t("workspace.stream.run.collapse") : t("workspace.stream.run.expand")}</span>
        </button>
        {open && (
          <div className="space-y-2 border-t px-3 py-2.5 text-[12px] text-muted-foreground">
            {open.run === "loading" && <p>{t("workspace.stream.loading")}</p>}
            {open.run === "failed" && <p>{t("workspace.stream.run.readFailed")}</p>}
            {open.run !== null && typeof open.run === "object" && (
              <>
                <p className="font-mono text-[11px]">{t("workspace.stream.run.prompt")}</p>
                <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded bg-background px-2.5 py-2 font-mono text-[11px] leading-relaxed">{open.run.prompt}</pre>
                <p className="font-mono text-[11px]">{t("workspace.stream.run.output")}</p>
                {open.run.stdout_tail.trim() === "" && open.run.stderr_tail.trim() === ""
                  ? <p>{t("workspace.stream.run.noOutput")}</p>
                  : <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded bg-zinc-950 px-2.5 py-2 font-mono text-[11px] leading-relaxed text-zinc-200">{open.run.stdout_tail}{open.run.stderr_tail.trim() !== "" ? `\n--- stderr ---\n${open.run.stderr_tail}` : ""}</pre>}
                {open.run.error_message && <p className="text-destructive">{open.run.error_message}</p>}
              </>
            )}
            <DeliverablesBlock runId={run.run_id} delivered={open.deliverables} onLoad={() => loadFiles(run.run_id)} t={t} compact />
          </div>
        )}
        {nested.length > 0 && (
          <div className="space-y-1.5 border-t bg-background/40 px-3 py-2">
            {nested.map((child) => runCard(child, step, []))}
          </div>
        )}
      </div>
    );
  };

  // --- render -------------------------------------------------------------------------
  const headerPill = status.kind === "running"
    ? <span className="inline-flex items-center gap-1.5 rounded-full border border-primary/50 bg-primary/10 px-2 py-0.5 font-mono text-[10px] text-primary"><span className="size-1.5 animate-pulse rounded-full bg-current" />{t("workspace.stream.status.running", { adapter: adapterLabel(status.run.adapter), step: status.step ?? "?" })}{status.also > 0 ? ` +${status.also}` : ""}</span>
    : status.kind === "waiting" ? <span className="rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 font-mono text-[10px] text-amber-700 dark:text-amber-400">{t("workspace.stream.status.waiting", { count: status.count })}</span>
    : status.kind === "idle" ? <span className="rounded-full border px-2 py-0.5 font-mono text-[10px] text-muted-foreground">{t("workspace.stream.status.idle")}</span>
    : <span className="rounded-full border px-2 py-0.5 font-mono text-[10px] text-muted-foreground">{t("workspace.stream.status.unknown")}</span>;

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      <div className="flex shrink-0 items-center gap-2.5 border-b px-4 py-3 text-[13px] text-muted-foreground">
        <b className="font-medium text-foreground">{projectName}</b>
        <span>· {t("workspace.stream.title")}</span>
        <span className="ml-auto">{headerPill}</span>
      </div>

      <div ref={streamRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-4">
        {runsFailed && <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-700 dark:text-amber-400">{t("workspace.stream.runsUnreadable")}</p>}
        {gatesFailed && <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-700 dark:text-amber-400">{t("workspace.stream.gatesUnreadable")}</p>}
        {runsCapped && <p className="text-center text-[11px] text-muted-foreground">{t("workspace.stream.runsCapped", { limit: RUNS_LIMIT })}</p>}
        {gatesCapped && <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[12px] text-amber-700 dark:text-amber-400">{t("workspace.stream.gatesCapped", { limit: GATES_LIMIT })}</p>}
        {runs === null && !runsFailed && <p className="text-[12px] text-muted-foreground">{t("workspace.stream.loading")}</p>}
        {runs !== null && merged.length === 0 && <p className="text-[13px] text-muted-foreground">{t("workspace.stream.empty")}</p>}
        {merged.map((item) => {
          if (item.type === "run") {
            // The exchange around the run: what the person said (its prompt) above the
            // card, what the agent said back (its output) below — the conversation, not
            // a folded record of it. Only for recent worker runs whose record is in hand.
            const full = details[item.unit.run.run_id];
            const record = full !== undefined && full !== "failed" ? full : null;
            const reply = record?.stdout_tail.trim() ?? "";
            const shown = reply.length > REPLY_FOLD ? reply.slice(reply.length - REPLY_FOLD) : reply;
            return (
              <div key={item.key} className="space-y-2">
                {record && record.prompt.trim() !== "" && (
                  <div className="ml-auto w-fit min-w-0 max-w-[56ch] rounded-xl border border-r-4 border-r-primary bg-primary/5 px-3 py-2 text-[14px] leading-relaxed">
                    <div className="mb-1 font-mono text-[10px] text-muted-foreground">{t("workspace.stream.you")} · {relTime(record.queued_at, t)}</div>
                    {displayPromptOf(record) === record.prompt.trim()
                      ? <Prose text={record.prompt} />
                      : (
                        /* What the person typed (or "the prompt for step N"); the text the
                           agent actually received is one fold away, not hidden. */
                        <details>
                          <summary className="cursor-pointer">{displayPromptOf(record)}</summary>
                          <div className="mt-2 text-[12px] text-muted-foreground"><Prose text={record.prompt} /></div>
                        </details>
                      )}
                  </div>
                )}
                {runCard(item.unit.run, item.step, item.unit.nested)}
                {/* The reply is what came back, so only once the run has settled — a
                    record fetched while it runs carries the prompt, not the answer. */}
                {record && reply !== "" && item.unit.run.state !== "queued" && item.unit.run.state !== "running" && (
                  <div className="min-w-0 max-w-[64ch] rounded-xl border bg-card px-3 py-2 text-[14px] leading-relaxed">
                    <div className="mb-1 font-mono text-[10px] text-muted-foreground">{t("workspace.stream.reply", { adapter: adapterLabel(record.adapter) })} · {relTime(record.finished_at ?? record.queued_at, t)}</div>
                    {reply.length > REPLY_FOLD && <p className="mb-1 text-[11px] text-muted-foreground">{t("workspace.stream.replyFolded")}</p>}
                    <Prose text={shown} />
                    {/* What the run changed — the result to look at, right here. Not
                        for a question: it was told not to change anything. */}
                    {item.unit.run.role !== "chat" && expanded[item.unit.run.run_id]?.deliverables !== undefined && expanded[item.unit.run.run_id]?.deliverables !== null && (
                      <div className="mt-2 border-t pt-2 text-[12px]">
                        <DeliverablesBlock runId={item.unit.run.run_id} delivered={expanded[item.unit.run.run_id].deliverables} t={t} compact />
                      </div>
                    )}
                  </div>
                )}
                {item.unit.nested.map((review) => {
                  const rec = details[review.run_id];
                  const verdict = rec !== undefined && rec !== "failed" ? rec.stdout_tail.trim() : "";
                  if (verdict === "") return null;
                  const cut = verdict.length > REPLY_FOLD ? verdict.slice(verdict.length - REPLY_FOLD) : verdict;
                  return (
                    <div key={review.run_id} className="min-w-0 max-w-[64ch] rounded-xl border border-l-4 border-l-amber-500 bg-card px-3 py-2 text-[14px] leading-relaxed">
                      <div className="mb-1 font-mono text-[10px] text-muted-foreground">{t("workspace.stream.reviewReply", { adapter: adapterLabel(review.adapter) })} · {relTime(review.finished_at ?? review.queued_at, t)}</div>
                      {verdict.length > REPLY_FOLD && <p className="mb-1 text-[11px] text-muted-foreground">{t("workspace.stream.replyFolded")}</p>}
                      <Prose text={cut} />
                    </div>
                  );
                })}
              </div>
            );
          }
          if (item.type === "unplaced") return runCard(item.run, item.step, [], item.because);
          if (item.type === "gate") {
            const responded = item.item.state !== "open";
            return (
              <div key={item.key} className={cn("max-w-[64ch] rounded-xl border px-3 py-2", responded ? "bg-card" : "border-l-4 border-l-amber-500 bg-card")}>
                <div className="flex flex-wrap items-baseline gap-2 text-[12px] text-muted-foreground">
                  <span className="font-semibold text-foreground">{t(responded ? "workspace.timeline.events.attentionResponded" : "workspace.timeline.events.attentionRaised")}</span>
                  {item.step !== null && <span>{t("workspace.stream.run.step", { step: item.step })}</span>}
                  <span className="text-[10px]">{relTime(item.item.created_at, t)}</span>
                  {responded && item.item.response_action && <span className="rounded-full border px-2 py-0.5 font-mono text-[10px]">{te("responseAction", item.item.response_action)}</span>}
                </div>
                {responded
                  ? (
                    <details className="mt-1 text-[13px]">
                      <summary className="cursor-pointer">{item.item.title}{item.item.response ? <span className="text-muted-foreground"> · {item.item.response}</span> : null}</summary>
                      <div className="mt-2 text-[12px] text-muted-foreground"><Prose text={item.item.body} /></div>
                    </details>
                  )
                  : <InlineGateBlock gate={{ kind: "open", item: item.item }} t={t} te={te} onAnswered={(outcome) => void handleGate(outcome)} />}
              </div>
            );
          }
          if (item.type === "user" || item.type === "note") {
            return (
              <div key={item.key} className="ml-auto w-fit min-w-0 max-w-[56ch] rounded-xl border border-r-4 border-r-primary bg-primary/5 px-3 py-2 text-[14px] leading-relaxed">
                <div className="mb-1 font-mono text-[10px] text-muted-foreground">
                  {t("workspace.stream.you")} · {relTime(item.at, t)}
                  {item.type === "note" && <> · {t("workspace.stream.noteTag")}{item.step !== null ? ` · ${t("workspace.stream.run.step", { step: item.step })}` : ""}</>}
                </div>
                <Prose text={item.text} />
              </div>
            );
          }
          if (item.type === "auto") {
            return <div key={item.key} className={cn("min-w-0 max-w-[64ch] rounded-xl border border-l-4 bg-card px-3 py-2 text-[13px] leading-relaxed", item.tone === "warn" ? "border-l-amber-500" : "border-l-primary")}><div className="mb-1 font-mono text-[10px] text-muted-foreground">{t("workspace.stream.auto")} · {relTime(item.at, t)}</div><Prose text={item.text} /></div>;
          }
          if (item.type === "idea") {
            const p = item.proposal;
            return (
              <div key={item.key} className="min-w-0 max-w-[64ch] rounded-xl border border-l-4 border-l-primary bg-card px-3 py-2 text-[13px] leading-relaxed [overflow-wrap:anywhere]">
                <div className="mb-1 font-mono text-[10px] text-muted-foreground">{t("workspace.stream.auto")} · {relTime(item.at, t)}</div>
                {item.busy && p === null && <p className="text-muted-foreground">{t("workspace.stream.idea.thinking")}</p>}
                {item.error && <p className="text-destructive">{t("workspace.stream.idea.failed", { reason: item.error })}</p>}
                {p && (
                  <>
                    <p className="text-muted-foreground">{t("workspace.stream.idea.echo", { echo: p.echo })}</p>
                    <p className="mt-1">{t(`workspace.stream.idea.verdict.${p.verdict}`, { position: p.position ?? "?" })} <span className="text-muted-foreground">{p.why}</span></p>
                    {p.verdict === "task" && p.task && item.placed === null && (
                      <>
                        <p className="mt-1.5 rounded-lg border bg-muted/30 px-2.5 py-1.5"><b className="font-medium">{p.task.title}</b><span className="block text-[12px] text-muted-foreground">{p.task.description}</span></p>
                        <div className="mt-2 flex flex-wrap gap-2">
                          <Button size="sm" onClick={() => void placeIdea(item.key, p, p.position)} disabled={item.busy}>{t("workspace.stream.idea.placeHere", { position: p.position ?? "?" })}</Button>
                          <Button size="sm" variant="outline" onClick={() => void placeIdea(item.key, p, null)} disabled={item.busy}>{t("workspace.stream.idea.placeLast")}</Button>
                          <Button size="sm" variant="ghost" onClick={() => patch(item.key, { placed: t("workspace.stream.idea.skipped") })} disabled={item.busy}>{t("workspace.stream.idea.skip")}</Button>
                        </div>
                        {p.order_truncated && <p className="mt-1 text-[11px] text-muted-foreground">{t("projectChat.ideas.orderTruncated")}</p>}
                      </>
                    )}
                    {item.placed && <p className="mt-1.5 text-emerald-700 dark:text-emerald-400">{item.placed}</p>}
                  </>
                )}
              </div>
            );
          }
          if (item.type === "advance") {
            return (
              <div key={item.key} className="min-w-0 max-w-[64ch] rounded-xl border border-l-4 border-l-emerald-500 bg-card px-3 py-2 text-[13px] leading-relaxed [overflow-wrap:anywhere]">
                <div className="mb-1 font-mono text-[10px] text-muted-foreground">{t("workspace.stream.auto")} · {relTime(item.at, t)}</div>
                <p>{t(item.committed && item.commit ? "workspace.stream.advance.done" : "workspace.stream.advance.doneNothing", { step: item.doneStep ?? "?", title: item.doneTitle, commit: item.commit?.slice(0, 7) ?? "" })}</p>
                {item.next === null
                  ? <p className="mt-1 text-muted-foreground">{t(item.nextCertain ? "workspace.stream.advance.noNext" : "workspace.stream.advance.noNextUncertain")}</p>
                  : (
                    <>
                      <p className="mt-1.5"><b className="font-medium">{t("workspace.stream.advance.next", { step: item.next.step, title: item.next.title })}</b></p>
                      {item.editing
                        ? <textarea value={item.seed} onChange={(e) => patch(item.key, { seed: e.target.value })} className="mt-1.5 min-h-24 w-full resize-y rounded-lg border bg-background p-2 font-mono text-[12px] leading-relaxed" aria-label={t("workspace.stream.advance.seed")} />
                        : <pre className="mt-1.5 max-h-40 overflow-auto whitespace-pre-wrap break-words border-l-2 border-primary/50 pl-2.5 font-mono text-[12px] leading-relaxed text-muted-foreground [overflow-wrap:anywhere]">{item.seed}</pre>}
                      {item.started
                        ? <p className="mt-1.5 text-emerald-700 dark:text-emerald-400">{t("workspace.stream.advance.started")}</p>
                        : (
                          <div className="mt-2 flex flex-wrap items-center gap-2">
                            <Button size="sm" onClick={() => void startNext(item.key, item.next!, item.seed)}>{t("workspace.stream.advance.start", { step: item.next.step })}</Button>
                            <Button size="sm" variant="ghost" onClick={() => patch(item.key, { editing: !item.editing })}>{item.editing ? t("workspace.stream.advance.editDone") : t("workspace.stream.advance.edit")}</Button>
                            <span className="text-[11px] text-muted-foreground">{t("workspace.stream.advance.with", { adapter: adapterLabel(adapter) })}</span>
                          </div>
                        )}
                      {item.error && <p className="mt-1 text-destructive">{t("workspace.stream.compose.sendFailed", { reason: item.error })}</p>}
                    </>
                  )}
              </div>
            );
          }
          return null;
        })}
      </div>

      <div className="shrink-0 space-y-2 border-t bg-muted/30 px-4 py-3">
        <textarea
          id="project-stream-box"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          // Enter sends; Shift+Enter breaks the line (the person's ask). An Enter that
          // ends an IME composition (Chinese / Japanese input) is not a send.
          onKeyDown={(e) => {
            if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing) return;
            e.preventDefault();
            void send();
          }}
          placeholder={t("workspace.stream.compose.placeholder")}
          className="min-h-16 w-full resize-y rounded-lg border bg-background px-3 py-2 text-[14px] leading-relaxed outline-none focus:border-ring"
          disabled={sending}
        />
        <div className="flex flex-wrap items-center gap-2 text-[12px] text-muted-foreground">
          <select
            id="project-stream-adapter"
            value={adapter}
            onChange={(e) => { setAdapter(e.target.value as ExecutionAdapter); setAdapterChosen(true); }}
            className="rounded-md border bg-background px-2 py-1 text-[12px]"
            aria-label={t("workspace.stream.compose.adapter")}
          >
            {(adapters ?? [{ name: adapter } as AdapterCapability]).map((item) => <option key={item.name} value={item.name}>{adapterLabel(item.name)}</option>)}
          </select>
          <span className="rounded-md border px-2 py-1" title={t("workspace.stream.compose.envTitle")}>{adapters === null ? "—" : envLabel(adapter)}</span>
          <span className="min-w-0 flex-1 truncate">
            {composeIntentOf(draft)?.kind === "dispatch"
              ? (target.kind === "step" ? t("workspace.stream.compose.to", { step: target.step.step, title: target.step.title })
                : target.kind === "busy" ? t("workspace.stream.compose.busyShort", { step: target.step.step })
                : t(`workspace.stream.compose.${target.reason}`))
              : composeIntentOf(draft)?.kind === "note" ? t("workspace.stream.compose.noteHint")
              : composeIntentOf(draft)?.kind === "idea" ? t("workspace.stream.compose.ideaHint")
              : (target.kind === "none" ? t(`workspace.stream.compose.${target.reason}`) : t("workspace.stream.compose.askHint", { step: target.step.step }))}
          </span>
          <Button size="sm" onClick={() => void send()} disabled={sending || composeIntentOf(draft) === null}>
            {sending ? <RefreshCw className="size-3.5 animate-spin" /> : <Send className="size-3.5" />}{t("workspace.stream.compose.send")}
          </Button>
        </div>
        {notice && <p className={cn("text-[12px]", notice.tone === "bad" ? "text-destructive" : notice.tone === "warn" ? "text-amber-700 dark:text-amber-400" : "text-muted-foreground")} role={notice.tone === "bad" ? "alert" : undefined}>{notice.text}</p>}
      </div>
    </div>
  );
}

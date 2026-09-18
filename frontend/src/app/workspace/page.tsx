"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import Link from "next/link";
import {
  AlertTriangle,
  ChevronDown,
  CircleCheck,
  ExternalLink,
  FolderOpen,
  RefreshCw,
  Target,
} from "lucide-react";
import {
  fetchDelivery,
  fetchProjectDashboard,
  fetchProjectDecisions,
  fetchProjectDefects,
  fetchTaskRecap,
  listOpenAttention,
  listProjects,
  listSpecs,
  listTaskEvents,
  listTasks,
  TASK_PAGE_LIMIT,
  listWorkspaceSources,
  OPEN_ATTENTION_PAGE_LIMIT,
  type AttentionItem,
  type Delivery,
  type NextAction,
  type ProjectDashboard,
  type ProjectDecisions,
  type ProjectDefects,
  type RecapItem,
  type TaskRecap,
  type RequirementSpec,
  type TaskEvent,
  type TaskManifest,
  getTask,
  handoffTask,
  transitionTask,
} from "@/lib/control-plane";
import {
  asRunSummary,
  createRun,
  getRun,
  getRunDeliverables,
  listActiveProjectRuns,
  listActiveRuns,
  listExecutionAdapters,
  listRuns,
  remoteTargetLabel,
  type AdapterCapability,
  TERMINAL_RUN_STATES,
  type RunDeliverables,
  type ExecutionAdapter,
  type ExecutionRun,
  type RunState,
  type RunSummary,
} from "@/lib/execution";
import { ContinueAction } from "@/components/continue-action";
import { ProjectChat } from "@/components/project-chat";
import { projectGateOf, type ProjectGate } from "@/lib/project-chat";
import { listAttention, type AttentionItem as FullAttentionItem } from "@/lib/attention";
import { CONTINUE_ADAPTER, CONTINUE_TIMEOUT_SECONDS, mergeRunUpdates, type ContinueSeedFacts } from "@/lib/continue-run";
import { nextStepAfter, noNextStepIsCertain, reviewFixPrompt } from "@/lib/auto-advance";
import type { GateOutcome } from "@/components/inline-gate";
import { ProjectStream } from "@/components/project-stream";
import { ProjectDetail, type ProjectView } from "@/components/project-detail";
import {
  RAIL_ATTENTION_NOTICE_KEY,
  RAIL_STALE_NOTICE_KEY,
  railReadReport,
  settleRead,
  type AttentionReach,
  type RailStaleOrigin,
} from "@/lib/rail-group";
// The left column is project-level navigation now (mockup v2). Its resource counts are
// capabilities rather than numbers: three of the eight resources have no read behind them
// yet, and each has to say which kind of nothing that is.
import { ProjectNav, type ProjectNavProject } from "@/components/project-nav";
// The plan is the page's opening view (W11): the project's tasks as an ordered whole.
// Every judgment behind it — step numbers, what a step waits on, whether it may be
// continued — is in lib/task-plan.ts, which is testable without a browser (CR-18).
import { ProjectSide } from "@/components/project-side";
import { TaskCards } from "@/components/task-cards";
import { DraftPanel } from "@/components/project-chat";
import { planHeadlineOf, planRowsOf, type PlanTotalReach } from "@/lib/task-plan";
import {
  NAV_FILES_NO_READER,
  NAV_SKILLS_UNBUILT,
  NAV_UNREPORTED,
  navFromCRLog,
  navFromDecisions,
  navFromDefects,
  navReadFailed,
  type NavCapability,
} from "@/lib/nav-capability";
import { crLogViewOf } from "@/lib/cr-log";
import {
  TASK_ATTENTION_NOTICE_KEY,
  openGateCount,
  taskAttentionView,
  taskBlockerClaim,
  type TaskAttentionView,
  type TaskBlockerClaim,
} from "@/lib/task-attention";
import {
  GATE_UNKNOWN_REASON_KEY,
  GATE_VERDICT_KEY,
  gateVerdict,
  latestDeliveredOf,
  outcomeClaimNotice,
  outcomeClaimOf,
  reviewStandingNotice,
  reviewStandingOf,
  runColumnOf,
  runRole,
  ROLE_WORKER,
  REVIEW_REQUESTER,
  type DeliveredResult,
  type GateVerdict,
  type ReviewStanding,
  type RunColumn,
} from "@/lib/run-column";
import { MAX_PAGE, readPage } from "@/lib/paged";
import { taskInventoryReachOf } from "@/lib/portfolio-bucket";
import {
  eventsLackKnown,
  runsLackKnown,
} from "@/lib/timeline";
// The same decision the portfolio cockpit takes, imported rather than restated. One
// rule, one implementation: a second copy is a second thing to forget to fix, which is
// how this page came to disagree with that one.
import { taskTrustLineOf, type TaskTrustLine } from "@/lib/portfolio-bucket";
// The tasks a registered workspace directory already holds (W7), read live. All
// judgement — the board's three-answer honesty, a source's verdict, a task's
// progress and its way onward — lives in lib/workspace-sources.ts with node tests;
// the components below only render what those functions decide.
import {
  MANIFEST_REASON_KEY,
  SOURCE_REASON_KEY,
  crSummaryOf,
  resumeSectionsOf,
  shouldInterceptClick,
  sourceVerdictOf,
  taskOnwardOf,
  taskProgressOf,
  workspaceBoardOf,
  type TaskProgress,
  type WorkspaceSourcePayload,
  type WorkspaceTaskPayload,
} from "@/lib/workspace-sources";
import { useI18n, type Translate, type TranslateEnum } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { Conversation } from "@/components/conversation";
import { TopBar } from "@/components/delivery-shell";
import { DeliverablesBlock } from "@/components/run-deliverables";
import { relTime } from "@/components/timeline-entry";

// The ball is in the human's court whenever a task awaits a decision; a running
// task is the agent's; review is its own stage; terminal/verified is done. The
// grouping itself — including the `unknown` group a capped attention read forces —
// lives in lib/rail-group.ts so it can be tested without a DOM.

const RUN_TONE: Record<RunState, string> = {
  queued: "bg-amber-500",
  running: "bg-blue-500",
  succeeded: "bg-emerald-500",
  failed: "bg-red-500",
  timed_out: "bg-red-500",
  cancelled: "bg-zinc-400",
  abandoned: "bg-red-500",
};

export default function WorkspacePage() {
  const { t } = useI18n();
  const [tasks, setTasks] = useState<TaskManifest[]>([]);
  // Every open attention item, for the rail's grouping and the selected task's
  // blocker line. Display only: it is one capped, global page, so a task's items
  // can fall outside it, and "absent from this list" is not "this task has no
  // open gate". The Continue gate therefore asks its own task-scoped question
  // (see TaskWorkspace) rather than counting what happens to be in here.
  const [attention, setAttention] = useState<AttentionItem[]>([]);
  // Whether that page is a complete answer about every task in the rail, and if
  // not, why not. The rail's grouping needs the yes/no because a group other than
  // "needs you" is a claim that the task needs nothing, and past the cap the list
  // cannot support that claim for a task it does not mention (see
  // lib/rail-group.ts). The notice needs the reason: "capped" and "unreadable"
  // group the same but are different facts, and one shared message told a user
  // whose read had failed that the 500-item limit had been reached. Starts
  // "pending": no read has settled yet, which is its own state and not a failure.
  const [attentionReach, setAttentionReach] = useState<AttentionReach>("pending");
  // The other reads of this same refresh that failed. Kept apart from
  // `attentionReach` because they are claims about different reads: a rejected run
  // or task list leaves the rail showing an older snapshot, which is worth saying,
  // and is not evidence of anything about the attention read. Folding them into
  // one flag is how "Open attention items could not be read" came to be printed
  // beside an attention read that had succeeded.
  const [staleOrigins, setStaleOrigins] = useState<readonly RailStaleOrigin[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // The tasks a registered workspace directory already holds (W7), read live and shown
  // in the rail so they are usable here in the workbench rather than only on a Settings
  // card. Its own read, kept apart from the control-plane rail reads above: a workspace
  // source is a different thing from a control-plane task, its failure has nothing to do
  // with theirs, and folding the two would let one empty the other. `null` is "not read
  // yet"; `workspaceReadFailed` is "the list read failed" — never rendered as "none
  // registered" (workspaceBoardOf keeps them apart).
  const [workspaceSources, setWorkspaceSources] = useState<WorkspaceSourcePayload[] | null>(null);
  const [workspaceReadFailed, setWorkspaceReadFailed] = useState(false);

  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [projectFilter, setProjectFilter] = useState("all");
  // Every project this control plane holds, for the column's switcher. Left as it was
  // when the read fails, exactly like the other four: an empty list would read as "this
  // is the only project", which is a claim a failed read cannot make.
  const [projects, setProjects] = useState<readonly ProjectNavProject[]>([]);
  // Whether each read covered the whole list. Both start "pending": before a read has
  // settled there are no rows *and* no evidence, and calling that "complete" is what let
  // the plan say "this project has no tasks" out of nothing at all.
  const [taskReach, setTaskReach] = useState<PlanTotalReach>("pending");
  // "Does this project have work in flight" — its own read and its own reach. Named
  // for the project because `TaskWorkspace` further down holds a task-scoped one.
  const [projectActiveRuns, setProjectActiveRuns] = useState<RunSummary[]>([]);
  const [projectActiveReach, setProjectActiveReach] = useState<PlanTotalReach>("pending");
  // The kickoff draft, handed up by the conversation so the right-hand column can keep
  // drawing it while the centre is showing a step. Cleared when the project changes:
  // one project's draft beside another's plan is the carry-over the column guards
  // against everywhere else.
  const [draftState, setDraftState] = useState<Parameters<NonNullable<React.ComponentProps<typeof ProjectChat>["onDraftState"]>>[0] | null>(null);
  // The right-hand column's host element while a step is open. The step's own blocks
  // (the run, the outcome, the requirements, the goal) keep their state inside
  // `TaskWorkspace` and are portalled here — handing React nodes up through state
  // would re-run on every render, which is the loop `onDraftState` just had.
  // W12: the right-hand column is the project's (four cards), so a step opened by URL
  // draws its side blocks inline instead — there is no host to portal into any more.
  const sideHost: HTMLElement | null = null;
  // What the page did on its own after an approval (CR-70): which step it moved to and
  // what to say there. Shown on the step it names, and that step's Continue opens with
  // the seed in view, so the next action is one click — the person's rule: what can be
  // automatic is, show more, click less.
  const [advance, setAdvance] = useState<AdvanceNotice | null>(null);
  // Which of the project's views the centre shows (W12): the conversation, or one of the
  // left column's rows opened in place. Never a route.
  const [view, setView] = useState<ProjectView>("chat");
  // Where each executor runs (W3-a U5), from the adapters read: a remote adapter names
  // its host, a local one this machine. `null` until read; a failed read stays null, so
  // the columns say nothing rather than "local".
  const [adapterCaps, setAdapterCaps] = useState<AdapterCapability[] | null>(null);
  useEffect(() => {
    const ctrl = new AbortController();
    listExecutionAdapters(ctrl.signal)
      .then((items) => { if (!ctrl.signal.aborted) setAdapterCaps(items); })
      .catch(() => {});
    return () => ctrl.abort();
  }, []);
  const environments = useMemo(
    () => adapterCaps?.map((item) => ({ adapter: item.name, target: remoteTargetLabel(item.remote) })) ?? [],
    [adapterCaps],
  );
  const envOf = useMemo(() => {
    if (adapterCaps === null) return undefined;
    return (name: string) => remoteTargetLabel(adapterCaps.find((item) => item.name === name)?.remote) ?? t("workspace.nav.environmentLocal");
  }, [adapterCaps, t]);
  /** A `?task=` from the URL, held until the tasks read can name its project. */
  const [pendingTaskId, setPendingTaskId] = useState<string | null>(null);
  // The project's runs for the "runs" view — its own read, asked for only while that
  // view is open; the rail's read is the active ones across every project.
  const [detailRuns, setDetailRuns] = useState<RunSummary[] | null>(null);
  const [detailRunsFailed, setDetailRunsFailed] = useState(false);

  // The project resources the left column reports on. Their own reads, keyed to the
  // project in context — the column may not show a count it never fetched, and a failed
  // fetch has to stay distinguishable from a project that keeps none.
  const [navDashboard, setNavDashboard] = useState<ProjectDashboard | null>(null);
  const [navDecisions, setNavDecisions] = useState<ProjectDecisions | null>(null);
  const [navDefects, setNavDefects] = useState<ProjectDefects | null>(null);
  const [navReadFailures, setNavReadFailures] = useState<{ dashboard: boolean; decisions: boolean; defects: boolean }>(
    { dashboard: false, decisions: false, defects: false },
  );
  // Bumped on every explicit refresh so the selected task's own reads (recap,
  // events, dashboard) reload too. Without it a run finishing without changing
  // the task's state leaves the child's effect deps untouched, and Refresh
  // silently updates only the rail.
  const [refreshNonce, setRefreshNonce] = useState(0);

  // Token-guarded like every other loader in this file, and for the sharpest
  // version of the reason: this one *replaces* the whole rail snapshot, and one of
  // the things it writes is the completeness flag the grouping rests on. Overlapping
  // loads are routine and have nothing to do with the Refresh button —
  // onTaskMayHaveChanged={load} fires from the selected task whenever a run
  // settles or a note is posted. So: load A reads 499 items and is slow, a 500th
  // item is created, load B returns first and correctly records "capped", then A
  // lands and puts its stale 499-item answer and its "complete" back. A running
  // task whose approval A never saw returns to "Running" — the rail asserting that
  // nothing is waiting, which is exactly the claim the capped read cannot make.
  // Only the newest request may write, and that covers every piece of state here,
  // not just the attention pair: tasks, projects and runs go stale together.
  const loadRequest = useRef(0);
  const load = useCallback(async () => {
    const token = ++loadRequest.current;
    const stale = () => token !== loadRequest.current;
    setRefreshNonce((n) => n + 1);
    setLoading(true);
    setError(null);
    try {
      // One settled outcome per read (see lib/rail-group.ts). Previously this was
      // one Promise.all with one catch that set attentionReach = "unreadable", so
      // a rejected sibling — the run list, say — made the rail state that the
      // open-attention read had failed when it had succeeded. Each read now
      // reports only about itself: `settleRead` never rejects, so there is no
      // combined failure left to infer a reach from.
      const [tasksRead, projectsRead, attentionRead, runsRead] = await Promise.all([
        settleRead(() => readPage(TASK_PAGE_LIMIT, listTasks)),
        settleRead(() => listProjects()),
        settleRead(() => listOpenAttention()),
        settleRead(() => readPage(MAX_PAGE, (limit) => listRuns({ limit }))),
      ]);
      // The generation guard is unchanged and still covers every write below,
      // including the spinner in `finally`: only the newest request may write.
      if (stale()) return;
      const report = railReadReport({
        tasks: tasksRead, projects: projectsRead, attention: attentionRead, runs: runsRead,
      });
      // Each read's own verdict, written first so no later step can leave the rail
      // grouping against a reach from an earlier load: "capped"/"complete" when the
      // attention read itself settled, "unreadable" only when *it* failed, and a
      // sibling's failure disclosed as its own stale read.
      setAttentionReach(report.attentionReach);
      setStaleOrigins(report.staleOrigins);
      setError(report.errors.length > 0 ? report.errors.join(" · ") : null);
      // A failed read leaves its own slice of the snapshot as it was — stale, and
      // disclosed as stale by the rail — rather than replacing it with an empty
      // list that would read as "there are none".
      // The page *and* whether it was the whole list. `listTasks` returns a bare array,
      // so a full page was indistinguishable from "there is more" — and the plan prints a
      // step count off it. Same fix the portfolio board already carries (CR-28).
      if (tasksRead.status === "fulfilled") setTasks([...tasksRead.value.items]);
      setTaskReach(taskInventoryReachOf(tasksRead));
      if (projectsRead.status === "fulfilled") {
        // No project in context yet (nothing in the URL, nothing remembered): the
        // control plane's current project is the one. A functional update, so a
        // project the URL or storage named meanwhile is not overwritten.
        const fallback = projectsRead.value.current;
        if (fallback) setProjectFilter((held) => (held === "all" ? fallback : held));
        setProjects(projectsRead.value.items.map(
          (project) => ({
            project_id: project.project_id,
            name: project.name,
            worker_agent: project.worker_agent,
            reviewer_agent: project.reviewer_agent,
          }),
        ));
      }
      if (attentionRead.status === "fulfilled") setAttention(attentionRead.value.items);
      if (runsRead.status === "fulfilled") setRuns([...runsRead.value.items]);

    } catch (err) {
      // Not a read failure: `settleRead` gives every read its own outcome and the
      // batch above cannot reject. This is a fault in the handling itself, so it is
      // reported as an error and claims nothing about any single read — least of all
      // that the attention read failed, which is the whole point of the split.
      if (stale()) return;
      setError((err as Error).message);
    } finally {
      // A superseded load must not clear the spinner either: a newer read owns it
      // and is still in flight.
      if (!stale()) setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // The workspace-sources read, token-guarded and separate from `load`. Separate
  // because it is a separate fact: `load` reloads the control-plane rail and this
  // reads registered directories off disk; a Refresh reloads both, but a failure in
  // one must not disturb the other's state. Only the newest read may write, so a slow
  // earlier response cannot land after a faster later one and revive a stale list.
  const workspaceRequest = useRef(0);
  const loadWorkspaceSources = useCallback(async () => {
    const token = ++workspaceRequest.current;
    try {
      const listed = await listWorkspaceSources();
      if (token !== workspaceRequest.current) return;
      setWorkspaceSources(listed);
      setWorkspaceReadFailed(false);
    } catch {
      if (token !== workspaceRequest.current) return;
      // Not `setWorkspaceSources([])`: an empty array is the settled answer "none
      // registered", and a failed read has established no such thing.
      setWorkspaceReadFailed(true);
    }
  }, []);
  useEffect(() => { void loadWorkspaceSources(); }, [loadWorkspaceSources]);

  // Read the initial selection from the URL (query params, not a dynamic route —
  // the app is statically exported).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const task = params.get("task");
    const project = params.get("project");
    if (project) { setProjectFilter(project); return; }
    // A task named in the URL (an older link) opens its project's conversation once the
    // tasks are read — the task page is not a place this product sends anyone (W12).
    if (task) { setPendingTaskId(task); return; }
    // Arrived with no project named — the top bar's tab does that. The page used to
    // land on an empty centre with no right-hand column, which read as the layout
    // having broken. Come back to the project last worked in instead; `load` falls
    // back to the control plane's current project when nothing was remembered.
    try {
      const remembered = window.localStorage.getItem(LAST_PROJECT_KEY);
      if (remembered) {
        setProjectFilter(remembered);
        params.set("project", remembered);
        window.history.replaceState(null, "", `${window.location.pathname}?${params}`);
      }
    } catch { /* storage unavailable: the fallback below still names a project */ }
  }, []);


  const selectedTask = useMemo(
    () => tasks.find((task) => task.task_id === selectedTaskId) ?? null,
    [tasks, selectedTaskId],
  );

  // This page's own two snapshots, narrowed to the selected task and handed down as
  // evidence that these items exist. The child cannot see them otherwise, and its reads
  // race with these: `load` bumps `refreshNonce` before its own four reads settle, so
  // the rail could learn of a gate or a run first and render it beside a timeline that
  // claimed to be complete without it. Existence only — a global read at its cap says
  // nothing by omission, and nothing here is read that way.
  const selectedTaskAttention = useMemo(
    () => (selectedTask === null
      ? []
      : attention.filter((item) => item.task_id === selectedTask.task_id)),
    [attention, selectedTask],
  );
  const selectedTaskRuns = useMemo(
    () => (selectedTask === null
      ? []
      : runs.filter((run) => run.task_id === selectedTask.task_id)),
    [runs, selectedTask],
  );

  // The project this workbench is inside. The mockup's workbench is always within one
  // project — you arrive from a Today card or the projects page — so this is context, not
  // a filter. The selected task decides it; before a task is open, a project may still
  // have been named by the URL. Null is a real answer and the column says so rather than
  // picking alphabetically, which is how the previous default silently pointed at Agora's
  // own source tree.
  const currentProjectId = useMemo(
    () => selectedTask?.project_id ?? (projectFilter !== "all" ? projectFilter : null),
    [selectedTask, projectFilter],
  );

  // Remember the project in context, for the next arrival without one in the URL.
  useEffect(() => {
    if (currentProjectId === null) return;
    try { window.localStorage.setItem(LAST_PROJECT_KEY, currentProjectId); } catch { /* per-viewer convenience only */ }
  }, [currentProjectId]);

  // The left column's own reads. Kept apart from the selected task's dashboard read
  // (which lives in TaskWorkspace): this one is keyed to the project, survives task
  // switches within it, and its failure must not be reported as the project having none.
  useEffect(() => {
    if (currentProjectId === null) {
      setNavDashboard(null);
      setNavDecisions(null);
      setNavDefects(null);
      setNavReadFailures({ dashboard: false, decisions: false, defects: false });
      return;
    }
    const ctrl = new AbortController();
    let alive = true;
    setNavReadFailures({ dashboard: false, decisions: false, defects: false });
    void fetchProjectDashboard(currentProjectId, ctrl.signal)
      .then((payload) => { if (alive) setNavDashboard(payload); })
      .catch(() => {
        if (!alive || ctrl.signal.aborted) return;
        setNavDashboard(null);
        setNavReadFailures((prev) => ({ ...prev, dashboard: true }));
      });
    void fetchProjectDecisions(currentProjectId, ctrl.signal)
      .then((payload) => { if (alive) setNavDecisions(payload); })
      .catch(() => {
        if (!alive || ctrl.signal.aborted) return;
        setNavDecisions(null);
        setNavReadFailures((prev) => ({ ...prev, decisions: true }));
      });
    void fetchProjectDefects(currentProjectId, ctrl.signal)
      .then((payload) => { if (alive) setNavDefects(payload); })
      .catch(() => {
        if (!alive || ctrl.signal.aborted) return;
        setNavDefects(null);
        setNavReadFailures((prev) => ({ ...prev, defects: true }));
      });
    return () => { alive = false; ctrl.abort(); };
  }, [currentProjectId, refreshNonce]);

  // One project's draft must never be drawn beside another's plan.
  useEffect(() => { setDraftState(null); }, [currentProjectId]);

  // Its own read for the reason `listActiveRuns` records: whether a run is active cannot
  // be inferred from a page of recent history. Project-scoped so the plan learns it for
  // every step in one moment rather than one request per row.
  useEffect(() => {
    if (currentProjectId === null) {
      setProjectActiveRuns([]);
      setProjectActiveReach("pending");
      return;
    }
    const ctrl = new AbortController();
    let alive = true;
    void readPage(MAX_PAGE, (limit) => listActiveProjectRuns(currentProjectId, limit, ctrl.signal))
      .then((page) => {
        if (!alive) return;
        setProjectActiveRuns([...page.items]);
        setProjectActiveReach(page.truncated ? "capped" : "complete");
      })
      .catch(() => {
        if (!alive || ctrl.signal.aborted) return;
        // Deliberately not cleared to an empty list: empty would read as "nothing is
        // running", which is precisely the claim this read exists to support.
        setProjectActiveReach("unreadable");
      });
    return () => { alive = false; ctrl.abort(); };
  }, [currentProjectId, refreshNonce]);

  /**
   * This project's tasks, and only this project's — **in the order the board returned
   * them**. `listTasks` reads `priority DESC, created_at DESC`, which is the development
   * order the kickoff adoption wrote; filtering preserves it and nothing here re-sorts.
   * A second ordering judgment beside the board's own is how the placement dialog and
   * the plan would start naming different steps by the same number.
   */
  const projectTasks = useMemo(
    () => (currentProjectId === null
      ? []
      : tasks.filter((task) => task.project_id === currentProjectId && !task.archived_at)),
    [tasks, currentProjectId],
  );


  // Each resource's capability. Every one of these is a sentence about what can be said,
  // and the three with no read behind them each say which kind of nothing they are.
  const navCapabilities = useMemo(() => {
    const failedOr = (failed: boolean, cap: NavCapability): NavCapability =>
      failed ? navReadFailed() : cap;
    // Only while the reads in hand are *this* project's — the same test the gate below
    // makes, for the same reason. Switching projects leaves the previous project's
    // dashboard and decisions in state until the new requests settle, and counting them
    // under the new project's name prints one project's change requests and decisions as
    // another's. Before the switcher this window was rare; it is now a routine gesture.
    const dashboardIsMine = navDashboard !== null && navDashboard.project_id === currentProjectId;
    const decisionsIsMine = navDecisions !== null && navDecisions.project_id === currentProjectId;
    const defectsIsMine = navDefects !== null && navDefects.project_id === currentProjectId;
    const runsCap: NavCapability = currentProjectId === null
      ? NAV_UNREPORTED
      : { kind: "count", value: projectTasks.length === 0 ? 0 : runs.filter(
          (run) => projectTasks.some((task) => task.task_id === run.task_id),
        ).length, caveat: null };
    return {
      runs: runsCap,
      // The dashboard carries the CR Log; cr-log.ts already classifies it, and a missing
      // field there means this server did not report one — not that the project keeps none.
      changeRequests: failedOr(
        navReadFailures.dashboard,
        dashboardIsMine ? navFromCRLog(crLogViewOf(navDashboard.cr_log)) : NAV_UNREPORTED,
      ),
      decisions: failedOr(
        navReadFailures.decisions,
        decisionsIsMine ? navFromDecisions(navDecisions) : NAV_UNREPORTED,
      ),
      // The open count from `.agora/defects.yaml` (W9 step B). Its own read, so a failed
      // one is "could not read", never the project having none.
      defects: failedOr(
        navReadFailures.defects,
        defectsIsMine ? navFromDefects(navDefects) : NAV_UNREPORTED,
      ),
      skills: NAV_SKILLS_UNBUILT,
      files: NAV_FILES_NO_READER,
    };
  }, [currentProjectId, projectTasks, runs, navDashboard, navDecisions, navDefects, navReadFailures]);

  // Whether this project may be given work (CR-61). Three answers, not two: the
  // dashboard read is what carries the charter, and a read that did not land cannot
  // support "this project has no goal" — so it says it could not tell instead.
  const gate = useMemo(() => {
    // Only while the dashboard in hand is *this* project's. Switching projects leaves
    // the previous one in state until the new request settles, and reading the charter
    // off it would open the gate for a project whose charter has not been read — the
    // window in which a run could be dispatched into a project with no goal at all.
    const mine = navDashboard !== null && navDashboard.project_id === currentProjectId;
    return projectGateOf(
      mine ? navDashboard.charter?.goal : undefined,
      currentProjectId === null ? "pending"
        : navReadFailures.dashboard ? "failed"
          : mine ? "landed" : "pending",
    );
  }, [navDashboard, navReadFailures.dashboard, currentProjectId]);

  /**
   * The plan the page opens on. `runsAreCurrent` is decided in one place rather than per
   * render site: when a sibling read failed, `runs` is stale, and both "this step is
   * running" and "no run is active, go ahead" would be claims this refresh cannot make.
   */
  const planRows = useMemo(() => {
    // One source, and it is the read that was actually asked this question. Judging it
    // from the completeness of *all* run history was worse than wrong: that page is
    // truncated forever once the control plane has held 500 runs, so "cannot vouch"
    // became "Continue is never offered again, on any step".
    const runsAreCurrent = projectActiveReach === "complete";
    return planRowsOf({
      tasks: projectTasks,
      attention,
      attentionReach,
      // Queued counts. A run waiting to start is work already dispatched; treating only
      // "running" as active offered Continue on a task that had one queued, and reported
      // it as nothing in flight.
      runningTaskIds: new Set(projectActiveRuns.map((run) => run.task_id)),
      runsAreCurrent,
      projectGate: gate.kind,
    });
  }, [projectTasks, attention, attentionReach, projectActiveRuns, projectActiveReach, gate.kind]);

  const planHeadline = useMemo(() => planHeadlineOf(planRows, taskReach), [planRows, taskReach]);

  // Drop a selection the project filter now excludes, so the main view never
  // shows a task hidden from the rail (notes would post to the wrong project).
  useEffect(() => {
    if (projectFilter !== "all" && selectedTask && selectedTask.project_id !== projectFilter) {
      setSelectedTaskId(null);
    }
  }, [projectFilter, selectedTask]);

  const selectTask = useCallback((task: TaskManifest) => {
    setSelectedTaskId(task.task_id);
    // A move clears what the last automatic move said; the automatic path sets its
    // own notice right after calling this, in the same batch.
    setAdvance(null);
    const params = new URLSearchParams(window.location.search);
    params.set("project", task.project_id);
    params.set("task", task.task_id);
    window.history.replaceState(null, "", `${window.location.pathname}?${params}`);
  }, []);

  // Opening one of the project's views (W12). It closes a step opened by URL — the
  // view takes the centre — and keeps the view asked for; `openProject` would reset it
  // to the conversation, which is why this does not go through it.
  const showView = useCallback((next: string) => {
    setSelectedTaskId(null);
    setAdvance(null);
    if (selectedTaskId !== null) {
      const params = new URLSearchParams(window.location.search);
      params.delete("task");
      window.history.replaceState(null, "", `${window.location.pathname}?${params}`);
    }
    setView(next as ProjectView);
  }, [selectedTaskId]);

  // The "runs" view's own read (W12). Asked only while that view is open for a project;
  // a failed read is said as such, and a change of project drops the previous answer.
  // One more than the page shows, so "there is more" is measured rather than assumed.
  const [detailRunsTruncated, setDetailRunsTruncated] = useState(false);
  useEffect(() => {
    if (view !== "runs" || currentProjectId === null) return;
    const ctrl = new AbortController();
    setDetailRuns(null);
    setDetailRunsFailed(false);
    listRuns({ project_id: currentProjectId, limit: DETAIL_RUNS_LIMIT + 1 }, ctrl.signal)
      .then((items) => {
        if (ctrl.signal.aborted) return;
        setDetailRunsTruncated(items.length > DETAIL_RUNS_LIMIT);
        setDetailRuns(items.slice(0, DETAIL_RUNS_LIMIT));
      })
      .catch(() => { if (!ctrl.signal.aborted) setDetailRunsFailed(true); });
    return () => ctrl.abort();
  }, [view, currentProjectId]);

  // After a step is handed off on approval (CR-70): say so, and move to the next planned
  // step with its Continue open. "No next step" is only claimed over a whole read.
  const onStepDone = useCallback((done: { taskId: string; commit: string | null; committed: boolean }) => {
    const row = planRows.find((candidate) => candidate.task_id === done.taskId);
    const completed = {
      key: done.committed && done.commit
        ? "workspace.continue.advance.completed"
        : "workspace.continue.advance.completedNothingToCommit",
      vars: { step: row?.step ?? "?", title: row?.title ?? done.taskId, commit: done.commit?.slice(0, 7) ?? "" },
    };
    const next = nextStepAfter(planRows, done.taskId);
    if (next === null) {
      setAdvance({
        showOn: done.taskId,
        lines: [completed, { key: noNextStepIsCertain(planHeadline.totalKnown, next)
          ? "workspace.continue.advance.noNext" : "workspace.continue.advance.noNextUncertain" }],
        continueOpen: false,
      });
      return;
    }
    const target = tasks.find((candidate) => candidate.task_id === next.task_id);
    if (target) selectTask(target); else setSelectedTaskId(next.task_id);
    setAdvance({ showOn: next.task_id, lines: [completed, { key: "workspace.continue.advance.nextHere" }], continueOpen: true });
    load();
  }, [planRows, planHeadline.totalKnown, tasks, selectTask, load]);

  // The way onward from a workspace-task row. Its target is this very page
  // (`/workspace?project=X`), and a `next/link` to the page you are already on is a
  // client-side navigation that does not remount — so the mount-only effect that reads
  // `?project` never re-runs and the filter never moves: the link looked live and did
  // nothing. So a plain primary click (see `shouldInterceptClick`) sets the filter
  // itself, exactly as `selectTask` does, and `replaceState` keeps the URL honest so a
  // real reload still lands on the same view. The row keeps the `href` as an accessible,
  // real-navigation fallback; this handler is what actually changes the state on click.
  const openProject = useCallback((projectId: string) => {
    setProjectFilter(projectId);
    setAdvance(null);
    setView("chat");
    // Opening a project must not leave a task from a different project named in the URL:
    // the click claims the project-only `href`, so the URL it produces must match it.
    // Clear the selection and drop the `task` param, or a copied/reloaded URL would
    // mount a task that does not belong to the project it names.
    setSelectedTaskId(null);
    const params = new URLSearchParams(window.location.search);
    params.set("project", projectId);
    params.delete("task");
    window.history.replaceState(null, "", `${window.location.pathname}?${params}`);
  }, []);

  // A task named in the URL resolves to its project once the tasks are read. Unknown
  // id: the param is dropped and the fallbacks above decide the project.
  useEffect(() => {
    if (pendingTaskId === null || tasks.length === 0) return;
    const task = tasks.find((candidate) => candidate.task_id === pendingTaskId);
    setPendingTaskId(null);
    if (task) { openProject(task.project_id); return; }
    const params = new URLSearchParams(window.location.search);
    params.delete("task");
    window.history.replaceState(null, "", `${window.location.pathname}${params.size ? `?${params}` : ""}`);
  }, [pendingTaskId, tasks, openProject]);

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-muted/20">
      <TopBar active="Workbench" />
      <div className="flex min-h-0 flex-1 overflow-hidden">
      <ProjectNav
        projectId={currentProjectId}
        // Only this project's name. The dashboard in state is the previous project's
        // until the new read lands, and printing its name over the new project's page is
        // how someone settles a goal for B under A's heading. Guarding the counts alone
        // (the round before) left exactly this one uncovered.
        projectName={
          navDashboard !== null && navDashboard.project_id === currentProjectId
            ? navDashboard.name
            : currentProjectId
        }
        // The dashboard does not report the project's root, so this stays null rather
        // than guessing one from the id.
        projectRoot={null}
        capabilities={navCapabilities}
        projects={projects}
        // Entering a project drops the task selection, so the page opens on that
        // project's plan rather than on a task belonging to the one just left.
        onSwitchProject={openProject}
        gate={gate}
        // The goal is settled in the middle column, so "settle it" is closing the task
        // that is covering it — and pinning the project first, because the project may
        // only have been in context through that task (`projectFilter` is still "all"
        // when the workbench was entered by task), and dropping the selection alone
        // would leave the column with no project and show the empty state instead.
        onSettleGoal={() => {
          if (currentProjectId !== null) openProject(currentProjectId);
          setSelectedTaskId(null);
        }}
        header={
          <RailHeader onRefresh={() => { void load(); void loadWorkspaceSources(); }} loading={loading} />
        }
        view={view}
        onSelectView={showView}
        environments={environments}
        otherWorkspaces={
          <WorkspaceTasksPanel
            sources={workspaceSources}
            readFailed={workspaceReadFailed}
            onOpenProject={openProject}
          />
        }
      />
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {/* The reach notices for the reads behind the task list. They used to sit in the
            left column; now that the column is project-scoped they belong beside the work
            rather than above a project's resources — a limit of a global attention read is
            not a property of this project. The reads are unchanged, and each notice still
            names which outcome occurred: a failed read is not a reached cap. */}
        {attentionReach !== "complete" && projectTasks.length > 0 && (
          <p className="mx-4 mt-4 shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[11px] leading-snug text-amber-700 dark:text-amber-400">
            {t(RAIL_ATTENTION_NOTICE_KEY[attentionReach], { limit: OPEN_ATTENTION_PAGE_LIMIT })}
          </p>
        )}
        {staleOrigins.map((origin) => (
          <p key={origin} className="mx-4 mt-2 shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[11px] leading-snug text-amber-700 dark:text-amber-400">
            {t(RAIL_STALE_NOTICE_KEY[origin])}
          </p>
        ))}
        {error && <div className="m-4 shrink-0 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{t("workspace.errors.loadFailed", { error })}</div>}
        {/* One window per project. The centre is the project's conversation, and a step
            opens *in it* — never on another page. The crumb is the way back, exactly as
            the design draws it: `← 项目对话`. */}
        {selectedTask ? (
          <>
            <div className="flex shrink-0 items-center gap-2 border-b px-4 py-3 text-[13px] text-muted-foreground">
              <button
                type="button"
                onClick={() => { if (currentProjectId !== null) openProject(currentProjectId); }}
                className="rounded-lg border px-2 py-1 text-[12px] hover:bg-accent"
              >
                ← {t("workspace.crumb.backToChat")}
              </button>
              <span>/</span>
              <b className="min-w-0 truncate font-medium text-foreground">{selectedTask.title}</b>
            </div>
            {/* The whole order, not just the step you clicked — the design draws the list
                here so a step never becomes a place you have to find your way out of. */}
            <TaskCards
              rows={planRows}
              selectedTaskId={selectedTaskId}
              onSelectStep={(taskId) => {
                const task = tasks.find((candidate) => candidate.task_id === taskId);
                if (task) selectTask(task);
              }}
            />
            <TaskWorkspace
              key={selectedTask.task_id}
              refreshNonce={refreshNonce}
              sideHost={sideHost}
              advance={advance}
              onStepDone={onStepDone}
              onAdvanceConsumed={() => setAdvance(null)}
              workerAdapter={projects.find((project) => project.project_id === selectedTask.project_id)?.worker_agent ?? null}
              task={selectedTask}
              projectGate={gate.kind}
              railAttention={selectedTaskAttention}
              railRuns={selectedTaskRuns}
              onTaskMayHaveChanged={load}
            />
          </>
        ) : (
          currentProjectId !== null ? (
            gate.kind !== "open" ? (
              /* The goal is still being settled: the kickoff conversation, as before. */
              <ProjectChat
                key={currentProjectId}
              projectId={currentProjectId}
              onAdopted={load}
              onDraftState={setDraftState}
              onOpenTask={(taskId) => {
                const task = tasks.find((candidate) => candidate.task_id === taskId);
                if (task) { selectTask(task); return; }
                setSelectedTaskId(taskId);
                const params = new URLSearchParams(window.location.search);
                params.set("project", currentProjectId);
                params.set("task", taskId);
                window.history.replaceState(null, "", `${window.location.pathname}?${params}`);
                load();
              }}
            />
            ) : view === "chat" ? (
              /* One window per project (W12): every run and gate of every step, one box. */
              <ProjectStream
                key={currentProjectId}
                projectId={currentProjectId}
                projectName={navDashboard !== null && navDashboard.project_id === currentProjectId ? navDashboard.name : currentProjectId}
                tasks={projectTasks}
                rows={planRows}
                rowsWhole={planHeadline.totalKnown}
                workerAdapter={projects.find((project) => project.project_id === currentProjectId)?.worker_agent ?? null}
                onChanged={() => { void load(); }}
              />
            ) : (
              <ProjectDetail
                view={view}
                dashboard={navDashboard !== null && navDashboard.project_id === currentProjectId ? navDashboard : null}
                dashboardFailed={navReadFailures.dashboard}
                decisions={navDecisions !== null && navDecisions.project_id === currentProjectId ? navDecisions : null}
                decisionsFailed={navReadFailures.decisions}
                defects={navDefects !== null && navDefects.project_id === currentProjectId ? navDefects : null}
                defectsFailed={navReadFailures.defects}
                tasks={projectTasks}
                rows={planRows}
                headline={planHeadline}
                runs={detailRuns}
                runsFailed={detailRunsFailed}
                runsTruncated={detailRunsTruncated}
                onBack={() => setView("chat")}
              />
            )
          ) : (
            <EmptyWorkspace hasTasks={tasks.length > 0} />
          )
        )}
      </main>

      {currentProjectId !== null && (
        <ProjectSide
          mode={gate.kind === "open" ? "adopted" : "settling"}
          rows={planRows}
          headline={planHeadline}
          goal={
            navReadFailures.dashboard ? { kind: "unreadable" }
              : navDashboard === null || navDashboard.project_id !== currentProjectId ? { kind: "loading" }
              : navDashboard.charter.goal.trim() === "" ? { kind: "none" }
              : { kind: "text", text: navDashboard.charter.goal }
          }
          // The project's own active-run read, and only when it is whole: a pending or
          // capped read cannot say "nothing running". Same for the gates — zero is a
          // claim only a complete attention read may make.
          activeRuns={projectActiveReach === "complete" ? projectActiveRuns : null}
          openGates={attentionReach === "complete" ? attention.filter((item) => item.project_id === currentProjectId && item.state === "open").length : null}
          onSelectView={showView}
          envOf={envOf}
          draft={(order) => (draftState !== null
            ? <DraftPanel step={draftState.step} record={draftState.record} freshKey={draftState.freshKey} t={t} middle={order} />
            : order)}
        />
      )}
      </div>
    </div>
  );
}

/**
 * The column's top strip: brand, refresh, and the global toggles.
 *
 * Passed into ProjectNav rather than living inside it, because none of it is about the
 * project — the mockup puts these in a top bar, and that move is a later cut of W9. Kept
 * as a unit here so that cut is a relocation rather than a rewrite.
 */
// The rail's own header keeps only what is about the rail — the refresh. The brand,
// language, theme and Settings moved up into the top bar the workbench now shares
// with every other screen (W9's shell cut); drawing them twice on one screen was noise.
function RailHeader({ onRefresh, loading }: { onRefresh: () => void; loading: boolean }) {
  const { t } = useI18n();
  return (
    <div className="flex items-center justify-end px-4 py-2">
      <button className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground" onClick={onRefresh} disabled={loading} aria-label={t("common.action.refresh")} title={t("common.action.refresh")}><RefreshCw className={cn("size-4", loading && "animate-spin")} /></button>
    </div>
  );
}

function WorkspaceTasksPanel({ sources, readFailed, onOpenProject }: { sources: WorkspaceSourcePayload[] | null; readFailed: boolean; onOpenProject: (projectId: string) => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(true);
  const board = workspaceBoardOf({ sources, readFailed });
  return (
    <section className="mt-4 border-t pt-3">
      <button
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="flex w-full items-center gap-2 px-2 py-1.5 text-left"
      >
        <ChevronDown className={cn("size-3 shrink-0 text-muted-foreground transition-transform", !open && "-rotate-90")} />
        <FolderOpen className="size-3.5 shrink-0 text-muted-foreground" />
        <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{t("workspace.workspaceTasks.title")}</span>
        {board.show === "sources" && <span className="text-[11px] text-muted-foreground">{board.count}</span>}
      </button>
      {open && (
        <div className="mt-1 px-2">
          {board.show === "loading" ? (
            <p className="px-1 py-2 text-[11px] text-muted-foreground">{t("workspace.workspaceTasks.loading")}</p>
          ) : board.show === "unreadable" ? (
            // A failed list read. Never "none registered": a person with sources
            // registered must not be told they have none because one request lost.
            <p className="px-1 py-2 text-[11px] text-amber-600 dark:text-amber-400">{t("workspace.workspaceTasks.unreadable")}</p>
          ) : board.show === "none" ? (
            <div className="px-1 py-2">
              <p className="text-[11px] text-muted-foreground">{t("workspace.workspaceTasks.none")}</p>
              <Link href="/settings" className="mt-1 inline-block text-[11px] font-medium text-primary hover:underline">{t("workspace.workspaceTasks.manage")}</Link>
            </div>
          ) : (
            <div className="space-y-3">
              {(sources ?? []).map((source) => <WorkspaceSourceBlock key={source.root} source={source} onOpenProject={onOpenProject} />)}
              <Link href="/settings" className="inline-block px-1 text-[11px] font-medium text-primary hover:underline">{t("workspace.workspaceTasks.manage")}</Link>
            </div>
          )}
        </div>
      )}
    </section>
  );
}

// One registered source: its root, then either the tasks it holds or the one sentence
// that says why it holds none. Every branch of sourceVerdictOf says something
// different; none of them renders "0" for a directory nobody could read.
function WorkspaceSourceBlock({ source, onOpenProject }: { source: WorkspaceSourcePayload; onOpenProject: (projectId: string) => void }) {
  const { t } = useI18n();
  const verdict = sourceVerdictOf(source);
  return (
    <div className="rounded-lg border bg-background/40 p-2">
      <p className="break-all font-mono text-[10px] text-muted-foreground">{source.root}</p>
      {verdict.show === "unavailable" ? (
        <p className="mt-1 text-[11px] text-amber-600 dark:text-amber-400">{t(SOURCE_REASON_KEY[verdict.reason])}</p>
      ) : verdict.show === "empty" ? (
        <p className="mt-1 text-[11px] text-muted-foreground">
          {t("workspace.workspaceTasks.emptyTasksDir")}
          {verdict.skipped.length > 0 ? ` ${t("workspace.workspaceTasks.skipped", { names: verdict.skipped.join(", ") })}` : ""}
        </p>
      ) : (
        <>
          <p className="mt-1 text-[11px] text-muted-foreground">
            {verdict.show === "partial"
              ? t("workspace.workspaceTasks.taskCountAtLeast", { count: String(verdict.count) })
              : t("workspace.workspaceTasks.taskCount", { count: String(verdict.count) })}
            {verdict.skipped.length > 0 ? ` · ${t("workspace.workspaceTasks.skipped", { names: verdict.skipped.join(", ") })}` : ""}
          </p>
          {/* An entry the server listed but could not stat: named, because the count
              above is a floor and the alternative is a task missing from the only place
              it shows. */}
          {verdict.show === "partial" && (
            <p className="mt-1 text-[11px] text-amber-600 dark:text-amber-400">
              {t("workspace.workspaceTasks.unreadableEntries", { names: verdict.unreadable.join(", ") })}
            </p>
          )}
          <ul className="mt-2 space-y-1.5">
            {(source.tasks ?? []).map((task) => <WorkspaceTaskRow key={task.name} task={task} onOpenProject={onOpenProject} />)}
          </ul>
        </>
      )}
    </div>
  );
}

// One workspace task, compact: its name and goal, a progress bar from the RESUME
// 「进度快照」 (✅ only in the percentage), its CR/ADR standing, and where it leads.
function WorkspaceTaskRow({ task, onOpenProject }: { task: WorkspaceTaskPayload; onOpenProject: (projectId: string) => void }) {
  const { t } = useI18n();
  const [open, setOpen] = useState(false);
  const onward = taskOnwardOf(task.repo_link);
  const progress = taskProgressOf(task.resume);
  const { currentState, nextStep } = resumeSectionsOf(task.resume);
  return (
    <li className="rounded-md bg-muted/40 px-2 py-1.5">
      <div className="flex items-center gap-1.5">
        <button onClick={() => setOpen((v) => !v)} className="flex min-w-0 flex-1 items-center gap-1.5 text-left" aria-expanded={open}>
          <ChevronDown className={cn("size-3 shrink-0 text-muted-foreground transition-transform", !open && "-rotate-90")} />
          <span className="min-w-0 truncate text-xs font-medium">{task.name}</span>
        </button>
        {onward.show === "open" && (
          // The target is this very page (`onward.href` is `/workspace?project=…`), so a
          // plain link click would navigate client-side without remounting and the
          // filter would never move — the link would look live and do nothing. So a
          // plain primary click is intercepted: `href` stays for real navigation and the
          // click sets the filter directly. But a modified click (Ctrl/Cmd new tab, Shift
          // new window, Alt, or any non-primary button) is the browser's own intent that
          // the in-page handler cannot honour, so `shouldInterceptClick` lets those fall
          // through to the default against the real `href`.
          <Link
            href={onward.href}
            onClick={(e) => {
              if (!shouldInterceptClick(e)) return;
              e.preventDefault();
              onOpenProject(onward.projectId);
            }}
            className="inline-flex shrink-0 items-center gap-0.5 text-[11px] font-medium text-primary hover:underline"
          >
            {t("workspace.workspaceTasks.openProject", { project: onward.projectId })}
            <ExternalLink className="size-2.5" />
          </Link>
        )}
      </div>
      {/* Goal, or which of its files could not be read — not "no goal". */}
      <p className="mt-0.5 truncate pl-4 text-[11px] text-muted-foreground">
        {task.manifest.available
          ? (task.manifest.goal ?? t("workspace.workspaceTasks.goalEmpty"))
          : <span className="text-amber-600 dark:text-amber-400">{t(MANIFEST_REASON_KEY[task.manifest.reason ?? "unreadable"])}</span>}
      </p>
      <div className="mt-1 pl-4">
        <WorkspaceTaskProgress progress={progress} />
      </div>
      {/* CR/ADR standing and the way onward, moved here from the deleted Settings card:
          an OPEN or UNRECOGNISED CR, an unreadable ADR directory, and every state of
          `taskOnwardOf` that is not the header link above are honest-failure facts, and
          dropping them was the slice's number-one recorded sin. */}
      <WorkspaceTaskStanding task={task} onward={onward} />
      {open && (
        <div className="mt-1.5 space-y-1 pl-4">
          {([["workspace.workspaceTasks.currentState", currentState], ["workspace.workspaceTasks.nextStep", nextStep]] as const).map(([label, section]) => (
            <p key={label} className="text-[11px]">
              <span className="text-muted-foreground">{t(label)}: </span>
              {section.show === "text" ? (
                <span className="whitespace-pre-wrap">{section.text}</span>
              ) : section.show === "missing" ? (
                <span className="text-muted-foreground">{t("workspace.workspaceTasks.sectionMissing")}</span>
              ) : (
                <span className="text-amber-600 dark:text-amber-400">{t("workspace.workspaceTasks.resumeUnreadable")}</span>
              )}
            </p>
          ))}
        </div>
      )}
    </li>
  );
}

// The task's CR log summary, its ADR count, and the way onward — the per-task detail
// the W8 move dropped. Every branch here is a state the payload carries and the deleted
// Settings renderer used to show; none of them may be silently swallowed:
//
//  * CR: `crSummaryOf` (reused, not re-summarised) keeps an open count from ever
//    travelling without the rows whose status could not be read, and renders "could not
//    read the CR log" as its own amber sentence rather than as blank — the very
//    substitution the workspace readers exist to refuse.
//  * ADR: `records: null` is "could not read adr/" (amber, unless the ordinary `absent`,
//    which says nothing); a number is the count.
//  * onward: `open` is the header link; the other five states each name a different next
//    move (register a directory, write/fix `primary_repo`, retry the project list, or —
//    for `unknown` — claim nothing at all).
function WorkspaceTaskStanding({
  task, onward,
}: { task: WorkspaceTaskPayload; onward: ReturnType<typeof taskOnwardOf> }) {
  const { t } = useI18n();
  const cr = crSummaryOf(task.cr_log);
  const adr = task.decisions.records?.length ?? null;
  const adrUnavailable = adr === null && task.decisions.reason !== "absent";
  // `unknown` claims nothing and the header carries `open`, so those two render no line;
  // everything else here is a fact owed to the reader.
  const hasOnwardLine = onward.show !== "open" && onward.show !== "unknown";
  if (cr.show !== "counts" && cr.show !== "unavailable" && adr === null && !adrUnavailable && !hasOnwardLine) {
    return null;
  }
  return (
    <div className="mt-0.5 flex flex-wrap items-baseline gap-x-2 gap-y-0.5 pl-4 text-[11px]">
      {cr.show === "counts" ? (
        <span className="text-muted-foreground">
          {t("workspace.workspaceTasks.crOpen", { open: String(cr.open) })}
          {/* Never shown without this: an open count says nothing about rows whose
              status could not be read, and on the first real log there were nine. */}
          {cr.unrecognised > 0
            ? ` · ${t("workspace.workspaceTasks.crUnrecognised", { n: String(cr.unrecognised) })}`
            : ""}
        </span>
      ) : (
        // Not blank: "the CR log could not be read" must not look like "this task has
        // no CRs" — the collapse the whole panel refuses.
        <span className="text-amber-600 dark:text-amber-400">{t("workspace.workspaceTasks.crUnavailable")}</span>
      )}
      {adrUnavailable ? (
        <span className="text-amber-600 dark:text-amber-400">{t("workspace.workspaceTasks.adrUnavailable")}</span>
      ) : adr !== null && adr > 0 ? (
        <span className="text-muted-foreground">{t("workspace.workspaceTasks.adrCount", { n: String(adr) })}</span>
      ) : null}
      {onward.show === "notRegistered" ? (
        <span className="text-muted-foreground">{t("workspace.workspaceTasks.repoNotRegistered", { repo: onward.repo })}</span>
      ) : onward.show === "noRepoRecorded" ? (
        <span className="text-muted-foreground">{t("workspace.workspaceTasks.noRepoRecorded")}</span>
      ) : onward.show === "repoMalformed" ? (
        <span className="text-amber-600 dark:text-amber-400">{t("workspace.workspaceTasks.repoMalformed")}</span>
      ) : onward.show === "projectsUnreadable" ? (
        // About the project list, not this task: nothing here failed, but a person whose
        // links just vanished is owed the sentence rather than a blank.
        <span className="text-amber-600 dark:text-amber-400">{t("workspace.workspaceTasks.projectsUnreadable")}</span>
      ) : null}
    </div>
  );
}

// The workspace task's progress bar. Four answers, three of them wordless-but-not-a-bar:
// a percentage exists only on the `counts` branch, so a read that failed, a task with no
// snapshot, and an empty snapshot can none of them render a number by accident.
function WorkspaceTaskProgress({ progress }: { progress: TaskProgress }) {
  const { t } = useI18n();
  // The one answer with no snapshot counterpart: the RESUME file could not be read.
  // Its own amber sentence — not "no snapshot", which would state a fact about the
  // snapshot from a fact about the file.
  if (progress.show === "resumeUnreadable") {
    return (
      <span className="text-[11px] text-amber-600 dark:text-amber-400">
        {t("workspace.workspaceTasks.progress.unreadable")}
      </span>
    );
  }
  // Everything else is the snapshot decision, made once in `progressBarOf`. This
  // renders the `ProgressBar` it returned and decides nothing: the same value type,
  // and the same rules, the portfolio cockpit's bar renders.
  const bar = progress.bar;
  if (bar.kind === "counts" || bar.kind === "countsDoneOnly") {
    // The count wording differs by convention: a marker snapshot has a real in-progress
    // figure; a checkbox snapshot cannot express one (`countsDoneOnly` carries no
    // in-progress field at all), so it says done / not-done and draws no in-flight
    // segment — a 0 there would claim nothing is in flight, which the file never said.
    const counts = bar.kind === "countsDoneOnly"
      ? t("workspace.workspaceTasks.progress.countsCheckbox", {
          done: bar.done, notDone: bar.notDone,
        })
      : t("workspace.workspaceTasks.progress.counts", {
          done: bar.done, inProgress: bar.inProgress, todo: bar.todo,
        });
    return (
      <span className="inline-flex items-center gap-2" title={counts}>
        <span className="text-[11px] font-semibold tabular-nums">{bar.donePct}%</span>
        <span className="inline-flex h-1.5 w-24 overflow-hidden rounded-full bg-muted align-middle" role="img" aria-label={counts}>
          {/* Solid: done. Translucent: in progress — its own segment, never folded
              into the percentage above (🔵 is not half credit). Absent entirely on a
              checkbox snapshot (`countsDoneOnly`), whose in-flight count is unknown,
              not zero, and which carries no `inProgressPct` field to draw from. */}
          <span className="h-full bg-emerald-500" style={{ width: `${bar.donePct}%` }} />
          {bar.kind === "counts" && bar.inProgressPct > 0 && (
            <span className="h-full bg-emerald-500/40" style={{ width: `${bar.inProgressPct}%` }} />
          )}
        </span>
      </span>
    );
  }
  // `loading` cannot occur here — the task path always maps a snapshot that is in hand
  // and read (`dashboardLoaded: true`) — but the shared `ProgressBar` type carries it,
  // so it is handled rather than asserted away: a dash, never a false "no snapshot".
  if (bar.kind === "loading") {
    return <span className="text-[11px] text-muted-foreground">—</span>;
  }
  // No number, and the sentence differs by which non-answer it is — decided by
  // `progressBarOf`, worded here. An empty snapshot (tracks nothing yet), a task that
  // records no 进度快照 section (`empty`/`noSnapshot`), and a section read but not
  // countable (`unknown` with a `because`) are each a different fact; the amber ones
  // are read failures, the muted ones are settled facts about the task.
  const isFailure = bar.kind === "unknown";
  return (
    <span className={cn("text-[11px]", isFailure ? "text-amber-600 dark:text-amber-400" : "text-muted-foreground")}>
      {t(WORKSPACE_PROGRESS_KEY[bar.because])}
    </span>
  );
}

/**
 * The task-worded sentence for each wordless `ProgressBar` outcome. `progressBarOf`
 * decides *which* outcome; this only chooses the copy, so a workspace RESUME reads in
 * task terms ("no progress snapshot recorded") while the same decision drives the
 * project-worded cockpit bar. A `because` with no specific key falls back to the
 * general "read but uncountable" line rather than to silence.
 */
const WORKSPACE_PROGRESS_KEY: Record<string, string> = {
  // A snapshot that exists and lists nothing yet, and a RESUME that records no 进度快照
  // section at all — the two `empty` reasons `progressBarOf` distinguishes.
  noItems: "workspace.workspaceTasks.progress.empty",
  noSnapshot: "workspace.workspaceTasks.progress.none",
  // Read fine, cannot be counted. Each names itself; the rest are read failures the
  // task panel shows as its general uncountable line.
  mixedConventions: "workspace.workspaceTasks.progress.mixedConventions",
  markersNotCounted: "workspace.workspaceTasks.progress.uncountedMarkers",
  countsUnusable: "workspace.workspaceTasks.progress.uncountable",
  snapshotUnread: "workspace.workspaceTasks.progress.uncountable",
  // A verdict for a task never carries these — they are dashboard-load facts, and the
  // task path always maps a snapshot that is in hand and read. Mapped to the general
  // uncountable line rather than left to crash if a payload ever produces one.
  dashboardUnread: "workspace.workspaceTasks.progress.uncountable",
  notReported: "workspace.workspaceTasks.progress.uncountable",
  conventionNotReported: "workspace.workspaceTasks.progress.uncountable",
};

function EmptyWorkspace({ hasTasks }: { hasTasks: boolean }) {
  const { t } = useI18n();
  return (
    <div className="grid min-h-0 flex-1 place-items-center p-8 text-center">
      <div className="max-w-sm space-y-2">
        <Target className="mx-auto size-8 text-muted-foreground" />
        <p className="text-sm text-muted-foreground">{hasTasks ? t("workspace.header.selectPrompt") : t("workspace.header.noTasksPrompt")}</p>
      </div>
    </div>
  );
}

// How many of this task's runs the history read asks for. The right column is
// derived from this page, so the cap matters: a task with more runs than this
// leaves the read truncated, and a truncated page cannot say which run is the
// latest or that no review exists (see lib/run-column.ts).
const RUN_HISTORY_LIMIT = 100;
// Retry window for a rollover boundary that is already in the past — see the
// rollover effect below. Wide enough that an offline page costs one request a
// minute at worst, short enough that a page recovers the day within one.
const ROLLOVER_RETRY_MIN_MS = 30_000;
const ROLLOVER_RETRY_MAX_MS = 300_000;

// How often an unsettled run is re-read. A dispatched run starts queued with no
// output, so without this the timeline would show an empty card until somebody
// pressed Refresh.
const RUN_POLL_MS = 2_500;

// How long to keep watching after the last run of a task settles.
//
// The dispatcher publishes a worker run's terminal state and only *then* builds
// and records the cross-review run, so at the instant polling would naturally
// stop there is nothing yet to see. Without a settle window the review run — and
// the approval it raises — appear only on a manual Refresh.
//
// Bounded, and expiring with nothing new is a normal outcome, not an error:
// auto_review can be off, the reviewer runtime can be unconfigured, and a run
// that failed is not reviewed at all. Wide enough to cover the review's own
// prompt build (it shells out to git over the worktree), then it stops.
const FOLLOW_UP_WINDOW_MS = 30_000;

// How many open attention items to ask for when gating on this task's human
// gates. A task will never have this many, which is the point: if the answer
// comes back full, it may have been cut off, and a cut-off page cannot say "no
// gate is open".
const TASK_ATTENTION_LIMIT = MAX_PAGE;
/** Per-viewer convenience: the project the workbench last showed (localStorage). */
const LAST_PROJECT_KEY = "agora.workspace.project";
/** The "runs" view's page (W12). One more is fetched so a full page is known to be cut. */
const DETAIL_RUNS_LIMIT = 200;

// How many of ONE run's attention items to ask for when checking whether the review
// path has raised its gate for it. A run will never have this many, which is the
// point: a full page may have been cut off, and a cut-off page cannot say the gate
// item is absent (see `ReviewGateFacts` in lib/run-column.ts).
const REVIEW_GATE_LIMIT = 100;

// How many of the handoff's delivered paths the gate block lists before it says how
// many more there are. Enough to recognise the delivery, short enough not to push
// the gate itself off the column.
const HANDOFF_FILES_SHOWN = 12;

// The parent's global attention list is deliberately NOT passed in, in any form.
// It is one capped page across every project, so it is not authoritative for one
// task, and substituting it when this component's own task-scoped read had no
// answer yet is exactly how the Attention panel came to say "nothing needs you"
// while the Continue gate on the same screen was blocking on the same fact as
// unknown. The same substitution came back once as an `ask` prop — the first
// global blocker's title, rendered by OutcomeSummary as *this task's* blocker,
// which disagreed with the panel in both directions: a resolved blocker stayed on
// screen until the next global refresh, and a blocker beyond the global page never
// appeared at all. This component asks its own question (loadTaskAttention) and
// every task-level attention claim on this screen — panel, Continue gate, and
// blocker line — derives from that one answer (lib/task-attention.ts).
// The parent's `latestRun` summary is deliberately NOT a prop any more. It was the
// right column's "Run" tab, and that tab is gone: the column is now derived from
// this component's own task-scoped reads (lib/run-column.ts), which know the
// difference between "no run is unsettled" and "whether one is could not be read".
// The parent's summary comes from a capped, global run page and cannot make that
// distinction — it would be the rail's projection standing in for the task's own
// answer, which is the substitution this file has had to remove twice already.
/** See `advance` in the page: what was done on its own, and where to say it. */
interface AdvanceNotice {
  showOn: string;
  lines: { key: string; vars?: Record<string, string | number> }[];
  continueOpen: boolean;
}

/**
 * Whether the page-level right-hand column is on screen (`lg:flex`, Tailwind's 1024px).
 * Below that the column is `display:none`, and a portal into it would render the step's
 * blocks into nothing — so they are drawn inline instead. Same breakpoint as the CSS;
 * measured, not assumed, and re-read on resize.
 */
function useWideLayout(): boolean {
  const [wide, setWide] = useState(() => typeof window !== "undefined" && window.matchMedia("(min-width: 1024px)").matches);
  useEffect(() => {
    const query = window.matchMedia("(min-width: 1024px)");
    const onChange = () => setWide(query.matches);
    onChange();
    query.addEventListener("change", onChange);
    return () => query.removeEventListener("change", onChange);
  }, []);
  return wide;
}

function TaskWorkspace({ task, projectGate, refreshNonce, sideHost, advance, onStepDone, onAdvanceConsumed, workerAdapter, railAttention, railRuns, onTaskMayHaveChanged }: { task: TaskManifest; projectGate: ProjectGate["kind"]; refreshNonce: number; sideHost: HTMLElement | null; advance: AdvanceNotice | null; onStepDone: (done: { taskId: string; commit: string | null; committed: boolean }) => void; onAdvanceConsumed: () => void; workerAdapter: string | null; railAttention: readonly AttentionItem[]; railRuns: readonly RunSummary[]; onTaskMayHaveChanged: () => void }) {
  const wide = useWideLayout();
  // The project's worker, or the fallback when the projects read did not name one.
  const worker = (workerAdapter as ExecutionAdapter | null) ?? CONTINUE_ADAPTER;
  const { t, te } = useI18n();
  const [dashboard, setDashboard] = useState<ProjectDashboard | null>(null);
  const [dashError, setDashError] = useState(false);
  const dashTask = dashboard?.tasks.find((it) => it.task_id === task.task_id) ?? null;
  const [specs, setSpecs] = useState<RequirementSpec[] | null>(null);
  const [specsError, setSpecsError] = useState(false);
  const [delivery, setDelivery] = useState<Delivery | null>(null);
  // D-6: a swallowed error left `delivery` null, which renders as "nothing
  // produced yet" — a load failure shown as an absence. Track it like the other
  // reads here do, so unknown stays unknown.
  const [deliveryError, setDeliveryError] = useState(false);
  const [recap, setRecap] = useState<TaskRecap | null>(null);
  const [recapError, setRecapError] = useState(false);
  const [events, setEvents] = useState<TaskEvent[] | null>(null);
  const [runs, setRuns] = useState<ExecutionRun[] | null>(null);
  // Whether this task's run list is trustworthy. A failed read used to land as
  // an empty list, which the Continue action would have read as "nothing is
  // running" — an unknown presented as an absence, on the one question that
  // decides whether a second run may be dispatched.
  const [runsError, setRunsError] = useState(false);
  const [eventsError, setEventsError] = useState(false);
  // The newest event the control plane has confirmed to this page but that the event
  // read may not have fetched yet — `appendTaskEvent` returns the record it wrote, and
  // discarding it was what left the timeline claiming to be whole in the seconds after
  // a note was posted. Only the newest is kept: the events endpoint takes no limit and
  // returns the whole table, so a read that has this one has every earlier one.
  const [postedEventId, setPostedEventId] = useState<string | null>(null);
  // Whether a run beyond this page actually exists. The read asks for one more than it
  // keeps (`lib/paged.ts`), so this is evidence and not the inference it used to be —
  // `length >= RUN_HISTORY_LIMIT`, which called a history of exactly that many runs
  // truncated and had the panels announce a read failure that never happened (CR-25,
  // CR-28). A page with more behind it cannot say which run is the latest, nor that no
  // review run exists for a worker run — both are claims about the whole list. Tracked
  // separately from `runsError` because "some records could not be read" and "there are
  // more records than were read" are different facts about the same read.
  const [runsTruncated, setRunsTruncated] = useState(false);
  // The task's unsettled runs, asked as their own question rather than filtered
  // out of the page of recent history above: that page is capped, so a long-lived
  // task can fill it with terminal runs while an older one is still queued, and
  // an active run missing from it is not an absence. null = no answer yet, or the
  // read failed — both unknown.
  const [activeRuns, setActiveRuns] = useState<RunSummary[] | null>(null);
  // Whether that read failed, as opposed to not having answered yet. Both leave
  // `activeRuns` null and both are unknown to the Continue gate, but the right
  // column has to *say* which: a spinner and "couldn't tell whether a run is
  // running" are different sentences, and neither is "nothing is running".
  const [activeRunsError, setActiveRunsError] = useState(false);
  // This task's own open attention items. Asked here, task-scoped, rather than
  // taken from the parent's global list: that list is one capped page, so past
  // the cap a task whose approval sits beyond it would read as "no open gate"
  // and unlock Continue. null = no answer yet or the read failed — the two are
  // told apart by `taskAttentionError`, because a spinner and an error are
  // different things to show (see lib/task-attention.ts).
  // Typed as the attention module's item — the one `listAttention` actually returns,
  // with `body`, `options` and `version` — because the conversation now shows the
  // item's words and answers it with its version (CR-74). Every other consumer reads
  // the structural subset it always did.
  const [taskAttention, setTaskAttention] = useState<FullAttentionItem[] | null>(null);
  // The task-scoped read itself failed. Tracked separately from the null above so
  // the panel can say "unavailable" instead of sitting on a spinner forever — and
  // so it never says "nothing needs you" about a fact it could not read.
  const [taskAttentionError, setTaskAttentionError] = useState(false);
  // The task-scoped read came back full, so it may have been cut off. "Not in a
  // page that may be truncated" is not "not there", so this reads as unknown too.
  const [taskAttentionTruncated, setTaskAttentionTruncated] = useState(false);
  // The review path's own gate items for ONE worker run, in EVERY state — the fact
  // `_reviews_owed` releases on (`_has_review_gate_item`), and the reason this is a
  // second read rather than a filter over `taskAttention`: that read asks for
  // `state=open`, and the item that releases the obligation is most often one the
  // human has already responded to or cancelled. Reading absence out of the open
  // list is what let the column say "clear" for a run the backend still owes a
  // review for. Carried with the run id it was read for, so an answer for the
  // previous run is never counted against the current one.
  const [reviewGate, setReviewGate] = useState<AttentionItem[] | null>(null);
  const [reviewGateFor, setReviewGateFor] = useState<string | null>(null);
  const [reviewGateError, setReviewGateError] = useState(false);
  const [reviewGateTruncated, setReviewGateTruncated] = useState(false);
  // When set, the follow-up watch below is armed until this timestamp.
  const [followUpUntil, setFollowUpUntil] = useState<number | null>(null);

  // Where the task was left off. Reusable because posting a note changes the
  // answer: the recap would otherwise keep saying "yesterday" while the timeline
  // already shows today's message. A success clears any earlier error, so a
  // transient failure does not leave the panel permanently unavailable.
  //
  // Guarded by a request token, because the answer changes over time. Posting a
  // note while the first read is still in flight leaves two requests running,
  // and the older one resolving last would overwrite the fresher recap with a
  // pre-note one — showing yesterday's date next to today's message until the
  // task is reopened. Only the newest request may write.
  const recapRequest = useRef(0);
  // Bumped whenever a recap read settles, success or failure. The midnight
  // reschedule keys on this rather than on a field of the response: keying on
  // recap.day looked right but does not advance on a quiet task (day is the last
  // *active* day, while midnight moves days_ago), so the timer fired once and
  // never rescheduled — a page left visible went stale after one night. A
  // counter removes the need to reason about which field changes.
  const [recapSettled, setRecapSettled] = useState(0);
  const loadRecap = useCallback((signal?: AbortSignal) => {
    const token = ++recapRequest.current;
    const stale = () => signal?.aborted || token !== recapRequest.current;
    fetchTaskRecap(task.task_id, signal)
      .then((value) => {
        if (stale()) return;
        setRecap(value);
        setRecapError(false);
        setRecapSettled((n) => n + 1);
      })
      .catch(() => {
        if (stale()) return;
        setRecapError(true);
        // Settle on failure too, so a failed midnight read still reschedules
        // instead of leaving the panel frozen until the next mount.
        setRecapSettled((n) => n + 1);
      });
  }, [task.task_id]);

  // Token-guarded like its siblings, and it needs the guard more than most: five
  // places start an events read (refresh, a run reaching a terminal state, follow-up,
  // dispatch completing, posting a note), several of them unsignalled, so reads
  // overlap routinely. Without a token an older answer can land last and put both the
  // list *and* its error bit back — and `eventsError` is what the timeline's honesty
  // now rests on, so a stale `false` there is a stale claim of completeness.
  const eventsRequest = useRef(0);
  const loadEvents = useCallback((signal?: AbortSignal) => {
    const token = ++eventsRequest.current;
    const stale = () => signal?.aborted || token !== eventsRequest.current;
    listTaskEvents(task.task_id, signal)
      .then((value) => { if (!stale()) { setEvents(value); setEventsError(false); } })
      // Not `setEvents([])`. That turned every failure into a confirmed empty history
      // -- the timeline then said "no activity yet" about a read that established
      // nothing -- and it also erased notes that were already on screen, so a
      // transient failure looked like the task losing its past.
      .catch(() => { if (!stale()) setEventsError(true); });
  }, [task.task_id]);

  // Full run records (prompt + stdout/stderr + result) so the timeline can show
  // what the agent actually did — the substance, like the terminal, not a ledger.
  // A failed list read, or a detail read that dropped a run from the list, leaves
  // `runsError` set: what we have is still shown, but "is a run active?" becomes
  // unknown rather than silently answered "no".
  //
  // Token-guarded like its siblings, and for a sharper reason: this one *replaces*
  // the array rather than folding updates in through mergeRunUpdates, so a stale
  // response landing last does not merely lose a version race — it overwrites the
  // whole snapshot. The terminal-run path calls this unsignalled and then calls
  // onTaskMayHaveChanged(), whose refreshNonce bump starts a second, newer read;
  // without a token the older answer could land after it and put the pre-settle
  // list back on screen. Only the newest request may write.
  const runsRequest = useRef(0);
  const loadRuns = useCallback((signal?: AbortSignal) => {
    const token = ++runsRequest.current;
    const stale = () => signal?.aborted || token !== runsRequest.current;
    // Asks for one run beyond the page, so `runsTruncated` is the fact that a further
    // run exists rather than the guess a full page used to make. The probe row is
    // dropped before the details are fetched, so it costs one summary and no detail
    // request.
    return readPage(RUN_HISTORY_LIMIT, (limit) =>
      listRuns({ task_id: task.task_id, limit }, signal))
      .then((page) =>
        Promise.all(page.items.map((summary) => getRun(summary.run_id, signal).catch(() => null)))
          .then((details) => {
            if (stale()) return;
            const summaries = page.items;
            const loaded = details.filter((run): run is ExecutionRun => run !== null);
            setRuns(loaded);
            setRunsError(loaded.length !== summaries.length);
            // Not "at least these runs" as a guess any more: a further run was
            // actually returned, so nothing derived from this page may claim to be
            // about all of them.
            setRunsTruncated(page.truncated);
          }),
      )
      .catch(() => { if (!stale()) setRunsError(true); });
  }, [task.task_id]);

  // "Is a run active?" — its own request, so the answer never depends on how much
  // history fits in one page. A failed read is unknown (null), never "none". The
  // answer is also returned, so a caller can act on it without waiting for a
  // re-render.
  //
  // Token-guarded for the same reason as the attention read below: the terminal
  // -state path calls this unsignalled and also bumps refreshNonce, so two reads
  // overlap and a stale empty one landing last would say "nothing running" over a
  // live run. A superseded read returns null — unknown — rather than its own stale
  // array, so the follow-up watcher keeps watching instead of acting on it.
  const activeRunsRequest = useRef(0);
  const loadActiveRuns = useCallback((signal?: AbortSignal) => {
    const token = ++activeRunsRequest.current;
    const stale = () => signal?.aborted || token !== activeRunsRequest.current;
    return listActiveRuns(task.task_id, signal)
      .then((summaries) => {
        if (stale()) return null;
        setActiveRuns(summaries);
        setActiveRunsError(false);
        return summaries as RunSummary[] | null;
      })
      .catch(() => {
        if (!stale()) { setActiveRuns(null); setActiveRunsError(true); }
        return null;
      });
  }, [task.task_id]);

  // "Is a human gate open on this task?" — its own task-scoped request, so the
  // answer never depends on where this task's items happen to fall in a global
  // page. A failed read leaves it unknown (null), never "none".
  //
  // Guarded by a request token, like the recap above, because the answer changes
  // and two reads overlap on the one path that matters: a run reaching a terminal
  // state calls this unsignalled AND calls onTaskMayHaveChanged(), whose
  // refreshNonce bump re-runs the mount effect and starts a second read. Without
  // a token the earlier — still empty — response can land after the newer one
  // that carries the review approval, and Continue would then read the gate as
  // absent at exactly the moment it exists. Only the newest request may write.
  const attentionRequest = useRef(0);
  const loadTaskAttention = useCallback((signal?: AbortSignal) => {
    const token = ++attentionRequest.current;
    const stale = () => signal?.aborted || token !== attentionRequest.current;
    return readPage(TASK_ATTENTION_LIMIT, (limit) => listAttention(
      { task_id: task.task_id, state: "open", limit }, signal,
    ))
      .then((page) => {
        if (stale()) return;
        setTaskAttention([...page.items]);
        setTaskAttentionError(false);
        setTaskAttentionTruncated(page.truncated);
      })
      .catch(() => {
        if (stale()) return;
        setTaskAttention(null);
        setTaskAttentionError(true);
        setTaskAttentionTruncated(false);
      });
  }, [task.task_id]);

  // Fetch this task's detail. The parent keys this component by task_id, so a
  // task switch remounts it and the initial state above already resets cleanly.
  useEffect(() => {
    const ctrl = new AbortController();
    fetchProjectDashboard(task.project_id, ctrl.signal)
      .then((dash) => { if (!ctrl.signal.aborted) { setDashboard(dash); setDashError(false); } })
      .catch(() => { if (!ctrl.signal.aborted) setDashError(true); });
    // A failed read stays "unknown" (null + error), never a false "empty".
    // Clear the error on success: an explicit Refresh after a transient failure
    // must not leave the page showing real data next to "unavailable".
    listSpecs(task.task_id)
      .then((value) => { if (!ctrl.signal.aborted) { setSpecs(value); setSpecsError(false); } })
      .catch(() => { if (!ctrl.signal.aborted) setSpecsError(true); });
    loadEvents(ctrl.signal);
    void loadRuns(ctrl.signal);
    void loadActiveRuns(ctrl.signal);
    void loadTaskAttention(ctrl.signal);
    if (task.state === "done") {
      fetchDelivery(task.task_id, ctrl.signal)
        .then((value) => { if (!ctrl.signal.aborted) { setDelivery(value); setDeliveryError(false); } })
        .catch(() => { if (!ctrl.signal.aborted) setDeliveryError(true); });
    }
    loadRecap(ctrl.signal);
    return () => ctrl.abort();
  // refreshNonce: the left rail's Refresh reloads the parent's data, but a run
  // finishing without changing the task's state leaves every dep here unchanged,
  // so the recap would keep showing the pre-run answer. The nonce makes an
  // explicit refresh reach this component's own reads too.
  }, [task.task_id, task.project_id, task.state, refreshNonce, loadEvents, loadRecap, loadRuns, loadActiveRuns, loadTaskAttention]);

  // The recap's wording depends on what day it is, so leaving the page open past
  // midnight would keep it saying "today" about yesterday's work. Refetch when
  // the display zone's day rolls over, and whenever the tab becomes visible
  // again — the common way a page survives a night.
  //
  // The rollover is computed from the zone the *server* renders in
  // (zone_offset_minutes), not the browser's: the display zone can be configured
  // and the backend may run elsewhere, which is exactly why that field exists.
  // Until the first response arrives there is nothing to schedule.
  const nextRollover = recap?.next_rollover;
  // How long to wait before retrying a boundary that has already passed. A ref,
  // not state, because writing it must not itself re-run the effect below — that
  // would restart the timer it is there to slow down.
  const rolloverBackoff = useRef(0);
  useEffect(() => {
    if (!nextRollover) return;
    const at = Date.parse(nextRollover);
    if (Number.isNaN(at)) return;
    // A second past the boundary, so the server has certainly rolled over too.
    const remaining = at - Date.now() + 1_000;
    if (remaining > 0) {
      // The boundary is genuinely ahead: schedule the real rollover read, and
      // forget any backoff an earlier failure built up — the day is being tracked
      // again, which is what the backoff was waiting for.
      rolloverBackoff.current = 0;
      const timer = window.setTimeout(() => loadRecap(), remaining);
      return () => window.clearTimeout(timer);
    }
    // The boundary is already in the past: either the midnight read failed (the
    // failure path settles too, and recapSettled re-runs this effect) or the
    // client's clock is ahead of the server's. Firing immediately would refetch,
    // fail, settle, re-run, and fire again — an offline page emitting a tight
    // request loop. Retry on a bounded, increasing delay instead; it resets above
    // as soon as a read comes back with a future boundary.
    const wait = Math.min(rolloverBackoff.current || ROLLOVER_RETRY_MIN_MS, ROLLOVER_RETRY_MAX_MS);
    rolloverBackoff.current = Math.min(wait * 2, ROLLOVER_RETRY_MAX_MS);
    const timer = window.setTimeout(() => loadRecap(), wait);
    return () => window.clearTimeout(timer);
  }, [nextRollover, loadRecap, recapSettled]);

  useEffect(() => {
    const onVisible = () => { if (document.visibilityState === "visible") loadRecap(); };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [loadRecap]);

  // The events read is handed to the conversation as facts (the list, whether the read
  // failed, whether the page knows of an entry it lacks); the stream's verdict is decided
  // there, through the same `timelineVerdictOf` this page used to call.
  //
  // `capped: false` for events is measured, not assumed: `GET /tasks/{id}/events`
  // takes no limit and `TaskStore.events` (backend/agora/tasks/store.py:368) selects
  // the whole table for the task. The runs read is the capped one.
  //
  // `behind` is derived, not tracked. Both lists have siblings that reach a fact before
  // they do — the attention reads see a gate the events read has not fetched, the
  // active-run read sees a run the capped history page has not — and the answer is a
  // comparison of what is in hand, not a flag someone has to set at each place the page
  // finds out. Both review rounds that attacked the flag version were about the places
  // it was not set and the state update it raced with; a derivation has neither.
  const eventsGap = eventsLackKnown(
    events,
    [taskAttention, reviewGate, railAttention],
    postedEventId === null ? [] : [postedEventId],
  );
  const runsGap = runsLackKnown(runs, [activeRuns, railRuns]);

  // A gap the page can close, so close it. Disclosing one without fetching it was a
  // notice with nothing behind it: during a run the 2.5s poll refreshes attention but
  // not events, so a gate raised mid-run put a caveat on screen that would have sat
  // there until the run settled or the user hit Refresh. Once per time a gap appears —
  // a boolean dependency cannot re-fire while it stays true, and if the fetch does not
  // close it the caveat is still the honest thing to show.
  useEffect(() => { if (eventsGap) loadEvents(); }, [eventsGap, loadEvents]);
  useEffect(() => { if (runsGap) void loadRuns(); }, [runsGap, loadRuns]);


  // The latest successful *worker* run is what "produced" the current result — or
  // the reason no such answer exists. In the auto-review flow the review run
  // finishes after the worker run it reviews, so an unfiltered "latest success"
  // shows the review verdict instead of the implementation; and the run page this
  // is filtered from is capped and partially readable, so "there is no success" is
  // a claim only a complete read may make. Both rules live in
  // lib/run-column.ts (`latestDeliveredOf`) rather than here, which is R3: the
  // truncation flag now applies on every path that consumes the capped history,
  // not only inside runColumnOf.
  const latestSuccess = useMemo<DeliveredResult<ExecutionRun>>(
    () => latestDeliveredOf({ runs, runsReadFailed: runsError, runsTruncated }),
    [runs, runsError, runsTruncated],
  );

  // Runs that have not settled: the authoritative active-run read, plus anything
  // non-terminal this view already holds (a run dispatched a moment ago is known
  // here before the next server answer arrives). A joined key, not the array, so
  // the poll below re-arms only when the *set* changes and not on every merged
  // output update.
  const activeRunIds = useMemo(() => {
    const ids = new Set<string>((activeRuns ?? []).map((run) => run.run_id));
    for (const run of runs ?? []) if (!TERMINAL_RUN_STATES.has(run.state)) ids.add(run.run_id);
    return [...ids].sort();
  }, [activeRuns, runs]);
  const activeRunKey = activeRunIds.join(",");

  // Poll the unsettled runs until they settle, so a run dispatched from this page
  // shows its real output here instead of an empty card awaiting a manual
  // Refresh. When one settles, re-read the whole list: the control plane may have
  // created another run in response (the automatic cross-review), and that one is
  // not in the set we were polling.
  useEffect(() => {
    if (!activeRunKey) return;
    const ctrl = new AbortController();
    let timer: number | undefined;
    const tick = async () => {
      const fresh = await Promise.all(
        activeRunKey.split(",").map((runId) => getRun(runId, ctrl.signal).catch(() => null)),
      );
      if (ctrl.signal.aborted) return;
      // R2: re-observe the human gate on every tick, not only when a run settles.
      // The loop used to refresh run records alone, so an approval or a question the
      // agent raised *mid-run* was invisible until the run reached a terminal state.
      // The live column says nothing when the attention read is in flight or empty —
      // that silence rule stays, because an empty banner reads as "nothing needs
      // you" — but presence has to be re-observed while presence can change, and a
      // question raised mid-run is the most time-critical item this product has.
      // Signalled, so it is dropped when this effect retires; the terminal branch
      // below re-reads unsignalled and, being the newer request, wins the token race
      // in loadTaskAttention.
      void loadTaskAttention(ctrl.signal);
      const updates = fresh.filter((run): run is ExecutionRun => run !== null);
      if (updates.length > 0) {
        setRuns((current) => mergeRunUpdates(current, updates));
        if (updates.some((run) => TERMINAL_RUN_STATES.has(run.state))) {
          // Deliberately unsignalled: this state change retires the effect that
          // owns `ctrl`, so passing it would abort the follow-up reads it exists
          // to trigger.
          void loadRuns();
          void loadActiveRuns();
          void loadTaskAttention();
          loadEvents();
          loadRecap();
          // The orchestration this run may trigger has not been recorded yet, so
          // keep watching for a bounded while (see FOLLOW_UP_WINDOW_MS). Also
          // reload the parent: a review run that just finished raises the
          // approval that must block Continue, and that lives in the parent's
          // attention read.
          setFollowUpUntil(Date.now() + FOLLOW_UP_WINDOW_MS);
          onTaskMayHaveChanged();
        }
      }
      if (!ctrl.signal.aborted) timer = window.setTimeout(() => void tick(), RUN_POLL_MS);
    };
    timer = window.setTimeout(() => void tick(), RUN_POLL_MS);
    return () => { ctrl.abort(); if (timer !== undefined) window.clearTimeout(timer); };
  }, [activeRunKey, loadEvents, loadRecap, loadRuns, loadActiveRuns, loadTaskAttention, onTaskMayHaveChanged]);

  // The settle window: a worker run reaching a terminal state is when the
  // dispatcher *starts* building the cross-review run, so the moment polling
  // would otherwise stop is the moment there is nothing yet to find. Keep asking
  // for a bounded while, and stop as soon as something unsettled shows up (the
  // poll above then owns it) or the window expires — which is the ordinary
  // outcome when auto-review is off or the run failed.
  //
  // The window is armed *state*, not just a scheduled timer, because while it is
  // armed the Continue gate is closed (block "followUpPending"): between the
  // worker run going terminal and the review run being queued, nothing is active
  // and no gate exists yet, and the backend would accept a second worker run.
  // So something has to disarm it, and that is `close` below.
  useEffect(() => {
    if (followUpUntil === null) return;
    if (activeRunKey) return; // a follow-up run exists; the poll above tracks it
    const ctrl = new AbortController();
    let timer: number | undefined;
    // Closing the window: disarm the gate AND re-read everything. A review run
    // that started and finished inside one interval (a launcher that fails
    // immediately does exactly that) is invisible to the active-run probe, so
    // without a full read here the approval or blocker it raised would never
    // reach this page while Continue quietly reopened. Unsignalled on purpose:
    // clearing the deadline retires the effect that owns `ctrl`.
    const close = () => {
      setFollowUpUntil(null);
      void loadRuns();
      void loadActiveRuns();
      void loadTaskAttention();
      loadEvents();
      loadRecap();
      onTaskMayHaveChanged();
    };
    if (Date.now() >= followUpUntil) { close(); return; }
    const watch = async () => {
      const found = await loadActiveRuns(ctrl.signal);
      if (ctrl.signal.aborted) return;
      if (found !== null && found.length > 0) {
        // The follow-up showed up. Pull its record and the events around it into
        // the view and stop: the poll above owns an unsettled run, and that run
        // being active is what keeps Continue closed from here on. Unsignalled on
        // purpose — this discovery retires the effect that owns `ctrl`.
        void loadRuns();
        loadEvents();
        setFollowUpUntil(null);
        return;
      }
      if (Date.now() >= followUpUntil) { close(); return; }
      // Asking only for the unsettled runs while the window is open: this repeats
      // on a timer, and the full read happens once, when the window closes.
      timer = window.setTimeout(() => void watch(), RUN_POLL_MS);
    };
    timer = window.setTimeout(() => void watch(), RUN_POLL_MS);
    return () => { ctrl.abort(); if (timer !== undefined) window.clearTimeout(timer); };
  }, [followUpUntil, activeRunKey, loadActiveRuns, loadRuns, loadEvents, loadRecap, loadTaskAttention, onTaskMayHaveChanged]);

  const activeRunCount = activeRuns === null || runsError
    ? null // unknown — never presented to the Continue gate as "nothing running"
    : activeRunIds.length;

  // Whether the auto-review handoff window is still open. Armed means: a run of
  // this task settled moments ago and whether it triggers a review is not yet
  // known. It is cleared by the effect above (on discovery or on expiry), so this
  // does not depend on a re-render happening at the right moment.
  const followUpArmed = followUpUntil !== null;

  // Open human gates on this task, as ONE derived answer that both the Continue
  // gate and the Attention panel read (see lib/task-attention.ts). They used to
  // derive it separately, from different sources, and disagreed: the panel fell
  // back to the parent's capped global list and could render "nothing needs you"
  // while the gate on the same screen was blocking with `attentionUnknown`.
  const attentionView = useMemo(
    () => taskAttentionView({
      items: taskAttention,
      readFailed: taskAttentionError,
      truncated: taskAttentionTruncated,
    }),
    [taskAttention, taskAttentionError, taskAttentionTruncated],
  );
  const openAttentionCount = openGateCount(attentionView);
  // The blocker line in the Outcome Summary, from the same view. It used to be the
  // `ask` prop the parent derived from its global page; see the note above this
  // component.
  const blockerClaim = useMemo(() => taskBlockerClaim(attentionView), [attentionView]);

  // Which of the two states the right column is in, derived from this task's runs
  // and from nothing the view can override — no stored tab, no default. See
  // lib/run-column.ts for why the four unreadable/loading shapes are distinct.
  const runColumn = useMemo<RunColumn<ExecutionRun>>(
    () => runColumnOf({
      runs,
      runsReadFailed: runsError,
      runsTruncated,
      activeRunIds: activeRuns === null ? null : activeRuns.map((run) => run.run_id),
      activeReadFailed: activeRunsError,
    }),
    [runs, runsError, runsTruncated, activeRuns, activeRunsError],
  );

  // The run whose review gate is worth asking about: the settled column's run, when
  // it is a successful worker run. Nothing else can owe a review, and
  // `reviewStandingOf` answers those cases without touching this read.
  const gateRunId = runColumn.kind === "settled"
    && runRole(runColumn.run) === ROLE_WORKER
    && runColumn.run.state === "succeeded"
    ? runColumn.run.run_id
    : null;

  // Read that run's approvals and blockers in every state. This is the one fact the
  // control plane releases the review obligation on, so the column derives its
  // standing from it instead of from the review run's state — which the backend is
  // explicit does not account for a review, however it ended.
  useEffect(() => {
    // Nothing to ask about. The stale answer for a previous run is deliberately left
    // in place rather than cleared here: `reviewStandingOf` compares `forRunId` with
    // the run it is judging and treats a mismatch as "not read", so an answer about
    // another run can never be counted against this one — and clearing it
    // synchronously in an effect only buys a cascading render.
    if (gateRunId === null) return;
    const ctrl = new AbortController();
    // No `state` filter, deliberately: a responded or cancelled item is exactly what
    // releases the obligation, and asking only for open ones is how "absent from the
    // open list" became "no gate was ever raised".
    readPage(REVIEW_GATE_LIMIT, (limit) =>
      listAttention({ task_id: task.task_id, run_id: gateRunId, limit }, ctrl.signal))
      .then((page) => {
        if (ctrl.signal.aborted) return;
        setReviewGate([...page.items]);
        setReviewGateFor(gateRunId);
        setReviewGateError(false);
        setReviewGateTruncated(page.truncated);
      })
      .catch(() => {
        if (ctrl.signal.aborted) return;
        // Unknown, not empty: the column must not read a failed request as "no gate
        // item exists". `reviewGateFor` is still recorded so the facts belong to this
        // run rather than looking like an answer that never arrived.
        setReviewGate(null);
        setReviewGateFor(gateRunId);
        setReviewGateError(true);
        setReviewGateTruncated(false);
      });
    return () => ctrl.abort();
  // taskAttention: when the open-item read changes, this run's items may have been
  // responded to or cancelled, and the two reads must not disagree about the same
  // item. It is written only by loadTaskAttention, so this cannot self-trigger.
  }, [gateRunId, task.task_id, refreshNonce, taskAttention]);

  // What accounts for the independent cross-review of the run the settled column is
  // showing. Only meaningful in that state, so it is null otherwise rather than
  // computed against a run that is still producing output.
  const reviewStanding = useMemo<ReviewStanding | null>(
    () => runColumn.kind !== "settled"
      ? null
      : reviewStandingOf({
        run: runColumn.run,
        runs,
        runsReadFailed: runsError,
        runsTruncated,
        gate: {
          items: reviewGate,
          readFailed: reviewGateError,
          truncated: reviewGateTruncated,
          forRunId: reviewGateFor,
        },
      }),
    [runColumn, runs, runsError, runsTruncated, reviewGate, reviewGateError, reviewGateTruncated, reviewGateFor],
  );

  // The gate headline: what stands between that run and done. Built from the same
  // attention view the Continue gate and the blocker line read, so the column
  // cannot disagree with the rest of the page about whether a gate is open.
  const verdict = useMemo<GateVerdict | null>(
    () => reviewStanding === null ? null : gateVerdict({ attention: attentionView, review: reviewStanding }),
    [attentionView, reviewStanding],
  );

  // What the Continue action seeds its prompt with: the recap's day and items as
  // this view already renders them, plus the task's own goal and recorded next
  // step. Unreadable facts travel as "unavailable" so the seed says so instead of
  // implying nothing happened.
  const nextAction = dashTask?.next_action ?? null;
  const continueSeed = useMemo<ContinueSeedFacts>(() => ({
    title: task.title,
    description: task.description,
    acceptance: task.acceptance,
    recapHeading: recap && recap.day !== null ? recapHeadingText(recap, t) : null,
    recapLines: (recap?.items ?? []).map((item) => recapItemText(item, t, te)),
    // What the recap did NOT include. The Resume Brief renders both facts, so the
    // seed must carry them: with only `items`, a trimmed day reaches the agent
    // looking complete — and with zero loaded items it would assert the day had
    // nothing to summarize.
    recapMore: recap?.more ?? 0,
    recapPartial: recap?.partial ?? false,
    recapUnavailable: recapError || recap === null,
    nextAction: nextAction && nextAction.action !== "none" ? te("nextAction", nextAction.action) : null,
    nextActionUnavailable: dashError || dashTask === null,
  }), [task.title, task.description, task.acceptance, recap, recapError, nextAction, dashError, dashTask, t, te]);

  // The step's side blocks. The design (`renderTask`) gives the right-hand column to the
  // step while one is open: the run first — "右栏跟着 Run 状态走：跑的时候看它在干什么，
  // 结束后看改动和门禁" — then the task itself, then the project's goal. They render
  // into the page-level column through a portal so that the column is one column
  // (CR-78: a second aside inside the centre made four), while every read behind them
  // stays here with the task. Nothing about a project's standing is drawn in this mode:
  // that was the `ProjectStrip` above the task, which the design does not have and
  // which put project rows in the middle of a step.
  // What a review answer sets in motion here, without another click (CR-70 / CR-83).
  // Approve: the step is handed off (committed, bound, done) and the page moves to the
  // next planned step. Reject: the task is put back to planned and the reviewer's words
  // plus the person's go to the worker as the next turn — on the adapter that did the
  // reviewed work, so the fix lands in the worktree that holds the defect. Every read
  // that can change is asked again either way; nothing about the new state is assumed.
  const [gateNote, setGateNote] = useState<{ key: string; vars?: Record<string, string> } | null>(null);
  const alive = useRef(true);
  useEffect(() => { alive.current = true; return () => { alive.current = false; }; }, []);
  const reloadAfterGate = useCallback(() => {
    void loadTaskAttention();
    loadEvents();
    loadRecap();
    void loadRuns();
    void loadActiveRuns();
    onTaskMayHaveChanged();
  }, [loadTaskAttention, loadEvents, loadRecap, loadRuns, loadActiveRuns, onTaskMayHaveChanged]);
  const handleGate = async (outcome: GateOutcome) => {
    reloadAfterGate();
    setGateNote(null);
    // Only Agora's own review gate completes a step or sends a fix turn. A tool or
    // file approval the agent raised mid-run is also an "approval" item, and answering
    // it must do nothing but answer it — the task is still running.
    if (outcome.item.requester !== REVIEW_REQUESTER) return;
    if (outcome.action === "approve") {
      try {
        const result = await handoffTask(task);
        if (!alive.current) return;
        onStepDone({ taskId: task.task_id, commit: result.commit, committed: result.committed });
      } catch (err) {
        if (!alive.current) return;
        setGateNote({ key: "workspace.continue.advance.completeFailed", vars: { reason: err instanceof Error ? err.message : String(err) } });
      }
      reloadAfterGate();
      return;
    }
    if (outcome.action === "reject") {
      try {
        const fresh = await getTask(task.task_id);
        const planned = fresh.state === "planned" ? fresh : await transitionTask(fresh, "planned");
        const reviewed = runs?.find((run) => run.run_id === outcome.item.run_id);
        await createRun({
          task_id: task.task_id,
          adapter: reviewed?.adapter ?? worker,
          prompt: reviewFixPrompt({ reviewTitle: outcome.item.title, reviewBody: outcome.item.body, reason: outcome.response }),
          timeout_seconds: CONTINUE_TIMEOUT_SECONDS,
          expected_task_version: planned.version,
          actor: "user",
        });
        if (!alive.current) return;
        setGateNote({ key: "workspace.continue.advance.fixSent" });
      } catch (err) {
        if (!alive.current) return;
        setGateNote({ key: "workspace.continue.advance.fixFailed", vars: { reason: err instanceof Error ? err.message : String(err) } });
      }
      reloadAfterGate();
    }
  };
  const advanceHere = advance !== null && advance.showOn === task.task_id ? advance : null;

  const side = (
    <>
      <RunColumnPane
        task={task}
        column={runColumn}
        attention={attentionView}
        review={reviewStanding}
        verdict={verdict}
        delivery={delivery}
        deliveryError={deliveryError}
        deliveryAsked={task.state === "done"}
      />
      <OutcomeSummary task={task} trustLine={taskTrustLineOf({ readFailed: dashError, hasTask: dashTask !== null, hasSpec: dashTask?.has_spec ?? false })} next={dashError ? null : dashTask?.next_action ?? null} blocker={blockerClaim} delivery={delivery} deliveryError={deliveryError} deliveryAsked={task.state === "done"} latestSuccess={latestSuccess} />
      {/* Requirements: the task's contract. The full spec, its state, its revision
          and its acceptance scenarios are all still here, plus the deep link. */}
      <RequirementsStrip task={task} specs={specs} error={specsError} />
      <section className="rounded-lg border px-4 py-3">
        <h5 className="mb-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">{t("workspace.side.goal")}</h5>
        {/* Three answers, kept apart: the read failed, it has not landed, the project
            has no goal. An empty string here would otherwise read as "no goal". */}
        <p className="text-[12px] leading-relaxed text-muted-foreground">
          {dashError ? t("workspace.panels.unavailable")
            : dashboard === null ? "—"
            : dashboard.charter.goal.trim() === "" ? t("workspace.side.goalNone")
            : dashboard.charter.goal}
        </p>
      </section>
    </>
  );

  return (
    <div className="flex min-h-0 min-w-0 flex-1 flex-col">
      {/* What the page just did on its own, said where it landed. */}
      {advanceHere && (
        <p className="mx-4 mt-3 shrink-0 rounded-lg border border-emerald-500/40 bg-emerald-500/10 px-3 py-2 text-[12px] leading-snug text-emerald-700 dark:text-emerald-400" aria-live="polite">
          {advanceHere.lines.map((line) => t(line.key, line.vars)).join(" ")}
        </p>
      )}
      {gateNote && (
        <p className="mx-4 mt-3 shrink-0 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[12px] leading-snug text-amber-700 dark:text-amber-400" aria-live="polite">
          {t(gateNote.key, gateNote.vars)}
        </p>
      )}
      {/* The Resume Brief and the action that acts on it, as one block. */}
      <div className="border-b bg-muted/20">
        <ResumeBrief recap={recap} recapError={recapError} t={t} te={te} />
        <ContinueAction
          initiallyOpen={advanceHere?.continueOpen ?? false}
          adapter={worker}
          task={task}
          projectGate={projectGate}
          seed={continueSeed}
          activeRunCount={activeRunCount}
          openAttentionCount={openAttentionCount}
          followUpArmed={followUpArmed}
          onCreated={(run) => {
            // The step the notice pointed at has been started; the notice is spent.
            onAdvanceConsumed();
            if (run === null) {
              // The POST's answer was lost and the re-read found an active run:
              // one exists, but its record is not in hand. Ask for it instead of
              // inventing a row — and take the authoritative active set from the
              // same read path the gate uses.
              void loadRuns();
              void loadActiveRuns();
              loadEvents();
              loadRecap();
              return;
            }
            setRuns((current) => mergeRunUpdates(current, [run]));
            // The dispatched run is unsettled by definition, so record it in the
            // authoritative set too rather than leaving that answer one poll
            // behind — otherwise the collapsed button would offer Continue again.
            setActiveRuns((current) => [...(current ?? []), asRunSummary(run)]);
            loadEvents();
            loadRecap();
          }}
          onTaskMayHaveChanged={onTaskMayHaveChanged}
        />
      </div>
      <div className="flex min-h-0 min-w-0 flex-1 flex-col">
        {/* Center: the conversation, which is the task's whole stream (W9). The
            conversation is the primitive the user handles (adr/0007); the task's other
            history — notes, spec approvals, stage changes, gates — is interleaved with
            its turns by time, and the completeness judgement (CR-26) travels with it:
            the events read goes down as facts and the stream decides what it may claim
            through the same `timelineVerdictOf` the old timeline tab used. */}
        <CenterColumn
          task={task}
          projectGate={projectGate}
          events={events}
          eventsReadFailed={eventsError}
          eventsBehind={eventsGap}
          specs={specs}
          knownActiveRuns={activeRuns}
          attention={attentionView}
          onGateAnswered={(outcome) => void handleGate(outcome)}
          onHistoryMayHaveChanged={() => loadEvents()}
          onPosted={(eventId) => { setPostedEventId(eventId); loadEvents(); loadRecap(); }}
          onTurnSettled={() => { void loadRuns(); void loadActiveRuns(); loadEvents(); loadRecap(); }}
          onTaskMayHaveChanged={onTaskMayHaveChanged}
        />
      </div>
      {/* Right: the page-level column while it is on screen; below `lg` that column is
          hidden, so the same blocks are drawn here, under the conversation, instead of
          into an element nobody can see. */}
      {wide && sideHost !== null
        ? createPortal(side, sideHost)
        /* Bounded and scrollable: these blocks can outgrow the column, and the page
           clips what a fixed-height flex row overflows. */
        : <div className="flex max-h-[45%] min-h-0 shrink-0 flex-col gap-4 overflow-y-auto border-t px-4 py-4">{side}</div>}
    </div>
  );
}

// The centre column: the conversation, which since W9 is the task's whole stream.
//
// It used to be two tabs — the conversation and a separate timeline — on the
// reasoning that the timeline's completeness judgement (CR-26) could not be made by a
// stream of turns. It can: the conversation now takes the same events read this page
// makes, interleaves it with its turns through the same `timelineVerdictOf`, and
// carries the same caveats. What the mockup draws (screen 2) is one column of run
// cards with the rest of the history between them, and one message box that either
// notes or dispatches. The two-tab layout put notes where nobody looked.
function CenterColumn({
  task,
  projectGate,
  events,
  eventsReadFailed,
  eventsBehind,
  specs,
  knownActiveRuns,
  attention,
  onGateAnswered,
  onHistoryMayHaveChanged,
  onPosted,
  onTurnSettled,
  onTaskMayHaveChanged,
}: {
  task: TaskManifest;
  projectGate: ProjectGate["kind"];
  events: TaskEvent[] | null;
  eventsReadFailed: boolean;
  eventsBehind: boolean;
  specs: RequirementSpec[] | null;
  knownActiveRuns: readonly RunSummary[] | null;
  attention: TaskAttentionView<FullAttentionItem>;
  onGateAnswered: (outcome: GateOutcome) => void;
  onHistoryMayHaveChanged: () => void;
  onPosted: (eventId: string) => void;
  onTurnSettled: () => void;
  onTaskMayHaveChanged: () => void;
}) {
  return (
    <div className="flex min-h-0 flex-1 flex-col border-r">
      <Conversation
        task={task}
        projectGate={projectGate}
        events={events}
        eventsReadFailed={eventsReadFailed}
        eventsBehind={eventsBehind}
        specs={specs}
        knownActiveRuns={knownActiveRuns}
        attention={attention}
        onGateAnswered={onGateAnswered}
        onHistoryMayHaveChanged={onHistoryMayHaveChanged}
        onPosted={onPosted}
        onTurnSettled={onTurnSettled}
        onTaskMayHaveChanged={onTaskMayHaveChanged}
      />
    </div>
  );
}

// The plain-language answer for the selected task: what it did, whether that's
// trustworthy, and the one next step. The timeline below is the evidence.
// Where this task was left off: the last day it moved, and what moved on it.
// First thing in the header because it answers the question you open a task with,
// which comes before "what state is it in" and "what is next".
//
// Every line is localized from a stable slug the backend derived from records —
// never generated prose. `item.summary` is the server's English fallback for API
// consumers and is deliberately not rendered here.
function recapItemText(item: RecapItem, t: Translate, te: TranslateEnum): string {
  const base = "workspace.recap.items";
  const adapter = item.adapter ?? "";
  switch (item.kind) {
    case "created":
      return t(`${base}.created`);
    case "delivered":
      // Three claims, three keys. A known count, a floor (the snapshot was
      // truncated), and an unknown count are different things to say, and the
      // backend keeps them apart precisely so the UI does not merge them.
      if (item.files === null || item.files === undefined) {
        return t(`${base}.deliveredUnknown`, { adapter });
      }
      return item.files_at_least
        ? t(`${base}.deliveredAtLeast`, { adapter, files: item.files })
        : t(`${base}.deliveredCount`, { adapter, files: item.files });
    case "ran_no_changes":
      return t(`${base}.ranNoChanges`, { adapter });
    case "run_failed":
      return t(`${base}.runFailed`, { adapter });
    case "reviewed":
      return t(`${base}.reviewed`, { adapter });
    case "spec_approved":
      return t(`${base}.specApproved`);
    case "spec_rejected":
      return t(`${base}.specRejected`);
    case "stage_reached":
      // Reuse the plain-language milestone wording rather than the raw state.
      return t(`${base}.stageReached`, {
        stage: item.state ? te("taskState", item.state as never) : "",
      });
    case "needs_you":
      return t(`${base}.needsYou`, { count: item.count ?? 0 });
    case "answered":
      return t(`${base}.answered`, { count: item.count ?? 0 });
    case "handed_off":
      return t(`${base}.handedOff`);
    case "noted":
      return t(`${base}.noted`, { count: item.count ?? 0 });
    default:
      // An unrecognized kind is a newer backend than this UI. Show the server's
      // own words rather than dropping the fact silently.
      return item.summary;
  }
}

// A failure and an unbroken run of nothing are different states and must read
// differently: "couldn't load" is not "nothing happened".
const RECAP_TONE: Record<string, string> = {
  run_failed: "bg-red-500",
  needs_you: "bg-amber-500",
  spec_rejected: "bg-red-500",
  delivered: "bg-emerald-500",
  handed_off: "bg-emerald-500",
  reviewed: "bg-violet-500",
};

// The heading the Resume Brief shows for the recap's day, and the same words the
// Continue action seeds its prompt with — one source, so the prompt can never
// claim a different day than the panel above it.
function recapHeadingText(recap: TaskRecap, t: Translate): string {
  const ago = recap.days_ago;
  // Follows days_ago, so a task nobody has touched for three weeks never claims
  // "yesterday".
  if (ago === 0) return t("workspace.recap.today");
  if (ago === 1) return t("workspace.recap.yesterday");
  if (ago !== null && ago < 7) return t("workspace.recap.daysAgo", { n: ago });
  return t("workspace.recap.onDate", { date: recap.day ?? "" });
}

function ResumeBrief(
  { recap, recapError, t, te }:
  { recap: TaskRecap | null; recapError: boolean; t: Translate; te: TranslateEnum },
) {
  if (recapError) {
    return (
      <div className="px-5 py-2">
        <p className="text-xs text-muted-foreground">{t("workspace.panels.unavailable")}</p>
      </div>
    );
  }
  if (recap === null) return null; // still loading; the header below already shows
  if (recap.day === null) {
    return (
      <div className="px-5 py-2">
        <p className="text-xs text-muted-foreground">{t("workspace.recap.never")}</p>
      </div>
    );
  }

  const heading = recapHeadingText(recap, t);

  return (
    <div className="px-5 py-2.5">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <p className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">
          {heading}
        </p>
        {recap.items.length === 0 && (
          // A day with only activity-only events (run.process_started and the
          // like) legitimately has a day and no items. Saying "nothing has ever
          // happened" here would contradict the timeline right below it — the
          // backend keeps the two apart on purpose, so the UI must too.
          <p className="text-xs text-muted-foreground">{t("workspace.recap.noSummary")}</p>
        )}
        <ul className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1">
          {recap.items.map((item, index) => (
            <li key={`${item.kind}-${index}`} className="flex items-center gap-1.5 text-xs">
              <span className={cn("size-1.5 shrink-0 rounded-full", RECAP_TONE[item.kind] ?? "bg-muted-foreground/50")} />
              {recapItemText(item, t, te)}
            </li>
          ))}
        </ul>
        {recap.more > 0 && (
          <span className="text-[11px] text-muted-foreground">
            {t("workspace.recap.more", { n: recap.more })}
          </span>
        )}
        {recap.partial && (
          <span className="text-[11px] text-muted-foreground">{t("workspace.recap.partial")}</span>
        )}
      </div>
    </div>
  );
}

// Given the decided trust line and the task's next action, not the dashboard row they
// came from: with `dashTask` out of scope, a later edit cannot reach past the decision
// to a retained value, which is the one thing a source scan could never establish.
function OutcomeSummary({ task, trustLine, next, blocker, delivery, deliveryError, deliveryAsked, latestSuccess }: { task: TaskManifest; trustLine: TaskTrustLine; next: NextAction | null; blocker: TaskBlockerClaim; delivery: Delivery | null; deliveryError: boolean; deliveryAsked: boolean; latestSuccess: DeliveredResult<ExecutionRun> }) {
  const { t, te } = useI18n();
  // A task-level action has no `partial`: the task is on the page or there is no
  // `dashTask` at all. So the only thing that can disqualify it here is staleness,
  // which `next` already carries by being null. Routed through the shared decision
  // anyway, so this page and the portfolio cockpit cannot drift on the same sentence.
  const stuck = task.state === "blocked" || task.state === "failed";
  // Two sources, deliberately, and they are not the same claim. The title comes
  // from the task-scoped attention view (the single source for every attention
  // claim on this screen). `stuck` comes from the task manifest's own lifecycle
  // state, which is authoritative on its own and needs no attention item to be
  // true — a blocked task with no readable item still says it is blocked, without
  // detail. Nothing here re-derives "is a gate open?" from a second attention read.
  const blockerText = blocker.kind === "blocker"
    ? blocker.title
    : stuck ? t("workspace.header.blockedNoDetail") : null;
  // No answer yet about a blocker, on a task whose own state does not already say
  // it is stuck. It must not fall through to "next step: X": that reads as "nothing
  // is in the way", which is the claim the read cannot support.
  const blockerUnknown = blocker.kind === "loading" || blocker.kind === "unknown";
  const done = task.state === "done";
  // The delivered result, as the claim it is (R3). `run` is a success that was
  // actually found — real whatever else the capped page dropped — and `certain` is
  // whether it is provably the *latest* one. `unknown` is the case that used to
  // print "Nothing produced yet": no success in a page that was truncated or that
  // lost records is not "nothing was produced".
  const produced = latestSuccess.kind === "run" ? latestSuccess.run : null;
  const producedUncertain = latestSuccess.kind === "run" && !latestSuccess.certain;
  const outcomeLine = firstMeaningfulLine(produced?.stdout_tail ?? "");
  const fileCount = delivery?.fileCount ?? 0;
  // S1: which of the six distinguishable outcomes this is, decided in
  // lib/run-column.ts. `deliveryAsked` travels because the task's handoff is only
  // fetched for a done task, so a zero file count here is usually "not asked" — and
  // "the handoff lists no files" may only be said when it was actually read.
  const outcomeNotice = outcomeClaimNotice(outcomeClaimOf({
    delivered: latestSuccess,
    hasOutcomeLine: outcomeLine !== "",
    fileCount,
    filesKnown: deliveryAsked && !deliveryError && delivery !== null,
    deliveryReadFailed: deliveryError,
  }));

  return (
    <header className="rounded-lg border bg-card px-4 py-3">
      <div className="flex items-start justify-between gap-3">
        {/* The crumb already carries the title in full; here it is the box's label. */}
        <h2 className="min-w-0 truncate text-sm font-semibold leading-snug">{task.title}</h2>
        <span className={cn("inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
          done ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" : stuck ? "bg-orange-500/10 text-orange-600 dark:text-orange-400" : "bg-muted text-muted-foreground")}>
          {done && <CircleCheck className="size-3.5" />}{te("taskState", task.state)}
        </span>
      </div>

      <div className="mt-3 grid gap-3">
        {/* What it did — the concrete outcome, not a log. */}
        <div className="rounded-xl border bg-muted/30 p-3">
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{t("workspace.summary.did")}</p>
          {outcomeNotice === null ? (
            <div className="mt-1 space-y-1">
              {outcomeLine && <p className="text-sm leading-relaxed">{outcomeLine}</p>}
              <p className="text-xs text-muted-foreground">
                {produced && <span>· {produced.adapter}</span>}
                {fileCount > 0 && <span> · {t("workspace.summary.files", { count: fileCount })}{delivery?.commit ? ` · ${delivery.commit.slice(0, 8)}` : ""}</span>}
                {deliveryError && <span> · {t("workspace.summary.deliveryUnknown")}</span>}
              </p>
            </div>
          ) : (
            <div className="mt-1 space-y-1">
              {/* One sentence per distinguishable outcome, chosen in
                  lib/run-column.ts (`outcomeClaimOf`). S1: "a run succeeded and
                  recorded nothing" is no longer the same sentence as "no successful
                  run is on record" — they were collapsed here while the data told
                  them apart. D-6 and R3 (a failed delivery read, a truncated run
                  page) keep their own sentences for the same reason. */}
              <p className="text-sm text-muted-foreground">{t(outcomeNotice)}</p>
              {produced && (
                /* The run is real even when it recorded nothing, so it is still
                   named — otherwise the sentence above reads as being about no run
                   at all. */
                <p className="text-xs text-muted-foreground">· {produced.adapter}</p>
              )}
            </div>
          )}
          {producedUncertain && (
            /* The success below is real; its primacy is not — the same sentence the
               right column shows over a settled run it cannot prove is the newest. */
            <p className="mt-1 text-[11px] leading-snug text-muted-foreground">{t("workspace.column.maybeNotLatest")}</p>
          )}
        </div>

        {/* Trust + next step stacked. */}
        <div className="space-y-3">
          <div>
            <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{t("workspace.summary.trust")}</p>
            {/* The third place this number lived. `verified_weight / total_weight`
                marks every criterion of the task's *current* spec verified from any
                historical worker success, with no binding to the revision those
                criteria belong to — a run against one revision reports the next
                revision's new criteria as verified. It was removed from the portfolio
                card and from the cockpit tile for that reason, and the commit that did
                so said the page had written down why it cannot be shown and went on
                showing it two components away. This was the third. */}
            <p className="mt-0.5 text-sm text-muted-foreground">
              {trustLine === "unavailable" ? t("workspace.header.unavailable")
                : trustLine === "loading" ? "—"
                : trustLine === "noCriteria" ? t("workspace.header.noCriteria")
                : t("workspace.header.completionUnbound")}
            </p>
          </div>
          <div>
            <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{t("workspace.summary.next")}</p>
            {blockerText ? (
              <p className="mt-0.5 text-sm font-medium text-orange-600 dark:text-orange-400">{blockerText}</p>
            ) : blockerUnknown ? (
              <p className="mt-0.5 text-sm text-muted-foreground">{blocker.kind === "loading" ? "—" : t("workspace.header.unavailable")}</p>
            ) : next ? (
              next.action === "none"
                ? <p className="mt-0.5 inline-flex items-center gap-1 text-sm text-emerald-600 dark:text-emerald-400"><CircleCheck className="size-3.5" />{t("workspace.summary.done")}</p>
                : <p className={cn("mt-0.5 text-sm font-semibold", next.attention ? "text-orange-600 dark:text-orange-400" : "text-primary")}>{te("nextAction", next.action)}</p>
            ) : (
              // `next` is null when the read failed or has not landed. The trust line
              // beside it already distinguishes those two, from the same facts, so this
              // borrows its answer rather than re-deriving one from a flag it no longer
              // has -- two derivations of "why is this empty" is how they came to
              // disagree in the first place.
              <p className="mt-0.5 text-sm text-muted-foreground">
                {trustLine === "unavailable" ? t("workspace.header.unavailable") : "—"}
              </p>
            )}
          </div>
        </div>
      </div>
    </header>
  );
}

// First non-empty line of an agent's output, stripped of markdown noise — a
// usable one-line "what it did" for the summary.
function firstMeaningfulLine(text: string): string {
  for (const raw of text.split("\n")) {
    const line = raw.replace(/^[#>\s`*-]+/, "").trim();
    if (line) return line.length > 160 ? `${line.slice(0, 160)}…` : line;
  }
  return "";
}

// One work unit in the timeline: a run, and the runs recorded against it indented
// underneath.
//
// Nesting costs prominence, so two things are done to stop it costing *information*.
// Each nested run keeps its own full card, state badge included — nothing is
// summarized away. And a failure among them is stated on the unit itself, because a
// failed review under a succeeded worker run is exactly the pairing a person has to
// notice and exactly the one indentation would bury.
// ---------------------------------------------------------------------------
// Requirements — moved OFF the right column.
//
// The recorded UI decision gives the right column to the run: its live output
// while it is unsettled, its changes and its gate once it settles. Requirements
// are neither. They are the task's contract: what it is for, and what counts as
// done. They do not change while a run executes and they are not what a person is
// looking at when they glance at the column mid-run.
//
// They are NOT dropped, and they are not demoted to a link. They live here, in the
// header stack, immediately under the outcome summary whose "Trust" cell scores the
// very acceptance criteria this strip lists — the two facts belong next to each
// other, and reading one without the other is how "3/7 verified" becomes a number
// with no referent. Collapsed by default because it is a contract, not a signal;
// one click opens the whole spec in place, and the deep link to the Requirements
// page is unchanged.
//
// The three-state honesty is unchanged from the panel this replaces: a failed read
// says so, no spec says so, and neither is presented as the other.
// ---------------------------------------------------------------------------
function RequirementsStrip({ task, specs, error }: { task: TaskManifest; specs: RequirementSpec[] | null; error: boolean }) {
  const { t, te } = useI18n();
  const [open, setOpen] = useState(false);
  const latest = specs && specs.length > 0 ? specs[0] : null;
  const summaryLine = error
    ? t("workspace.panels.unavailable")
    : specs === null
      ? t("workspace.requirements.loading")
      : !latest
        ? t("workspace.panels.requirements.none")
        : t("workspace.panels.requirements.state", { state: te("status", latest.state), rev: latest.revision });

  return (
    <div className="rounded-lg border bg-background/60">
      <div className="flex items-center gap-2 px-3 py-2">
        <button
          onClick={() => setOpen((v) => !v)}
          aria-expanded={open}
          className="flex min-w-0 flex-1 items-center gap-2 text-left text-xs text-muted-foreground hover:text-foreground"
        >
          <ChevronDown className={cn("size-3 shrink-0 transition-transform", open && "rotate-180")} />
          <span className="font-medium uppercase tracking-wide">{t("workspace.requirements.label")}</span>
          <span className="min-w-0 truncate">{summaryLine}</span>
          {/* `!error` too: the summary line beside it already goes unavailable on a
              failed refresh and the expanded body is hidden, but this badge counted
              the retained revision's scenarios independently -- so the strip could
              read "Unavailable · 7 scenarios", where the seven belong to the read
              that failed. A count is a claim as much as a sentence is. */}
          {!error && latest && latest.acceptance_scenarios.length > 0 && (
            <span className="shrink-0">· {t("workspace.requirements.scenarioCount", { count: latest.acceptance_scenarios.length })}</span>
          )}
        </button>
        <Link href={`/requirements?task=${encodeURIComponent(task.task_id)}`} className="inline-flex shrink-0 items-center gap-1 text-xs font-medium text-primary hover:underline">
          {t("workspace.panels.openFull")} <ExternalLink className="size-3" />
        </Link>
      </div>
      {open && (
        <div className="border-t px-3 py-3">
          {error ? (
            <p className="text-sm text-muted-foreground">{t("workspace.panels.unavailable")}</p>
          ) : specs === null ? (
            <p className="text-sm text-muted-foreground">{t("workspace.requirements.loading")}</p>
          ) : !latest ? (
            <p className="text-sm text-muted-foreground">{t("workspace.panels.requirements.none")}</p>
          ) : (
            <div className="space-y-3">
              {latest.summary && <p className="text-sm leading-relaxed">{latest.summary}</p>}
              {latest.acceptance_scenarios.length > 0 && (
                <div>
                  <p className="text-xs font-medium text-muted-foreground">{t("workspace.panels.requirements.scenarios")} ({latest.acceptance_scenarios.length})</p>
                  <ul className="mt-1 space-y-1 text-sm">
                    {latest.acceptance_scenarios.map((s) => (
                      <li key={s.scenario_id} className="rounded-md bg-muted/40 px-2 py-1 text-xs leading-relaxed">
                        <span className="font-medium">{s.given}</span> → {s.when} → <span className="font-medium">{s.then}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// The right column: two states, chosen by run state.
//
// Not a tab bar. The column answers "what is happening right now": while a run of
// this task is unsettled it IS that run's output; once it settles it IS what that
// run changed plus the gate standing between it and done. The selection is derived
// in lib/run-column.ts and there is nothing here to click that changes it — a
// stored tab index is exactly what let this surface show a requirements document
// while a run was executing.
//
// "Live" means the run's real output, updating as the existing 2.5s poll updates
// it. No streaming, no websocket, no PTY: the poll already merges fresh records
// into `runs`, so this re-renders with them.
// ---------------------------------------------------------------------------
const COLUMN_TITLE_KEY: Record<RunColumn<ExecutionRun>["kind"], string> = {
  loading: "workspace.column.title.loading",
  unreadable: "workspace.column.title.unreadable",
  none: "workspace.column.title.none",
  live: "workspace.column.title.live",
  liveWithoutRecord: "workspace.column.title.live",
  settled: "workspace.column.title.settled",
};

function RunColumnPane({
  task, column, attention, review, verdict, delivery, deliveryError, deliveryAsked,
}: {
  task: TaskManifest;
  column: RunColumn<ExecutionRun>;
  attention: TaskAttentionView<AttentionItem>;
  review: ReviewStanding | null;
  verdict: GateVerdict | null;
  delivery: Delivery | null;
  deliveryError: boolean;
  deliveryAsked: boolean;
}) {
  const { t } = useI18n();
  return (
    <div className="flex min-h-0 flex-col">
      <div className="flex items-center gap-2 pb-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted-foreground">{t(COLUMN_TITLE_KEY[column.kind])}</span>
        <Link href={`/runs?task=${encodeURIComponent(task.task_id)}`} className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">
          {t("workspace.column.openRuns")} <ExternalLink className="size-3" />
        </Link>
      </div>
      <div className="min-h-0 rounded-lg border p-3">
        {column.kind === "loading" && (
          <p className="text-sm text-muted-foreground">{t("workspace.column.loading")}</p>
        )}
        {column.kind === "unreadable" && (
          /* The read failed, so whether a run is executing right now is not known.
             It must not fall through to a settled snapshot: that would present a
             finished run as the present state of a task that may be running. */
          <p className="text-sm text-muted-foreground">{t("workspace.column.unreadable")}</p>
        )}
        {column.kind === "none" && (
          <p className="text-sm text-muted-foreground">{t("workspace.column.none")}</p>
        )}
        {column.kind === "liveWithoutRecord" && (
          <div className="space-y-3">
            <LiveGateBanner attention={attention} />
            {/* A run IS unsettled and its record is not in hand yet (dispatched a
                moment ago, or the history read failed). Saying so beats both
                silence and a stale snapshot. */}
            <p className="text-sm text-muted-foreground">{t("workspace.column.liveWithoutRecord", { count: column.count })}</p>
          </div>
        )}
        {column.kind === "live" && (
          <div className="space-y-3">
            <LiveGateBanner attention={attention} />
            <LiveRun run={column.run} alsoActive={column.alsoActive} />
          </div>
        )}
        {column.kind === "settled" && (
          <div className="space-y-4">
            {!column.latestCertain && (
              /* The history read was partial, so "this is the latest run" is not a
                 claim it can make. The run below is real; its primacy is not. */
              <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[11px] leading-snug text-amber-700 dark:text-amber-400">
                {t("workspace.column.maybeNotLatest")}
              </p>
            )}
            <SettledRunChanges run={column.run} />
            <GateBlock
              verdict={verdict}
              review={review}
              attention={attention}
              delivery={delivery}
              deliveryError={deliveryError}
              deliveryAsked={deliveryAsked}
            />
          </div>
        )}
      </div>
    </div>
  );
}

/**
 * The one thing the live column still says about human gates.
 *
 * A question the agent raised *while it was running* is the single most
 * time-critical item in this product, and the live column is what a person is
 * looking at when it appears. So presence is surfaced here — never absence: this
 * renders only for items that were actually observed, or to say the attention read
 * failed. It says nothing at all when the read is merely in flight or came back
 * empty, because the live column makes no claim about gates and an empty banner
 * would be read as one. The full list, and the count, live in the settled state.
 */
function LiveGateBanner({ attention }: { attention: TaskAttentionView<AttentionItem> }) {
  const { t } = useI18n();
  const observed = (attention.kind === "open" || attention.kind === "partial") ? attention.items.length : 0;
  if (observed === 0 && attention.kind !== "unreadable") return null;
  return (
    <Link
      href="/attention"
      className="flex items-start gap-2 rounded-lg border border-orange-500/40 bg-orange-500/10 px-2 py-1.5 text-xs leading-snug text-orange-700 hover:bg-orange-500/15 dark:text-orange-400"
    >
      <AlertTriangle className="mt-0.5 size-3.5 shrink-0" />
      <span>
        {attention.kind === "unreadable"
          ? t("workspace.column.gate.unreadableDuringRun")
          : t("workspace.column.gate.duringRun", { count: observed })}
      </span>
    </Link>
  );
}

/**
 * The unsettled run, as the column's whole content: what it was asked to do, and
 * what it is printing. The output pane scrolls itself and follows the tail, so a
 * run that keeps writing does not push the rest of the column away.
 */
function LiveRun({ run, alsoActive }: { run: ExecutionRun; alsoActive: number }) {
  const { t, te } = useI18n();
  const [showPrompt, setShowPrompt] = useState(false);
  const out = (run.stdout_tail || "").trimEnd();
  const err = (run.stderr_tail || "").trimEnd();
  const outRef = useRef<HTMLPreElement>(null);
  // Follow the tail as the poll merges new output in. Length, not the string, so a
  // re-render that changed nothing does not fight a user who scrolled up.
  useEffect(() => {
    const el = outRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [out.length]);

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn("size-2 shrink-0 animate-pulse rounded-full", RUN_TONE[run.state])} />
        <span className="text-sm font-semibold">{t("workspace.column.live.header", { adapter: run.adapter })}</span>
        <span className="rounded bg-muted px-1.5 py-0.5 text-[11px] font-medium text-muted-foreground">{te("runState", run.state)}</span>
        <span className="ml-auto text-[11px] text-muted-foreground">{relTime(run.started_at ?? run.queued_at, t)}</span>
      </div>
      {alsoActive > 0 && (
        /* More than one run of this task has not settled. The column shows the
           newest; saying how many others there are keeps it from reading as "this
           is the only thing happening". */
        <p className="text-[11px] text-muted-foreground">{t("workspace.column.live.alsoActive", { count: alsoActive })}</p>
      )}
      {run.prompt && (
        <div>
          <button onClick={() => setShowPrompt((v) => !v)} className="flex items-center gap-1 text-[11px] font-medium text-muted-foreground hover:text-foreground">
            <ChevronDown className={cn("size-3 transition-transform", showPrompt && "rotate-180")} /> {t("workspace.timeline.run.prompt")}
          </button>
          {showPrompt && <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted/50 p-2 text-xs leading-relaxed">{run.prompt}</pre>}
        </div>
      )}
      <div>
        <p className="mb-1 text-[11px] font-medium text-muted-foreground">{t("workspace.timeline.run.output")}</p>
        {out ? (
          <pre ref={outRef} className="max-h-[28rem] overflow-auto whitespace-pre rounded-lg bg-zinc-950 p-3 text-xs leading-relaxed text-zinc-100">{out}</pre>
        ) : (
          /* Nothing has been captured YET — a queued run has no output at all, and
             that is not the same claim as a finished run that printed nothing. */
          <p className="text-xs text-muted-foreground">{t("workspace.column.live.noOutputYet")}</p>
        )}
      </div>
      {err && (
        <div>
          <p className="mb-1 text-[11px] font-medium text-muted-foreground">{t("workspace.timeline.run.stderr")}</p>
          <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-red-500/5 p-2 text-xs leading-relaxed text-red-700 dark:text-red-300">{err}</pre>
        </div>
      )}
    </div>
  );
}

/**
 * What the settled run changed: its own frozen deliverables snapshot, loaded for
 * this state rather than hidden behind a "show files" click — in the settled column
 * the changes ARE the content.
 *
 * Every honesty rule from the run card is kept, because they are the same facts:
 * `source: "none"` means the list was never recorded and is not guessed from the
 * worktree; `truncated` means "at least these"; `content_available === false`
 * means the paths are still true but nothing can be opened. A failed read is its
 * own sentence and never "no files changed".
 */
function SettledRunChanges({ run }: { run: ExecutionRun }) {
  const { t, te } = useI18n();
  // Keyed by the run it answers for, so a response that arrives after the column
  // has moved to a different run cannot be shown as that run's files — and so the
  // reset to "loading" is a derivation rather than a write inside the effect.
  const [loaded, setLoaded] = useState<{ runId: string; value: RunDeliverables | "failed" } | null>(null);
  // Bumped by the block's retry after a failed read; the effect re-asks for the same run.
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    const ctrl = new AbortController();
    getRunDeliverables(run.run_id, ctrl.signal)
      .then((value) => { if (!ctrl.signal.aborted) setLoaded({ runId: run.run_id, value }); })
      .catch(() => { if (!ctrl.signal.aborted) setLoaded({ runId: run.run_id, value: "failed" }); });
    return () => ctrl.abort();
  }, [run.run_id, attempt]);
  const delivered: RunDeliverables | "loading" | "failed" =
    loaded !== null && loaded.runId === run.run_id ? loaded.value : "loading";
  const failed = run.state === "failed" || run.state === "timed_out" || run.state === "abandoned";

  return (
    <section className="space-y-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className={cn("size-2 shrink-0 rounded-full", RUN_TONE[run.state])} />
        <span className="text-sm font-semibold">{t("workspace.column.changes.header", { adapter: run.adapter })}</span>
        <span className={cn("rounded px-1.5 py-0.5 text-[11px] font-medium", failed ? "bg-red-500/10 text-red-600 dark:text-red-400" : "bg-muted text-muted-foreground")}>{te("runState", run.state)}</span>
        <span className="ml-auto text-[11px] text-muted-foreground">{relTime(run.finished_at ?? run.started_at ?? run.queued_at, t)}</span>
      </div>
      {failed && (run.error_message || run.stderr_tail) && (
        <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-red-500/5 p-2 text-xs leading-relaxed text-red-700 dark:text-red-300">{run.error_message || run.stderr_tail.trimEnd()}</pre>
      )}
      <DeliverablesBlock
        runId={run.run_id}
        delivered={delivered}
        onLoad={() => { setLoaded(null); setAttempt((value) => value + 1); }}
        t={t}
      />
    </section>
  );
}

/**
 * The gate: what stands between this settled run and done.
 *
 * Three facts, each with its own sentence and none allowed to stand in for
 * another — the headline verdict, what is on record about the independent review,
 * and the open attention items themselves (the list the Attention tab used to
 * hold, which is where it belongs: the gate IS the attention). The task's handoff
 * delivery is shown only when it was actually read, because a task that has not
 * reached `done` was never asked, and "no deliverable yet" about a question nobody
 * put is an absence invented out of an unknown.
 */
function GateBlock({ verdict, review, attention, delivery, deliveryError, deliveryAsked }: {
  verdict: GateVerdict | null;
  review: ReviewStanding | null;
  attention: TaskAttentionView<AttentionItem>;
  delivery: Delivery | null;
  deliveryError: boolean;
  deliveryAsked: boolean;
}) {
  const { t, te } = useI18n();
  if (verdict === null || review === null) return null;
  const reviewNotice = reviewStandingNotice(review, (state) => te("runState", state));
  return (
    <section className="space-y-2 rounded-xl border bg-card p-3">
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{t("workspace.column.gate.label")}</p>
      <p className={cn("text-sm font-medium",
        verdict.kind === "actionRequired" ? "text-orange-600 dark:text-orange-400"
          : verdict.kind === "clear" ? "text-emerald-600 dark:text-emerald-400"
            : verdict.kind === "reviewNotAccounted" ? "text-amber-700 dark:text-amber-400"
              : "text-foreground")}>
        {t(GATE_VERDICT_KEY[verdict.kind], verdict.kind === "actionRequired"
          ? { count: verdict.count }
          : verdict.kind === "reviewRunning" ? { adapter: verdict.adapter } : {})}
      </p>
      {verdict.kind === "unknown" && (
        /* Which read could not answer, in its own words. One shared "unavailable"
           is how a failed request came to wear the page cap's explanation. */
        <p className="text-xs text-muted-foreground">{t(GATE_UNKNOWN_REASON_KEY[verdict.reason])}</p>
      )}
      <p className="text-xs text-muted-foreground">{t(reviewNotice.key, reviewNotice.vars)}</p>
      <AttentionList view={attention} />
      {deliveryAsked && (
        deliveryError ? (
          <p className="text-xs text-muted-foreground">{t("workspace.panels.unavailable")}</p>
        ) : !delivery || (delivery.fileCount === 0 && !delivery.commit) ? (
          <p className="text-xs text-muted-foreground">{t("workspace.panels.changes.none")}</p>
        ) : (
          <div className="space-y-1">
            <p className="text-xs text-muted-foreground">
              {t("workspace.panels.changes.delivered", { count: delivery.fileCount })}
              {" · "}
              {delivery.commit ? t("workspace.panels.changes.commit", { sha: delivery.commit.slice(0, 8) }) : t("workspace.panels.changes.uncommitted")}
            </p>
            {/* R4: the handoff's file *paths* were still fetched and then rendered
                nowhere — the changes tab that listed them is gone, and a count is not
                the list. "Recoverable through the timeline card" is a different
                artifact (that card shows a run's worktree snapshot; this is what the
                handoff bound to the task), so the paths get their home back here. */}
            {delivery.files.length > 0 && (
              <>
                <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{t("workspace.panels.changes.files")}</p>
                <ul className="space-y-0.5">
                  {delivery.files.slice(0, HANDOFF_FILES_SHOWN).map((path) => (
                    <li key={path} className="truncate font-mono text-[11px] text-muted-foreground" title={path}>{path}</li>
                  ))}
                </ul>
                {delivery.files.length > HANDOFF_FILES_SHOWN && (
                  <p className="text-[11px] text-muted-foreground">
                    {t("workspace.panels.changes.moreFiles", { count: delivery.files.length - HANDOFF_FILES_SHOWN })}
                  </p>
                )}
              </>
            )}
            {delivery.fileCount > delivery.files.length && (
              /* The count comes from the handoff payload's own `file_count`, which can
                 exceed the paths it recorded. Say so rather than letting the shorter
                 list read as the whole delivery. */
              <p className="text-[11px] text-muted-foreground">{t("workspace.timeline.run.partial")}</p>
            )}
          </div>
        )
      )}
    </section>
  );
}

/**
 * This task's open gate items, from the one task-scoped attention view every other
 * attention claim on the page derives from. Unchanged in substance from the panel
 * it replaces: three different facts get three different sentences, a truncated
 * page shows its items and says the count cannot be trusted, and nothing here ever
 * reports an unknown as "nothing needs you".
 */
function AttentionList({ view }: { view: TaskAttentionView<AttentionItem> }) {
  const { t, te } = useI18n();
  if (view.kind === "loading" || view.kind === "unreadable" || view.kind === "none") {
    return <p className="text-xs text-muted-foreground">{t(TASK_ATTENTION_NOTICE_KEY[view.kind])}</p>;
  }
  return (
    <div className="space-y-1.5">
      {view.kind === "partial" && (
        <p className="text-[11px] text-muted-foreground">{t("workspace.panels.attention.partial")}</p>
      )}
      {view.items.length === 0 ? (
        <p className="text-xs text-muted-foreground">{t("workspace.panels.attention.partialEmpty")}</p>
      ) : (
        <ul className="space-y-1.5">
          {view.items.map((item) => (
            <li key={item.item_id} className="flex items-start gap-2 rounded-md bg-muted/40 px-2 py-1.5">
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0 text-orange-500" />
              <div className="min-w-0">
                <p className="text-sm leading-snug">{item.title}</p>
                <p className="text-[11px] text-muted-foreground">{te("attentionKind", item.kind)} · {te("urgency", item.urgency)}</p>
              </div>
            </li>
          ))}
        </ul>
      )}
      <Link href="/attention" className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">
        {t("workspace.column.gate.answerThem")} <ExternalLink className="size-3" />
      </Link>
    </div>
  );
}

// Dead-simple task creation: three questions, everything else uses project
// defaults (advanced settings live on the full task page).

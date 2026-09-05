"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useTheme } from "next-themes";
import Link from "next/link";
import {
  AlertTriangle,
  ArrowRight,
  ChevronDown,
  CircleCheck,
  ExternalLink,
  Languages,
  Moon,
  Network,
  Plus,
  RefreshCw,
  Search,
  Send,
  Settings,
  Sun,
  Target,
  X,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  ApiError,
  appendTaskEvent,
  createTask,
  fetchDelivery,
  fetchProjectDashboard,
  fetchTaskRecap,
  listOpenAttention,
  listProjects,
  listSpecs,
  listTaskEvents,
  listTasks,
  OPEN_ATTENTION_PAGE_LIMIT,
  type AttentionItem,
  type AttentionKind,
  type Delivery,
  type NextAction,
  type ProjectDashboard,
  type RecapItem,
  type TaskRecap,
  type RequirementSpec,
  type TaskEvent,
  type TaskManifest,
} from "@/lib/control-plane";
import {
  asRunSummary,
  canOpenDeliverable,
  deliverableRawUrl,
  getDeliverableContent,
  getRun,
  getRunDeliverables,
  listActiveRuns,
  listRuns,
  TERMINAL_RUN_STATES,
  type Deliverable,
  type RunDeliverables,
  type ExecutionRun,
  type RunState,
  type RunSummary,
} from "@/lib/execution";
import { ContinueAction } from "@/components/continue-action";
import { listAttention } from "@/lib/attention";
import { mergeRunUpdates, type ContinueSeedFacts } from "@/lib/continue-run";
import {
  RAIL_ATTENTION_NOTICE_KEY,
  RAIL_ORDER,
  RAIL_STALE_NOTICE_KEY,
  railRunIsCurrent,
  attentionReadIsComplete,
  railGroupOf,
  railReadReport,
  railSubtitleOf,
  settleRead,
  type AttentionReach,
  type RailGroup,
  type RailStaleOrigin,
} from "@/lib/rail-group";
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
  type DeliveredResult,
  type GateVerdict,
  type ReviewStanding,
  type RunColumn,
} from "@/lib/run-column";
import {
  FAILED_RUN_STATES,
  nestedFailureCount,
  type RunUnit,
  type UnplacedRun,
} from "@/lib/run-tree";
import {
  ATTENTION_TIMELINE_EVENT_KEY,
  eventsLackKnown,
  runsLackKnown,
  timelineReadOf,
  timelineVerdictOf,
  type TimelineVerdict,
} from "@/lib/timeline";
import { progressBarOf, type ProgressBar } from "@/lib/progress-snapshot";
// The same two decisions the portfolio cockpit takes, imported rather than
// restated. One rule, one implementation: a second copy is a second thing to
// forget to fix, which is how this page came to disagree with that one.
import { taskTrustLineOf, workspaceProjectStripOf,
  type TaskTrustLine, type WorkspaceProjectStrip } from "@/lib/portfolio-bucket";
import { ProgressSnapshotBar } from "@/components/progress-snapshot-bar";
import { useI18n, type Translate, type TranslateEnum } from "@/lib/i18n";
import { cn } from "@/lib/utils";

// The ball is in the human's court whenever a task awaits a decision; a running
// task is the agent's; review is its own stage; terminal/verified is done. The
// grouping itself — including the `unknown` group a capped attention read forces —
// lives in lib/rail-group.ts so it can be tested without a DOM.
const GROUP_DOT: Record<RailGroup, string> = {
  needsYou: "bg-red-500",
  unknown: "bg-amber-500",
  running: "bg-blue-500",
  review: "bg-violet-500",
  done: "bg-emerald-500",
};

const NO_KINDS: ReadonlySet<AttentionKind> = new Set<AttentionKind>();

// Best-effort activity time for ordering: TaskManifest.updated_at does not move
// when events/runs are written, so fold in the latest run's timestamps.
function activityOf(task: TaskManifest, run: RunSummary | null): string {
  const candidates = [task.updated_at, run?.finished_at, run?.started_at, run?.queued_at].filter(Boolean) as string[];
  return candidates.sort().at(-1) ?? task.updated_at;
}

const RUN_TONE: Record<RunState, string> = {
  queued: "bg-amber-500",
  running: "bg-blue-500",
  succeeded: "bg-emerald-500",
  failed: "bg-red-500",
  timed_out: "bg-red-500",
  cancelled: "bg-zinc-400",
  abandoned: "bg-red-500",
};

function relTime(iso: string | null, t: Translate): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return "";
  const mins = Math.max(0, Math.floor((Date.now() - then) / 60000));
  if (mins < 1) return t("workspace.rail.relative.justNow");
  if (mins < 60) return t("workspace.rail.relative.minAgo", { n: mins });
  const hours = Math.floor(mins / 60);
  if (hours < 24) return t("workspace.rail.relative.hourAgo", { n: hours });
  return t("workspace.rail.relative.dayAgo", { n: Math.floor(hours / 24) });
}

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
  const attentionComplete = attentionReadIsComplete(attentionReach);
  // The other reads of this same refresh that failed. Kept apart from
  // `attentionReach` because they are claims about different reads: a rejected run
  // or task list leaves the rail showing an older snapshot, which is worth saying,
  // and is not evidence of anything about the attention read. Folding them into
  // one flag is how "Open attention items could not be read" came to be printed
  // beside an attention read that had succeeded.
  const [staleOrigins, setStaleOrigins] = useState<readonly RailStaleOrigin[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [projectIds, setProjectIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [projectFilter, setProjectFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [showCreate, setShowCreate] = useState(false);
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
        settleRead(() => listTasks()),
        settleRead(() => listProjects()),
        settleRead(() => listOpenAttention()),
        settleRead(() => listRuns({ limit: 500 })),
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
      if (tasksRead.status === "fulfilled") setTasks(tasksRead.value);
      if (projectsRead.status === "fulfilled") {
        setProjectIds(projectsRead.value.items.map((item) => item.project_id));
      }
      if (attentionRead.status === "fulfilled") setAttention(attentionRead.value.items);
      if (runsRead.status === "fulfilled") setRuns(runsRead.value);
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

  // Read the initial selection from the URL (query params, not a dynamic route —
  // the app is statically exported).
  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const task = params.get("task");
    const project = params.get("project");
    if (project) setProjectFilter(project);
    if (task) setSelectedTaskId(task);
  }, []);

  const attentionByTask = useMemo(() => {
    const map = new Map<string, { kinds: Set<AttentionKind>; ask: string | null }>();
    for (const item of attention) {
      const entry = map.get(item.task_id) ?? { kinds: new Set<AttentionKind>(), ask: null };
      entry.kinds.add(item.kind);
      if (!entry.ask) entry.ask = item.title;
      map.set(item.task_id, entry);
    }
    return map;
  }, [attention]);

  const latestRunByTask = useMemo(() => {
    const map = new Map<string, RunSummary>();
    for (const run of runs) if (!map.has(run.task_id)) map.set(run.task_id, run);
    return map;
  }, [runs]);

  const visibleTasks = useMemo(() => {
    const scoped = projectFilter === "all" ? tasks : tasks.filter((task) => task.project_id === projectFilter);
    const active = scoped.filter((task) => !task.archived_at);
    const needle = query.trim().toLowerCase();
    return needle ? active.filter((task) => task.title.toLowerCase().includes(needle)) : active;
  }, [tasks, projectFilter, query]);

  const grouped = useMemo(() => {
    const groups: Record<RailGroup, Array<{ task: TaskManifest; ask: string | null; run: RunSummary | null; activity: string }>> = {
      needsYou: [], unknown: [], running: [], review: [], done: [],
    };
    // Dropped in one place rather than at each render site, so the state dot, the
    // relative time and the ordering all stop using a run the page can no longer
    // vouch for. `activityOf` falls back to `task.updated_at`, which is current.
    const runsAreCurrent = railRunIsCurrent(staleOrigins);
    for (const task of visibleTasks) {
      const info = attentionByTask.get(task.task_id);
      const run = runsAreCurrent ? latestRunByTask.get(task.task_id) ?? null : null;
      const group = railGroupOf({ task, kinds: info?.kinds ?? NO_KINDS, attentionComplete });
      groups[group].push({ task, ask: info?.ask ?? null, run, activity: activityOf(task, run) });
    }
    // Newest activity first within each group.
    for (const group of RAIL_ORDER) {
      groups[group].sort((a, b) => b.activity.localeCompare(a.activity));
    }
    return groups;
  }, [visibleTasks, attentionByTask, latestRunByTask, attentionComplete, staleOrigins]);

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

  // Drop a selection the project filter now excludes, so the main view never
  // shows a task hidden from the rail (notes would post to the wrong project).
  useEffect(() => {
    if (projectFilter !== "all" && selectedTask && selectedTask.project_id !== projectFilter) {
      setSelectedTaskId(null);
    }
  }, [projectFilter, selectedTask]);

  const selectTask = useCallback((task: TaskManifest) => {
    setSelectedTaskId(task.task_id);
    const params = new URLSearchParams(window.location.search);
    params.set("project", task.project_id);
    params.set("task", task.task_id);
    window.history.replaceState(null, "", `${window.location.pathname}?${params}`);
  }, []);

  return (
    <div className="flex h-screen overflow-hidden bg-muted/20">
      <TaskRail
        groups={grouped}
        attentionReach={attentionReach}
        staleOrigins={staleOrigins}
        projectIds={projectIds}
        projectFilter={projectFilter}
        onProjectFilter={setProjectFilter}
        query={query}
        onQuery={setQuery}
        selectedTaskId={selectedTaskId}
        onSelect={selectTask}
        onNewTask={() => setShowCreate(true)}
        onRefresh={load}
        loading={loading}
      />
      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {error && <div className="m-4 shrink-0 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{t("workspace.errors.loadFailed", { error })}</div>}
        {selectedTask ? (
          <TaskWorkspace
            key={selectedTask.task_id}
            refreshNonce={refreshNonce}
            task={selectedTask}
            // What this page's own reads have seen of the selected task. The child's
            // reads and these overlap in time — `load` bumps `refreshNonce` before its
            // four reads settle — so the rail can learn of a gate or a run before the
            // child's reads do, and used to render "Needs you" beside a timeline
            // claiming to be complete without that event. Passed as evidence of
            // existence only; absence from a capped global page says nothing.
            railAttention={selectedTaskAttention}
            railRuns={selectedTaskRuns}
            onTaskMayHaveChanged={load}
          />
        ) : (
          <EmptyWorkspace hasTasks={tasks.length > 0} />
        )}
      </main>
      {showCreate && (
        <CreateTaskDialog
          projectIds={projectIds}
          defaultProject={projectFilter !== "all" ? projectFilter : (projectIds[0] ?? "")}
          onClose={() => setShowCreate(false)}
          onCreated={(task) => { setTasks((current) => [task, ...current]); setShowCreate(false); selectTask(task); }}
        />
      )}
    </div>
  );
}

function TaskRail({
  groups, attentionReach, staleOrigins, projectIds, projectFilter, onProjectFilter, query, onQuery, selectedTaskId, onSelect, onNewTask, onRefresh, loading,
}: {
  groups: Record<RailGroup, Array<{ task: TaskManifest; ask: string | null; run: RunSummary | null; activity: string }>>;
  attentionReach: AttentionReach;
  staleOrigins: readonly RailStaleOrigin[];
  projectIds: string[]; projectFilter: string; onProjectFilter: (value: string) => void;
  query: string; onQuery: (value: string) => void;
  selectedTaskId: string | null; onSelect: (task: TaskManifest) => void;
  onNewTask: () => void; onRefresh: () => void; loading: boolean;
}) {
  const { t, te } = useI18n();
  const { resolvedTheme, setTheme } = useTheme();
  const { locale, toggleLocale } = useI18n();
  const total = RAIL_ORDER.reduce((sum, group) => sum + groups[group].length, 0);

  return (
    <aside className="flex h-screen w-72 shrink-0 flex-col border-r bg-sidebar lg:w-80">
      {/* Brand + global toggles */}
      <div className="flex items-center justify-between px-4 py-3">
        <Link href="/workspace" className="flex items-center gap-2">
          <span className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground"><Network className="size-4" /></span>
          <span className="text-sm font-bold">{t("common.brand.name")}</span>
        </Link>
        <div className="flex items-center gap-0.5">
          <button className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground" onClick={toggleLocale} aria-label={t("common.action.toggleLanguage")} title={locale === "zh" ? "EN" : "中文"}><Languages className="size-4" /></button>
          <button className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground" onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")} aria-label={t("common.action.toggleTheme")}><Sun className="hidden size-4 dark:block" /><Moon className="size-4 dark:hidden" /></button>
          <Link href="/settings" className="rounded-md p-1.5 text-muted-foreground hover:bg-accent hover:text-foreground" aria-label="Settings"><Settings className="size-4" /></Link>
        </div>
      </div>

      {/* Project filter + new task */}
      <div className="space-y-2 px-4 pb-3">
        <div className="flex items-center gap-2">
          <select value={projectFilter} onChange={(e) => onProjectFilter(e.target.value)} className="h-8 min-w-0 flex-1 rounded-lg border bg-background px-2 text-xs" aria-label={t("workspace.rail.allProjects")}>
            <option value="all">{t("workspace.rail.allProjects")}</option>
            {projectIds.map((id) => <option key={id} value={id}>{id}</option>)}
          </select>
          <button onClick={onRefresh} disabled={loading} className="rounded-lg border p-1.5 text-muted-foreground hover:bg-accent" aria-label="Refresh"><RefreshCw className={cn("size-3.5", loading && "animate-spin")} /></button>
        </div>
        <Button size="sm" className="w-full" onClick={onNewTask}><Plus className="size-4" /> {t("workspace.rail.newTask")}</Button>
        <div className="relative">
          <Search className="pointer-events-none absolute left-2 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
          <input value={query} onChange={(e) => onQuery(e.target.value)} placeholder={t("workspace.rail.search")} className="h-8 w-full rounded-lg border bg-background pl-7 pr-2 text-xs outline-none focus:border-ring" />
        </div>
      </div>

      {/* Grouped task list */}
      <div className="min-h-0 flex-1 overflow-y-auto px-2 pb-4">
        {/* Why the rail says this out loud rather than quietly grouping anyway: the
            groups below "needs you" are a claim that nothing is waiting, and once
            the global attention page is capped or unreadable it cannot support
            that claim for a task it does not mention. Those tasks are in
            "unknown", and this line is the reason — the actual reason: the message
            names which of the three non-complete outcomes occurred, because a
            failed read wearing the cap's explanation is a confident falsehood. */}
        {attentionReach !== "complete" && total > 0 && (
          <p className="mx-2 mb-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[11px] leading-snug text-amber-700 dark:text-amber-400">
            {t(RAIL_ATTENTION_NOTICE_KEY[attentionReach], { limit: OPEN_ATTENTION_PAGE_LIMIT })}
          </p>
        )}
        {/* A sibling read of the same refresh failed. Its own sentence, about its
            own read: what it costs is a stale rail (or, on a first load, an empty
            one — which is why this is not gated on there being tasks: "No tasks
            yet" below would otherwise be the only thing said about a task list
            that could not be read). It deliberately claims nothing about the
            attention read, whose reach is settled above from its own outcome. */}
        {staleOrigins.map((origin) => (
          <p key={origin} className="mx-2 mb-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-2 py-1.5 text-[11px] leading-snug text-amber-700 dark:text-amber-400">
            {t(RAIL_STALE_NOTICE_KEY[origin])}
          </p>
        ))}
        {total === 0 && (
          <p className="px-2 py-6 text-center text-xs text-muted-foreground">{query.trim() ? t("workspace.rail.noMatch") : t("workspace.rail.empty")}</p>
        )}
        {RAIL_ORDER.map((group) => {
          const items = groups[group];
          if (items.length === 0) return null;
          return (
            <section key={group} className="mb-3">
              <div className="flex items-center gap-2 px-2 py-1.5">
                <span className={cn("size-1.5 rounded-full", GROUP_DOT[group])} />
                <span className="text-[11px] font-semibold uppercase tracking-wide text-muted-foreground">{t(`workspace.rail.groups.${group}`)}</span>
                <span className="text-[11px] text-muted-foreground">{items.length}</span>
              </div>
              <ul className="space-y-0.5">
                {items.map(({ task, ask, run, activity }) => {
                  const selected = task.task_id === selectedTaskId;
                  // Never "the lifecycle state, where the ask should have been":
                  // in the unknown group that substitution is what made a task
                  // with an off-page approval read as a calm "running".
                  const subtitle = railSubtitleOf(group, ask);
                  const sub =
                    subtitle.kind === "ask"
                      ? subtitle.text
                      : subtitle.kind === "stateWithUnknownAttention"
                        ? t("workspace.rail.unknownSubtitle", { state: te("taskState", task.state) })
                        : te("taskState", task.state);
                  return (
                    <li key={task.task_id}>
                      <button
                        onClick={() => onSelect(task)}
                        className={cn("w-full rounded-lg px-2 py-2 text-left transition-colors", selected ? "bg-primary/10 ring-1 ring-primary/30" : "hover:bg-accent")}
                      >
                        <div className="flex items-center gap-2">
                          <span className={cn("size-2 shrink-0 rounded-full", GROUP_DOT[group])} />
                          <span className="min-w-0 flex-1 truncate text-sm font-medium">{task.title}</span>
                          {run && <span className={cn("size-1.5 shrink-0 rounded-full", RUN_TONE[run.state])} title={run.state} />}
                        </div>
                        <div className="mt-0.5 flex items-center justify-between gap-2 pl-4">
                          <span className="min-w-0 truncate text-[11px] text-muted-foreground">{sub}</span>
                          <span className="shrink-0 text-[11px] text-muted-foreground">{relTime(activity, t)}</span>
                        </div>
                      </button>
                    </li>
                  );
                })}
              </ul>
            </section>
          );
        })}
      </div>
    </aside>
  );
}

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
const TASK_ATTENTION_LIMIT = 500;

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
function TaskWorkspace({ task, refreshNonce, railAttention, railRuns, onTaskMayHaveChanged }: { task: TaskManifest; refreshNonce: number; railAttention: readonly AttentionItem[]; railRuns: readonly RunSummary[]; onTaskMayHaveChanged: () => void }) {
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
  // Whether that page came back at its cap — which is to say, whether this read can
  // be confirmed complete. It cannot distinguish a truncated history from one of
  // exactly `RUN_HISTORY_LIMIT` runs, so it is pessimistic by one and never optimistic;
  // what reads it must therefore not describe it as a failure (CR-25). A history that
  // cannot be confirmed complete cannot say which run is the latest, nor that no review
  // run exists for a worker run — both are claims about the whole list. Tracked
  // separately from `runsError` because "some
  // records could not be read" and "there are more records than were read" are
  // different facts about the same read.
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
  const [taskAttention, setTaskAttention] = useState<AttentionItem[] | null>(null);
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
    return listRuns({ task_id: task.task_id, limit: RUN_HISTORY_LIMIT }, signal)
      .then((summaries) =>
        Promise.all(summaries.map((summary) => getRun(summary.run_id, signal).catch(() => null)))
          .then((details) => {
            if (stale()) return;
            const loaded = details.filter((run): run is ExecutionRun => run !== null);
            setRuns(loaded);
            setRunsError(loaded.length !== summaries.length);
            // At the cap the page is "at least these runs", so nothing derived from
            // it may claim to be about all of them.
            setRunsTruncated(summaries.length >= RUN_HISTORY_LIMIT);
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
    return listAttention(
      { task_id: task.task_id, state: "open", limit: TASK_ATTENTION_LIMIT }, signal,
    )
      .then((items) => {
        if (stale()) return;
        setTaskAttention(items);
        setTaskAttentionError(false);
        setTaskAttentionTruncated(items.length >= TASK_ATTENTION_LIMIT);
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

  // What the timeline may say, decided here because this is where the read facts
  // live, and handed down as one value. `TimelineColumn` gets the verdict and not the
  // events, the runs or the error bits: the first fix for this passed the raw facts
  // and two helpers, and the column then said "no activity yet" while a read was
  // still in flight, ignored the run cap, and rendered "some of this could not be
  // read" beside "could not be read". Nothing it cannot see can it get wrong again.
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

  const timeline = useMemo<TimelineVerdict<TaskEvent, ExecutionRun>>(
    () => timelineVerdictOf(
      timelineReadOf(events, { failed: eventsError, capped: false, behind: eventsGap }),
      timelineReadOf(runs, { failed: runsError, capped: runsTruncated, behind: runsGap }),
    ),
    [events, eventsError, eventsGap, runs, runsError, runsGap, runsTruncated],
  );

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
    listAttention({ task_id: task.task_id, run_id: gateRunId, limit: REVIEW_GATE_LIMIT }, ctrl.signal)
      .then((items) => {
        if (ctrl.signal.aborted) return;
        setReviewGate(items);
        setReviewGateFor(gateRunId);
        setReviewGateError(false);
        setReviewGateTruncated(items.length >= REVIEW_GATE_LIMIT);
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

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ProjectStrip
        name={dashboard?.name ?? null}
        strip={dashboard ? workspaceProjectStripOf({
          readFailed: dashError,
          truncated: dashboard.truncated,
          done: dashboard.tasks.filter((it) => it.state === "done").length,
          pageSize: dashboard.tasks.length,
          action: dashboard.next_action.action,
          partial: dashboard.next_action.partial,
          attentionCount: dashboard.next_action.attention_count,
          attention: dashboard.next_action.attention,
          title: dashboard.next_action.title,
        }) : null}
        bar={progressBarOf({
          snapshot: dashboard?.progress_snapshot,
          dashboardLoaded: dashboard !== null,
          dashboardReadFailed: dashError,
        })}
        snapshotSource={dashboard?.progress_snapshot?.source ?? undefined}
      />
      {/* The Resume Brief and the action that acts on it, as one block. */}
      <div className="border-b bg-muted/20">
        <ResumeBrief recap={recap} recapError={recapError} t={t} te={te} />
        <ContinueAction
          task={task}
          seed={continueSeed}
          activeRunCount={activeRunCount}
          openAttentionCount={openAttentionCount}
          followUpArmed={followUpArmed}
          onCreated={(run) => {
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
      <OutcomeSummary task={task} trustLine={taskTrustLineOf({ readFailed: dashError, hasTask: dashTask !== null, hasSpec: dashTask?.has_spec ?? false })} next={dashError ? null : dashTask?.next_action ?? null} blocker={blockerClaim} delivery={delivery} deliveryError={deliveryError} deliveryAsked={task.state === "done"} latestSuccess={latestSuccess} />
      {/* Requirements: the task's contract, not "what is happening now", so it is
          NOT in the right column any more. It sits here, directly under the
          outcome header whose Trust cell scores the very criteria it lists, as a
          collapsed strip that opens in place. Nothing was dropped — the full spec,
          its state, its revision and its acceptance scenarios are all still here,
          plus the deep link to the Requirements page. */}
      <RequirementsStrip task={task} specs={specs} error={specsError} />
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        {/* Center: the unified, chat-like task timeline + message box. */}
        <TimelineColumn taskId={task.task_id} verdict={timeline} specs={specs} runHistoryLimit={RUN_HISTORY_LIMIT} onPosted={(eventId) => { setPostedEventId(eventId); loadEvents(); loadRecap(); }} />
        {/* Right: the run. While one is unsettled the column IS its output; once it
            settles the column IS what it changed plus the gate before done. Which
            of the two is decided by run state alone (lib/run-column.ts). */}
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
      </div>
    </div>
  );
}

// The unified task timeline: the whole task process as one readable, chat-like
// stream (requirement drafting/approval, stage changes, runs, review, handoff,
// and the human's own notes), with a message box to continue or correct course.
//
// S2: runs appear as work units, not as process launches — the interleave, the
// ordering and the run tie-break are `timelineItemsOf` in lib/timeline.ts, together
// with the decision of what this column may say about completeness. What arrives here
// is a `TimelineVerdict`: entries to render, or one sentence and the reason for it.
function TimelineColumn({ taskId, verdict, specs, runHistoryLimit, onPosted }: { taskId: string; verdict: TimelineVerdict<TaskEvent, ExecutionRun>; specs: RequirementSpec[] | null; runHistoryLimit: number; onPosted: (eventId: string) => void }) {
  const { t, te } = useI18n();
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const specsById = useMemo(() => {
    const map = new Map<string, RequirementSpec>();
    for (const spec of specs ?? []) map.set(spec.spec_id, spec);
    return map;
  }, [specs]);

  // Keep the newest entry in view as the stream grows. There are no entries to grow
  // on any other branch of the verdict, so the count is zero there.
  const entryCount = verdict.show === "entries" ? verdict.items.length : 0;
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [entryCount]);

  const send = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      // The written record, not just an acknowledgement: its id is what lets the
      // timeline say it is missing an entry until the read that fetches it lands.
      const written = await appendTaskEvent(taskId, "user_message", { text });
      setDraft("");
      onPosted(written.event_id);
    } catch {
      // Surfaced by leaving the draft in place; a full error toast is later work.
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto p-5">
        {/* One branch per thing the timeline may say, and the "may not be the whole
            history" notice is inside the branch that has entries — the only branch it
            can apply to. Rendered above them, because a caveat under a list nobody
            scrolls is a caveat nobody reads. */}
        {verdict.show !== "entries" ? (
          <p className="py-8 text-center text-sm text-muted-foreground">
            {verdict.show === "loading" ? "…"
              : verdict.show === "unknown" ? t("workspace.timeline.unknown")
              : verdict.show === "missing" ? t("workspace.timeline.notShownYet")
              : t("workspace.timeline.empty")}
          </p>
        ) : (
          <>
            {verdict.partial !== null && (
              <p className="pb-2 text-center text-xs text-muted-foreground">
                {t(verdict.partial === "loading" ? "workspace.timeline.stillChecking"
                  : verdict.partial === "missing" ? "workspace.timeline.notShownYet"
                  : "workspace.timeline.partial")}
              </p>
            )}
            {verdict.items.map((item) => item.kind === "event"
              ? <TimelineEntry key={item.key} event={item.event} spec={specForEvent(item.event, specsById)} t={t} te={te} />
              : item.kind === "unit"
                ? <RunWorkUnit key={item.key} unit={item.unit} t={t} te={te} />
                : <UnplacedRunEntry key={item.key} unplaced={item.unplaced} runHistoryLimit={runHistoryLimit} t={t} te={te} />)}
          </>
        )}
      </div>
      <div className="border-t bg-background p-3">
        <div className="flex items-end gap-2">
          <textarea
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); void send(); } }}
            rows={1}
            placeholder={t("workspace.timeline.inputPlaceholder")}
            className="max-h-40 min-h-[38px] flex-1 resize-y rounded-lg border bg-background px-3 py-2 text-sm outline-none focus:border-ring"
          />
          <Button size="icon" onClick={() => void send()} disabled={sending || !draft.trim()} aria-label={t("workspace.timeline.send")}>
            {sending ? <RefreshCw className="size-4 animate-spin" /> : <Send className="size-4" />}
          </Button>
        </div>
      </div>
    </div>
  );
}

const MILESTONE_STATES = new Set([
  "backlog", "requirements", "design", "planned", "running", "review", "verified", "done", "blocked", "failed", "cancelled",
]);

function lookupMilestone(state: string, t: Translate): string | null {
  return MILESTONE_STATES.has(state) ? t(`workspace.timeline.milestone.${state}`) : null;
}

function timelineView(event: TaskEvent, t: Translate, te: TranslateEnum): { who: "you" | "agent" | "system"; title: string; detail: string | null } {
  const p = event.payload as Record<string, unknown>;
  const adapter = String(p.adapter ?? "");
  const type = event.event_type;
  if (type === "user_message") return { who: "you", title: t("workspace.timeline.you"), detail: String(p.text ?? "") };
  if (type === "task_created") return { who: "system", title: t("workspace.timeline.events.taskCreated"), detail: null };
  if (type === "state_changed") {
    const to = String(p.to ?? "");
    // Plain-language milestone by the state reached; fall back to the localized
    // state name for anything unmapped. A human actor reads as "you".
    const milestone = lookupMilestone(to, t) ?? te("taskState", to);
    return { who: event.actor === "user" ? "you" : "system", title: milestone, detail: p.reason ? String(p.reason) : null };
  }
  if (type === "spec.created") return { who: "system", title: t("workspace.timeline.events.specCreated"), detail: null };
  if (type === "spec.approved") return { who: "you", title: t("workspace.timeline.events.specApproved"), detail: p.reason ? String(p.reason) : null };
  if (type === "spec.rejected") return { who: "you", title: t("workspace.timeline.events.specRejected"), detail: p.reason ? String(p.reason) : null };
  if (type === "run.queued") return { who: "agent", title: t("workspace.timeline.events.runQueued", { adapter }), detail: null };
  if (type === "run.started") return { who: "agent", title: t("workspace.timeline.events.runStarted", { adapter }), detail: null };
  if (type === "run.succeeded") return { who: "agent", title: t("workspace.timeline.events.runSucceeded", { adapter }), detail: null };
  if (type === "run.failed") return { who: "agent", title: t("workspace.timeline.events.runFailed", { adapter }), detail: p.error_message ? String(p.error_message) : null };
  if (type.startsWith("attention.")) {
    // One sentence per event (CR-27). An unmapped `attention.*` name falls through to
    // the humanised-type fallback below rather than borrowing "Needs you" — a name this
    // build does not know is a fact about this build, not a thing the user must do.
    const key = ATTENTION_TIMELINE_EVENT_KEY[type];
    if (key !== undefined) return { who: "system", title: t(key), detail: p.title ? String(p.title) : null };
  }
  if (type.startsWith("handoff.")) return { who: "system", title: t("workspace.timeline.events.handoff"), detail: p.commit ? String(p.commit).slice(0, 8) : null };
  // Fallback: humanized type.
  return { who: "system", title: type.replaceAll(".", " ").replaceAll("_", " "), detail: null };
}

const WHO_DOT: Record<"you" | "agent" | "system", string> = { you: "bg-primary", agent: "bg-blue-500", system: "bg-zinc-400" };

// The spec a spec.created/spec.approved event refers to (by spec_id), so the
// requirements step can show what was actually specified — not just a label.
function specForEvent(event: TaskEvent, specsById: Map<string, RequirementSpec>): RequirementSpec | null {
  if (!event.event_type.startsWith("spec.")) return null;
  const id = String((event.payload as Record<string, unknown>).spec_id ?? "");
  return specsById.get(id) ?? null;
}

function TimelineEntry({ event, spec, t, te }: { event: TaskEvent; spec: RequirementSpec | null; t: Translate; te: TranslateEnum }) {
  const view = timelineView(event, t, te);
  const isYou = view.who === "you";
  return (
    <div className={cn("flex gap-2.5", isYou && "flex-row-reverse")}>
      <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", WHO_DOT[view.who])} />
      <div className={cn("max-w-[85%] rounded-xl border px-3 py-2", isYou ? "bg-primary/5" : "bg-card")}>
        <div className={cn("flex items-baseline gap-2", isYou && "flex-row-reverse")}>
          <span className="text-xs font-semibold">{view.title}</span>
          <span className="text-[10px] text-muted-foreground">{relTime(event.created_at, t)}</span>
        </div>
        {view.detail && <p className={cn("mt-0.5 whitespace-pre-wrap text-sm leading-relaxed", isYou && "text-right")}>{view.detail}</p>}
        {spec && <SpecBlock spec={spec} t={t} />}
      </div>
    </div>
  );
}

// What the requirements step actually produced: summary, requirements, and the
// given/when/then acceptance scenarios — collapsed by default.
function SpecBlock({ spec, t }: { spec: RequirementSpec; t: Translate }) {
  const [open, setOpen] = useState(false);
  const reqs = [...spec.functional, ...spec.non_functional];
  return (
    <div className="mt-1.5">
      <button onClick={() => setOpen((v) => !v)} className="flex items-center gap-1 text-[11px] font-medium text-primary hover:underline">
        <ChevronDown className={cn("size-3 transition-transform", open && "rotate-180")} /> {open ? t("workspace.timeline.spec.hide") : t("workspace.timeline.spec.view")}
      </button>
      {open && (
        <div className="mt-1.5 space-y-2 rounded-lg bg-muted/40 p-2 text-xs leading-relaxed">
          {spec.summary && (
            <div>
              <p className="font-medium text-muted-foreground">{t("workspace.timeline.spec.summary")}</p>
              <p className="mt-0.5">{spec.summary}</p>
            </div>
          )}
          {reqs.length > 0 && (
            <div>
              <p className="font-medium text-muted-foreground">{t("workspace.timeline.spec.requirements")} ({reqs.length})</p>
              <ul className="mt-0.5 list-disc space-y-0.5 pl-4">
                {reqs.map((r) => <li key={r.requirement_id}>{r.statement}</li>)}
              </ul>
            </div>
          )}
          {spec.acceptance_scenarios.length > 0 && (
            <div>
              <p className="font-medium text-muted-foreground">{t("workspace.timeline.spec.scenarios")} ({spec.acceptance_scenarios.length})</p>
              <ul className="mt-0.5 space-y-0.5">
                {spec.acceptance_scenarios.map((s) => (
                  <li key={s.scenario_id} className="rounded bg-background/60 px-2 py-1">
                    <span className="font-medium">{s.given}</span> → {s.when} → <span className="font-medium">{s.then}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// Top strip: project-level orientation, so switching tasks never loses "where
// is this whole project and what's its next move?".
// Takes the decided view and the two things that are not decisions -- the project's
// name and its snapshot bar. **Not the dashboard.** A reviewer's point that no source
// scan can answer: while the response is in scope, nothing stops a later edit reading
// a raw count in another branch, and the test can only look for spellings it has
// thought of. With the response out of scope that bypass is not a thing you can
// write, which is a different kind of guarantee from a test that looks for it.
function ProjectStrip(
  { name, strip, bar, snapshotSource }: {
    name: string | null;
    strip: WorkspaceProjectStrip | null;
    bar: ProgressBar;
    snapshotSource: string | undefined;
  },
) {
  const { t, te } = useI18n();
  if (!strip || name === null) {
    return <div className="border-b bg-muted/30 px-5 py-2 text-xs text-muted-foreground">{bar.kind === "unknown" ? t("workspace.project.unavailable") : "…"}</div>;
  }
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b bg-muted/30 px-5 py-2 text-xs">
      <span className="inline-flex items-center gap-1.5 font-semibold"><Network className="size-3.5 text-primary" />{name}</span>
      {strip.count.kind === "exact" ? (
        <span className="text-muted-foreground">{t("workspace.project.strip", { done: strip.count.done, total: strip.count.total })}</span>
      ) : strip.count.kind === "atLeast" ? (
        <span className="text-muted-foreground">{t("workspace.project.stripPartial", { done: strip.count.done })}</span>
      ) : null}
      {/* The bar replaces the verified percentage that used to be in the line above.
          That number came from `metrics.verified_pct`, which reads a criterion as
          verified from any historical successful run of its task — so new criteria
          added by a requirements change are reported as verified by a run that
          predates them. Rewording it could not have fixed it; the source had to go. */}
      <ProgressSnapshotBar bar={bar} source={snapshotSource} compact />
      <span className="text-muted-foreground/50">·</span>
      {strip.next.kind === "unknown" ? (
        <span className="inline-flex items-center gap-1 text-muted-foreground">
          {t(strip.claim === "stale" ? "workspace.project.nextStale" : "workspace.project.nextPartial")}
        </span>
      ) : strip.next.kind === "allClear" ? (
        <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400"><CircleCheck className="size-3.5" />{t("workspace.project.allDone")}</span>
      ) : (
        <span className={cn("inline-flex items-center gap-1", strip.next.attention ? "text-orange-600 dark:text-orange-400" : "text-foreground")}>
          <ArrowRight className="size-3.5" />
          <span className="font-medium">{t("workspace.project.next")}:</span> {te("nextAction", strip.next.action)}
          {strip.next.title && <span className="text-muted-foreground">{t("workspace.project.forTask", { title: strip.next.title })}</span>}
        </span>
      )}
      {strip.attention.kind !== "hidden" && (
        <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-orange-500/10 px-2 py-0.5 font-medium text-orange-600 dark:text-orange-400">
          <AlertTriangle className="size-3" />
          {strip.attention.kind === "atLeast"
            ? t("workspace.project.attentionAtLeast", { count: strip.attention.count })
            : t("workspace.project.attention", { count: strip.attention.count })}
        </span>
      )}
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
    <header className="border-b bg-card px-5 py-4">
      <div className="flex items-start justify-between gap-3">
        <h1 className="text-xl font-bold leading-snug">{task.title}</h1>
        <span className={cn("inline-flex shrink-0 items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium",
          done ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400" : stuck ? "bg-orange-500/10 text-orange-600 dark:text-orange-400" : "bg-muted text-muted-foreground")}>
          {done && <CircleCheck className="size-3.5" />}{te("taskState", task.state)}
        </span>
      </div>

      <div className="mt-3 grid gap-3 md:grid-cols-3">
        {/* What it did — the concrete outcome, not a log. */}
        <div className="rounded-xl border bg-muted/30 p-3 md:col-span-2">
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
function RunWorkUnit({ unit, t, te }: { unit: RunUnit<ExecutionRun>; t: Translate; te: TranslateEnum }) {
  const failedNested = nestedFailureCount(unit);
  return (
    <div className="space-y-2">
      <RunCard run={unit.run} t={t} te={te} />
      {unit.nested.length > 0 && (
        <div className="ml-4 space-y-2 border-l-2 pl-3">
          <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
            {t("workspace.timeline.nested.label")}
          </p>
          {failedNested > 0 && (
            <p className="text-[11px] font-medium text-red-600 dark:text-red-400">
              {t("workspace.timeline.nested.failed", { count: failedNested })}
            </p>
          )}
          {unit.nested.map((run) => <RunCard key={run.run_id} run={run} t={t} te={te} />)}
        </div>
      )}
    </div>
  );
}

/** The sentence for each reason a run could not be nested; one per reason, no sharing. */
const UNPLACED_RUN_KEY: Record<UnplacedRun<ExecutionRun>["because"], string> = {
  parentNotLoaded: "workspace.timeline.nested.unplacedParentNotLoaded",
  namesNoRun: "workspace.timeline.nested.unplacedNamesNoRun",
  namesItself: "workspace.timeline.nested.unplacedNamesItself",
  chainNotRooted: "workspace.timeline.nested.unplacedChainNotRooted",
};

// A run the tree could not place, shown in full with the reason it stands alone.
// This is the whole point of `unplaceable`: the run page is capped and partially
// readable, so "the run this reviews is not here" is ordinary — and it means the
// placement is unknown, not that the run does not exist. Rendering it at the top
// level *without* the reason would be the other error, quietly promoting a review to
// a work unit of its own.
function UnplacedRunEntry(
  { unplaced, runHistoryLimit, t, te }:
  { unplaced: UnplacedRun<ExecutionRun>; runHistoryLimit: number; t: Translate; te: TranslateEnum },
) {
  return (
    <div className="space-y-1 rounded-xl border border-dashed p-2">
      <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">
        {t("workspace.timeline.nested.unplacedTitle")}
      </p>
      <p className="text-[11px] leading-snug text-muted-foreground">
        {t(UNPLACED_RUN_KEY[unplaced.because], { limit: runHistoryLimit })}
      </p>
      <RunCard run={unplaced.run} t={t} te={te} />
    </div>
  );
}

// A run rendered as the substance it is: the prompt the agent got, its actual
// terminal output, the failure (if any), and the files it changed.
function RunCard({ run, t, te }: { run: ExecutionRun; t: Translate; te: TranslateEnum }) {
  const [showPrompt, setShowPrompt] = useState(false);
  const [expanded, setExpanded] = useState(false);
  // Keep the whole response, not just `files`: whether the list is this run's
  // frozen snapshot, the still-moving worktree, or unknown changes what we may
  // honestly claim about it.
  const [delivered, setDelivered] = useState<RunDeliverables | "loading" | null>(null);
  // One definition of "failed", shared with the work-unit header that reports a
  // nested failure — two lists would let a card and its parent disagree about
  // whether the run under it failed.
  const failed = FAILED_RUN_STATES.has(run.state);
  const isReview = String((run.result_metadata as Record<string, unknown>)?.agora_role ?? "") === "review";
  const out = (run.stdout_tail || "").trimEnd();
  const err = (run.stderr_tail || "").trimEnd();
  const longOutput = out.length > 600;
  const files = delivered && delivered !== "loading" ? delivered.files : [];

  const loadFiles = async () => {
    setDelivered("loading");
    try { setDelivered(await getRunDeliverables(run.run_id)); }
    catch { setDelivered({ run_id: run.run_id, workspace: "", files: [], source: "none" }); }
  };

  return (
    <div className="rounded-xl border bg-card">
      <div className="flex flex-wrap items-center gap-2 border-b px-3 py-2">
        <span className={cn("size-2 shrink-0 rounded-full", RUN_TONE[run.state])} />
        <span className="text-sm font-semibold">{t(isReview ? "workspace.timeline.run.titleReview" : "workspace.timeline.run.title", { adapter: run.adapter })}</span>
        <span className={cn("rounded px-1.5 py-0.5 text-[11px] font-medium", failed ? "bg-red-500/10 text-red-600 dark:text-red-400" : "bg-muted text-muted-foreground")}>{te("runState", run.state)}</span>
        {run.exit_code !== null && <span className="text-[11px] text-muted-foreground">{t("workspace.timeline.run.exit", { code: run.exit_code })}</span>}
        <span className="ml-auto text-[11px] text-muted-foreground">{relTime(run.finished_at ?? run.started_at ?? run.queued_at, t)}</span>
      </div>

      <div className="space-y-2 p-3">
        {/* What the agent was asked to do. */}
        {run.prompt && (
          <div>
            <button onClick={() => setShowPrompt((v) => !v)} className="flex items-center gap-1 text-[11px] font-medium text-muted-foreground hover:text-foreground">
              <ChevronDown className={cn("size-3 transition-transform", showPrompt && "rotate-180")} /> {t("workspace.timeline.run.prompt")}
            </button>
            {showPrompt && <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-muted/50 p-2 text-xs leading-relaxed">{run.prompt}</pre>}
          </div>
        )}

        {/* The actual terminal output. */}
        <div>
          <p className="mb-1 text-[11px] font-medium text-muted-foreground">{t("workspace.timeline.run.output")}</p>
          {out ? (
            <>
              <pre className={cn("overflow-auto whitespace-pre rounded-lg bg-zinc-950 p-3 text-xs leading-relaxed text-zinc-100", expanded ? "max-h-none" : "max-h-64")}>{out}</pre>
              {longOutput && (
                <button onClick={() => setExpanded((v) => !v)} className="mt-1 text-[11px] font-medium text-primary hover:underline">
                  {expanded ? t("workspace.timeline.run.collapse") : t("workspace.timeline.run.expand")}
                </button>
              )}
            </>
          ) : (
            <p className="text-xs text-muted-foreground">{t("workspace.timeline.run.noOutput")}</p>
          )}
        </div>

        {/* Failure detail. */}
        {failed && (run.error_message || err) && (
          <div>
            <p className="mb-1 text-[11px] font-medium text-red-600 dark:text-red-400">{run.error_message ? t("workspace.timeline.run.errorMessage") : t("workspace.timeline.run.stderr")}</p>
            <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-words rounded-lg bg-red-500/5 p-2 text-xs leading-relaxed text-red-700 dark:text-red-300">{run.error_message || err}</pre>
          </div>
        )}

        {/* Files the run delivered (lazy). "none" means the list was never
            recorded — say so rather than showing the current worktree. */}
        {delivered === null ? (
          <button onClick={() => void loadFiles()} className="text-[11px] font-medium text-primary hover:underline">{t("workspace.timeline.run.showFiles")}</button>
        ) : delivered === "loading" ? (
          <p className="text-[11px] text-muted-foreground">{t("workspace.timeline.run.loadingFiles")}</p>
        ) : delivered.source === "none" ? (
          <p className="text-[11px] text-muted-foreground">{t("workspace.timeline.run.noSnapshot")}</p>
        ) : files.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">{t("workspace.timeline.run.noFiles")}</p>
        ) : (
          <div>
            <p className="mb-1 text-[11px] font-medium text-muted-foreground">
              {t("workspace.timeline.run.files", { count: files.length })}
              {delivered.source === "live" && (
                <span className="ml-2 font-normal">{t("workspace.timeline.run.liveFiles")}</span>
              )}
            </p>
            <ul className="space-y-0.5 text-xs">
              {files.map((f) => (
                <FileRow
                  key={f.path}
                  runId={run.run_id}
                  file={f}
                  openable={canOpenDeliverable(delivered, f)}
                  t={t}
                />
              ))}
            </ul>
            {delivered.content_available === false && (
              <p className="mt-1 text-[11px] text-muted-foreground">{t("workspace.timeline.run.gone")}</p>
            )}
            {delivered.truncated && (
              <p className="mt-1 text-[11px] text-muted-foreground">{t("workspace.timeline.run.partial")}</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

// A changed file you can actually open: view its content inline, or open the
// rendered artifact in a new tab (e.g. see the mockup page as it looks now).
function FileRow(
  { runId, file, openable = true, t }:
  { runId: string; file: Deliverable; openable?: boolean; t: Translate },
) {
  const [content, setContent] = useState<string | null | "loading">(null);
  const [failed, setFailed] = useState(false);
  const [open, setOpen] = useState(false);

  const toggle = async () => {
    if (open) { setOpen(false); return; }
    setOpen(true);
    if (content === null) {
      setContent("loading");
      // A 409 here means the file no longer holds what this run delivered (a
      // later run rewrote it). Say that instead of rendering an empty box.
      try { setContent((await getDeliverableContent(runId, file.path)).content); }
      catch { setFailed(true); setContent(""); }
    }
  };

  return (
    <li className="rounded bg-muted/40">
      <div className="flex items-center gap-2 px-2 py-0.5 font-mono">
        <span className="shrink-0 text-[10px] uppercase text-muted-foreground">{file.status}</span>
        {openable ? (
          <>
            <button onClick={() => void toggle()} className="min-w-0 flex-1 truncate text-left hover:underline">{file.path}</button>
            <button onClick={() => void toggle()} className="shrink-0 font-sans text-[11px] font-medium text-primary hover:underline">{open ? t("workspace.timeline.run.hideFile") : t("workspace.timeline.run.viewFile")}</button>
            <a href={deliverableRawUrl(runId, file.path)} target="_blank" rel="noopener noreferrer" className="shrink-0 font-sans text-[11px] font-medium text-primary hover:underline">{t("workspace.timeline.run.openRaw")}</a>
          </>
        ) : (
          // The worktree is gone: the path is still what the run delivered, but
          // offering a link that can only 404 would be a lie.
          <span className="min-w-0 flex-1 truncate text-muted-foreground">{file.path}</span>
        )}
      </div>
      {open && (
        content === "loading"
          ? <p className="px-2 py-1 text-[11px] text-muted-foreground">{t("workspace.timeline.run.loadingFile")}</p>
          : failed
            ? <p className="px-2 py-1 text-[11px] text-muted-foreground">{t("workspace.timeline.run.changedSince")}</p>
            : <pre className="max-h-72 overflow-auto whitespace-pre rounded-b bg-zinc-950 p-3 text-[11px] leading-relaxed text-zinc-100">{content}</pre>
      )}
    </li>
  );
}

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
    <div className="border-b bg-background/60">
      <div className="flex items-center gap-2 px-5 py-2">
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
        <div className="border-t px-5 py-3">
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
    <div className="flex min-h-0 w-full flex-col border-t lg:w-[400px] lg:border-l lg:border-t-0">
      <div className="flex items-center gap-2 border-b bg-background/90 px-4 py-2">
        <span className="text-xs font-semibold uppercase tracking-wide">{t(COLUMN_TITLE_KEY[column.kind])}</span>
        <Link href={`/runs?task=${encodeURIComponent(task.task_id)}`} className="ml-auto inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">
          {t("workspace.column.openRuns")} <ExternalLink className="size-3" />
        </Link>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-4">
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
  useEffect(() => {
    const ctrl = new AbortController();
    getRunDeliverables(run.run_id, ctrl.signal)
      .then((value) => { if (!ctrl.signal.aborted) setLoaded({ runId: run.run_id, value }); })
      .catch(() => { if (!ctrl.signal.aborted) setLoaded({ runId: run.run_id, value: "failed" }); });
    return () => ctrl.abort();
  }, [run.run_id]);
  const delivered: RunDeliverables | "loading" | "failed" =
    loaded !== null && loaded.runId === run.run_id ? loaded.value : "loading";
  const failed = run.state === "failed" || run.state === "timed_out" || run.state === "abandoned";
  const files = delivered !== "loading" && delivered !== "failed" ? delivered.files : [];

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
      {delivered === "loading" ? (
        <p className="text-xs text-muted-foreground">{t("workspace.timeline.run.loadingFiles")}</p>
      ) : delivered === "failed" ? (
        <p className="text-xs text-muted-foreground">{t("workspace.panels.unavailable")}</p>
      ) : delivered.source === "none" ? (
        <p className="text-xs text-muted-foreground">{t("workspace.timeline.run.noSnapshot")}</p>
      ) : files.length === 0 ? (
        <p className="text-xs text-muted-foreground">{t("workspace.timeline.run.noFiles")}</p>
      ) : (
        <div>
          <p className="mb-1 text-[11px] font-medium text-muted-foreground">{t("workspace.timeline.run.files", { count: files.length })}</p>
          <ul className="space-y-0.5 text-xs">
            {files.map((f) => (
              <FileRow key={f.path} runId={run.run_id} file={f} openable={canOpenDeliverable(delivered, f)} t={t} />
            ))}
          </ul>
          {delivered.content_available === false && (
            <p className="mt-1 text-[11px] text-muted-foreground">{t("workspace.timeline.run.gone")}</p>
          )}
          {delivered.truncated && (
            <p className="mt-1 text-[11px] text-muted-foreground">{t("workspace.timeline.run.partial")}</p>
          )}
        </div>
      )}
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
function CreateTaskDialog({ projectIds, defaultProject, onClose, onCreated }: { projectIds: string[]; defaultProject: string; onClose: () => void; onCreated: (task: TaskManifest) => void }) {
  const { t } = useI18n();
  const [projectId, setProjectId] = useState(defaultProject);
  const [goal, setGoal] = useState("");
  const [done, setDone] = useState("");
  const [avoid, setAvoid] = useState("");
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const ref = useRef<HTMLFormElement>(null);

  useEffect(() => {
    const handleKeyDown = (event: KeyboardEvent) => { if (event.key === "Escape") onClose(); };
    document.addEventListener("keydown", handleKeyDown);
    ref.current?.querySelector<HTMLElement>("input, textarea")?.focus();
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  const toLines = (value: string) => value.split("\n").map((line) => line.trim()).filter(Boolean);

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/35" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <form
        ref={ref}
        role="dialog"
        aria-modal="true"
        aria-labelledby="ws-create-title"
        className="h-full w-full max-w-lg space-y-5 overflow-y-auto bg-background p-6 shadow-2xl"
        onSubmit={async (event) => {
          event.preventDefault(); setSubmitting(true); setError(null);
          const avoidLines = toLines(avoid);
          const description = avoidLines.length > 0 ? `${t("workspace.create.avoidLabel")}\n${avoidLines.map((line) => `- ${line}`).join("\n")}` : "";
          try {
            onCreated(await createTask({ project_id: projectId.trim(), title: goal.trim(), description, acceptance: toLines(done) }));
          } catch (err) {
            setError(err instanceof ApiError ? err.message : (err as Error).message); setSubmitting(false);
          }
        }}
      >
        <div className="flex items-center justify-between">
          <div><p className="text-xs uppercase tracking-widest text-muted-foreground">{t("workspace.create.eyebrow")}</p><h2 id="ws-create-title" className="text-xl font-bold">{t("workspace.create.title")}</h2></div>
          <Button type="button" variant="ghost" size="icon" onClick={onClose} aria-label={t("workspace.create.close")}><X /></Button>
        </div>

        <label className="block space-y-1.5 text-sm font-medium">
          <span>{t("workspace.create.project")}</span>
          <input list="ws-projects" required value={projectId} onChange={(e) => setProjectId(e.target.value)} className="field" />
          <datalist id="ws-projects">{projectIds.map((id) => <option key={id}>{id}</option>)}</datalist>
        </label>
        <label className="block space-y-1.5 text-sm font-medium">
          <span>{t("workspace.create.goalLabel")}</span>
          <input required value={goal} onChange={(e) => setGoal(e.target.value)} className="field" placeholder={t("workspace.create.goalPlaceholder")} />
        </label>
        <label className="block space-y-1.5 text-sm font-medium">
          <span>{t("workspace.create.doneLabel")}</span>
          <textarea value={done} onChange={(e) => setDone(e.target.value)} className="field min-h-24 resize-y" placeholder={t("workspace.create.donePlaceholder")} />
        </label>
        <label className="block space-y-1.5 text-sm font-medium">
          <span>{t("workspace.create.avoidLabel")}</span>
          <textarea value={avoid} onChange={(e) => setAvoid(e.target.value)} className="field min-h-20 resize-y" placeholder={t("workspace.create.avoidPlaceholder")} />
        </label>

        <div>
          <button type="button" onClick={() => setShowAdvanced((v) => !v)} className="flex items-center gap-1 text-xs font-medium text-muted-foreground hover:text-foreground">
            <ChevronDown className={cn("size-3.5 transition-transform", showAdvanced && "rotate-180")} /> {t("workspace.create.advanced")}
          </button>
          {showAdvanced && <p className="mt-2 rounded-lg bg-muted/40 p-3 text-xs leading-relaxed text-muted-foreground">{t("workspace.create.advancedHint")}</p>}
        </div>

        {error && <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{error}</div>}
        <Button type="submit" size="lg" className="w-full" disabled={submitting || !projectId.trim() || !goal.trim()}>{submitting ? t("workspace.create.submitting") : t("workspace.create.submit")}</Button>
      </form>
    </div>
  );
}

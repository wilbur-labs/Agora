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
  type DashboardTask,
  type Delivery,
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
    for (const task of visibleTasks) {
      const info = attentionByTask.get(task.task_id);
      const run = latestRunByTask.get(task.task_id) ?? null;
      const group = railGroupOf({ task, kinds: info?.kinds ?? NO_KINDS, attentionComplete });
      groups[group].push({ task, ask: info?.ask ?? null, run, activity: activityOf(task, run) });
    }
    // Newest activity first within each group.
    for (const group of RAIL_ORDER) {
      groups[group].sort((a, b) => b.activity.localeCompare(a.activity));
    }
    return groups;
  }, [visibleTasks, attentionByTask, latestRunByTask, attentionComplete]);

  const selectedTask = useMemo(
    () => tasks.find((task) => task.task_id === selectedTaskId) ?? null,
    [tasks, selectedTaskId],
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
          <TaskWorkspace key={selectedTask.task_id} refreshNonce={refreshNonce} task={selectedTask} latestRun={latestRunByTask.get(selectedTask.task_id) ?? null} onTaskMayHaveChanged={load} />
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

type Tab = "requirements" | "run" | "attention" | "changes";
const TABS: Tab[] = ["requirements", "run", "attention", "changes"];

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
function TaskWorkspace({ task, latestRun, refreshNonce, onTaskMayHaveChanged }: { task: TaskManifest; latestRun: RunSummary | null; refreshNonce: number; onTaskMayHaveChanged: () => void }) {
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
  // The task's unsettled runs, asked as their own question rather than filtered
  // out of the page of recent history above: that page is capped, so a long-lived
  // task can fill it with terminal runs while an older one is still queued, and
  // an active run missing from it is not an absence. null = no answer yet, or the
  // read failed — both unknown.
  const [activeRuns, setActiveRuns] = useState<RunSummary[] | null>(null);
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
  // When set, the follow-up watch below is armed until this timestamp.
  const [followUpUntil, setFollowUpUntil] = useState<number | null>(null);
  const [tab, setTab] = useState<Tab>("requirements");

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

  const loadEvents = useCallback((signal?: AbortSignal) => {
    listTaskEvents(task.task_id, signal)
      .then((value) => { if (!signal?.aborted) setEvents(value); })
      .catch(() => { if (!signal?.aborted) setEvents([]); });
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
    return listRuns({ task_id: task.task_id, limit: 100 }, signal)
      .then((summaries) =>
        Promise.all(summaries.map((summary) => getRun(summary.run_id, signal).catch(() => null)))
          .then((details) => {
            if (stale()) return;
            const loaded = details.filter((run): run is ExecutionRun => run !== null);
            setRuns(loaded);
            setRunsError(loaded.length !== summaries.length);
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
        return summaries as RunSummary[] | null;
      })
      .catch(() => { if (!stale()) setActiveRuns(null); return null; });
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

  // The latest successful *worker* run is what "produced" the current result.
  // In the auto-review flow the review run finishes after the worker run it
  // reviews, so an unfiltered "latest success" shows the review verdict instead
  // of the implementation. Role rule, identical to dispatcher.py and
  // handoff.py: a missing role is a legacy worker run, but anything present
  // must equal "worker" exactly — an unknown or future role is never presented
  // as the delivered result.
  const latestSuccess = useMemo(() => {
    const ok = (runs ?? []).filter(
      (r) =>
        r.state === "succeeded" &&
        String((r.result_metadata as Record<string, unknown>)?.agora_role ?? "worker") === "worker",
    );
    ok.sort((a, b) => (a.finished_at ?? a.queued_at).localeCompare(b.finished_at ?? b.queued_at));
    return ok.at(-1) ?? null;
  }, [runs]);

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
      <ProjectStrip dashboard={dashboard} dashError={dashError} />
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
      <OutcomeSummary task={task} dashTask={dashTask} dashError={dashError} blocker={blockerClaim} delivery={delivery} deliveryError={deliveryError} latestSuccess={latestSuccess} />
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        {/* Center: the unified, chat-like task timeline + message box. */}
        <TimelineColumn taskId={task.task_id} events={events} runs={runs} specs={specs} onPosted={() => { loadEvents(); loadRecap(); }} />
        {/* Right: result + control tabs (read-only, deep-link to full pages). */}
        <div className="flex min-h-0 w-full flex-col border-t lg:w-[400px] lg:border-l lg:border-t-0">
          <div className="flex gap-1 border-b bg-background/90 px-4 py-2">
            {TABS.map((item) => (
              <button
                key={item}
                onClick={() => setTab(item)}
                className={cn("rounded-lg px-2.5 py-1.5 text-xs font-medium transition-colors", tab === item ? "bg-primary text-primary-foreground" : "text-muted-foreground hover:bg-accent hover:text-foreground")}
              >
                {t(`workspace.tabs.${item}`)}
              </button>
            ))}
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto p-4">
            {tab === "requirements" && <RequirementsPanel task={task} specs={specs} error={specsError} />}
            {tab === "run" && <RunPanel task={task} latestRun={latestRun} />}
            {tab === "attention" && <AttentionPanel view={attentionView} />}
            {tab === "changes" && <ChangesPanel task={task} delivery={delivery} deliveryError={deliveryError} />}
          </div>
        </div>
      </div>
    </div>
  );
}

// The unified task timeline: the whole task process as one readable, chat-like
// stream (requirement drafting/approval, stage changes, runs, review, handoff,
// and the human's own notes), with a message box to continue or correct course.
type TimelineItem =
  | { kind: "event"; at: string; key: string; event: TaskEvent }
  | { kind: "run"; at: string; key: string; run: ExecutionRun };

function TimelineColumn({ taskId, events, runs, specs, onPosted }: { taskId: string; events: TaskEvent[] | null; runs: ExecutionRun[] | null; specs: RequirementSpec[] | null; onPosted: () => void }) {
  const { t, te } = useI18n();
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);
  const specsById = useMemo(() => {
    const map = new Map<string, RequirementSpec>();
    for (const spec of specs ?? []) map.set(spec.spec_id, spec);
    return map;
  }, [specs]);

  // The run.* lifecycle events are noise once we show the actual run cards, so
  // drop them and interleave one rich run card per run (by its queue time).
  const items = useMemo<TimelineItem[]>(() => {
    const out: TimelineItem[] = [];
    for (const e of events ?? []) {
      if (e.event_type.startsWith("run.")) continue;
      out.push({ kind: "event", at: e.created_at, key: e.event_id, event: e });
    }
    for (const r of runs ?? []) {
      out.push({ kind: "run", at: r.queued_at, key: r.run_id, run: r });
    }
    return out.sort((a, b) => a.at.localeCompare(b.at));
  }, [events, runs]);

  // Keep the newest entry in view as the stream grows.
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [items.length]);

  const send = async () => {
    const text = draft.trim();
    if (!text || sending) return;
    setSending(true);
    try {
      await appendTaskEvent(taskId, "user_message", { text });
      setDraft("");
      onPosted();
    } catch {
      // Surfaced by leaving the draft in place; a full error toast is later work.
    } finally {
      setSending(false);
    }
  };

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <div ref={scrollRef} className="min-h-0 flex-1 space-y-3 overflow-y-auto p-5">
        {items.length === 0 ? (
          <p className="py-8 text-center text-sm text-muted-foreground">{t("workspace.timeline.empty")}</p>
        ) : (
          items.map((item) => item.kind === "event"
            ? <TimelineEntry key={item.key} event={item.event} spec={specForEvent(item.event, specsById)} t={t} te={te} />
            : <RunCard key={item.key} run={item.run} t={t} te={te} />)
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
  if (type.startsWith("attention.")) return { who: "system", title: t(type === "attention.responded" ? "workspace.timeline.events.attentionResponded" : "workspace.timeline.events.attentionRaised"), detail: p.title ? String(p.title) : null };
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
function ProjectStrip({ dashboard, dashError }: { dashboard: ProjectDashboard | null; dashError: boolean }) {
  const { t, te } = useI18n();
  if (!dashboard) {
    return <div className="border-b bg-muted/30 px-5 py-2 text-xs text-muted-foreground">{dashError ? t("workspace.project.unavailable") : "…"}</div>;
  }
  const done = dashboard.tasks.filter((it) => it.state === "done").length;
  const total = dashboard.tasks.length;
  const next = dashboard.next_action;
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-1 border-b bg-muted/30 px-5 py-2 text-xs">
      <span className="inline-flex items-center gap-1.5 font-semibold"><Network className="size-3.5 text-primary" />{dashboard.name}</span>
      <span className="text-muted-foreground">{t("workspace.project.strip", { done, total, verified: dashboard.metrics.verified_pct })}</span>
      <span className="text-muted-foreground/50">·</span>
      {next.action === "none" ? (
        <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400"><CircleCheck className="size-3.5" />{t("workspace.project.allDone")}</span>
      ) : (
        <span className={cn("inline-flex items-center gap-1", next.attention ? "text-orange-600 dark:text-orange-400" : "text-foreground")}>
          <ArrowRight className="size-3.5" />
          <span className="font-medium">{t("workspace.project.next")}:</span> {te("nextAction", next.action)}
          {next.title && <span className="text-muted-foreground">{t("workspace.project.forTask", { title: next.title })}</span>}
        </span>
      )}
      {next.attention_count > 0 && (
        <span className="ml-auto inline-flex items-center gap-1 rounded-full bg-orange-500/10 px-2 py-0.5 font-medium text-orange-600 dark:text-orange-400">
          <AlertTriangle className="size-3" />{t("workspace.project.attention", { count: next.attention_count })}
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

function OutcomeSummary({ task, dashTask, dashError, blocker, delivery, deliveryError, latestSuccess }: { task: TaskManifest; dashTask: DashboardTask | null; dashError: boolean; blocker: TaskBlockerClaim; delivery: Delivery | null; deliveryError: boolean; latestSuccess: ExecutionRun | null }) {
  const { t, te } = useI18n();
  const next = dashTask?.next_action ?? null;
  const total = dashTask?.total_weight ?? 0;
  const verified = dashTask?.verified_weight ?? 0;
  const pct = total ? Math.round((verified * 100) / total) : 0;
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
  const outcomeLine = firstMeaningfulLine(latestSuccess?.stdout_tail ?? "");
  const fileCount = delivery?.fileCount ?? 0;

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
          {outcomeLine || fileCount > 0 ? (
            <div className="mt-1 space-y-1">
              {outcomeLine && <p className="text-sm leading-relaxed">{outcomeLine}</p>}
              <p className="text-xs text-muted-foreground">
                {latestSuccess && <span>· {latestSuccess.adapter}</span>}
                {fileCount > 0 && <span> · {t("workspace.summary.files", { count: fileCount })}{delivery?.commit ? ` · ${delivery.commit.slice(0, 8)}` : ""}</span>}
                {deliveryError && <span> · {t("workspace.summary.deliveryUnknown")}</span>}
              </p>
            </div>
          ) : (
            <p className="mt-1 text-sm text-muted-foreground">
              {/* D-6: a failed delivery read used to land here as "nothing
                  produced yet". An absence and an unknown are different. */}
              {deliveryError ? t("workspace.panels.unavailable") : t("workspace.summary.nothingYet")}
            </p>
          )}
        </div>

        {/* Trust + next step stacked. */}
        <div className="space-y-3">
          <div>
            <p className="text-[11px] font-medium uppercase tracking-wide text-muted-foreground">{t("workspace.summary.trust")}</p>
            {!dashTask ? (
              <p className="mt-0.5 text-sm text-muted-foreground">{dashError ? t("workspace.header.unavailable") : "—"}</p>
            ) : !dashTask.has_spec ? (
              <p className="mt-0.5 text-sm text-muted-foreground">{t("workspace.header.noCriteria")}</p>
            ) : (
              <div className="mt-0.5 flex items-center gap-2">
                <span className="text-sm font-semibold tabular-nums">{t("workspace.header.completionValue", { done: verified, total })}</span>
                {total > 0 && <div className="h-1.5 flex-1 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-emerald-500" style={{ width: `${pct}%` }} /></div>}
              </div>
            )}
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
            ) : <p className="mt-0.5 text-sm text-muted-foreground">{dashError ? t("workspace.header.unavailable") : "—"}</p>}
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

// A run rendered as the substance it is: the prompt the agent got, its actual
// terminal output, the failure (if any), and the files it changed.
function RunCard({ run, t, te }: { run: ExecutionRun; t: Translate; te: TranslateEnum }) {
  const [showPrompt, setShowPrompt] = useState(false);
  const [expanded, setExpanded] = useState(false);
  // Keep the whole response, not just `files`: whether the list is this run's
  // frozen snapshot, the still-moving worktree, or unknown changes what we may
  // honestly claim about it.
  const [delivered, setDelivered] = useState<RunDeliverables | "loading" | null>(null);
  const failed = run.state === "failed" || run.state === "timed_out" || run.state === "abandoned";
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

function PanelShell({ href, hrefLabel, children }: { href: string; hrefLabel: string; children: React.ReactNode }) {
  return (
    <div className="space-y-3">
      <div className="rounded-xl border bg-card p-4">{children}</div>
      <Link href={href} className="inline-flex items-center gap-1 text-xs font-medium text-primary hover:underline">
        {hrefLabel} <ExternalLink className="size-3" />
      </Link>
    </div>
  );
}

function RequirementsPanel({ task, specs, error }: { task: TaskManifest; specs: RequirementSpec[] | null; error: boolean }) {
  const { t, te } = useI18n();
  const latest = specs && specs.length > 0 ? specs[0] : null;
  return (
    <PanelShell href={`/requirements?task=${encodeURIComponent(task.task_id)}`} hrefLabel={t("workspace.panels.openFull")}>
      {error ? (
        <p className="text-sm text-muted-foreground">{t("workspace.panels.unavailable")}</p>
      ) : !latest ? (
        <p className="text-sm text-muted-foreground">{t("workspace.panels.requirements.none")}</p>
      ) : (
        <div className="space-y-3">
          <p className="text-xs font-medium text-muted-foreground">{t("workspace.panels.requirements.state", { state: te("status", latest.state), rev: latest.revision })}</p>
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
    </PanelShell>
  );
}

function RunPanel({ task, latestRun }: { task: TaskManifest; latestRun: RunSummary | null }) {
  const { t, te } = useI18n();
  return (
    <PanelShell href={`/runs?task=${encodeURIComponent(task.task_id)}`} hrefLabel={t("workspace.panels.openFull")}>
      {!latestRun ? (
        <p className="text-sm text-muted-foreground">{t("workspace.panels.run.none")}</p>
      ) : (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">{t("workspace.panels.run.latest")}</p>
          <p className="flex items-center gap-2 text-sm font-medium">
            <span className={cn("size-2 rounded-full", RUN_TONE[latestRun.state])} />
            {t("workspace.panels.run.status", { adapter: latestRun.adapter, state: te("runState", latestRun.state) })}
          </p>
          {(latestRun.started_at || latestRun.queued_at) && (
            <p className="text-xs text-muted-foreground">{t("workspace.panels.run.started", { when: relTime(latestRun.started_at ?? latestRun.queued_at, t) })}</p>
          )}
        </div>
      )}
    </PanelShell>
  );
}

function AttentionPanel({ view }: { view: TaskAttentionView<AttentionItem> }) {
  const { t, te } = useI18n();
  return (
    <PanelShell href="/attention" hrefLabel={t("workspace.panels.openFull")}>
      {view.kind === "loading" || view.kind === "unreadable" || view.kind === "none" ? (
        /* Three different facts, three different sentences. This panel used to
           collapse them all into "nothing needs you on this task" by falling back
           to the parent's capped global list — which is not authoritative for one
           task, and made the page contradict its own Continue gate. */
        <p className="text-sm text-muted-foreground">{t(TASK_ATTENTION_NOTICE_KEY[view.kind])}</p>
      ) : (
        <div className="space-y-2">
          {view.kind === "partial" && (
            /* The read came back at the limit, so this list may be missing items.
               Shown, because what we have is real and worth acting on; labelled,
               because "these are the ones waiting" would be a claim the read
               cannot support. The Continue gate treats the count as unknown. */
            <p className="text-xs text-muted-foreground">{t("workspace.panels.attention.partial")}</p>
          )}
          {view.items.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t("workspace.panels.attention.partialEmpty")}</p>
          ) : (
            <ul className="space-y-2">
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
        </div>
      )}
    </PanelShell>
  );
}

function ChangesPanel({ task, delivery, deliveryError }: { task: TaskManifest; delivery: Delivery | null; deliveryError: boolean }) {
  const { t } = useI18n();
  return (
    <PanelShell href={`/runs?task=${encodeURIComponent(task.task_id)}`} hrefLabel={t("workspace.panels.openFull")}>
      {deliveryError ? (
        /* A failed read is not an absence. Without this the summary above says
           "unavailable" while this panel says "no changes" — the same page
           contradicting itself about the same fact. */
        <p className="text-sm text-muted-foreground">{t("workspace.panels.unavailable")}</p>
      ) : !delivery || (delivery.fileCount === 0 && !delivery.commit) ? (
        <p className="text-sm text-muted-foreground">{t("workspace.panels.changes.none")}</p>
      ) : (
        <div className="space-y-2">
          <p className="text-sm font-medium">{t("workspace.panels.changes.delivered", { count: delivery.fileCount })}</p>
          <p className="text-xs text-muted-foreground">{delivery.commit ? t("workspace.panels.changes.commit", { sha: delivery.commit.slice(0, 8) }) : t("workspace.panels.changes.uncommitted")}</p>
          {delivery.files.length > 0 && (
            <ul className="space-y-0.5 text-xs">
              {delivery.files.map((file) => <li key={file} className="truncate rounded bg-muted/40 px-2 py-0.5 font-mono">{file}</li>)}
            </ul>
          )}
        </div>
      )}
    </PanelShell>
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

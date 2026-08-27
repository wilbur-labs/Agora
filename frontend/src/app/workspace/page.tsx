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
  listOpenAttention,
  listProjects,
  listSpecs,
  listTaskEvents,
  listTasks,
  type AttentionItem,
  type AttentionKind,
  type DashboardTask,
  type Delivery,
  type ProjectDashboard,
  type RequirementSpec,
  type TaskEvent,
  type TaskManifest,
} from "@/lib/control-plane";
import {
  deliverableRawUrl,
  getDeliverableContent,
  getRun,
  getRunDeliverables,
  listRuns,
  type Deliverable,
  type ExecutionRun,
  type RunState,
  type RunSummary,
} from "@/lib/execution";
import { useI18n, type Translate, type TranslateEnum } from "@/lib/i18n";
import { cn } from "@/lib/utils";

// The ball is in the human's court whenever a task awaits a decision; a running
// task is the agent's; review is its own stage; terminal/verified is done.
type RailGroup = "needsYou" | "running" | "review" | "done";

const RAIL_ORDER: RailGroup[] = ["needsYou", "running", "review", "done"];

const GROUP_DOT: Record<RailGroup, string> = {
  needsYou: "bg-red-500",
  running: "bg-blue-500",
  review: "bg-violet-500",
  done: "bg-emerald-500",
};

function railGroupOf(task: TaskManifest, kinds: Set<AttentionKind>): RailGroup {
  if (kinds.size > 0) return "needsYou";
  if (task.state === "blocked" || task.state === "failed") return "needsYou";
  if (task.state === "running") return "running";
  // Verified still owes a human handoff (mark_done), so it is NOT "done" — it
  // sits in review/acceptance until it actually reaches the done state.
  if (task.state === "review" || task.state === "verified") return "review";
  if (task.state === "done" || task.state === "cancelled") return "done";
  return "needsYou"; // backlog / requirements / design / planned all await the human
}

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
  const [attention, setAttention] = useState<AttentionItem[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [projectIds, setProjectIds] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [projectFilter, setProjectFilter] = useState("all");
  const [query, setQuery] = useState("");
  const [showCreate, setShowCreate] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [loadedTasks, projectList, openAttention, loadedRuns] = await Promise.all([
        listTasks(), listProjects(), listOpenAttention(), listRuns({ limit: 500 }),
      ]);
      setTasks(loadedTasks);
      setProjectIds(projectList.items.map((item) => item.project_id));
      setAttention(openAttention);
      setRuns(loadedRuns);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
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
    const map = new Map<string, { kinds: Set<AttentionKind>; ask: string | null; blocker: string | null }>();
    for (const item of attention) {
      const entry = map.get(item.task_id) ?? { kinds: new Set<AttentionKind>(), ask: null, blocker: null };
      entry.kinds.add(item.kind);
      if (!entry.ask) entry.ask = item.title;
      if (item.kind === "blocker" && !entry.blocker) entry.blocker = item.title;
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
      needsYou: [], running: [], review: [], done: [],
    };
    for (const task of visibleTasks) {
      const info = attentionByTask.get(task.task_id);
      const run = latestRunByTask.get(task.task_id) ?? null;
      const group = railGroupOf(task, info?.kinds ?? new Set());
      groups[group].push({ task, ask: info?.ask ?? null, run, activity: activityOf(task, run) });
    }
    // Newest activity first within each group.
    for (const group of RAIL_ORDER) {
      groups[group].sort((a, b) => b.activity.localeCompare(a.activity));
    }
    return groups;
  }, [visibleTasks, attentionByTask, latestRunByTask]);

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
          <TaskWorkspace key={selectedTask.task_id} task={selectedTask} ask={attentionByTask.get(selectedTask.task_id)?.blocker ?? null} attention={attention.filter((a) => a.task_id === selectedTask.task_id)} latestRun={latestRunByTask.get(selectedTask.task_id) ?? null} />
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
  groups, projectIds, projectFilter, onProjectFilter, query, onQuery, selectedTaskId, onSelect, onNewTask, onRefresh, loading,
}: {
  groups: Record<RailGroup, Array<{ task: TaskManifest; ask: string | null; run: RunSummary | null; activity: string }>>;
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
                  const sub = ask ?? te("taskState", task.state);
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

function TaskWorkspace({ task, ask, attention, latestRun }: { task: TaskManifest; ask: string | null; attention: AttentionItem[]; latestRun: RunSummary | null }) {
  const { t } = useI18n();
  const [dashboard, setDashboard] = useState<ProjectDashboard | null>(null);
  const [dashError, setDashError] = useState(false);
  const dashTask = dashboard?.tasks.find((it) => it.task_id === task.task_id) ?? null;
  const [specs, setSpecs] = useState<RequirementSpec[] | null>(null);
  const [specsError, setSpecsError] = useState(false);
  const [delivery, setDelivery] = useState<Delivery | null>(null);
  const [events, setEvents] = useState<TaskEvent[] | null>(null);
  const [runs, setRuns] = useState<ExecutionRun[] | null>(null);
  const [tab, setTab] = useState<Tab>("requirements");

  const loadEvents = useCallback((signal?: AbortSignal) => {
    listTaskEvents(task.task_id, signal)
      .then((value) => { if (!signal?.aborted) setEvents(value); })
      .catch(() => { if (!signal?.aborted) setEvents([]); });
  }, [task.task_id]);

  // Fetch this task's detail. The parent keys this component by task_id, so a
  // task switch remounts it and the initial state above already resets cleanly.
  useEffect(() => {
    const ctrl = new AbortController();
    fetchProjectDashboard(task.project_id, ctrl.signal)
      .then((dash) => { if (!ctrl.signal.aborted) setDashboard(dash); })
      .catch(() => { if (!ctrl.signal.aborted) setDashError(true); });
    // A failed read stays "unknown" (null + error), never a false "empty".
    listSpecs(task.task_id).then((value) => { if (!ctrl.signal.aborted) setSpecs(value); }).catch(() => { if (!ctrl.signal.aborted) setSpecsError(true); });
    loadEvents(ctrl.signal);
    // Full run records (prompt + stdout/stderr + result) so the timeline can show
    // what the agent actually did — the substance, like the terminal, not a ledger.
    listRuns({ task_id: task.task_id, limit: 100 }, ctrl.signal)
      .then(async (summaries) => {
        const details = await Promise.all(summaries.map((s) => getRun(s.run_id, ctrl.signal).catch(() => null)));
        if (!ctrl.signal.aborted) setRuns(details.filter((r): r is ExecutionRun => r !== null));
      })
      .catch(() => { if (!ctrl.signal.aborted) setRuns([]); });
    if (task.state === "done") {
      fetchDelivery(task.task_id, ctrl.signal).then((value) => { if (!ctrl.signal.aborted) setDelivery(value); }).catch(() => {});
    }
    return () => ctrl.abort();
  }, [task.task_id, task.project_id, task.state, loadEvents]);

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

  return (
    <div className="flex min-h-0 flex-1 flex-col">
      <ProjectStrip dashboard={dashboard} dashError={dashError} />
      <OutcomeSummary task={task} dashTask={dashTask} dashError={dashError} blocker={ask} delivery={delivery} latestSuccess={latestSuccess} />
      <div className="flex min-h-0 flex-1 flex-col lg:flex-row">
        {/* Center: the unified, chat-like task timeline + message box. */}
        <TimelineColumn taskId={task.task_id} events={events} runs={runs} specs={specs} onPosted={() => loadEvents()} />
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
            {tab === "attention" && <AttentionPanel items={attention} />}
            {tab === "changes" && <ChangesPanel task={task} delivery={delivery} />}
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
function OutcomeSummary({ task, dashTask, dashError, blocker, delivery, latestSuccess }: { task: TaskManifest; dashTask: DashboardTask | null; dashError: boolean; blocker: string | null; delivery: Delivery | null; latestSuccess: ExecutionRun | null }) {
  const { t, te } = useI18n();
  const next = dashTask?.next_action ?? null;
  const total = dashTask?.total_weight ?? 0;
  const verified = dashTask?.verified_weight ?? 0;
  const pct = total ? Math.round((verified * 100) / total) : 0;
  const stuck = task.state === "blocked" || task.state === "failed";
  const blockerText = blocker ?? (stuck ? t("workspace.header.blockedNoDetail") : null);
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
              </p>
            </div>
          ) : (
            <p className="mt-1 text-sm text-muted-foreground">{t("workspace.summary.nothingYet")}</p>
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
  const [files, setFiles] = useState<Deliverable[] | "loading" | null>(null);
  const failed = run.state === "failed" || run.state === "timed_out" || run.state === "abandoned";
  const isReview = String((run.result_metadata as Record<string, unknown>)?.agora_role ?? "") === "review";
  const out = (run.stdout_tail || "").trimEnd();
  const err = (run.stderr_tail || "").trimEnd();
  const longOutput = out.length > 600;

  const loadFiles = async () => {
    setFiles("loading");
    try { setFiles((await getRunDeliverables(run.run_id)).files); }
    catch { setFiles([]); }
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

        {/* Files the run changed (lazy). */}
        {files === null ? (
          <button onClick={() => void loadFiles()} className="text-[11px] font-medium text-primary hover:underline">{t("workspace.timeline.run.showFiles")}</button>
        ) : files === "loading" ? (
          <p className="text-[11px] text-muted-foreground">{t("workspace.timeline.run.loadingFiles")}</p>
        ) : files.length === 0 ? (
          <p className="text-[11px] text-muted-foreground">{t("workspace.timeline.run.noFiles")}</p>
        ) : (
          <div>
            <p className="mb-1 text-[11px] font-medium text-muted-foreground">{t("workspace.timeline.run.files", { count: files.length })}</p>
            <ul className="space-y-0.5 text-xs">
              {files.map((f) => <FileRow key={f.path} runId={run.run_id} file={f} t={t} />)}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}

// A changed file you can actually open: view its content inline, or open the
// rendered artifact in a new tab (e.g. see the mockup page as it looks now).
function FileRow({ runId, file, t }: { runId: string; file: Deliverable; t: Translate }) {
  const [content, setContent] = useState<string | null | "loading">(null);
  const [open, setOpen] = useState(false);

  const toggle = async () => {
    if (open) { setOpen(false); return; }
    setOpen(true);
    if (content === null) {
      setContent("loading");
      try { setContent((await getDeliverableContent(runId, file.path)).content); }
      catch { setContent(""); }
    }
  };

  return (
    <li className="rounded bg-muted/40">
      <div className="flex items-center gap-2 px-2 py-0.5 font-mono">
        <span className="shrink-0 text-[10px] uppercase text-muted-foreground">{file.status}</span>
        <button onClick={() => void toggle()} className="min-w-0 flex-1 truncate text-left hover:underline">{file.path}</button>
        <button onClick={() => void toggle()} className="shrink-0 font-sans text-[11px] font-medium text-primary hover:underline">{open ? t("workspace.timeline.run.hideFile") : t("workspace.timeline.run.viewFile")}</button>
        <a href={deliverableRawUrl(runId, file.path)} target="_blank" rel="noopener noreferrer" className="shrink-0 font-sans text-[11px] font-medium text-primary hover:underline">{t("workspace.timeline.run.openRaw")}</a>
      </div>
      {open && (
        content === "loading"
          ? <p className="px-2 py-1 text-[11px] text-muted-foreground">{t("workspace.timeline.run.loadingFile")}</p>
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

function AttentionPanel({ items }: { items: AttentionItem[] }) {
  const { t, te } = useI18n();
  return (
    <PanelShell href="/attention" hrefLabel={t("workspace.panels.openFull")}>
      {items.length === 0 ? (
        <p className="text-sm text-muted-foreground">{t("workspace.panels.attention.none")}</p>
      ) : (
        <ul className="space-y-2">
          {items.map((item) => (
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
    </PanelShell>
  );
}

function ChangesPanel({ task, delivery }: { task: TaskManifest; delivery: Delivery | null }) {
  const { t } = useI18n();
  return (
    <PanelShell href={`/runs?task=${encodeURIComponent(task.task_id)}`} hrefLabel={t("workspace.panels.openFull")}>
      {!delivery || (delivery.fileCount === 0 && !delivery.commit) ? (
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

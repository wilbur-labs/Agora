"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { AlertTriangle, ArrowRight, ChevronRight, CircleCheck, FileCheck2, FolderPlus, Pencil, Plus, RefreshCw, Sparkles, Target, X } from "lucide-react";
import { DeliveryShell } from "@/components/delivery-shell";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import {
  ApiError,
  archiveTask,
  type Charter,
  createProject,
  createTask,
  type DashboardTask,
  fetchDelivery,
  fetchProjectDashboard,
  type Delivery,
  handoffTask,
  listOpenAttention,
  listProjects,
  listTasks,
  type ProjectDashboard,
  setProjectCharter,
  TASK_PAGE_LIMIT,
  transitionTask,
  unarchiveTask,
  type AdapterName,
  type AttentionItem,
  type AttentionKind,
  type TaskManifest,
  type TaskRisk,
  type TaskState,
} from "@/lib/control-plane";
import { listRuns, type RunState, type RunSummary } from "@/lib/execution";

// The runs page this board asks for. Named rather than written twice, so the
// reach check compares against the same number the request used — a literal in
// one of the two places is how a capped read starts looking complete.
const RUNS_PAGE_LIMIT = MAX_PAGE;
import { progressBarOf } from "@/lib/progress-snapshot";
import { attentionRowsOf, crLogHeadlineOf, crLogViewOf } from "@/lib/cr-log";
import {
  PORTFOLIO_ATTENTION_NOTICE_KEY,
  PORTFOLIO_GROUPS,
  PORTFOLIO_TASK_INVENTORY_NOTICE_KEY,
  attentionReadCovers,
  portfolioAllClearSayable,
  portfolioBucketOf,
  archivedCountOf,
  portfolioGroupCountOf,
  projectMetricsSayable,
  projectNextActionLineOf,
  taskInventoryReachOf,
  taskRunsLineOf,
  type AttentionRead,
  type PortfolioBucket,
  type TaskInventoryReach,
} from "@/lib/portfolio-bucket";
// One read, one outcome. Shared with the Workspace rail rather than reimplemented:
// the rule that a read may only speak for itself is not specific to a surface, and
// two copies of it are how the rail's fix failed to reach this page in the first
// place. `railReadReport` already decides the attention read's reach from the
// attention read alone and orders the failure messages attention-first, and it is
// pinned by tests/rail-group.test.mts — a second copy here could drift from it
// silently. What this board does not take from it is `staleOrigins`: the rail names
// which sibling went stale, and this board has no such notice. It answers the one
// question it needs about the task read — how far that read got — from the read's own
// settled result, with `taskInventoryReachOf`. Dropping the task read entirely was
// how "Nothing needs you right now" came to be printed under a load-failure banner.
import { railReadReport, settleRead } from "@/lib/rail-group";
import { MAX_PAGE, readPage } from "@/lib/paged";
import { ProgressSnapshotBar } from "@/components/progress-snapshot-bar";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

// Attention-first buckets. A task lands in the highest-priority bucket that
// applies, so "needs you" always wins over "running", etc. The rule itself lives in
// lib/portfolio-bucket.ts, with the tests that pin it — it had to move out of this
// file because it was deciding the board's whole triage claim from a page whose
// `truncated` flag this component was throwing away.
type Bucket = PortfolioBucket;

// `unsorted` sits between "needs you" and "in progress" on purpose: a task the
// attention read could not cover may in fact need a human, so it must not be filed
// below the work that is already settled. It is a group of its own rather than a
// member of `action`, because "Needs you" is a claim and this group's whole content
// is the absence of one.


// Left-accent + pill color per bucket (theme-aware Tailwind tokens).
const BUCKET_META: Record<Bucket, { border: string; pill: string; dot: string }> = {
  needsReply: { border: "border-l-red-500", pill: "bg-red-500/10 text-red-600 dark:text-red-400", dot: "bg-red-500" },
  needsApproval: { border: "border-l-violet-500", pill: "bg-violet-500/10 text-violet-600 dark:text-violet-400", dot: "bg-violet-500" },
  blocked: { border: "border-l-orange-500", pill: "bg-orange-500/10 text-orange-600 dark:text-orange-400", dot: "bg-orange-500" },
  // Deliberately not a calm colour and not an alarming one: the card is saying "I do
  // not know", and either extreme would state something it does not know.
  unknown: { border: "border-l-amber-500", pill: "bg-amber-500/10 text-amber-600 dark:text-amber-400", dot: "bg-amber-500" },
  running: { border: "border-l-blue-500", pill: "bg-blue-500/10 text-blue-600 dark:text-blue-400", dot: "bg-blue-500" },
  verified: { border: "border-l-teal-500", pill: "bg-teal-500/10 text-teal-600 dark:text-teal-400", dot: "bg-teal-500" },
  shaping: { border: "border-l-sky-400", pill: "bg-sky-400/10 text-sky-600 dark:text-sky-300", dot: "bg-sky-400" },
  done: { border: "border-l-emerald-500", pill: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400", dot: "bg-emerald-500" },
  closed: { border: "border-l-zinc-400", pill: "bg-zinc-400/10 text-zinc-500", dot: "bg-zinc-400" },
};


export default function PortfolioPage() {
  const { t } = useI18n();
  const [tasks, setTasks] = useState<TaskManifest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attention, setAttention] = useState<AttentionItem[]>([]);
  // How far the ONE global open-attention read got. `pending` until it settles:
  // before any read, an empty kind set is not evidence of anything either.
  const [attentionRead, setAttentionRead] = useState<AttentionRead>("pending");
  // How far the task read got. `pending` until the first load is applied — all four
  // reads publish together below, so this is a fact about the load, not about the task
  // read finishing: before anything lands, the board's emptiness is evidence of
  // nothing, and calling that a failed read is its own falsehood.
  const [taskInventory, setTaskInventory] = useState<TaskInventoryReach>("pending");
  const [runsReach, setRunsReach] = useState<TaskInventoryReach>("pending");
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [showNewProject, setShowNewProject] = useState(false);
  const [registeredProjects, setRegisteredProjects] = useState<string[]>([]);
  const [projectFilter, setProjectFilter] = useState("all");
  const [showArchived, setShowArchived] = useState(false);
  const [showCompleted, setShowCompleted] = useState(false);
  const [dashboard, setDashboard] = useState<ProjectDashboard | null>(null);
  const [showCharter, setShowCharter] = useState(false);
  // Which refresh is current. A ref, not state: it is read and written inside one
  // async call and must never itself cause a render.
  const loadToken = useRef(0);

  const load = useCallback(async () => {
    // Refreshes can overlap: the mount effect, a conflict retry from a card, and the
    // refresh button all call this. Four settled reads always resolve, so an older
    // call always reaches the writes below — and would then publish its own answer
    // over a newer one, which for `attentionRead` means a stale "could not be read"
    // replacing a read that has since succeeded. Only the newest call writes.
    const token = ++loadToken.current;
    setLoading(true);
    setError(null);
    // Four reads, four outcomes. `Promise.all` under one `catch` was wrong in a way
    // that matters here: a failing *tasks*, *projects* or *runs* read landed in the
    // same handler and the attention read was recorded as `unreadable` — so every
    // calm task moved to "Could not tell" and the board said "Open items could not
    // be read" about a read that had in fact succeeded. The reach is defined as the
    // attention read's own result **and nothing else**; a combined failure path
    // leaves it nothing to answer from.
    const [tasksRead, projectsRead, attentionResult, runsRead] = await Promise.all([
      // Both list reads go through `readPage`, which asks for one row beyond the page:
      // `truncated` is then the fact that a further row exists, not the guess that a
      // full page implies one. The cards used to say "not fully read" about a read of
      // exactly 500 that had missed nothing.
      settleRead(() => readPage(TASK_PAGE_LIMIT, (limit) => listTasks(limit))),
      settleRead(() => listProjects()),
      settleRead(() => listOpenAttention()),
      settleRead(() => readPage(RUNS_PAGE_LIMIT, (limit) => listRuns({ limit }))),
    ]);
    // How far the attention read got, kept rather than discarded. Only `items` was
    // read here, and `truncated` — which `listOpenAttention` has always returned —
    // was dropped on the floor, so a task whose question was past the cap arrived
    // with no kinds and was filed by lifecycle state alone. See lib/portfolio-bucket.ts.
    const report = railReadReport({ tasks: tasksRead, projects: projectsRead, attention: attentionResult, runs: runsRead });
    if (token !== loadToken.current) return; // a newer refresh already owns the board
    setAttentionRead(report.attentionReach);
    setTaskInventory(taskInventoryReachOf(tasksRead));
    // The runs read is one page too, and its cap is the number this component asks
    // for. Without this the cards could only tell a failed read from a good one, and
    // a *capped* read looks exactly like "this task has no runs" from a card's seat.
    setRunsReach(taskInventoryReachOf(runsRead));
    if (attentionResult.status === "fulfilled") {
      setAttention(attentionResult.value.items);
    } else {
      // Presence is provable only from the page it was read on, and this read has no
      // page. Keeping the previous one alongside `unreadable` would let a stale ask
      // go on asserting itself — the same mistake in the other direction.
      setAttention([]);
    }
    if (tasksRead.status === "fulfilled") setTasks([...tasksRead.value.items]);
    if (projectsRead.status === "fulfilled") {
      setRegisteredProjects(projectsRead.value.items.map((item) => item.project_id));
    }
    if (runsRead.status === "fulfilled") setRuns([...runsRead.value.items]);
    // Attention first, so its failure is not buried behind a sibling's (the order is
    // railReadReport's, not this page's).
    setError(report.errors.length > 0 ? report.errors.join(" · ") : null);
    setLoading(false);
  }, []);

  useEffect(() => {
    const timeout = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timeout);
  }, [load]);

  // A deep link into one project's cockpit — Today's "让 agent 读一遍 →" lands here. Read
  // once on mount (query params, not a dynamic route: the app is statically exported).
  useEffect(() => {
    const project = new URLSearchParams(window.location.search).get("project");
    if (project) {
      const timeout = window.setTimeout(() => setProjectFilter(project), 0);
      return () => window.clearTimeout(timeout);
    }
  }, []);

  // The cockpit is per-project: fetch the dashboard (charter + metrics + the one
  // deterministic next action) only when scoped to a single project. Refetch
  // when the task set changes so the metrics track advances/creates.
  const loadDashboard = useCallback(
    (signal?: AbortSignal) => {
      if (projectFilter === "all") {
        setDashboard(null);
        return;
      }
      fetchProjectDashboard(projectFilter, signal)
        .then((next) => { if (!signal?.aborted) setDashboard(next); })
        .catch(() => { if (!signal?.aborted) setDashboard(null); });
    },
    [projectFilter],
  );

  useEffect(() => {
    const ctrl = new AbortController();
    // Deferred for the same reason the load effect above is: `loadDashboard` clears
    // the dashboard synchronously when the filter is "all", and a setState in an
    // effect body cascades renders (react-hooks/set-state-in-effect). This was
    // always true here; it became *visible* when `load` stopped using
    // try/catch-around-await, which had made the React compiler bail out of this
    // component and take its whole rule set with it. Nothing else in the component
    // was flagged once it started being analysed.
    const timeout = window.setTimeout(() => loadDashboard(ctrl.signal), 0);
    return () => {
      window.clearTimeout(timeout);
      ctrl.abort();
    };
  }, [loadDashboard, tasks]);

  const dashboardByTask = useMemo(() => {
    const map = new Map<string, DashboardTask>();
    for (const task of dashboard?.tasks ?? []) map.set(task.task_id, task);
    return map;
  }, [dashboard]);

  const projects = useMemo(
    () => Array.from(new Set([...registeredProjects, ...tasks.map((task) => task.project_id)])).sort(),
    [tasks, registeredProjects],
  );
  const scoped = projectFilter === "all" ? tasks : tasks.filter((task) => task.project_id === projectFilter);
  const archivedCount = scoped.filter((task) => task.archived_at).length;
  const archivedShown = archivedCountOf(archivedCount, taskInventory);
  const visible = showArchived ? scoped : scoped.filter((task) => !task.archived_at);

  // task_id -> open attention kinds present, plus the first open item's title (the "ask").
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

  // task_id -> its most recent run (runs arrive newest-first).
  const latestRunByTask = useMemo(() => {
    const map = new Map<string, RunSummary>();
    for (const run of runs) if (!map.has(run.task_id)) map.set(run.task_id, run);
    return map;
  }, [runs]);

  // Every visible task tagged with its bucket + the specific ask (if any).
  const tagged = useMemo(
    () => visible.map((task) => {
      const info = attentionByTask.get(task.task_id);
      return {
        task,
        bucket: portfolioBucketOf({
          task,
          kinds: info?.kinds ?? new Set<AttentionKind>(),
          attentionComplete: attentionReadCovers(attentionRead),
        }),
        ask: info?.ask ?? null,
      };
    }),
    [visible, attentionByTask, attentionRead],
  );

  const replaceTask = (updated: TaskManifest) => {
    setTasks((current) => current.map((task) => (task.task_id === updated.task_id ? updated : task)));
  };

  // This project, as far as this page can see, has no goal. Only claimed while the
  // dashboard in hand is the filtered project's: a dashboard left over from the previous
  // selection would gate the wrong project, and a read that has not landed says nothing.
  const gatedProject = dashboard !== null
    && dashboard.project_id === projectFilter
    && !(dashboard.charter?.goal ?? "").trim();

  return (
    <DeliveryShell active="Portfolio">
      <header className="border-b bg-background/80 px-5 py-5 backdrop-blur md:px-8">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground">{t("portfolio.header.eyebrow")}</p>
            <h1 className="mt-1 text-2xl font-bold">{t("portfolio.header.title")}</h1>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={projectFilter}
              // The editor is bound to whatever project the cockpit is showing. Left
              // open across a filter change it re-mounted for the next project as soon
              // as that dashboard arrived — an edit box for one project's charter,
              // opened for another's.
              onChange={(event) => { setShowCharter(false); setProjectFilter(event.target.value); }}
              className="h-9 rounded-lg border bg-background px-3 text-sm"
              aria-label={t("portfolio.filter.label")}
            >
              <option value="all">{t("portfolio.filter.allProjects")}</option>
              {projects.map((project) => <option key={project}>{project}</option>)}
            </select>
            {/* Uncertainty is deliberately NOT a reason to show this. Clicking it
                filters the tasks already loaded — it fetches nothing — so on a capped
                read that loaded no archived tasks the button would reveal nothing and
                then offer to hide it again. The board already says its read was
                partial, in the notice below the action group; a button that cannot act
                on that is noise, not honesty. */}
            {(archivedCount > 0 || showArchived) && (
              <Button variant={showArchived ? "secondary" : "outline"} size="lg" onClick={() => setShowArchived((value) => !value)} aria-pressed={showArchived}>
                {showArchived
                  ? t("portfolio.archived.hide")
                  // Counted off the same bounded, possibly stale snapshot as the group
                  // badges, so it goes through the same helper rather than stating a
                  // number the read does not support.
                  : archivedShown.kind === "exact"
                    ? t("portfolio.archived.show", { count: archivedShown.count })
                    : t("portfolio.archived.showSome")}
              </Button>
            )}
            <Button variant="outline" size="lg" onClick={load} disabled={loading} aria-label={t("portfolio.actions.refresh")}>
              <RefreshCw className={cn("size-4", loading && "animate-spin")} />
            </Button>
            <Button variant="outline" size="lg" onClick={() => setShowNewProject(true)}><FolderPlus /> {t("portfolio.actions.newProject")}</Button>
            {/* Off for a project this page can see has no goal (CR-61): the block below
                says tasks are settled in the conversation, and a button beside it that
                creates one anyway makes the gate advice into a suggestion. Only when the
                board is filtered to that project — in the all-projects view this page
                holds no charter per project, and guessing would be the same mistake in
                the other direction. That case is what the server-side refusal is for,
                and it is still open. */}
            <Button
              size="lg"
              disabled={gatedProject}
              title={gatedProject ? t("projectChat.gate.noCharter") : undefined}
              onClick={() => setShowCreate(true)}
            >
              <Plus /> {t("portfolio.actions.newTask")}
            </Button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-[1600px] space-y-6 p-5 md:p-8">
        {error && <Notice tone="error">{t("portfolio.errors.loadFailed", { error })}</Notice>}

        {/* Only while it is this project's. Selecting another project leaves the
            previous dashboard in state until the new request settles, and the cockpit
            underneath a B-filtered board was rendering A's charter, metrics and next
            action as if they were B's. */}
        {dashboard && dashboard.project_id === projectFilter && (
          <ProjectCockpit dashboard={dashboard} onEditCharter={() => setShowCharter(true)} />
        )}
        {/* The kickoff is not a form on this screen any more (CR-62). It is the project's
            conversation, in the workbench's middle column, and it stays open after the
            goal is settled — so this screen points at it rather than holding a second,
            differently-shaped copy of it. */}
        {gatedProject && (
          <section className="flex flex-wrap items-center gap-3 rounded-xl border border-dashed bg-muted/20 p-4">
            <Sparkles className="size-4 text-primary" />
            <p className="text-sm text-muted-foreground">{t("projectChat.gate.noCharter")}</p>
            <Link
              href={`/workspace?project=${encodeURIComponent(dashboard.project_id)}`}
              className={cn(buttonVariants({ size: "sm" }), "ml-auto")}
            >
              {t("projectChat.gate.settle")}
            </Link>
          </section>
        )}

        {PORTFOLIO_GROUPS.map((group) => {
          const cards = tagged
            .filter((entry) => group.buckets.includes(entry.bucket))
            .sort((a, b) => group.buckets.indexOf(a.bucket) - group.buckets.indexOf(b.bucket));
          const collapsed = group.collapsible && !showCompleted;
          return (
            <section key={group.id}>
              <button
                type="button"
                onClick={() => group.collapsible && setShowCompleted((v) => !v)}
                className={cn("mb-3 flex w-full items-center gap-2 text-left", !group.collapsible && "cursor-default")}
              >
                {group.collapsible && <ChevronRight className={cn("size-4 transition-transform", !collapsed && "rotate-90")} />}
                <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">{t(`portfolio.groups.${group.id}`)}</h2>
                <GroupCount count={cards.length} taskInventory={taskInventory} attentionRead={attentionRead} />
              </button>
              {/* "Nothing needs you right now" is a claim, and an empty action group
                  only supports it when the attention read covered every open item
                  *and* the task inventory read was complete — succeeding is not
                  enough, since a page filled to the cap succeeds. With a capped or
                  failed attention read the same emptiness means nothing was found on
                  the page; with a capped or failed task read it means the board is
                  placing a page, or nothing at all.
                  Both were false absences in this component, next to the bucketing
                  itself — the rule is `portfolioAllClearSayable`. */}
              {group.id === "action" && cards.length === 0 && !loading
                && portfolioAllClearSayable({ attentionRead, taskInventory }) && (
                <p className="rounded-xl border border-dashed bg-card px-4 py-6 text-center text-sm text-muted-foreground">{t("portfolio.allClear")}</p>
              )}
              {/* And when it cannot be said, why. Suppressing the sentence and
                  printing nothing keeps the uncertainty inside the component: the
                  user sees an empty "Needs you" column and has no way to tell a quiet
                  board from a partial one. A failed read is also named in the banner
                  above; a capped one is named only here.
                  Not gated on `!loading`, unlike the reassurance and like the
                  attention notice below: `pending` is only ever true while the first
                  load is in flight, so gating it there made that sentence — and the
                  test pinning it — unreachable. A refresh keeps the previous reach,
                  which is still what the board is showing. */}
              {group.id === "action" && taskInventory !== "complete" && (
                <Notice tone={taskInventory === "unreadable" ? "error" : "info"}>
                  {t(PORTFOLIO_TASK_INVENTORY_NOTICE_KEY[taskInventory])}
                </Notice>
              )}
              {/* Why some tasks could not be sorted, in the group that holds them. */}
              {group.id === "unsorted" && !attentionReadCovers(attentionRead) && (
                <Notice tone={attentionRead === "unreadable" ? "error" : "info"}>
                  {t(PORTFOLIO_ATTENTION_NOTICE_KEY[attentionRead as Exclude<AttentionRead, "complete">])}
                </Notice>
              )}
              {!collapsed && cards.length > 0 && (
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {cards.map(({ task, bucket, ask }) => (
                    <TaskCard key={task.task_id} task={task} bucket={bucket} ask={ask} latestRun={latestRunByTask.get(task.task_id) ?? null} runsReach={runsReach} dash={dashboardByTask.get(task.task_id) ?? null} onConflict={load} onAdvanced={replaceTask} />
                  ))}
                </div>
              )}
            </section>
          );
        })}
      </div>

      {showCreate && (
        <CreateTaskPanel
          projects={projects}
          onClose={() => setShowCreate(false)}
          onCreated={(task) => { setTasks((current) => [task, ...current]); setShowCreate(false); }}
        />
      )}
      {showNewProject && (
        <NewProjectPanel
          onClose={() => setShowNewProject(false)}
          onCreated={async () => { setShowNewProject(false); await load(); }}
        />
      )}
      {showCharter && dashboard && dashboard.project_id === projectFilter && (
        <CharterPanel
          projectId={dashboard.project_id}
          charter={dashboard.charter}
          onClose={() => setShowCharter(false)}
          onSaved={(charter) => {
            setDashboard((current) => (current ? { ...current, charter } : current));
            setShowCharter(false);
          }}
        />
      )}
    </DeliveryShell>
  );
}

// The project cockpit (1.2): the human's charter, progress vs verified
// completion, and the single deterministic next action — the answer to "where
// is this project and what do I do next?" without reading the whole board.
function ProjectCockpit({ dashboard, onEditCharter }: { dashboard: ProjectDashboard; onEditCharter: () => void }) {
  const { t, te } = useI18n();
  const { charter, metrics, next_action: next } = dashboard;
  const metricsSayable = projectMetricsSayable(dashboard.truncated);
  const nextLine = projectNextActionLineOf(next.action, next.partial);
  const hasCharter = Boolean(charter.goal || charter.non_goals.length || charter.success_criteria.length);
  const attention = metrics.health === "attention";

  return (
    <section className="space-y-4 rounded-2xl border bg-card p-5 shadow-sm md:p-6">
      {/* Charter: what "done" means, defined by the human. */}
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted-foreground">
            <Target className="size-3.5" /> {t("portfolio.cockpit.charter.title")}
          </div>
          {hasCharter ? (
            <>
              <p className="mt-1.5 text-lg font-semibold leading-snug">{charter.goal || "—"}</p>
              {(charter.non_goals.length > 0 || charter.success_criteria.length > 0) && (
                <div className="mt-2 grid gap-x-8 gap-y-2 text-xs sm:grid-cols-2">
                  {charter.success_criteria.length > 0 && (
                    <div>
                      <p className="font-medium text-muted-foreground">{t("portfolio.cockpit.charter.successCriteria")}</p>
                      <ul className="mt-1 list-disc space-y-0.5 pl-4">
                        {charter.success_criteria.map((item, i) => <li key={i} className="leading-relaxed">{item}</li>)}
                      </ul>
                    </div>
                  )}
                  {charter.non_goals.length > 0 && (
                    <div>
                      <p className="font-medium text-muted-foreground">{t("portfolio.cockpit.charter.nonGoals")}</p>
                      <ul className="mt-1 list-disc space-y-0.5 pl-4 text-muted-foreground">
                        {charter.non_goals.map((item, i) => <li key={i} className="leading-relaxed">{item}</li>)}
                      </ul>
                    </div>
                  )}
                </div>
              )}
            </>
          ) : (
            <p className="mt-1.5 text-sm text-muted-foreground">{t("portfolio.cockpit.charter.empty")}</p>
          )}
        </div>
        <Button variant="outline" size="sm" onClick={onEditCharter}><Pencil className="size-3.5" /> {t("portfolio.cockpit.charter.edit")}</Button>
      </div>

      {/* The project's own change log, read from its repository.

          Why it sits here, directly under the charter: the charter says what this
          project is for, and an open CR is the record of something proposing to
          change that. The two belong in one eyeful or the mechanism is decoration. */}
      <ProjectChangeLog dashboard={dashboard} />

      {/* Produced progress, snapshot-driven completion, and health.
          The middle tile used to be `metrics.verified_pct`. It is gone from the
          display layer by recorded decision: that percentage counts a criterion as
          verified from any historical successful run of its task, so criteria added
          by a later requirements change read as verified by a run that predates
          them. The tile now renders the project's RESUME progress snapshot — done
          items only in the number, in-progress as a translucent second segment — and
          renders no number at all when there is no snapshot to count. */}
      <div className="grid gap-3 sm:grid-cols-2">
        {/* The weight-derived progress tile is gone, and the reason is the one written
            over the card block below it: `tasks/progress.py` marks every criterion of
            the task's **current** spec "produced" whenever any historical worker run
            succeeded, with no binding to the revision those criteria belong to. A run
            against revision A therefore reports revision B's new criteria as produced,
            and the tile could read 100% and "N/N criteria" with nothing having
            produced them.
            Calling it Progress rather than Verified does not supply the missing
            evidence — especially beside a subline that names the units as current
            criteria. `truncated` was not the defect here and gating on it did not
            repair this; it is the same unbound signal `verified_pct` was dropped for.
            What remains in this row is the snapshot bar beside it, which counts a file
            the humans maintain, and health, whose blocked/failed evidence does not
            depend on revision binding. Restoring a number here is a backend change. */}
        <div className="rounded-xl border bg-muted/30 p-3">
          <p className="text-xs font-medium text-muted-foreground">{t("common.progress.label")}</p>
          <ProgressSnapshotBar
            bar={progressBarOf({
              snapshot: dashboard.progress_snapshot,
              // This component only renders with a dashboard in hand; the page above
              // owns the loading and failed states.
              dashboardLoaded: true,
              dashboardReadFailed: false,
            })}
            source={dashboard.progress_snapshot?.source}
            className="mt-1"
          />
        </div>
        <div className={cn("rounded-xl border p-3", attention && metricsSayable ? "border-orange-500/40 bg-orange-500/5" : "bg-muted/30")}>
          <p className="text-xs font-medium text-muted-foreground">{t("portfolio.cockpit.metrics.health")}</p>
          {/* Health is the same rollup. A green "normal" over the first page says
              nothing about the task beyond it, and green is the half nobody re-checks. */}
          {metricsSayable ? (
            <p className={cn("mt-2 flex items-center gap-1.5 text-lg font-semibold", attention ? "text-orange-600 dark:text-orange-400" : "text-emerald-600 dark:text-emerald-400")}>
              {attention ? <AlertTriangle className="size-4" /> : <CircleCheck className="size-4" />}
              {te("health", metrics.health)}
            </p>
          ) : (
            <p className="mt-2 text-xs leading-relaxed text-muted-foreground">{t("portfolio.cockpit.metrics.partial")}</p>
          )}
        </div>
      </div>

      {/* The one deterministic next action for the whole project. */}
      <div className={cn("rounded-xl border-l-4 bg-muted/30 px-4 py-3", next.attention ? "border-l-orange-500" : nextLine.kind === "allClear" ? "border-l-emerald-500" : "border-l-primary")}>
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="min-w-0">
            <p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{t("portfolio.cockpit.nextAction.label")}</p>
            {/* All three of these are the same decision, taken once. Guarding only the
                dedicated all-clear sentence left the enum label "Nothing to do" saying
                it again one line down, and let a partial page's second-best candidate
                be labelled the project's next action. */}
            {nextLine.kind === "unknown" ? (
              <p className="mt-0.5 text-sm text-muted-foreground">{t("portfolio.cockpit.nextAction.unknown")}</p>
            ) : nextLine.kind === "allClear" ? (
              <p className="mt-0.5 text-sm font-medium">{t("portfolio.cockpit.nextAction.allClear")}</p>
            ) : (
              <p className="mt-0.5 text-sm font-semibold">
                {te("nextAction", next.action)}
                {next.title && <span className="ml-2 font-normal text-muted-foreground">· {t("portfolio.cockpit.nextAction.forTask", { title: next.title })}</span>}
              </p>
            )}
          </div>
          {/* Both badges are rollups over the same bounded page as the line above, and
              the backend says so: `pending_count` may undercount when `partial`, and on
              truncation the route supplements only off-page `blocked` and `failed`, so
              an off-page `running` task with no worker success raises attention that is
              never counted. Beside a line reading "next action not determined", an
              exact count was the same unsupported precision the line had just given up.
              Shown as a floor instead — the observation is real, the total is not. */}
          <div className="flex items-center gap-2 text-[11px]">
            {next.attention_count > 0 && (
              <span className="inline-flex items-center gap-1 rounded-full bg-orange-500/10 px-2 py-0.5 font-medium text-orange-600 dark:text-orange-400">
                <AlertTriangle className="size-3" />
                {next.partial
                  ? t("portfolio.cockpit.nextAction.attentionAtLeast", { count: next.attention_count })
                  : t("portfolio.cockpit.nextAction.attention", { count: next.attention_count })}
              </span>
            )}
            {next.pending_count > 0 && (
              <span className="rounded-full bg-primary/10 px-2 py-0.5 font-medium text-primary">
                {next.partial
                  ? t("portfolio.cockpit.nextAction.pendingAtLeast", { count: next.pending_count })
                  : t("portfolio.cockpit.nextAction.pending", { count: next.pending_count })}
              </span>
            )}
          </div>
        </div>
        {next.partial && nextLine.kind !== "unknown" && <p className="mt-1 text-[11px] text-muted-foreground">{t("portfolio.cockpit.nextAction.partial")}</p>}
      </div>
    </section>
  );
}

// Manual lifecycle advances the UI drives directly. Transitions handled
// elsewhere are intentionally absent: requirements→design lives on the
// Requirements page (needs an approved spec), and planned→running happens
// automatically when a Run is dispatched. `backlog` keeps its own "Define"
// label; the rest read from portfolio.card.advance.*.
const ADVANCE: Partial<Record<TaskState, { to: TaskState; labelKey: string }>> = {
  backlog: { to: "requirements", labelKey: "portfolio.card.define" },
  design: { to: "planned", labelKey: "portfolio.card.advance.toPlanned" },
  running: { to: "review", labelKey: "portfolio.card.advance.toReview" },
  review: { to: "verified", labelKey: "portfolio.card.advance.toVerified" },
  verified: { to: "done", labelKey: "portfolio.card.advance.toDone" },
};

const RUN_TONE: Record<RunState, string> = {
  queued: "bg-amber-500",
  running: "bg-blue-500",
  succeeded: "bg-emerald-500",
  failed: "bg-red-500",
  timed_out: "bg-red-500",
  cancelled: "bg-zinc-400",
  abandoned: "bg-red-500",
};

// The group's badge. `cards.length` is how many this board is holding, which is only
// the group's size when the task read covered every task — see portfolioGroupCountOf.
function GroupCount({ count, taskInventory, attentionRead }: { count: number; taskInventory: TaskInventoryReach; attentionRead: AttentionRead }) {
  const { t } = useI18n();
  const shown = portfolioGroupCountOf(count, taskInventory, attentionRead);
  if (shown.kind === "unknown") return null;
  return (
    <Badge variant="secondary">
      {shown.kind === "exact"
        ? shown.count
        : t("portfolio.cockpit.group.atLeast", { count: String(shown.count) })}
    </Badge>
  );
}

function TaskCard({ task, bucket, ask, latestRun, runsReach, dash, onAdvanced, onConflict }: { task: TaskManifest; bucket: Bucket; ask: string | null; latestRun: RunSummary | null; runsReach: TaskInventoryReach; dash: DashboardTask | null; onAdvanced: (updated: TaskManifest) => void; onConflict: () => Promise<void> }) {
  const { t, te } = useI18n();
  const runLine = taskRunsLineOf(Boolean(latestRun), runsReach);
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<null | "advance" | "archive">(null);
  const [error, setError] = useState<string | null>(null);
  const advance = ADVANCE[task.state];
  const isArchived = task.archived_at !== null;
  const meta = BUCKET_META[bucket];
  const [delivery, setDelivery] = useState<Delivery | null>(null);

  // A done task carries a handoff record (commit + files); surface it so "done"
  // shows what was actually delivered. Fetched only for done cards (few).
  useEffect(() => {
    if (task.state !== "done") { setDelivery(null); return; }
    const ctrl = new AbortController();
    fetchDelivery(task.task_id, ctrl.signal).then(setDelivery).catch(() => {});
    return () => ctrl.abort();
  }, [task.state, task.task_id]);

  const run = async (op: () => Promise<TaskManifest>) => {
    setBusy(true); setError(null);
    try {
      onAdvanced(await op());
      setPending(null);
    } catch (err) {
      setError(err instanceof ApiError && err.status === 409 ? t("portfolio.errors.conflict") : (err as Error).message);
      if (err instanceof ApiError && err.status === 409) { setPending(null); await onConflict(); }
    } finally { setBusy(false); }
  };

  return (
    <article className={cn("rounded-xl border border-l-4 bg-card p-4 shadow-sm", meta.border, isArchived && "opacity-60")}>
      <div className="flex items-start justify-between gap-2">
        <Badge variant="outline">{task.project_id}</Badge>
        <span className={cn("inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium", meta.pill)}>
          <span className={cn("size-1.5 rounded-full", meta.dot)} />
          {t(`portfolio.buckets.${bucket}`)}
        </span>
      </div>
      <h3 className="mt-3 font-semibold leading-snug">{task.title}</h3>
      {ask && <p className="mt-1 line-clamp-2 rounded-md bg-muted/50 px-2 py-1 text-xs leading-relaxed">{ask}</p>}
      {!ask && task.description && <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">{task.description}</p>}
      <div className="mt-2 flex items-center gap-2 text-[11px]">
        {/* "No runs yet" is a claim about the task; absence from a page the board did
            not fully read is not that claim. Which of the three this is is decided
            once, by taskRunsLineOf — asking `latestRun ?` here as well would be the
            same rule in two places, and the reassuring branch is the one it would
            eventually disagree about. */}
        {runLine.kind === "run" && latestRun ? (
          <>
            <span className="flex items-center gap-1 text-muted-foreground">
              <span className={cn("size-1.5 rounded-full", RUN_TONE[latestRun.state])} />
              {t("portfolio.card.run.status", { adapter: latestRun.adapter, state: te("runState", latestRun.state) })}
            </span>
            <a href={`/runs?task=${encodeURIComponent(task.task_id)}`} className="font-medium text-primary hover:underline">{t("portfolio.card.run.viewResult")}</a>
          </>
        ) : (
          <span className="text-muted-foreground">
            {runLine.kind === "none"
              ? t("portfolio.card.run.none")
              : t("portfolio.card.run.unknown")}
          </span>
        )}
      </div>
      {delivery && (delivery.commit || delivery.fileCount > 0) && (
        <div className="mt-1 flex items-center gap-1.5 text-[11px] text-emerald-600 dark:text-emerald-400">
          <FileCheck2 className="size-3" />
          <span className="text-muted-foreground">
            {t("portfolio.card.delivery", { count: delivery.fileCount })}
            {delivery.commit ? ` · ${delivery.commit.slice(0, 8)}` : ""}
          </span>
        </div>
      )}
      {dash && dash.next_action.action !== "none" && (
        <p className={cn("mt-2 flex items-center gap-1 text-[11px] font-medium", dash.next_action.attention ? "text-orange-600 dark:text-orange-400" : "text-primary")}>
          <ArrowRight className="size-3 shrink-0" />
          {t("portfolio.cockpit.card.next", { action: te("nextAction", dash.next_action.action) })}
        </p>
      )}
      {/* The card used to render "Verified N%" here, from `dash.verified_weight` over
          `dash.total_weight`. That is the same number the cockpit tile above removed,
          reached the same way: `tasks/progress.py` marks the task's *current* criterion
          set verified whenever the task is verified/done and any historical worker run
          succeeded, without binding that run to the spec revision the criteria belong
          to. A task whose requirements were revised after a successful run therefore
          read 100% while nothing had verified the new criteria.
          The page had already written down why that number cannot be shown and then
          went on showing it one component away — the defect is not the wording, it is
          the evidence, and no label repairs it. The lifecycle state below is kept: it
          is authoritative state, not a verification claim. Restoring a percentage here
          needs evidence bound to the current revision, which is a backend change. */}
      <div className="mt-4 flex items-center justify-between gap-2">
        <span className="text-xs capitalize text-muted-foreground">{te("taskState", task.state)} · P{task.priority}</span>
        <span className="flex items-center gap-3">
          {isArchived ? (
            <button
              disabled={busy}
              onClick={() => void run(() => unarchiveTask(task))}
              className="flex items-center gap-1 text-xs font-medium hover:underline disabled:opacity-50"
            >
              {busy ? <RefreshCw className="size-3 animate-spin" /> : null}{t("portfolio.card.restore")}
            </button>
          ) : (
            <>
              {advance && !pending && (
                <button
                  disabled={busy}
                  onClick={() => { setError(null); setPending("advance"); }}
                  className="flex items-center gap-1 text-xs font-medium hover:underline disabled:opacity-50"
                >
                  {t(advance.labelKey)} <ArrowRight className="size-3" />
                </button>
              )}
              {(task.state === "requirements" || task.state === "design") && (
                <a href={`/requirements?task=${task.task_id}`} className="text-xs font-medium hover:underline">{t("portfolio.card.requirements")}</a>
              )}
              {task.state === "planned" && (
                <a href={`/runs?new=${encodeURIComponent(task.task_id)}`} className="text-xs font-medium hover:underline">{t("portfolio.card.dispatchRun")}</a>
              )}
              <a href={`/control-plane?project=${encodeURIComponent(task.project_id)}&task=${encodeURIComponent(task.task_id)}`} className="text-xs font-medium hover:underline">{t("portfolio.card.authority")}</a>
              {!pending && (
                <button
                  disabled={busy}
                  onClick={() => { setError(null); setPending("archive"); }}
                  className="text-xs font-medium text-muted-foreground hover:text-destructive hover:underline disabled:opacity-50"
                >
                  {t("portfolio.card.archive")}
                </button>
              )}
            </>
          )}
        </span>
      </div>

      {pending === "advance" && advance && (
        <div className="mt-3 space-y-2 rounded-lg border bg-muted/30 p-3">
          <p className="text-xs font-medium">{t("portfolio.card.confirm.question", { from: te("taskState", task.state), to: te("taskState", advance.to) })}</p>
          <p className="text-[11px] leading-4 text-muted-foreground">{t(`portfolio.card.confirm.hint.${advance.to}`)}</p>
          {advance.to === "review" && (
            <a href={`/runs?project=${encodeURIComponent(task.project_id)}`} className="inline-block text-[11px] font-medium text-primary hover:underline">{t("portfolio.card.confirm.viewRuns")}</a>
          )}
          <div className="flex items-center justify-end gap-2 pt-1">
            <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => { setPending(null); setError(null); }}>{t("portfolio.card.confirm.cancel")}</Button>
            <Button type="button" size="sm" variant={advance.to === "done" ? "destructive" : "default"} disabled={busy} onClick={() => void run(() => advance.to === "done" ? handoffTask(task).then((r) => r.task) : transitionTask(task, advance.to))}>{busy ? <RefreshCw className="animate-spin" /> : null}{t("portfolio.card.confirm.confirm")}</Button>
          </div>
        </div>
      )}

      {pending === "archive" && (
        <div className="mt-3 space-y-2 rounded-lg border bg-muted/40 p-3">
          <p className="text-xs font-medium">{t("portfolio.card.confirmArchive.question", { title: task.title })}</p>
          <p className="text-[11px] leading-4 text-muted-foreground">{t("portfolio.card.confirmArchive.hint")}</p>
          <div className="flex items-center justify-end gap-2 pt-1">
            <Button type="button" variant="ghost" size="sm" disabled={busy} onClick={() => { setPending(null); setError(null); }}>{t("portfolio.card.confirmArchive.keep")}</Button>
            <Button type="button" size="sm" disabled={busy} onClick={() => void run(() => archiveTask(task))}>{busy ? <RefreshCw className="animate-spin" /> : null}{t("portfolio.card.confirmArchive.confirm")}</Button>
          </div>
        </div>
      )}
      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
    </article>
  );
}

function CreateTaskPanel({ projects, onClose, onCreated }: { projects: string[]; onClose: () => void; onCreated: (task: TaskManifest) => void }) {
  const { t, te } = useI18n();
  const [projectId, setProjectId] = useState(projects[0] ?? "");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [risk, setRisk] = useState<TaskRisk>("medium");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const panelRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    const panel = panelRef.current;
    const focusable = () => Array.from(panel?.querySelectorAll<HTMLElement>("button, input, textarea, select, [href], [tabindex]:not([tabindex='-1'])") ?? []).filter((item) => !item.hasAttribute("disabled"));
    focusable()[0]?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { onClose(); return; }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => document.removeEventListener("keydown", handleKeyDown);
  }, [onClose]);

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/35" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <form
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-task-title"
        className="h-full w-full max-w-lg space-y-5 overflow-y-auto bg-background p-6 shadow-2xl"
        onSubmit={async (event) => {
          event.preventDefault(); setSubmitting(true); setError(null);
          try { onCreated(await createTask({ project_id: projectId.trim(), title: title.trim(), description: description.trim(), risk })); }
          catch (err) { setError((err as Error).message); setSubmitting(false); }
        }}
      >
        <div className="flex items-center justify-between">
          <div><p className="text-xs uppercase tracking-widest text-muted-foreground">{t("portfolio.create.eyebrow")}</p><h2 id="create-task-title" className="text-xl font-bold">{t("portfolio.create.title")}</h2></div>
          <Button type="button" variant="ghost" size="icon" onClick={onClose} aria-label={t("portfolio.create.close")}><X /></Button>
        </div>
        <Field label={t("portfolio.create.fields.projectId")}><input list="project-ids" required value={projectId} onChange={(e) => setProjectId(e.target.value)} className="field" placeholder={t("portfolio.create.fields.projectIdPlaceholder")} /><datalist id="project-ids">{projects.map((project) => <option key={project}>{project}</option>)}</datalist></Field>
        <Field label={t("portfolio.create.fields.title")}><input required value={title} onChange={(e) => setTitle(e.target.value)} className="field" placeholder={t("portfolio.create.fields.titlePlaceholder")} /></Field>
        <Field label={t("portfolio.create.fields.description")}><textarea value={description} onChange={(e) => setDescription(e.target.value)} className="field min-h-32 resize-y" placeholder={t("portfolio.create.fields.descriptionPlaceholder")} /></Field>
        <Field label={t("portfolio.create.fields.risk")}><select value={risk} onChange={(e) => setRisk(e.target.value as TaskRisk)} className="field"><option value="low">{te("risk", "low")}</option><option value="medium">{te("risk", "medium")}</option><option value="high">{te("risk", "high")}</option><option value="critical">{te("risk", "critical")}</option></select></Field>
        {error && <Notice tone="error">{error}</Notice>}
        <Button type="submit" size="lg" className="w-full" disabled={submitting || !projectId.trim() || !title.trim()}>{submitting ? t("portfolio.create.submitting") : t("portfolio.create.submit")}</Button>
      </form>
    </div>
  );
}

const AGENT_OPTIONS: AdapterName[] = ["codex", "claude", "kiro"];

function NewProjectPanel({ onClose, onCreated }: { onClose: () => void; onCreated: () => Promise<void> }) {
  const { t } = useI18n();
  const [projectId, setProjectId] = useState("");
  const [name, setName] = useState("");
  const [worker, setWorker] = useState<AdapterName>("codex");
  const [reviewer, setReviewer] = useState<AdapterName>("claude");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/35" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-project-title"
        className="h-full w-full max-w-lg space-y-5 overflow-y-auto bg-background p-6 shadow-2xl"
        onSubmit={async (event) => {
          event.preventDefault(); setSubmitting(true); setError(null);
          try { await createProject(projectId.trim(), name.trim() || undefined, { worker_agent: worker, reviewer_agent: reviewer }); await onCreated(); }
          catch (err) { setError((err as Error).message); setSubmitting(false); }
        }}
      >
        <div className="flex items-center justify-between">
          <div><p className="text-xs uppercase tracking-widest text-muted-foreground">{t("portfolio.newProject.eyebrow")}</p><h2 id="create-project-title" className="text-xl font-bold">{t("portfolio.newProject.title")}</h2></div>
          <Button type="button" variant="ghost" size="icon" onClick={onClose} aria-label={t("portfolio.newProject.close")}><X /></Button>
        </div>
        <Field label={t("portfolio.newProject.idLabel")}><input required value={projectId} onChange={(e) => setProjectId(e.target.value)} className="field" placeholder={t("portfolio.newProject.idPlaceholder")} pattern="[A-Za-z0-9][A-Za-z0-9_-]*" /></Field>
        <Field label={t("portfolio.newProject.nameLabel")}><input value={name} onChange={(e) => setName(e.target.value)} className="field" placeholder={t("portfolio.newProject.namePlaceholder")} /></Field>
        <div className="grid gap-3 sm:grid-cols-2">
          <Field label={t("portfolio.newProject.workerLabel")}><select value={worker} onChange={(e) => setWorker(e.target.value as AdapterName)} className="field">{AGENT_OPTIONS.map((a) => <option key={a} value={a}>{a}</option>)}</select></Field>
          <Field label={t("portfolio.newProject.reviewerLabel")}><select value={reviewer} onChange={(e) => setReviewer(e.target.value as AdapterName)} className="field">{AGENT_OPTIONS.map((a) => <option key={a} value={a}>{a}</option>)}</select></Field>
        </div>
        <p className="text-xs text-muted-foreground">{t("portfolio.newProject.agentsHint")}</p>
        <p className="text-xs text-muted-foreground">{t("portfolio.newProject.hint")}</p>
        {error && <Notice tone="error">{error}</Notice>}
        <Button type="submit" size="lg" className="w-full" disabled={submitting || !projectId.trim()}>{submitting ? t("portfolio.newProject.submitting") : t("portfolio.newProject.submit")}</Button>
      </form>
    </div>
  );
}

// The project's CR Log strip. It renders; it does not decide.
//
// Every sentence and its tone come from `crLogHeadlineOf` (lib/cr-log.ts), which
// `node --test` executes directly. That split is not tidiness: while the branching
// lived here in JSX it could not be executed by a test, and the tests written for it
// passed with the component's own logic reverted — a test that cannot fail, guarding
// the one claim in this feature that must never be made wrongly. "No open change
// requests" is a claim, and it is reachable only through `allClearSayable`.
function ProjectChangeLog({ dashboard }: { dashboard: ProjectDashboard }) {
  const { t } = useI18n();
  const view = crLogViewOf(dashboard.cr_log);
  const rows = attentionRowsOf(view);
  const path = dashboard.cr_log?.source ?? ".agora/development/CR-LOG.md";
  const { key, params, tone } = crLogHeadlineOf(view, path);

  return (
    <div className="rounded-xl border bg-muted/30 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="flex items-center gap-1.5 text-xs font-medium uppercase tracking-wider text-muted-foreground">
          <FileCheck2 className="size-3.5" /> {t("portfolio.cockpit.crLog.title")}
        </span>
        <span
          className={cn(
            "text-sm",
            tone === "attention" && "font-semibold text-amber-600 dark:text-amber-400",
            tone === "unknown" && "text-muted-foreground",
          )}
        >
          {t(`portfolio.cockpit.crLog.${key}`, params)}
        </span>
      </div>
      {/* Stated whenever it is true, including beside a non-zero count: the number
          next to it is then a floor, and a floor presented alone is a wrong number. */}
      {view.kind === "rows" && view.unrecognised > 0 && (
        <p className="mt-1 text-xs text-muted-foreground">
          {t("portfolio.cockpit.crLog.unclassified", { count: String(view.unrecognised) })}
        </p>
      )}
      {rows.length > 0 && (
        <ul className="mt-2 space-y-1">
          {rows.map((cr) => (
            <li key={cr.id} className="flex items-baseline gap-2 text-xs leading-relaxed">
              <Badge variant={cr.status_token === "open" ? "default" : "outline"} className="shrink-0">
                {cr.status_token === "open"
                  ? t("portfolio.cockpit.crLog.openBadge")
                  : t("portfolio.cockpit.crLog.unknownBadge")}
              </Badge>
              <span className="shrink-0 font-mono text-muted-foreground">{cr.id}</span>
              <span className="min-w-0 flex-1">{cr.description}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function CharterPanel({ projectId, charter, onClose, onSaved }: { projectId: string; charter: Charter; onClose: () => void; onSaved: (charter: Charter) => void }) {
  const { t } = useI18n();
  const [goal, setGoal] = useState(charter.goal);
  const [nonGoals, setNonGoals] = useState(charter.non_goals.join("\n"));
  const [success, setSuccess] = useState(charter.success_criteria.join("\n"));
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toLines = (value: string) => value.split("\n").map((line) => line.trim()).filter(Boolean);

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/35" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <form
        role="dialog"
        aria-modal="true"
        aria-labelledby="charter-title"
        className="h-full w-full max-w-lg space-y-5 overflow-y-auto bg-background p-6 shadow-2xl"
        onSubmit={async (event) => {
          event.preventDefault(); setSubmitting(true); setError(null);
          const next: Charter = { goal: goal.trim(), non_goals: toLines(nonGoals), success_criteria: toLines(success) };
          try { await setProjectCharter(projectId, next); onSaved(next); }
          catch (err) { setError((err as Error).message); setSubmitting(false); }
        }}
      >
        <div className="flex items-center justify-between">
          <div><p className="text-xs uppercase tracking-widest text-muted-foreground">{t("portfolio.cockpit.charter.eyebrow")}</p><h2 id="charter-title" className="text-xl font-bold">{t("portfolio.cockpit.charter.editTitle")}</h2></div>
          <Button type="button" variant="ghost" size="icon" onClick={onClose} aria-label={t("portfolio.cockpit.charter.close")}><X /></Button>
        </div>
        <Field label={t("portfolio.cockpit.charter.goalLabel")}><textarea value={goal} onChange={(e) => setGoal(e.target.value)} className="field min-h-20 resize-y" placeholder={t("portfolio.cockpit.charter.goalPlaceholder")} /></Field>
        <Field label={t("portfolio.cockpit.charter.successLabel")}><textarea value={success} onChange={(e) => setSuccess(e.target.value)} className="field min-h-28 resize-y" placeholder={t("portfolio.cockpit.charter.successPlaceholder")} /></Field>
        <Field label={t("portfolio.cockpit.charter.nonGoalsLabel")}><textarea value={nonGoals} onChange={(e) => setNonGoals(e.target.value)} className="field min-h-24 resize-y" placeholder={t("portfolio.cockpit.charter.nonGoalsPlaceholder")} /></Field>
        {error && <Notice tone="error">{error}</Notice>}
        <Button type="submit" size="lg" className="w-full" disabled={submitting}>{submitting ? t("portfolio.cockpit.charter.saving") : t("portfolio.cockpit.charter.save")}</Button>
      </form>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block space-y-2 text-sm font-medium"><span>{label}</span>{children}</label>;
}

function Notice({ tone, children }: { tone: "error" | "info"; children: React.ReactNode }) {
  return <div className={cn("rounded-lg border p-3 text-sm", tone === "error" ? "border-destructive/30 bg-destructive/5 text-destructive" : "bg-muted")}>{children}</div>;
}

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ArrowRight, ChevronRight, FolderPlus, Plus, RefreshCw, X } from "lucide-react";
import { DeliveryShell } from "@/components/delivery-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  ApiError,
  archiveTask,
  createProject,
  createTask,
  listOpenAttention,
  listProjects,
  listTasks,
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
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

// Attention-first buckets. A task lands in the highest-priority bucket that
// applies, so "needs you" always wins over "running", etc.
type Bucket = "needsReply" | "needsApproval" | "blocked" | "running" | "verified" | "shaping" | "done" | "closed";

const GROUPS: Array<{ id: "action" | "inProgress" | "completed"; buckets: Bucket[]; collapsible?: boolean }> = [
  { id: "action", buckets: ["needsReply", "needsApproval", "blocked"] },
  { id: "inProgress", buckets: ["running", "verified", "shaping"] },
  { id: "completed", buckets: ["done", "closed"], collapsible: true },
];

// Left-accent + pill color per bucket (theme-aware Tailwind tokens).
const BUCKET_META: Record<Bucket, { border: string; pill: string; dot: string }> = {
  needsReply: { border: "border-l-red-500", pill: "bg-red-500/10 text-red-600 dark:text-red-400", dot: "bg-red-500" },
  needsApproval: { border: "border-l-violet-500", pill: "bg-violet-500/10 text-violet-600 dark:text-violet-400", dot: "bg-violet-500" },
  blocked: { border: "border-l-orange-500", pill: "bg-orange-500/10 text-orange-600 dark:text-orange-400", dot: "bg-orange-500" },
  running: { border: "border-l-blue-500", pill: "bg-blue-500/10 text-blue-600 dark:text-blue-400", dot: "bg-blue-500" },
  verified: { border: "border-l-teal-500", pill: "bg-teal-500/10 text-teal-600 dark:text-teal-400", dot: "bg-teal-500" },
  shaping: { border: "border-l-sky-400", pill: "bg-sky-400/10 text-sky-600 dark:text-sky-300", dot: "bg-sky-400" },
  done: { border: "border-l-emerald-500", pill: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400", dot: "bg-emerald-500" },
  closed: { border: "border-l-zinc-400", pill: "bg-zinc-400/10 text-zinc-500", dot: "bg-zinc-400" },
};

function bucketOf(task: TaskManifest, kinds: Set<AttentionKind>): Bucket {
  if (kinds.has("question")) return "needsReply";
  if (kinds.has("approval") || task.state === "review") return "needsApproval";
  if (kinds.has("blocker") || task.state === "blocked" || task.state === "failed") return "blocked";
  if (task.state === "running") return "running";
  if (task.state === "verified") return "verified";
  if (task.state === "done") return "done";
  if (task.state === "cancelled") return "closed";
  return "shaping"; // backlog / requirements / design / planned
}

export default function PortfolioPage() {
  const { t } = useI18n();
  const [tasks, setTasks] = useState<TaskManifest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [attention, setAttention] = useState<AttentionItem[]>([]);
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [showCreate, setShowCreate] = useState(false);
  const [showNewProject, setShowNewProject] = useState(false);
  const [registeredProjects, setRegisteredProjects] = useState<string[]>([]);
  const [projectFilter, setProjectFilter] = useState("all");
  const [showArchived, setShowArchived] = useState(false);
  const [showCompleted, setShowCompleted] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [loadedTasks, projectList, openAttention, loadedRuns] = await Promise.all([listTasks(), listProjects(), listOpenAttention(), listRuns({ limit: 500 })]);
      setTasks(loadedTasks);
      setRegisteredProjects(projectList.items.map((item) => item.project_id));
      setAttention(openAttention);
      setRuns(loadedRuns);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    const timeout = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timeout);
  }, [load]);

  const projects = useMemo(
    () => Array.from(new Set([...registeredProjects, ...tasks.map((task) => task.project_id)])).sort(),
    [tasks, registeredProjects],
  );
  const scoped = projectFilter === "all" ? tasks : tasks.filter((task) => task.project_id === projectFilter);
  const archivedCount = scoped.filter((task) => task.archived_at).length;
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
      return { task, bucket: bucketOf(task, info?.kinds ?? new Set<AttentionKind>()), ask: info?.ask ?? null };
    }),
    [visible, attentionByTask],
  );

  const replaceTask = (updated: TaskManifest) => {
    setTasks((current) => current.map((task) => (task.task_id === updated.task_id ? updated : task)));
  };

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
              onChange={(event) => setProjectFilter(event.target.value)}
              className="h-9 rounded-lg border bg-background px-3 text-sm"
              aria-label={t("portfolio.filter.label")}
            >
              <option value="all">{t("portfolio.filter.allProjects")}</option>
              {projects.map((project) => <option key={project}>{project}</option>)}
            </select>
            {(archivedCount > 0 || showArchived) && (
              <Button variant={showArchived ? "secondary" : "outline"} size="lg" onClick={() => setShowArchived((value) => !value)} aria-pressed={showArchived}>
                {showArchived ? t("portfolio.archived.hide") : t("portfolio.archived.show", { count: archivedCount })}
              </Button>
            )}
            <Button variant="outline" size="lg" onClick={load} disabled={loading} aria-label={t("portfolio.actions.refresh")}>
              <RefreshCw className={cn("size-4", loading && "animate-spin")} />
            </Button>
            <Button variant="outline" size="lg" onClick={() => setShowNewProject(true)}><FolderPlus /> {t("portfolio.actions.newProject")}</Button>
            <Button size="lg" onClick={() => setShowCreate(true)}><Plus /> {t("portfolio.actions.newTask")}</Button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-[1600px] space-y-6 p-5 md:p-8">
        {error && <Notice tone="error">{t("portfolio.errors.loadFailed", { error })}</Notice>}

        {GROUPS.map((group) => {
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
                <Badge variant="secondary">{cards.length}</Badge>
              </button>
              {group.id === "action" && cards.length === 0 && !loading && (
                <p className="rounded-xl border border-dashed bg-card px-4 py-6 text-center text-sm text-muted-foreground">{t("portfolio.allClear")}</p>
              )}
              {!collapsed && cards.length > 0 && (
                <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
                  {cards.map(({ task, bucket, ask }) => (
                    <TaskCard key={task.task_id} task={task} bucket={bucket} ask={ask} latestRun={latestRunByTask.get(task.task_id) ?? null} onConflict={load} onAdvanced={replaceTask} />
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
    </DeliveryShell>
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

function TaskCard({ task, bucket, ask, latestRun, onAdvanced, onConflict }: { task: TaskManifest; bucket: Bucket; ask: string | null; latestRun: RunSummary | null; onAdvanced: (updated: TaskManifest) => void; onConflict: () => Promise<void> }) {
  const { t, te } = useI18n();
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<null | "advance" | "archive">(null);
  const [error, setError] = useState<string | null>(null);
  const advance = ADVANCE[task.state];
  const isArchived = task.archived_at !== null;
  const meta = BUCKET_META[bucket];

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
        {latestRun ? (
          <>
            <span className="flex items-center gap-1 text-muted-foreground">
              <span className={cn("size-1.5 rounded-full", RUN_TONE[latestRun.state])} />
              {t("portfolio.card.run.status", { adapter: latestRun.adapter, state: te("runState", latestRun.state) })}
            </span>
            <a href={`/runs?task=${encodeURIComponent(task.task_id)}`} className="font-medium text-primary hover:underline">{t("portfolio.card.run.viewResult")}</a>
          </>
        ) : (
          <span className="text-muted-foreground">{t("portfolio.card.run.none")}</span>
        )}
      </div>
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
            <Button type="button" size="sm" variant={advance.to === "done" ? "destructive" : "default"} disabled={busy} onClick={() => void run(() => transitionTask(task, advance.to))}>{busy ? <RefreshCw className="animate-spin" /> : null}{t("portfolio.card.confirm.confirm")}</Button>
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

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block space-y-2 text-sm font-medium"><span>{label}</span>{children}</label>;
}

function Notice({ tone, children }: { tone: "error" | "info"; children: React.ReactNode }) {
  return <div className={cn("rounded-lg border p-3 text-sm", tone === "error" ? "border-destructive/30 bg-destructive/5 text-destructive" : "bg-muted")}>{children}</div>;
}

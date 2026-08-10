"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ArrowRight, Plus, RefreshCw, X } from "lucide-react";
import { DeliveryShell } from "@/components/delivery-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  ApiError,
  archiveTask,
  createTask,
  listTasks,
  transitionTask,
  unarchiveTask,
  type TaskManifest,
  type TaskRisk,
  type TaskState,
} from "@/lib/control-plane";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const columns: Array<{ id: string; states: TaskState[] }> = [
  { id: "intake", states: ["backlog"] },
  { id: "define", states: ["requirements", "design"] },
  { id: "build", states: ["planned", "running"] },
  { id: "verify", states: ["review", "verified"] },
  { id: "complete", states: ["done"] },
];

const attentionStates: TaskState[] = ["blocked", "failed"];

export default function PortfolioPage() {
  const { t, te } = useI18n();
  const [tasks, setTasks] = useState<TaskManifest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [projectFilter, setProjectFilter] = useState("all");
  const [showArchived, setShowArchived] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setTasks(await listTasks());
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
    () => Array.from(new Set(tasks.map((task) => task.project_id))).sort(),
    [tasks],
  );
  const scoped = projectFilter === "all" ? tasks : tasks.filter((task) => task.project_id === projectFilter);
  const archivedCount = scoped.filter((task) => task.archived_at).length;
  const visible = showArchived ? scoped : scoped.filter((task) => !task.archived_at);
  const attention = visible.filter((task) => !task.archived_at && attentionStates.includes(task.state));

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
            <Button size="lg" onClick={() => setShowCreate(true)}><Plus /> {t("portfolio.actions.newTask")}</Button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-[1600px] space-y-6 p-5 md:p-8">
        {error && <Notice tone="error">{t("portfolio.errors.loadFailed", { error })}</Notice>}
        {attention.length > 0 && (
          <section className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-amber-700 dark:text-amber-300">
              <AlertTriangle className="size-4" /> {t("portfolio.attention.heading", { count: attention.length })}
            </div>
            <div className="flex flex-wrap gap-2">
              {attention.map((task) => (
                <a key={task.task_id} href={`/requirements?task=${task.task_id}`} className="rounded-lg border bg-background px-3 py-2 text-sm hover:bg-accent">
                  <span className="font-medium">{task.title}</span>
                  <span className="ml-2 text-xs text-muted-foreground">{task.project_id} · {te("taskState", task.state)}</span>
                </a>
              ))}
            </div>
          </section>
        )}

        <section className="grid gap-4 xl:grid-cols-5">
          {columns.map((column) => {
            const cards = visible.filter((task) => column.states.includes(task.state));
            return (
              <div key={column.id} className="min-h-64 rounded-xl border bg-muted/25 p-3">
                <div className="mb-3 flex items-center justify-between px-1">
                  <h2 className="text-sm font-semibold">{t(`portfolio.columns.${column.id}`)}</h2>
                  <Badge variant="secondary">{cards.length}</Badge>
                </div>
                <div className="space-y-3">
                  {cards.map((task) => (
                    <TaskCard key={task.task_id} task={task} onConflict={load} onAdvanced={replaceTask} />
                  ))}
                  {!loading && cards.length === 0 && <p className="px-1 py-8 text-center text-xs text-muted-foreground">{t("portfolio.empty")}</p>}
                </div>
              </div>
            );
          })}
        </section>
      </div>

      {showCreate && (
        <CreateTaskPanel
          projects={projects}
          onClose={() => setShowCreate(false)}
          onCreated={(task) => { setTasks((current) => [task, ...current]); setShowCreate(false); }}
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

function TaskCard({ task, onAdvanced, onConflict }: { task: TaskManifest; onAdvanced: (updated: TaskManifest) => void; onConflict: () => Promise<void> }) {
  const { t, te } = useI18n();
  const [busy, setBusy] = useState(false);
  const [pending, setPending] = useState<null | "advance" | "archive">(null);
  const [error, setError] = useState<string | null>(null);
  const advance = ADVANCE[task.state];
  const isArchived = task.archived_at !== null;

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
    <article className={cn("rounded-xl border bg-card p-4 shadow-sm", isArchived && "opacity-60")}>
      <div className="flex items-start justify-between gap-2">
        <Badge variant="outline">{task.project_id}</Badge>
        {isArchived
          ? <Badge variant="secondary">{t("portfolio.card.archivedBadge")}</Badge>
          : <span className={cn("text-xs font-medium", riskColor(task.risk))}>{te("risk", task.risk)}</span>}
      </div>
      <h3 className="mt-3 font-semibold leading-snug">{task.title}</h3>
      {task.description && <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">{task.description}</p>}
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

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block space-y-2 text-sm font-medium"><span>{label}</span>{children}</label>;
}

function Notice({ tone, children }: { tone: "error" | "info"; children: React.ReactNode }) {
  return <div className={cn("rounded-lg border p-3 text-sm", tone === "error" ? "border-destructive/30 bg-destructive/5 text-destructive" : "bg-muted")}>{children}</div>;
}

function riskColor(risk: TaskRisk): string {
  if (risk === "critical") return "text-red-600 dark:text-red-400";
  if (risk === "high") return "text-orange-600 dark:text-orange-400";
  if (risk === "medium") return "text-amber-600 dark:text-amber-400";
  return "text-emerald-600 dark:text-emerald-400";
}

"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, ArrowRight, Plus, RefreshCw, X } from "lucide-react";
import { DeliveryShell } from "@/components/delivery-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  ApiError,
  createTask,
  listTasks,
  transitionTask,
  type TaskManifest,
  type TaskRisk,
  type TaskState,
} from "@/lib/control-plane";
import { cn } from "@/lib/utils";

const columns: Array<{ label: string; states: TaskState[] }> = [
  { label: "Intake", states: ["backlog"] },
  { label: "Define", states: ["requirements", "design"] },
  { label: "Build", states: ["planned", "running"] },
  { label: "Verify", states: ["review", "verified"] },
  { label: "Complete", states: ["done"] },
];

const attentionStates: TaskState[] = ["blocked", "failed"];

export default function PortfolioPage() {
  const [tasks, setTasks] = useState<TaskManifest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [projectFilter, setProjectFilter] = useState("all");

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
  const visible = projectFilter === "all" ? tasks : tasks.filter((task) => task.project_id === projectFilter);
  const attention = visible.filter((task) => attentionStates.includes(task.state));

  const replaceTask = (updated: TaskManifest) => {
    setTasks((current) => current.map((task) => (task.task_id === updated.task_id ? updated : task)));
  };

  return (
    <DeliveryShell active="Portfolio">
      <header className="border-b bg-background/80 px-5 py-5 backdrop-blur md:px-8">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground">All projects</p>
            <h1 className="mt-1 text-2xl font-bold">Delivery Portfolio</h1>
          </div>
          <div className="flex items-center gap-2">
            <select
              value={projectFilter}
              onChange={(event) => setProjectFilter(event.target.value)}
              className="h-9 rounded-lg border bg-background px-3 text-sm"
              aria-label="Filter by project"
            >
              <option value="all">All projects</option>
              {projects.map((project) => <option key={project}>{project}</option>)}
            </select>
            <Button variant="outline" size="lg" onClick={load} disabled={loading} aria-label="Refresh tasks">
              <RefreshCw className={cn("size-4", loading && "animate-spin")} />
            </Button>
            <Button size="lg" onClick={() => setShowCreate(true)}><Plus /> New task</Button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-[1600px] space-y-6 p-5 md:p-8">
        {error && <Notice tone="error">Could not load control plane: {error}</Notice>}
        {attention.length > 0 && (
          <section className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-amber-700 dark:text-amber-300">
              <AlertTriangle className="size-4" /> Needs attention ({attention.length})
            </div>
            <div className="flex flex-wrap gap-2">
              {attention.map((task) => (
                <a key={task.task_id} href={`/tasks?task=${task.task_id}`} className="rounded-lg border bg-background px-3 py-2 text-sm hover:bg-accent">
                  <span className="font-medium">{task.title}</span>
                  <span className="ml-2 text-xs text-muted-foreground">{task.project_id} · {task.state}</span>
                </a>
              ))}
            </div>
          </section>
        )}

        <section className="grid gap-4 xl:grid-cols-5">
          {columns.map((column) => {
            const cards = visible.filter((task) => column.states.includes(task.state));
            return (
              <div key={column.label} className="min-h-64 rounded-xl border bg-muted/25 p-3">
                <div className="mb-3 flex items-center justify-between px-1">
                  <h2 className="text-sm font-semibold">{column.label}</h2>
                  <Badge variant="secondary">{cards.length}</Badge>
                </div>
                <div className="space-y-3">
                  {cards.map((task) => (
                    <TaskCard key={task.task_id} task={task} onConflict={load} onTransition={async () => replaceTask(await transitionTask(task, "requirements"))} />
                  ))}
                  {!loading && cards.length === 0 && <p className="px-1 py-8 text-center text-xs text-muted-foreground">No tasks</p>}
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

function TaskCard({ task, onTransition, onConflict }: { task: TaskManifest; onTransition: () => Promise<void>; onConflict: () => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  return (
    <article className="rounded-xl border bg-card p-4 shadow-sm">
      <div className="flex items-start justify-between gap-2">
        <Badge variant="outline">{task.project_id}</Badge>
        <span className={cn("text-xs font-medium", riskColor(task.risk))}>{task.risk}</span>
      </div>
      <h3 className="mt-3 font-semibold leading-snug">{task.title}</h3>
      {task.description && <p className="mt-1 line-clamp-2 text-xs leading-relaxed text-muted-foreground">{task.description}</p>}
      <div className="mt-4 flex items-center justify-between gap-2">
        <span className="text-xs capitalize text-muted-foreground">{task.state} · P{task.priority}</span>
        {task.state === "backlog" ? (
          <button
            disabled={busy}
            onClick={async () => {
              setBusy(true); setError(null);
              try { await onTransition(); } catch (err) {
                setError(err instanceof ApiError && err.status === 409 ? "Task changed elsewhere. The board was refreshed; try again." : (err as Error).message);
                if (err instanceof ApiError && err.status === 409) await onConflict();
              }
              finally { setBusy(false); }
            }}
            className="flex items-center gap-1 text-xs font-medium hover:underline disabled:opacity-50"
          >
            Define <ArrowRight className="size-3" />
          </button>
        ) : (
          <a href={`/tasks?task=${task.task_id}`} className="text-xs font-medium hover:underline">Open</a>
        )}
      </div>
      {error && <p className="mt-2 text-xs text-destructive">{error}</p>}
    </article>
  );
}

function CreateTaskPanel({ projects, onClose, onCreated }: { projects: string[]; onClose: () => void; onCreated: (task: TaskManifest) => void }) {
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
          <div><p className="text-xs uppercase tracking-widest text-muted-foreground">Control plane</p><h2 id="create-task-title" className="text-xl font-bold">Create task</h2></div>
          <Button type="button" variant="ghost" size="icon" onClick={onClose} aria-label="Close create task dialog"><X /></Button>
        </div>
        <Field label="Project ID"><input list="project-ids" required value={projectId} onChange={(e) => setProjectId(e.target.value)} className="field" placeholder="my-project" /><datalist id="project-ids">{projects.map((project) => <option key={project}>{project}</option>)}</datalist></Field>
        <Field label="Title"><input required value={title} onChange={(e) => setTitle(e.target.value)} className="field" placeholder="What should the agents deliver?" /></Field>
        <Field label="Description"><textarea value={description} onChange={(e) => setDescription(e.target.value)} className="field min-h-32 resize-y" placeholder="Context, desired outcome, and important constraints" /></Field>
        <Field label="Risk"><select value={risk} onChange={(e) => setRisk(e.target.value as TaskRisk)} className="field"><option>low</option><option>medium</option><option>high</option><option>critical</option></select></Field>
        {error && <Notice tone="error">{error}</Notice>}
        <Button type="submit" size="lg" className="w-full" disabled={submitting || !projectId.trim() || !title.trim()}>{submitting ? "Creating…" : "Create in backlog"}</Button>
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

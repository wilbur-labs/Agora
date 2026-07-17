"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, CircleDot, Play, Plus, RefreshCw, RotateCcw, ShieldCheck, X } from "lucide-react";
import { DeliveryShell } from "@/components/delivery-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiError, listTasks, type TaskManifest, type TaskRisk } from "@/lib/control-plane";
import {
  approveOrchestration,
  attachOrchestration,
  createOrchestratedTask,
  getOrchestration,
  resumeOrchestration,
  retryStage,
  runNextStage,
  type OrchestrationStage,
  type TaskOrchestrationStatus,
} from "@/lib/orchestration";
import { cn } from "@/lib/utils";

export default function TaskWorkbenchPage() {
  const [tasks, setTasks] = useState<TaskManifest[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<TaskOrchestrationStatus | null>(null);
  const [missingPlan, setMissingPlan] = useState(false);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [approvalReason, setApprovalReason] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const mounted = useRef(true);
  const selectedRef = useRef("");
  const detailRequest = useRef(0);
  const detailAbort = useRef<AbortController | null>(null);
  const actionGuard = useRef(false);

  const selectedTask = tasks.find((task) => task.task_id === selectedId) ?? null;
  const projects = useMemo(
    () => Array.from(new Set(["agora", ...tasks.map((task) => task.project_id)])).sort(),
    [tasks],
  );

  const loadDetail = useCallback(async (taskId: string) => {
    const requestId = ++detailRequest.current;
    detailAbort.current?.abort();
    const controller = new AbortController();
    detailAbort.current = controller;
    setDetailLoading(true); setError(null); setMissingPlan(false);
    try {
      const loaded = await getOrchestration(taskId, controller.signal);
      if (!mounted.current || requestId !== detailRequest.current) return;
      setDetail(loaded); setMissingPlan(false);
    } catch (err) {
      if (!mounted.current || requestId !== detailRequest.current || (err as Error).name === "AbortError") return;
      if (err instanceof ApiError && err.status === 404) {
        setDetail(null); setMissingPlan(true);
      } else setError((err as Error).message);
    } finally {
      if (mounted.current && requestId === detailRequest.current) setDetailLoading(false);
    }
  }, []);

  const loadTasks = useCallback(async (preferQuery = false) => {
    setLoading(true); setError(null);
    try {
      const loaded = await listTasks();
      if (!mounted.current) return;
      setTasks(loaded);
      const queryTask = preferQuery ? new URLSearchParams(window.location.search).get("task") ?? "" : "";
      setSelectedId((current) => {
        const desired = queryTask || current;
        const selected = loaded.some((task) => task.task_id === desired) ? desired : loaded[0]?.task_id ?? "";
        selectedRef.current = selected;
        return selected;
      });
    } catch (err) { if (mounted.current) setError((err as Error).message); }
    finally { if (mounted.current) setLoading(false); }
  }, []);

  useEffect(() => {
    mounted.current = true;
    const timer = window.setTimeout(() => void loadTasks(true), 0);
    return () => {
      mounted.current = false; detailRequest.current += 1;
      window.clearTimeout(timer); detailAbort.current?.abort();
    };
  }, [loadTasks]);

  useEffect(() => {
    if (!selectedId) { setDetail(null); setMissingPlan(false); return; }
    const timer = window.setTimeout(() => void loadDetail(selectedId), 0);
    return () => window.clearTimeout(timer);
  }, [loadDetail, selectedId]);

  const selectTask = (taskId: string) => {
    if (taskId === selectedId) return;
    selectedRef.current = taskId;
    detailRequest.current += 1;
    detailAbort.current?.abort();
    setSelectedId(taskId); setDetail(null); setMissingPlan(false);
    const query = new URLSearchParams(window.location.search);
    query.set("task", taskId);
    window.history.replaceState(null, "", `${window.location.pathname}?${query}`);
  };

  const act = async (action: () => Promise<TaskOrchestrationStatus | unknown>) => {
    if (!selectedId || actionGuard.current) return;
    const taskId = selectedId;
    actionGuard.current = true; setActing(true); setError(null);
    const actionRequestId = ++detailRequest.current;
    detailAbort.current?.abort();
    try {
      const result = await action();
      if (!mounted.current || selectedRef.current !== taskId || detailRequest.current !== actionRequestId) return;
      if (isStatus(result)) { setDetail(result); setMissingPlan(false); }
      else await loadDetail(taskId);
    } catch (err) {
      if (mounted.current && selectedRef.current === taskId && detailRequest.current === actionRequestId) {
        const message = (err as Error).message;
        await loadDetail(taskId);
        if (mounted.current && selectedRef.current === taskId) setError(message);
      }
    } finally {
      actionGuard.current = false;
      if (mounted.current) setActing(false);
    }
  };

  return (
    <DeliveryShell active="Task Workbench">
      <header className="border-b bg-background px-5 py-5 md:px-8">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center justify-between gap-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground">Provisional AI-DLC demo</p>
            <h1 className="mt-1 text-2xl font-bold">Task Workbench</h1>
          </div>
          <div className="flex gap-2">
            <Button variant="outline" size="lg" onClick={() => void loadTasks(false)} disabled={loading || acting}>
              <RefreshCw className={cn("size-4", loading && "animate-spin")} /> Refresh
            </Button>
            <Button size="lg" onClick={() => setShowCreate(true)} disabled={acting}><Plus /> New guided task</Button>
          </div>
        </div>
      </header>

      <div className="mx-auto grid max-w-[1500px] gap-6 p-5 md:p-8 lg:grid-cols-[320px_minmax(0,1fr)]">
        <aside className="space-y-3">
          <div className="flex items-center justify-between px-1">
            <h2 className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">Tasks</h2>
            <Badge variant="secondary">{tasks.length}</Badge>
          </div>
          {tasks.map((task) => (
            <button key={task.task_id} onClick={() => selectTask(task.task_id)} className={cn(
              "w-full rounded-xl border p-4 text-left transition-colors",
              task.task_id === selectedId ? "border-primary bg-primary/5" : "bg-card hover:bg-accent/50",
            )}>
              <div className="flex items-center justify-between gap-2">
                <Badge variant="outline">{task.project_id}</Badge>
                <span className="text-xs capitalize text-muted-foreground">{task.state}</span>
              </div>
              <p className="mt-3 text-sm font-semibold leading-snug">{task.title}</p>
              <p className="mt-1 text-xs text-muted-foreground">{task.kind === "aidlc_foundation" ? "Guided planning" : "Attachable task"}</p>
            </button>
          ))}
          {!loading && tasks.length === 0 && <Empty compact title="No tasks yet" detail="Create a guided task to try the orchestration demo." />}
        </aside>

        <main className="min-w-0 space-y-5">
          {error && <Notice tone="error">{error}</Notice>}
          {!selectedTask ? <Empty title="Select or create a task" detail="Agora will keep its plan, stage, run, and usage state here." /> : detailLoading ? (
            <Empty title="Loading authoritative state…" detail="Reading the persisted Task orchestration record." />
          ) : missingPlan ? (
            <section className="rounded-2xl border bg-card p-6">
              <Badge variant="outline">Existing task</Badge>
              <h2 className="mt-4 text-xl font-bold">{selectedTask.title}</h2>
              <p className="mt-2 text-sm text-muted-foreground">This Task has no guided plan. Attach the provisional three-runtime planning method without changing its existing lifecycle state.</p>
              <Button className="mt-5" disabled={acting} onClick={() => void act(() => attachOrchestration(selectedTask.task_id))}>
                <Plus /> {acting ? "Attaching…" : "Attach demo plan"}
              </Button>
            </section>
          ) : detail ? (
            <>
              <section className="rounded-2xl border bg-card p-6 shadow-sm">
                <div className="flex flex-wrap items-start justify-between gap-4">
                  <div>
                    <div className="flex flex-wrap items-center gap-2">
                      <Badge variant="outline">{selectedTask.project_id}</Badge>
                      <Badge variant="secondary">{detail.plan.methodology_id}@{detail.plan.methodology_version}</Badge>
                      {detail.plan.provisional && <Badge className="bg-amber-500/15 text-amber-700 hover:bg-amber-500/15 dark:text-amber-300">Provisional</Badge>}
                    </div>
                    <h2 className="mt-4 text-2xl font-bold">{selectedTask.title}</h2>
                    {selectedTask.description && <p className="mt-2 max-w-3xl text-sm leading-relaxed text-muted-foreground">{selectedTask.description}</p>}
                  </div>
                  <PlanStateBadge state={detail.plan.state} />
                </div>
                <div className="mt-5 rounded-xl border bg-muted/30 p-4">
                  <p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">Next safe action</p>
                  <p className="mt-1 text-sm font-medium">{detail.next_safe_action}</p>
                </div>
                <div className="mt-5 flex flex-wrap items-center gap-3">
                  {detail.plan.state === "active" && !detail.stages.some((stage) => stage.state === "running") && (
                    <Button disabled={acting} onClick={() => void act(() => runNextStage(selectedTask.task_id))}>
                      <Play /> {acting ? "Runtime is working…" : "Run next stage"}
                    </Button>
                  )}
                  {detail.runs.some((run) => run.state === "running") && (
                    <Button disabled={acting} variant="outline" onClick={() => void act(() => resumeOrchestration(selectedTask.task_id))}>
                      <RotateCcw /> Reconcile interrupted run
                    </Button>
                  )}
                  {acting && <span role="status" className="text-xs text-muted-foreground">Keep this page open. A native CLI review can take several minutes.</span>}
                </div>
              </section>

              <Usage status={detail} />

              <section className="space-y-3">
                <h3 className="px-1 text-xs font-semibold uppercase tracking-widest text-muted-foreground">Planning and review stages</h3>
                <div className="grid gap-4 xl:grid-cols-3">
                  {detail.stages.map((stage) => (
                    <StageCard key={stage.stage_id} stage={stage} acting={acting} onRetry={() => void act(() => retryStage(selectedTask.task_id, stage.stage_key))} />
                  ))}
                </div>
              </section>

              {detail.plan.state === "awaiting_approval" && (
                <section className="rounded-2xl border border-emerald-500/30 bg-emerald-500/5 p-6">
                  <div className="flex items-center gap-2 text-emerald-700 dark:text-emerald-300"><ShieldCheck className="size-5" /><h3 className="font-bold">Human decision required</h3></div>
                  <p className="mt-2 text-sm text-muted-foreground">All three read-only stages passed. Approval only marks this reviewed plan ready for a later implementation workflow.</p>
                  <textarea className="field mt-4 min-h-24 resize-y" value={approvalReason} onChange={(event) => setApprovalReason(event.target.value)} placeholder="Why is this plan acceptable?" maxLength={4000} />
                  <Button className="mt-3" disabled={acting || !approvalReason.trim()} onClick={() => void act(async () => {
                    const result = await approveOrchestration(selectedTask.task_id, approvalReason.trim());
                    if (mounted.current) setApprovalReason("");
                    return result;
                  })}><CheckCircle2 /> Approve reviewed plan</Button>
                </section>
              )}

              <section className="space-y-3">
                <h3 className="px-1 text-xs font-semibold uppercase tracking-widest text-muted-foreground">Run history</h3>
                {detail.runs.length === 0 ? <Empty compact title="No runtime runs yet" detail="Run the next stage when you are ready." /> : detail.runs.slice().reverse().map((run) => (
                  <details key={run.run_id} className="rounded-xl border bg-card p-4">
                    <summary className="cursor-pointer list-none">
                      <div className="flex flex-wrap items-center justify-between gap-3">
                        <div className="flex items-center gap-2"><Badge variant="outline">{run.adapter}</Badge><span className="text-sm font-semibold">{run.stage_key} · attempt {run.attempt}</span></div>
                        <span className="text-xs capitalize text-muted-foreground">{run.state} · exit {run.exit_code ?? "n/a"}{run.timed_out ? " · timeout" : ""}</span>
                      </div>
                      {run.semantic_summary && <p className="mt-3 text-sm text-muted-foreground">{run.semantic_summary}</p>}
                    </summary>
                    {run.findings.length > 0 && <ul className="mt-4 list-disc space-y-1 pl-5 text-sm">{run.findings.map((finding, index) => <li key={`${run.run_id}-${index}`}>{finding}</li>)}</ul>}
                    {run.error_message && <Notice tone="error">{run.error_message}</Notice>}
                    <pre className="mt-4 max-h-80 overflow-auto whitespace-pre-wrap rounded-lg bg-muted p-3 text-xs">{run.output || "No stdout captured."}</pre>
                  </details>
                ))}
              </section>
            </>
          ) : null}
        </main>
      </div>

      {showCreate && <CreateDialog projects={projects} onClose={() => setShowCreate(false)} onCreated={(task) => {
        setTasks((current) => [task, ...current.filter((item) => item.task_id !== task.task_id)]);
        selectedRef.current = task.task_id;
        setSelectedId(task.task_id); setShowCreate(false);
        const query = new URLSearchParams(window.location.search); query.set("task", task.task_id);
        window.history.replaceState(null, "", `${window.location.pathname}?${query}`);
      }} />}
    </DeliveryShell>
  );
}

function StageCard({ stage, acting, onRetry }: { stage: OrchestrationStage; acting: boolean; onRetry: () => void }) {
  return <article className="rounded-xl border bg-card p-5">
    <div className="flex items-center justify-between gap-2"><Badge variant="outline">{stage.adapter}</Badge><span className="text-xs capitalize text-muted-foreground">{stage.state}</span></div>
    <h4 className="mt-4 font-bold">{stage.sequence}. {stage.title}</h4>
    <p className="mt-1 text-xs text-muted-foreground">{stage.role} · {stage.token_budget.toLocaleString()} tokens · {stage.attempt_count} attempts</p>
    {stage.semantic_summary && <p className="mt-4 text-sm leading-relaxed">{stage.semantic_summary}</p>}
    {stage.blockers.map((blocker, index) => <div key={index} className="mt-3 flex gap-2 text-xs text-destructive"><AlertTriangle className="mt-0.5 size-3 shrink-0" />{blocker}</div>)}
    {stage.state === "blocked" && <Button className="mt-4" size="sm" variant="outline" disabled={acting} onClick={onRetry}><RotateCcw /> Retry stage</Button>}
  </article>;
}

function Usage({ status }: { status: TaskOrchestrationStatus }) {
  const total = status.plan.total_token_budget;
  const spent = status.tokens_used + status.tokens_reserved;
  const percent = total ? Math.min(100, Math.round((spent / total) * 100)) : 0;
  return <section className="grid gap-4 rounded-2xl border bg-card p-5 md:grid-cols-3">
    <Metric label="Token budget" value={total.toLocaleString()} detail={`${percent}% used or reserved`} />
    <Metric label="Estimated usage" value={status.tokens_used.toLocaleString()} detail={`${status.tokens_remaining.toLocaleString()} remaining`} />
    <Metric label="Cost" value={status.cost_used_usd === null ? "Unavailable" : `$${status.cost_used_usd.toFixed(2)}`} detail={status.cost_measurement === "unavailable" ? "Native CLIs did not report cost; never treated as zero" : status.cost_measurement} />
  </section>;
}

function Metric({ label, value, detail }: { label: string; value: string; detail: string }) {
  return <div><p className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{label}</p><p className="mt-1 text-xl font-bold">{value}</p><p className="mt-1 text-xs text-muted-foreground">{detail}</p></div>;
}

function PlanStateBadge({ state }: { state: TaskOrchestrationStatus["plan"]["state"] }) {
  const ready = state === "ready_for_implementation";
  const blocked = state === "blocked";
  return <div className={cn("flex items-center gap-2 rounded-full border px-3 py-2 text-sm font-semibold", ready ? "border-emerald-500/30 text-emerald-700 dark:text-emerald-300" : blocked ? "border-destructive/30 text-destructive" : "bg-muted")}>
    {ready ? <CheckCircle2 className="size-4" /> : blocked ? <AlertTriangle className="size-4" /> : <CircleDot className="size-4" />}
    {state.replaceAll("_", " ")}
  </div>;
}

function CreateDialog({ projects, onClose, onCreated }: { projects: string[]; onClose: () => void; onCreated: (task: TaskManifest) => void }) {
  const [projectId, setProjectId] = useState(projects[0] ?? "agora");
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [risk, setRisk] = useState<TaskRisk>("medium");
  const [tokens, setTokens] = useState("30000");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const submitGuard = useRef(false);
  const busyRef = useRef(false);
  const dialogRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    const previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const focusable = () => Array.from(dialogRef.current?.querySelectorAll<HTMLElement>(
      "button, input, textarea, select, [href], [tabindex]:not([tabindex='-1'])",
    ) ?? []).filter((item) => !item.hasAttribute("disabled"));
    focusable()[1]?.focus();
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && !busyRef.current) {
        event.preventDefault(); event.stopPropagation(); onClose(); return;
      }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (items.length === 0) return;
      const first = items[0]; const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => { document.removeEventListener("keydown", handleKeyDown); previousFocus?.focus(); };
  }, [onClose]);

  return <div className="fixed inset-0 z-50 grid place-items-center bg-black/40 p-4" onMouseDown={(event) => event.target === event.currentTarget && !busy && onClose()}>
    <form ref={dialogRef} role="dialog" aria-modal="true" aria-labelledby="guided-task-title" className="max-h-[90vh] w-full max-w-xl space-y-4 overflow-y-auto rounded-2xl bg-background p-6 shadow-2xl" onSubmit={async (event) => {
      event.preventDefault(); if (submitGuard.current) return; submitGuard.current = true; busyRef.current = true; setBusy(true); setError(null);
      try { onCreated(await createOrchestratedTask({ project_id: projectId.trim(), title: title.trim(), description: description.trim(), risk, total_token_budget: Number(tokens) })); }
      catch (err) { setError((err as Error).message); submitGuard.current = false; busyRef.current = false; setBusy(false); }
    }}>
      <div className="flex items-start justify-between gap-4"><div><p className="text-xs uppercase tracking-widest text-muted-foreground">Read-only planning demo</p><h2 id="guided-task-title" className="text-xl font-bold">New guided task</h2></div><Button type="button" size="icon" variant="ghost" disabled={busy} onClick={onClose} aria-label="Close"><X /></Button></div>
      <Notice tone="info">Creates a Task plus the provisional Codex → Claude → Kiro planning method. It does not implement code or modify the project.</Notice>
      <Field label="Project ID"><input required list="workbench-projects" className="field" value={projectId} onChange={(event) => setProjectId(event.target.value)} maxLength={128} /><datalist id="workbench-projects">{projects.map((project) => <option key={project}>{project}</option>)}</datalist></Field>
      <Field label="Title"><input required className="field" value={title} onChange={(event) => setTitle(event.target.value)} maxLength={300} placeholder="What should the three runtimes plan and review?" /></Field>
      <Field label="Description"><textarea className="field min-h-28 resize-y" value={description} onChange={(event) => setDescription(event.target.value)} maxLength={20000} placeholder="Context, desired outcome, and constraints" /></Field>
      <div className="grid gap-4 sm:grid-cols-2"><Field label="Risk"><select className="field" value={risk} onChange={(event) => setRisk(event.target.value as TaskRisk)}><option>low</option><option>medium</option><option>high</option><option>critical</option></select></Field><Field label="Token budget"><input required className="field" type="number" min={3000} max={10000000} step={1000} value={tokens} onChange={(event) => setTokens(event.target.value)} /></Field></div>
      {error && <Notice tone="error">{error}</Notice>}
      <Button type="submit" size="lg" className="w-full" disabled={busy || !projectId.trim() || !title.trim() || Number(tokens) < 3000}>{busy ? "Creating…" : "Create guided task"}</Button>
    </form>
  </div>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="block space-y-2 text-sm font-medium"><span>{label}</span>{children}</label>; }
function Empty({ title, detail, compact = false }: { title: string; detail: string; compact?: boolean }) { return <div className={cn("rounded-2xl border border-dashed text-center", compact ? "p-6" : "p-12")}><p className="font-semibold">{title}</p><p className="mt-1 text-sm text-muted-foreground">{detail}</p></div>; }
function Notice({ tone, children }: { tone: "error" | "info"; children: React.ReactNode }) { return <div className={cn("mt-3 rounded-lg border p-3 text-sm", tone === "error" ? "border-destructive/30 bg-destructive/5 text-destructive" : "bg-muted text-muted-foreground")}>{children}</div>; }
function isStatus(value: unknown): value is TaskOrchestrationStatus { return typeof value === "object" && value !== null && "plan" in value && "stages" in value; }

"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { GitBranch, Play, RefreshCw, RotateCw, X } from "lucide-react";
import { DeliveryShell } from "@/components/delivery-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { usePoll } from "@/hooks/use-poll";
import { ApiError } from "@/lib/control-plane";
import { cn } from "@/lib/utils";
import {
  activateWorkflow, dispatchWorkflow, getWorkflow, listWorkflows,
  type WorkflowDispatchResult, type WorkflowManifest, type WorkflowState,
  type WorkflowStep, type WorkflowStepState, type WorkflowSummary,
} from "@/lib/workflows";

const terminal = new Set<WorkflowState>(["completed", "failed", "cancelled"]);

export default function WorkflowsPage() {
  const [items, setItems] = useState<WorkflowSummary[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<WorkflowManifest | null>(null);
  const [loading, setLoading] = useState(true);
  const [acting, setActing] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dispatchResult, setDispatchResult] = useState<WorkflowDispatchResult | null>(null);
  const mounted = useRef(true);
  const listAbort = useRef<AbortController | null>(null);
  const detailAbort = useRef<AbortController | null>(null);
  const detailRequest = useRef(0);
  const selectedRef = useRef("");

  const loadList = useCallback(async (initial = false) => {
    listAbort.current?.abort(); const controller = new AbortController(); listAbort.current = controller;
    if (initial) setLoading(true);
    try {
      const loaded = await listWorkflows(controller.signal);
      if (!mounted.current) return;
      setItems(loaded);
      setSelectedId((current) => loaded.some((item) => item.workflow_id === current) ? current : loaded[0]?.workflow_id || "");
      setError(null);
    } catch (err) { if ((err as Error).name !== "AbortError" && mounted.current) setError((err as Error).message); }
    finally { if (mounted.current && initial) setLoading(false); }
  }, []);

  const loadDetail = useCallback(async () => {
    if (!selectedId) { setDetail(null); return; }
    const requestId = ++detailRequest.current;
    detailAbort.current?.abort(); const controller = new AbortController(); detailAbort.current = controller;
    try {
      const loaded = await getWorkflow(selectedId, controller.signal);
      if (!mounted.current || requestId !== detailRequest.current || selectedRef.current !== loaded.workflow_id) return;
      setDetail((current) => current?.workflow_id === loaded.workflow_id && current.version > loaded.version ? current : loaded);
    }
    catch (err) { if ((err as Error).name !== "AbortError" && mounted.current) setError((err as Error).message); }
  }, [selectedId]);

  useEffect(() => {
    mounted.current = true; const timer = window.setTimeout(() => void loadList(true), 0);
    return () => { mounted.current = false; detailRequest.current += 1; window.clearTimeout(timer); listAbort.current?.abort(); detailAbort.current?.abort(); };
  }, [loadList]);
  useEffect(() => { const timer = window.setTimeout(() => void loadDetail(), 0); return () => window.clearTimeout(timer); }, [loadDetail]);
  useEffect(() => { selectedRef.current = selectedId; detailRequest.current += 1; detailAbort.current?.abort(); }, [selectedId]);
  usePoll(() => loadList(false), 5_000, true);
  usePoll(loadDetail, 3_000, Boolean(detail && !terminal.has(detail.state)));

  const graph = useMemo(() => detail ? dagLevels(detail.steps) : { levels: [], unresolved: false }, [detail]);
  const refreshAll = async () => { await loadList(false); await loadDetail(); };
  const resyncConflict = async (err: unknown) => {
    if (err instanceof ApiError && err.status === 409) {
      setError("Workflow changed elsewhere. Current state was refreshed; review before retrying.");
      await refreshAll(); return true;
    }
    return false;
  };

  return (
    <DeliveryShell active="Workflows">
      <header className="border-b bg-background/85 px-5 py-5 backdrop-blur md:px-8">
        <div className="mx-auto flex max-w-[1600px] flex-wrap items-center justify-between gap-4">
          <div><p className="text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground">Cross-project orchestration</p><h1 className="mt-1 text-2xl font-bold">Workflow Operations</h1></div>
          <Button variant="outline" size="lg" onClick={() => void refreshAll()} disabled={loading || acting}><RefreshCw className={cn("size-4", loading && "animate-spin")} /> Refresh</Button>
        </div>
      </header>
      <div className="mx-auto grid max-w-[1600px] gap-5 p-5 md:p-8 xl:grid-cols-[340px_minmax(0,1fr)]">
        {error && <div className="xl:col-span-2 flex items-center justify-between rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive" role="alert"><span>{error}</span><button onClick={() => setError(null)} aria-label="Dismiss error"><X className="size-4" /></button></div>}
        <section className="overflow-hidden rounded-xl border bg-card">
          <div className="border-b px-4 py-3"><h2 className="text-sm font-semibold">Workflows</h2><p className="text-xs text-muted-foreground">{items.length} plans · read polling every 5s</p></div>
          <div className="max-h-[72vh] overflow-y-auto" aria-busy={loading}>
            {items.map((item) => <button key={item.workflow_id} disabled={acting} aria-pressed={selectedId === item.workflow_id} onClick={() => { selectedRef.current = item.workflow_id; setSelectedId(item.workflow_id); setDispatchResult(null); }} className={cn("block w-full border-b p-4 text-left hover:bg-accent/50 disabled:opacity-60", selectedId === item.workflow_id && "bg-primary/5")}>
              <span className="flex items-start justify-between gap-2"><strong className="text-sm">{item.title}</strong><StateBadge state={item.state} /></span>
              <span className="mt-2 block font-mono text-[11px] text-muted-foreground">{shortId(item.workflow_id)}</span>
              <span className="mt-1 block text-xs text-muted-foreground">{item.step_count} steps · {item.ready_count} ready · v{item.version}</span>
            </button>)}
            {!loading && !items.length && <div className="p-10 text-center text-sm text-muted-foreground"><GitBranch className="mx-auto mb-3 size-8 opacity-40" />No workflows yet. Create one through the API; the visual composer is the next increment.</div>}
          </div>
        </section>

        <section className="min-w-0 space-y-5">
          {!detail ? <div className="rounded-xl border border-dashed bg-card p-12 text-center text-sm text-muted-foreground">Select a workflow to inspect its DAG.</div> : <>
            <div className="sr-only" aria-live="polite">Workflow {detail.title} is {detail.state}.</div>
            <div className="rounded-xl border bg-card p-5">
              <div className="flex flex-wrap items-start justify-between gap-4"><div><div className="flex items-center gap-2"><StateBadge state={detail.state} /><span className="font-mono text-xs text-muted-foreground">{detail.workflow_id}</span></div><h2 className="mt-2 text-xl font-bold">{detail.title}</h2>{detail.description && <p className="mt-1 text-sm text-muted-foreground">{detail.description}</p>}</div>
                <div className="flex gap-2">{detail.state === "draft" && <Button disabled={acting} onClick={async () => { const target = detail; setActing(true); detailRequest.current += 1; detailAbort.current?.abort(); try { const updated = await activateWorkflow(target); if (selectedRef.current === target.workflow_id) setDetail((current) => !current || current.version <= updated.version ? updated : current); await loadList(false); } catch (err) { if (!await resyncConflict(err)) setError((err as Error).message); } finally { if (mounted.current) setActing(false); } }}><Play /> Activate</Button>}
                  {(detail.state === "active" || detail.state === "failed") && <Button disabled={acting} onClick={async () => { const targetId = detail.workflow_id; setActing(true); detailRequest.current += 1; detailAbort.current?.abort(); try { const result = await dispatchWorkflow(targetId); if (selectedRef.current === targetId) setDispatchResult(result); await refreshAll(); } catch (err) { if (!await resyncConflict(err)) setError((err as Error).message); } finally { if (mounted.current) setActing(false); } }}><RotateCw className={cn(acting && "animate-spin")} /> {detail.state === "failed" ? "Cleanup" : "Dispatch / reconcile"}</Button>}</div>
              </div>
              {dispatchResult && <div className="mt-4 rounded-lg border bg-muted/30 p-3 text-xs"><p>{dispatchResult.dispatched_run_ids.length} runs dispatched · {dispatchResult.blockers.length} blockers</p>{dispatchResult.blockers.map((item) => <p key={item.step_id} className="mt-1 text-amber-700 dark:text-amber-300">{stepName(detail.steps, item.step_id)}: {item.reason}</p>)}</div>}
            </div>
            <div className="overflow-x-auto rounded-xl border bg-muted/20 p-4">{graph.unresolved && <p className="mb-3 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-700 dark:text-amber-300" role="status">Some dependencies could not be resolved; affected steps are shown in the warning stage.</p>}<div className="flex min-w-max items-stretch gap-8">{graph.levels.map((level, index) => <div key={index} className="w-72 space-y-3"><div className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">{graph.unresolved && index === graph.levels.length - 1 ? "Unresolved" : `Stage ${index + 1}`}</div>{level.map((step) => <StepCard key={step.step_id} step={step} allSteps={detail.steps} />)}</div>)}</div></div>
          </>}
        </section>
      </div>
    </DeliveryShell>
  );
}

function StepCard({ step, allSteps }: { step: WorkflowStep; allSteps: WorkflowStep[] }) {
  return <article className={cn("rounded-xl border bg-card p-4 shadow-sm", step.dispatch_error && "border-amber-500/40")}>
    <div className="flex items-start justify-between gap-2"><Badge variant="outline">{step.project_id}</Badge><StepBadge state={step.state} /></div>
    <h3 className="mt-3 font-semibold">{step.title}</h3><p className="mt-1 text-xs text-muted-foreground">{step.adapter} · v{step.version}</p>
    {step.depends_on.length > 0 && <p className="mt-3 text-xs text-muted-foreground">After: {step.depends_on.map((id) => stepName(allSteps, id)).join(", ")}</p>}
    {step.dispatch_error && <p className="mt-3 rounded-md bg-amber-500/10 p-2 text-xs text-amber-700 dark:text-amber-300">{step.dispatch_error}</p>}
    <div className="mt-3 flex gap-3 text-xs">{step.run_id && <Link className="font-medium hover:underline" href={`/runs?run=${encodeURIComponent(step.run_id)}`}>Open run</Link>}{step.task_id && <Link className="font-medium hover:underline" href={`/requirements?task=${encodeURIComponent(step.task_id)}`}>Open task</Link>}</div>
  </article>;
}

function dagLevels(steps: WorkflowStep[]): { levels: WorkflowStep[][]; unresolved: boolean } {
  const levels = new Map<string, number>(); const pending = new Set(steps.map((step) => step.step_id));
  let unresolved = false; while (pending.size) { let progressed = false; for (const step of steps) { if (!pending.has(step.step_id) || !step.depends_on.every((id) => levels.has(id))) continue; levels.set(step.step_id, step.depends_on.length ? Math.max(...step.depends_on.map((id) => levels.get(id) ?? 0)) + 1 : 0); pending.delete(step.step_id); progressed = true; } if (!progressed) { unresolved = true; break; } }
  const result: WorkflowStep[][] = []; for (const step of steps) { if (pending.has(step.step_id)) continue; const level = levels.get(step.step_id) ?? 0; (result[level] ??= []).push(step); } if (pending.size) result.push(steps.filter((step) => pending.has(step.step_id))); return { levels: result, unresolved };
}
function stepName(steps: WorkflowStep[], id: string) { return steps.find((step) => step.step_id === id)?.title ?? shortId(id); }
function shortId(value: string) { return value.length > 20 ? `${value.slice(0, 12)}…${value.slice(-5)}` : value; }
function StateBadge({ state }: { state: WorkflowState }) { return <Badge variant={state === "failed" ? "destructive" : state === "completed" ? "default" : "secondary"} className="capitalize">{state}</Badge>; }
function StepBadge({ state }: { state: WorkflowStepState }) { return <Badge variant={state === "failed" ? "destructive" : state === "succeeded" ? "default" : "secondary"} className="capitalize">{state}</Badge>; }

"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  CheckCircle2,
  CircleDot,
  Clock3,
  DatabaseZap,
  FileCheck2,
  Fingerprint,
  Gauge,
  KeyRound,
  ListFilter,
  RefreshCw,
  Route,
  ShieldCheck,
  UserRoundCheck,
  X,
} from "lucide-react";
import { DeliveryShell } from "@/components/delivery-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiError } from "@/lib/control-plane";
import {
  clearProtectedControlPlaneView,
  ProjectionRequestLifecycle,
  runProtocolDimensions,
} from "@/lib/control-plane-view";
import {
  getUnifiedTaskProjection,
  getControlPlaneTaskIndex,
  type UnifiedRunProjection,
  type UnifiedStageProjection,
  type UnifiedTaskProjection,
  type UnifiedTaskIndexItem,
} from "@/lib/unified-control-plane";
import { cn } from "@/lib/utils";

const TOKEN_KEY = "agora.controlPlaneBearer";

export default function ControlPlanePage() {
  const [projectId, setProjectId] = useState("");
  const [taskId, setTaskId] = useState("");
  const [token, setToken] = useState("");
  const [projection, setProjection] = useState<UnifiedTaskProjection | null>(null);
  const [taskOptions, setTaskOptions] = useState<UnifiedTaskIndexItem[]>([]);
  const [taskTotal, setTaskTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const lifecycleRef = useRef<ProjectionRequestLifecycle | null>(null);
  if (lifecycleRef.current === null) {
    lifecycleRef.current = new ProjectionRequestLifecycle();
  }
  const lifecycle = lifecycleRef.current;
  const discoveryLifecycleRef = useRef<ProjectionRequestLifecycle | null>(null);
  if (discoveryLifecycleRef.current === null) {
    discoveryLifecycleRef.current = new ProjectionRequestLifecycle();
  }
  const discoveryLifecycle = discoveryLifecycleRef.current;

  const discoverTasks = useCallback(async (projectValue: string, bearer: string, selectedTask = "") => {
    const lease = discoveryLifecycle.begin();
    const project = projectValue.trim();
    if (!project || !bearer) {
      setTaskOptions([]);
      setTaskTotal(0);
      setDiscovering(false);
      setError("Project and bearer token are required to discover Tasks.");
      discoveryLifecycle.finish(lease.requestId);
      return;
    }
    setDiscovering(true);
    setError(null);
    try {
      const index = await getControlPlaneTaskIndex(project, bearer, lease.signal);
      if (!discoveryLifecycle.isCurrent(lease.requestId)) return;
      setTaskOptions(index.tasks);
      setTaskTotal(index.page.total);
      if (!selectedTask && index.tasks.length > 0) setTaskId(index.tasks[0].task_id);
      window.sessionStorage.setItem(TOKEN_KEY, bearer);
    } catch (err) {
      if ((err as Error).name === "AbortError" || !discoveryLifecycle.isCurrent(lease.requestId)) return;
      setTaskOptions([]);
      setTaskTotal(0);
      setError(messageFor(err));
    } finally {
      if (discoveryLifecycle.isCurrent(lease.requestId)) {
        setDiscovering(false);
        discoveryLifecycle.finish(lease.requestId);
      }
    }
  }, [discoveryLifecycle]);

  const loadProjection = useCallback(async (projectValue: string, taskValue: string, bearer: string) => {
    const lease = lifecycle.begin();
    const project = projectValue.trim();
    const task = taskValue.trim();
    if (!project || !task || !bearer) {
      setProjection(null);
      setLoading(false);
      setError("Project, Task, and bearer token are required.");
      lifecycle.finish(lease.requestId);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const next = await getUnifiedTaskProjection(project, task, bearer, lease.signal);
      if (!lifecycle.isCurrent(lease.requestId)) return;
      setProjection(next);
      window.sessionStorage.setItem(TOKEN_KEY, bearer);
      const query = new URLSearchParams({ project, task });
      window.history.replaceState(null, "", `${window.location.pathname}?${query}`);
    } catch (err) {
      if ((err as Error).name === "AbortError" || !lifecycle.isCurrent(lease.requestId)) return;
      setProjection(null);
      setError(messageFor(err));
    } finally {
      if (lifecycle.isCurrent(lease.requestId)) {
        setLoading(false);
        lifecycle.finish(lease.requestId);
      }
    }
  }, [lifecycle]);

  const load = useCallback(
    () => loadProjection(projectId, taskId, token),
    [loadProjection, projectId, taskId, token],
  );

  useEffect(() => {
    const query = new URLSearchParams(window.location.search);
    const project = query.get("project") ?? "";
    const task = query.get("task") ?? "";
    const bearer = window.sessionStorage.getItem(TOKEN_KEY) ?? "";
    setProjectId(project);
    setTaskId(task);
    setToken(bearer);
    setReady(true);
    if (project && task && bearer) {
      const timeout = window.setTimeout(() => {
        void discoverTasks(project, bearer, task);
        void loadProjection(project, task, bearer);
      }, 0);
      return () => window.clearTimeout(timeout);
    }
    if (project && bearer) {
      const timeout = window.setTimeout(() => void discoverTasks(project, bearer), 0);
      return () => window.clearTimeout(timeout);
    }
  }, [discoverTasks, loadProjection]);

  useEffect(() => () => {
    lifecycle.invalidate();
    discoveryLifecycle.invalidate();
  }, [discoveryLifecycle, lifecycle]);

  return (
    <DeliveryShell active="Control Plane">
      <header className="border-b bg-background/90 px-5 py-5 backdrop-blur md:px-8">
        <div className="mx-auto flex max-w-[1520px] flex-wrap items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground"><ShieldCheck className="size-3.5" /> Consensus authority</div>
            <h1 className="mt-1 text-2xl font-bold">Task Control Plane</h1>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="h-7 gap-1.5 border-emerald-500/30 bg-emerald-500/5 text-emerald-700 dark:text-emerald-300"><DatabaseZap /> Read-only snapshot</Badge>
            {projection && <Button variant="outline" size="lg" onClick={() => void load()} disabled={loading}><RefreshCw className={cn(loading && "animate-spin")} /> Refresh</Button>}
          </div>
        </div>
      </header>

      <main className="mx-auto max-w-[1520px] space-y-5 p-5 md:p-8">
        <ConnectionPanel
          projectId={projectId}
          taskId={taskId}
          token={token}
          tasks={taskOptions}
          taskTotal={taskTotal}
          loading={loading}
          discovering={discovering}
          ready={ready}
          onProject={(value) => {
            lifecycle.invalidate();
            discoveryLifecycle.invalidate();
            setProjectId(value);
            setTaskOptions([]);
            setTaskTotal(0);
            setProjection(null);
            setLoading(false);
            setDiscovering(false);
          }}
          onTask={(value) => {
            lifecycle.invalidate();
            setTaskId(value);
            setProjection(null);
            setLoading(false);
          }}
          onToken={(value) => {
            lifecycle.invalidate();
            discoveryLifecycle.invalidate();
            window.sessionStorage.removeItem(TOKEN_KEY);
            const cleared = clearProtectedControlPlaneView({
              projection,
              tasks: taskOptions,
              taskTotal,
              error,
            });
            setToken(value);
            setProjection(cleared.projection);
            setTaskOptions(cleared.tasks);
            setTaskTotal(cleared.taskTotal);
            setError(cleared.error);
            setLoading(false);
            setDiscovering(false);
          }}
          onConnect={() => void load()}
          onDiscover={() => void discoverTasks(projectId, token, taskId)}
          onForget={() => {
            lifecycle.invalidate();
            discoveryLifecycle.invalidate();
            window.sessionStorage.removeItem(TOKEN_KEY);
            setToken("");
            setProjection(null);
            setTaskOptions([]);
            setTaskTotal(0);
            setLoading(false);
            setDiscovering(false);
            setError(null);
          }}
        />

        {error && <div role="alert" className="flex items-center justify-between gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive"><span className="flex items-center gap-2"><AlertTriangle className="size-4" />{error}</span><Button variant="ghost" size="icon-sm" onClick={() => setError(null)} aria-label="Dismiss error"><X /></Button></div>}

        {!projection && !loading && (
          <section className="grid min-h-72 place-items-center rounded-2xl border border-dashed bg-card p-8 text-center">
            <div className="max-w-md"><Fingerprint className="mx-auto size-10 text-muted-foreground/50" /><h2 className="mt-4 text-lg font-semibold">Inspect one authoritative Task</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">Connect with a <code>control_plane.read</code> credential. The token stays in this browser tab and is never written to the URL.</p></div>
          </section>
        )}

        {projection && <ProjectionDashboard projection={projection} />}
      </main>
    </DeliveryShell>
  );
}

function ConnectionPanel({ projectId, taskId, token, tasks, taskTotal, loading, discovering, ready, onProject, onTask, onToken, onConnect, onDiscover, onForget }: {
  projectId: string;
  taskId: string;
  token: string;
  tasks: UnifiedTaskIndexItem[];
  taskTotal: number;
  loading: boolean;
  discovering: boolean;
  ready: boolean;
  onProject: (value: string) => void;
  onTask: (value: string) => void;
  onToken: (value: string) => void;
  onConnect: () => void;
  onDiscover: () => void;
  onForget: () => void;
}) {
  return (
    <form className="grid gap-3 rounded-2xl border bg-card p-4 shadow-sm md:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)_minmax(0,1.5fr)_auto]" onSubmit={(event) => { event.preventDefault(); onConnect(); }} aria-label="Control Plane connection">
      <Field label="Project ID"><input className="field font-mono" value={projectId} onChange={(event) => onProject(event.target.value)} placeholder="agora" autoComplete="off" /></Field>
      <Field label={tasks.length > 0 ? (taskTotal > tasks.length ? `Task (${tasks.length} of ${taskTotal} shown; any ID accepted)` : `Task (${taskTotal} inspectable)`) : "Task ID"}><input list="control-plane-task-options" className="field font-mono" value={taskId} onChange={(event) => onTask(event.target.value)} placeholder="Choose a suggestion or enter a Task ID" autoComplete="off" /><datalist id="control-plane-task-options">{tasks.map((task) => <option key={task.task_id} value={task.task_id}>{`[${task.task_state.replaceAll("_", " ")}] ${task.title}`}</option>)}</datalist></Field>
      <Field label="Bearer token"><div className="relative"><KeyRound className="pointer-events-none absolute left-3 top-2.5 size-4 text-muted-foreground" /><input className="field pl-9 font-mono" type="password" value={token} onChange={(event) => onToken(event.target.value)} placeholder="Stored for this tab only" autoComplete="off" /></div></Field>
      <div className="flex flex-wrap items-end gap-2 md:col-span-2 xl:col-span-1"><Button type="button" variant="outline" size="lg" onClick={onDiscover} disabled={!ready || discovering || loading}>{discovering ? <RefreshCw className="animate-spin" /> : <ListFilter />}{discovering ? "Finding" : "Find Tasks"}</Button><Button type="submit" size="lg" className="min-w-24" disabled={!ready || loading || discovering}>{loading ? <RefreshCw className="animate-spin" /> : <ArrowRight />}{loading ? "Loading" : "Connect"}</Button>{token && <Button type="button" variant="ghost" size="lg" onClick={onForget}>Forget</Button>}</div>
    </form>
  );
}

function ProjectionDashboard({ projection }: { projection: UnifiedTaskProjection }) {
  const state = projection.task_state;
  const progressPercent = projection.progress.total_stages ? Math.round(((projection.progress.completed_stages ?? 0) / projection.progress.total_stages) * 100) : null;
  const drift = state !== null && projection.task.state !== state;
  const safeAction = projection.next_safe_action.value ?? projection.next_safe_action.unavailable_reason ?? "No gate-derived action is currently available.";
  return (
    <div className="space-y-5">
      <section className="overflow-hidden rounded-2xl border bg-[radial-gradient(circle_at_top_right,var(--color-muted),transparent_42%)] shadow-sm">
        <div className="grid gap-6 p-5 md:p-7 lg:grid-cols-[minmax(0,1.5fr)_minmax(300px,0.8fr)]">
          <div>
            <div className="flex flex-wrap items-center gap-2"><StatusBadge value={state ?? "unavailable"} prominent /><Badge variant="outline">authority: {projection.task_state_source}</Badge><Badge variant="outline">v{projection.task_state_version ?? "—"}</Badge>{projection.task_state_lifecycle === "reconciliation_required" && <Badge variant="destructive">reconciliation required</Badge>}</div>
            <p className="mt-5 font-mono text-xs text-muted-foreground">{projection.task.task_id}</p>
            <h2 className="mt-1 max-w-3xl text-2xl font-bold tracking-tight md:text-3xl">{projection.task.title}</h2>
            {projection.task.description && <p className="mt-3 max-w-3xl text-sm leading-6 text-muted-foreground">{projection.task.description}</p>}
            <div className="mt-5 flex flex-wrap gap-x-6 gap-y-2 text-xs text-muted-foreground"><span>Project <strong className="text-foreground">{projection.task.project_id}</strong></span><span>Schema <strong className="text-foreground">{projection.schema_version}</strong></span><span>Snapshot <strong className="text-foreground">{formatDate(projection.snapshot_at)}</strong></span></div>
          </div>
          <div className="rounded-xl border bg-background/80 p-4">
            <div className="flex items-center justify-between text-sm"><span className="font-medium">Formal stage progress</span><span className="font-mono text-muted-foreground">{progressPercent === null ? "—" : `${progressPercent}%`}</span></div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-foreground transition-[width]" style={{ width: `${progressPercent ?? 0}%` }} /></div>
            <dl className="mt-4 grid grid-cols-2 gap-3 text-xs"><Fact label="Completed" value={projection.progress.completed_stages === null ? "—" : `${projection.progress.completed_stages} / ${projection.progress.total_stages}`} /><Fact label="Current" value={projection.progress.current_stage_key ?? "Not routed"} /><Fact label="Route source" value={routeSourceLabel(projection.progress.current_stage_source)} /><Fact label="Inventory" value={projection.progress.inventory_complete ? "Complete" : "Unavailable"} /><Fact label="Plan" value={projection.plan.state} /></dl>
          </div>
        </div>
        <div className="grid border-t bg-background/60 md:grid-cols-[minmax(0,1fr)_minmax(280px,0.35fr)]">
          <div className="p-5 md:px-7"><p className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted-foreground"><Route className="size-3.5" /> Next safe action</p><p className="mt-2 text-sm font-medium leading-6">{safeAction}</p>{projection.next_safe_action.source_gate_key && <p className="mt-1 font-mono text-xs text-muted-foreground">gate: {projection.next_safe_action.source_gate_key}</p>}</div>
          <div className={cn("border-t p-5 md:border-l md:border-t-0", drift && "bg-amber-500/5")}><p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">Legacy compatibility state</p><p className="mt-2 flex items-center gap-2 text-sm font-semibold capitalize">{projection.task.state}{drift && <AlertTriangle className="size-4 text-amber-600" />}</p><p className="mt-1 text-xs text-muted-foreground">Informational only; never routing authority.</p></div>
        </div>
      </section>

      {(projection.task_state_unavailable_reason || projection.progress.inventory_unavailable_reason || projection.stage_route_unavailable_reason) && (
        <section className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 text-sm"><p className="flex items-center gap-2 font-semibold text-amber-800 dark:text-amber-200"><AlertTriangle className="size-4" /> Authority readiness</p><ul className="mt-2 space-y-1 text-xs leading-5 text-muted-foreground">{[projection.task_state_unavailable_reason, projection.progress.inventory_unavailable_reason, projection.stage_route_unavailable_reason].filter((item): item is string => Boolean(item)).map((item) => <li key={item}>• {item}</li>)}</ul></section>
      )}

      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <Metric icon={Gauge} label="Tokens" value={formatNumber(projection.budget.token_remaining)} detail={`${formatNumber(projection.budget.token_reserved)} reserved · ${projection.budget.token_measurement}`} />
        <Metric icon={Gauge} label="Cost remaining" value={formatCurrency(projection.budget.cost_remaining_usd)} detail={`${formatCurrency(projection.budget.cost_reserved_usd)} reserved · ${projection.budget.cost_measurement}`} />
        <Metric icon={Activity} label="Runs" value={String(projection.collection_totals.runs ?? 0)} detail={`${projection.runs.filter((run) => run.attention_required).length} need attention`} />
        <Metric icon={FileCheck2} label="Evidence" value={String(projection.collection_totals.evidence ?? 0)} detail={`${projection.collection_totals.artifacts ?? 0} artifacts · ${projection.collection_totals.approvals ?? 0} approvals`} />
        <Metric icon={UserRoundCheck} label="Human actions" value={String(projection.required_human_actions.length)} detail={`${projection.collection_totals.attention ?? 0} attention items`} />
      </section>

      <section className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(330px,0.65fr)]"><StageLedger stages={projection.stages} currentStageSource={projection.progress.current_stage_source} /><HumanActions projection={projection} /></section>
      <RunLedger runs={projection.runs} />
      <section className="grid items-start gap-5 xl:grid-cols-2 2xl:grid-cols-4"><ArtifactLedger projection={projection} /><EvidenceLedger projection={projection} /><ApprovalLedger projection={projection} /><AuditLedger projection={projection} /></section>
    </div>
  );
}

function StageLedger({ stages, currentStageSource }: { stages: UnifiedStageProjection[]; currentStageSource: "control_plane_route" | "compatibility_plan" | null }) {
  const sourceLabel = routeSourceLabel(currentStageSource);
  return <section className="overflow-hidden rounded-xl border bg-card"><SectionHeader icon={Route} title="Stage ledger" meta={`${stages.length} stages · ${sourceLabel}`} /><div className="divide-y">{stages.map((stage, index) => <article key={stage.stage_key} className={cn("grid gap-3 p-4 sm:grid-cols-[36px_minmax(0,1fr)_auto]", stage.current && "bg-primary/5")}><div className={cn("grid size-9 place-items-center rounded-full border font-mono text-xs", stage.current && currentStageSource === "control_plane_route" && "border-primary bg-primary text-primary-foreground")}>{stage.sequence ?? index + 1}</div><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="font-medium">{stage.title ?? stage.stage_key}</h3>{stage.current && currentStageSource === "control_plane_route" && <Badge>authority current</Badge>}{stage.current && currentStageSource === "compatibility_plan" && <Badge variant="outline" className="border-amber-500/40 text-amber-700 dark:text-amber-300">compatibility cursor</Badge>}</div><p className="mt-1 font-mono text-xs text-muted-foreground">{stage.stage_key} · {stage.runtime ?? "runtime unassigned"}</p>{stage.semantic_summary && <p className="mt-2 text-xs leading-5 text-muted-foreground">{stage.semantic_summary}</p>}{stage.blockers.length > 0 && <p className="mt-2 text-xs text-destructive">{stage.blockers.join(" · ")}</p>}</div><div className="flex items-start gap-2 sm:justify-end"><StatusBadge value={stage.authoritative_stage?.status ?? "not initialized"} /><StatusBadge value={stage.gate?.status ? `gate ${stage.gate.status}` : "no gate"} muted /></div></article>)}{stages.length === 0 && <EmptyState text="No stages are present in the projection." />}</div></section>;
}

function HumanActions({ projection }: { projection: UnifiedTaskProjection }) {
  return <section className="overflow-hidden rounded-xl border bg-card"><SectionHeader icon={UserRoundCheck} title="Human checkpoint" meta={`${projection.required_human_actions.length} required`} /><div className="divide-y">{projection.required_human_actions.map((action) => <article key={action.action_id} className="p-4"><div className="flex items-start gap-3"><CircleDot className="mt-0.5 size-4 text-amber-600" /><div><p className="text-sm font-medium">{action.title}</p><p className="mt-1 font-mono text-[11px] text-muted-foreground">{action.kind} · {action.source_id}</p></div></div></article>)}{projection.required_human_actions.length === 0 && <EmptyState icon={CheckCircle2} text="No human action is currently required." />}</div><div className="border-t bg-muted/25 p-4"><p className="text-xs font-medium text-muted-foreground">Methodology</p><p className="mt-1 text-sm">{projection.plan.methodology_id} <span className="text-muted-foreground">v{projection.plan.methodology_version}{projection.plan.provisional ? " · provisional" : ""}</span></p></div></section>;
}

function RunLedger({ runs }: { runs: UnifiedRunProjection[] }) {
  return <section className="overflow-hidden rounded-xl border bg-card"><SectionHeader icon={Activity} title="Protocol runs" meta={`${runs.length} in this page · dimensions stay separate`} /><div className="overflow-x-auto"><table className="w-full min-w-[1180px] text-left text-sm"><caption className="sr-only">Run wait, process, exit, transport, schema, and semantic status</caption><thead className="bg-muted/40 text-xs text-muted-foreground"><tr><th className="px-4 py-3">Run / stage</th><th className="px-3 py-3">Runtime</th><th className="px-3 py-3">Wait</th><th className="px-3 py-3">Process</th><th className="px-3 py-3">Exit</th><th className="px-3 py-3">Transport</th><th className="px-3 py-3">Schema</th><th className="px-3 py-3">Semantic result</th><th className="px-3 py-3">Elapsed</th></tr></thead><tbody className="divide-y">{runs.map((run) => { const dimensions = runProtocolDimensions(run); return <tr key={run.run_id}><th scope="row" className="px-4 py-3"><span className="block font-mono text-xs">{shortId(run.run_id)}</span><span className="mt-1 block font-normal text-muted-foreground">{run.stage_key}</span></th><td className="px-3 py-3 capitalize">{run.runtime ?? "—"}</td><td className="px-3 py-3"><StatusBadge value={dimensions.wait} muted /></td><td className="px-3 py-3"><StatusBadge value={dimensions.process} /></td><td className={cn("px-3 py-3 font-mono text-xs", dimensions.exitFailed && "font-semibold text-destructive")}>{dimensions.exit}</td><td className="px-3 py-3"><StatusBadge value={run.transport_status ?? "unavailable"} muted /></td><td className="px-3 py-3"><StatusBadge value={run.schema_status ?? "unavailable"} muted /></td><td className="px-3 py-3"><StatusBadge value={dimensions.semantic} /><span className="mt-1 block text-[11px] text-muted-foreground">{run.semantic_source}</span></td><td className="px-3 py-3 font-mono text-xs text-muted-foreground">{formatDuration(run.elapsed_seconds)}</td></tr>; })}</tbody></table></div>{runs.length === 0 && <EmptyState icon={Clock3} text="No protocol runs have been recorded." />}</section>;
}

function ArtifactLedger({ projection }: { projection: UnifiedTaskProjection }) {
  return <section className="overflow-hidden rounded-xl border bg-card"><SectionHeader icon={DatabaseZap} title="Artifacts" meta={`${projection.artifacts.length} shown`} /><div className="divide-y">{projection.artifacts.slice(0, 6).map((item) => <article key={`${item.version_ref.artifact_id}-${item.version_ref.version}`} className="p-4"><div className="flex items-center justify-between gap-2"><p className="truncate font-mono text-xs">{item.version_ref.artifact_id}</p><Badge variant="outline">v{item.version_ref.version}</Badge></div><p className="mt-2 text-sm">{item.version_ref.kind}</p><p className="mt-1 text-[11px] text-muted-foreground">{item.stage_key} · {item.producer_runtime}</p><p className="mt-2 truncate font-mono text-[10px] text-muted-foreground" title={item.version_ref.sha256}>sha256 {item.version_ref.sha256}</p></article>)}{projection.artifacts.length === 0 && <EmptyState text="No artifacts registered." />}</div></section>;
}

function EvidenceLedger({ projection }: { projection: UnifiedTaskProjection }) {
  return <section className="overflow-hidden rounded-xl border bg-card"><SectionHeader icon={FileCheck2} title="Evidence" meta={`${projection.evidence.length} shown`} /><div className="divide-y">{projection.evidence.slice(0, 6).map((item) => <article key={item.evidence_id} className="p-4"><div className="flex items-center justify-between gap-2"><p className="truncate font-mono text-xs">{item.evidence_id}</p><StatusBadge value={item.status} /></div><p className="mt-2 line-clamp-2 text-sm">{item.summary}</p><p className="mt-2 text-[11px] text-muted-foreground">{item.kind} · {formatDate(item.observed_at)}</p></article>)}{projection.evidence.length === 0 && <EmptyState text="No evidence registered." />}</div></section>;
}

function ApprovalLedger({ projection }: { projection: UnifiedTaskProjection }) {
  return <section className="overflow-hidden rounded-xl border bg-card"><SectionHeader icon={ShieldCheck} title="Approvals" meta={`${projection.approvals.length} shown`} /><div className="divide-y">{projection.approvals.slice(0, 6).map((item) => <article key={item.approval_id} className="p-4"><div className="flex items-center justify-between gap-2"><p className="truncate font-mono text-xs">{item.approval_id}</p><StatusBadge value={item.status} /></div><p className="mt-2 text-sm">{item.approved_by}</p><p className="mt-1 text-[11px] text-muted-foreground">{item.gate_key} · {formatDate(item.approved_at)}</p>{item.stale_reason && <p className="mt-2 text-xs text-destructive">{item.stale_reason}</p>}</article>)}{projection.approvals.length === 0 && <EmptyState text="No approvals registered." />}</div></section>;
}

function AuditLedger({ projection }: { projection: UnifiedTaskProjection }) {
  return <section className="overflow-hidden rounded-xl border bg-card"><SectionHeader icon={Fingerprint} title="Audit trail" meta={`${projection.audit_events.length} shown`} /><div className="max-h-[430px] divide-y overflow-y-auto">{projection.audit_events.slice(0, 12).map((event) => <article key={`${event.source}-${event.event_id}`} className="p-4"><div className="flex items-center justify-between gap-2"><p className="truncate text-sm font-medium">{event.event_type}</p><Badge variant="outline">{event.source}</Badge></div><p className="mt-1 text-xs text-muted-foreground">{event.actor} · {formatDate(event.created_at)}</p>{event.payload_truncated && <p className="mt-1 text-[11px] text-amber-600">Payload truncated by projection boundary</p>}</article>)}{projection.audit_events.length === 0 && <EmptyState text="No audit events in this page." />}</div></section>;
}

function Metric({ icon: Icon, label, value, detail }: { icon: typeof Activity; label: string; value: string; detail: string }) {
  return <article className="rounded-xl border bg-card p-4"><div className="flex items-center justify-between"><p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{label}</p><Icon className="size-4 text-muted-foreground" /></div><p className="mt-3 text-2xl font-bold">{value}</p><p className="mt-1 text-xs text-muted-foreground">{detail}</p></article>;
}

function SectionHeader({ icon: Icon, title, meta }: { icon: typeof Activity; title: string; meta: string }) {
  return <div className="flex items-center justify-between gap-3 border-b px-4 py-3"><h2 className="flex items-center gap-2 text-sm font-semibold"><Icon className="size-4" />{title}</h2><span className="text-xs text-muted-foreground">{meta}</span></div>;
}

function StatusBadge({ value, prominent = false, muted = false }: { value: string; prominent?: boolean; muted?: boolean }) {
  const normalized = value.toLowerCase();
  const positive = ["active", "ready", "completed", "passed", "valid", "succeeded"].some((item) => normalized.includes(item));
  const negative = ["blocked", "failed", "stale", "cancelled", "reconciliation", "unavailable", "not initialized"].some((item) => normalized.includes(item));
  return <span className={cn("inline-flex w-fit items-center rounded-full border px-2 py-1 text-[11px] font-medium capitalize", prominent && "px-3 py-1.5 text-sm", muted && "bg-muted/40 text-muted-foreground", !muted && positive && "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-300", !muted && negative && "border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-200", !muted && !positive && !negative && "bg-muted/50 text-foreground")}>{value.replaceAll("_", " ")}</span>;
}

function EmptyState({ text, icon: Icon = CircleDot }: { text: string; icon?: typeof CircleDot }) {
  return <div className="p-7 text-center text-xs text-muted-foreground"><Icon className="mx-auto mb-2 size-5 opacity-40" />{text}</div>;
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return <label className="block space-y-1.5 text-xs font-medium text-muted-foreground"><span>{label}</span>{children}</label>;
}

function Fact({ label, value }: { label: string; value: string }) {
  return <div><dt className="text-muted-foreground">{label}</dt><dd className="mt-1 truncate font-medium capitalize text-foreground" title={value}>{value.replaceAll("_", " ")}</dd></div>;
}

function messageFor(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.status === 401) return "Authentication failed. Check the bearer credential.";
    if (error.status === 403) return "This credential cannot read the selected project.";
    if (error.status === 404) return "The Task does not exist in this project, or its orchestration plan is not initialized.";
    if (error.status === 409) return "The authoritative ledgers conflict. Reconciliation is required before this snapshot can be trusted.";
    if (error.status === 503) return "The Control Plane is temporarily busy. Retry in a moment.";
  }
  return error instanceof Error ? error.message : "Could not load the Control Plane snapshot.";
}

function formatNumber(value: number | null): string {
  return value === null ? "unmeasured" : new Intl.NumberFormat().format(value);
}

function formatCurrency(value: number | null): string {
  return value === null ? "unmeasured" : new Intl.NumberFormat(undefined, { style: "currency", currency: "USD" }).format(value);
}

function routeSourceLabel(source: "control_plane_route" | "compatibility_plan" | null): string {
  if (source === "control_plane_route") return "Control Plane route";
  if (source === "compatibility_plan") return "Compatibility Plan cursor";
  return "Not routed";
}

function formatDate(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
}

function formatDuration(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  return `${Math.floor(seconds / 60)}m ${Math.round(seconds % 60)}s`;
}

function shortId(value: string): string {
  return value.length > 18 ? `${value.slice(0, 10)}…${value.slice(-6)}` : value;
}

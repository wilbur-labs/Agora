"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Bell, BellOff, CircleStop, Play, RefreshCw, X } from "lucide-react";
import { DeliveryShell } from "@/components/delivery-shell";
import { RunCreatePanel } from "@/components/run-create-panel";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ApiError, listTasks, type TaskManifest } from "@/lib/control-plane";
import {
  TERMINAL_RUN_STATES,
  asRunSummary,
  cancelRun,
  getRun,
  listRuns,
  type ExecutionAdapter,
  type ExecutionRun,
  type RunState,
  type RunSummary,
} from "@/lib/execution";
import { usePoll } from "@/hooks/use-poll";
import { useRunNotifications } from "@/hooks/use-run-notifications";
import { cn } from "@/lib/utils";

const states: RunState[] = ["queued", "running", "succeeded", "failed", "timed_out", "cancelled", "abandoned"];
const adapters: ExecutionAdapter[] = ["codex", "claude", "kiro"];

export default function RunsPage() {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [tasks, setTasks] = useState<TaskManifest[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [detail, setDetail] = useState<ExecutionRun | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actionMessage, setActionMessage] = useState("");
  const [showCreate, setShowCreate] = useState(false);
  const [cancelTarget, setCancelTarget] = useState<RunSummary | null>(null);
  const [projectFilter, setProjectFilter] = useState("all");
  const [stateFilter, setStateFilter] = useState("all");
  const [adapterFilter, setAdapterFilter] = useState("all");
  const [filtersReady, setFiltersReady] = useState(false);
  const [notificationsEnabled, setNotificationsEnabled] = useState(false);
  const runsRef = useRef<RunSummary[]>([]);
  const listRequestRef = useRef(0);
  const detailRequestRef = useRef(0);
  const listAbortRef = useRef<AbortController | null>(null);
  const detailAbortRef = useRef<AbortController | null>(null);
  const mountedRef = useRef(true);
  const notifyTransitions = useRunNotifications(notificationsEnabled);

  const applyRuns = useCallback((next: RunSummary[], emitNotifications = true) => {
    notifyTransitions(next, emitNotifications);
    runsRef.current = next;
    setRuns(next);
  }, [notifyTransitions]);

  const loadRuns = useCallback(async (initial = false) => {
    const requestId = ++listRequestRef.current;
    listAbortRef.current?.abort();
    const controller = new AbortController();
    listAbortRef.current = controller;
    if (initial) setLoading(true);
    try {
      const loaded = await listRuns({ limit: 100 }, controller.signal);
      if (!mountedRef.current || requestId !== listRequestRef.current) return;
      const current = new Map(runsRef.current.map((run) => [run.run_id, run]));
      const merged = loaded.map((run) => {
        const known = current.get(run.run_id);
        return known && known.version > run.version ? known : run;
      });
      applyRuns(merged);
      setError(null);
    } catch (err) {
      if ((err as Error).name !== "AbortError" && mountedRef.current && requestId === listRequestRef.current) setError((err as Error).message);
    } finally {
      if (listAbortRef.current === controller) listAbortRef.current = null;
      if (mountedRef.current && initial && requestId === listRequestRef.current) setLoading(false);
    }
  }, [applyRuns]);

  const loadTasks = useCallback(async () => {
    try { const loaded = await listTasks(); if (mountedRef.current) setTasks(loaded); }
    catch (err) { if (mountedRef.current) setError((err as Error).message); }
  }, []);

  const loadDetail = useCallback(async () => {
    if (!selectedId) { setDetail(null); return; }
    const requestId = ++detailRequestRef.current;
    detailAbortRef.current?.abort();
    const controller = new AbortController();
    detailAbortRef.current = controller;
    try {
      const loaded = await getRun(selectedId, controller.signal);
      if (!mountedRef.current || requestId !== detailRequestRef.current) return;
      setDetail((current) => current?.run_id === loaded.run_id && current.version > loaded.version ? current : loaded);
      const summary = asRunSummary(loaded);
      const next = runsRef.current.map((run) => run.run_id === summary.run_id && run.version <= summary.version ? summary : run);
      applyRuns(next);
    } catch (err) {
      if ((err as Error).name !== "AbortError" && mountedRef.current && requestId === detailRequestRef.current) setError((err as Error).message);
    } finally {
      if (detailAbortRef.current === controller) detailAbortRef.current = null;
      if (mountedRef.current && requestId === detailRequestRef.current) setDetailLoading(false);
    }
  }, [applyRuns, selectedId]);

  useEffect(() => {
    mountedRef.current = true;
    const timeout = window.setTimeout(() => { void loadRuns(true); void loadTasks(); }, 0);
    const query = new URLSearchParams(window.location.search);
    setProjectFilter(query.get("project") || "all");
    const requestedState = query.get("state");
    const requestedAdapter = query.get("adapter");
    setStateFilter(requestedState && states.includes(requestedState as RunState) ? requestedState : "all");
    setAdapterFilter(requestedAdapter && adapters.includes(requestedAdapter as ExecutionAdapter) ? requestedAdapter : "all");
    setFiltersReady(true);
    const stored = window.localStorage.getItem("agora.runNotifications") === "enabled";
    if (stored && typeof Notification !== "undefined" && Notification.permission === "granted") setNotificationsEnabled(true);
    return () => {
      mountedRef.current = false;
      listRequestRef.current += 1;
      detailRequestRef.current += 1;
      listAbortRef.current?.abort();
      detailAbortRef.current?.abort();
      window.clearTimeout(timeout);
    };
  }, [loadRuns, loadTasks]);

  useEffect(() => {
    if (!selectedId) { setDetail(null); return; }
    setDetailLoading(true);
    const timeout = window.setTimeout(() => void loadDetail(), 0);
    return () => { window.clearTimeout(timeout); detailAbortRef.current?.abort(); detailRequestRef.current += 1; };
  }, [loadDetail, selectedId]);

  useEffect(() => {
    if (!filtersReady) return;
    const query = new URLSearchParams(window.location.search);
    setQuery(query, "project", projectFilter);
    setQuery(query, "state", stateFilter);
    setQuery(query, "adapter", adapterFilter);
    window.history.replaceState(null, "", `${window.location.pathname}${query.size ? `?${query}` : ""}`);
  }, [adapterFilter, filtersReady, projectFilter, stateFilter]);

  usePoll(() => loadRuns(false), 5_000, true);
  const selectedSummary = runs.find((run) => run.run_id === selectedId) ?? null;
  const selectedIsActive = Boolean(selectedSummary && !TERMINAL_RUN_STATES.has(selectedSummary.state));
  usePoll(loadDetail, 3_000, selectedIsActive);

  const taskNames = useMemo(() => new Map(tasks.map((task) => [task.task_id, task.title])), [tasks]);
  const projects = useMemo(() => Array.from(new Set(runs.map((run) => run.project_id))).sort(), [runs]);
  useEffect(() => {
    if (filtersReady && !loading && projectFilter !== "all" && !projects.includes(projectFilter)) setProjectFilter("all");
  }, [filtersReady, loading, projectFilter, projects]);
  const visible = runs.filter((run) =>
    (projectFilter === "all" || run.project_id === projectFilter)
    && (stateFilter === "all" || run.state === stateFilter)
    && (adapterFilter === "all" || run.adapter === adapterFilter));

  const upsertRun = useCallback((run: ExecutionRun, emitNotifications = true) => {
    listRequestRef.current += 1;
    listAbortRef.current?.abort();
    const summary = asRunSummary(run);
    const without = runsRef.current.filter((item) => item.run_id !== summary.run_id);
    applyRuns([summary, ...without], emitNotifications);
    setSelectedId(summary.run_id);
    setDetail(run);
  }, [applyRuns]);

  return (
    <DeliveryShell active="Runs">
      <header className="border-b bg-background/85 px-5 py-5 backdrop-blur md:px-8">
        <div className="mx-auto flex max-w-[1500px] flex-wrap items-center justify-between gap-4">
          <div><p className="text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground">Agent lifecycle</p><h1 className="mt-1 text-2xl font-bold">Execution Run Center</h1></div>
          <div className="flex items-center gap-2">
            <Button
              variant="outline"
              size="lg"
              aria-pressed={notificationsEnabled}
              aria-label={notificationsEnabled ? "Disable browser notifications for run completions" : "Enable browser notifications for run completions"}
              onClick={async () => {
                if (notificationsEnabled) {
                  setNotificationsEnabled(false); window.localStorage.removeItem("agora.runNotifications"); return;
                }
                if (typeof Notification === "undefined") { setError("This browser does not support desktop notifications."); return; }
                const permission = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
                if (permission === "granted") {
                  setNotificationsEnabled(true); window.localStorage.setItem("agora.runNotifications", "enabled"); setActionMessage("Run notifications enabled.");
                } else setError("Notification permission was not granted. You can change it in browser settings.");
              }}
            >
              {notificationsEnabled ? <Bell className="size-4" /> : <BellOff className="size-4" />}
              <span className="hidden sm:inline">{notificationsEnabled ? "Alerts on" : "Alerts off"}</span>
            </Button>
            <Button variant="outline" size="lg" onClick={() => void loadRuns(false)} disabled={loading} aria-label="Refresh execution runs"><RefreshCw className={cn("size-4", loading && "animate-spin")} /></Button>
            <Button size="lg" onClick={() => setShowCreate(true)}><Play /> New run</Button>
          </div>
        </div>
      </header>

      <div className="mx-auto max-w-[1500px] space-y-5 p-5 md:p-8">
        <div className="sr-only" aria-live="polite">{actionMessage}</div>
        {error && <div className="flex items-center justify-between gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive" role="alert"><span>{error}</span><Button variant="ghost" size="icon-sm" onClick={() => setError(null)} aria-label="Dismiss error"><X /></Button></div>}

        <section className="grid gap-3 rounded-xl border bg-card p-4 sm:grid-cols-3" aria-label="Run filters">
          <Filter label="Project" value={projectFilter} onChange={setProjectFilter}><option value="all">All projects</option>{projects.map((project) => <option key={project}>{project}</option>)}</Filter>
          <Filter label="State" value={stateFilter} onChange={setStateFilter}><option value="all">All states</option>{states.map((state) => <option key={state} value={state}>{state.replace("_", " ")}</option>)}</Filter>
          <Filter label="Adapter" value={adapterFilter} onChange={setAdapterFilter}><option value="all">All adapters</option>{adapters.map((adapter) => <option key={adapter}>{adapter}</option>)}</Filter>
        </section>

        <div className="grid items-start gap-5 xl:grid-cols-[minmax(0,1fr)_440px]">
          <section className="min-w-0 overflow-hidden rounded-xl border bg-card">
            <div className="flex items-center justify-between border-b px-4 py-3"><h2 className="text-sm font-semibold">Recent runs</h2><span className="text-xs text-muted-foreground">{visible.length} shown · polling every 5s</span></div>
            <div className="overflow-x-auto">
              <table className="w-full min-w-[780px] text-left text-sm">
                <caption className="sr-only">Execution runs across registered projects</caption>
                <thead className="bg-muted/50 text-xs text-muted-foreground"><tr><th scope="col" className="px-4 py-3">Run</th><th scope="col" className="px-3 py-3">Task</th><th scope="col" className="px-3 py-3">Adapter</th><th scope="col" className="px-3 py-3">State</th><th scope="col" className="px-3 py-3">Timing</th><th scope="col" className="px-3 py-3"><span className="sr-only">Actions</span></th></tr></thead>
                <tbody aria-busy={loading}>
                  {visible.map((run) => (
                    <tr key={run.run_id} className={cn("border-t", selectedId === run.run_id && "bg-primary/5")}>
                      <th scope="row" className="px-4 py-3 font-medium"><button className="font-mono text-xs hover:underline" onClick={() => setSelectedId(run.run_id)}>{shortId(run.run_id)}</button><span className="mt-1 block font-sans text-xs font-normal text-muted-foreground">{run.project_id}</span></th>
                      <td className="max-w-64 px-3 py-3"><span className="block truncate font-medium">{taskNames.get(run.task_id) ?? run.task_id}</span><span className="block truncate font-mono text-[11px] text-muted-foreground">{run.task_id}</span></td>
                      <td className="px-3 py-3 capitalize">{run.adapter}</td>
                      <td className="px-3 py-3"><StateBadge state={run.state} /></td>
                      <td className="px-3 py-3 text-xs text-muted-foreground"><span className="block">{formatDate(run.queued_at)}</span><span className="block">{duration(run)}</span></td>
                      <td className="px-3 py-3 text-right">{isCancellable(run) && <Button variant="destructive" size="sm" onClick={() => setCancelTarget(run)}><CircleStop /> Cancel</Button>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {!loading && visible.length === 0 && <div className="p-10 text-center text-sm text-muted-foreground"><Play className="mx-auto mb-3 size-8 opacity-40" /><p className="font-medium text-foreground">No runs match these filters</p><p className="mt-1">Queue a planned task or adjust the filters.</p></div>}
          </section>

          <RunDetail run={detail?.run_id === selectedId ? detail : null} loading={detailLoading} taskTitle={detail ? taskNames.get(detail.task_id) : undefined} onCancel={(run) => setCancelTarget(asRunSummary(run))} />
        </div>
      </div>

      {showCreate && <RunCreatePanel tasks={tasks} onClose={() => setShowCreate(false)} onRefreshTasks={loadTasks} onCreated={(run) => { upsertRun(run, false); setShowCreate(false); setActionMessage(`${run.adapter} run queued.`); }} />}
      {cancelTarget && (
        <CancelDialog
          run={cancelTarget}
          onClose={() => setCancelTarget(null)}
          onConfirm={async (reason) => {
            try {
              const cancelled = await cancelRun(cancelTarget.run_id, { expected_version: cancelTarget.version, actor: "user", reason: reason || null });
              upsertRun(cancelled, false); setCancelTarget(null); setActionMessage("Run cancelled.");
            } catch (err) {
              if (err instanceof ApiError && err.status === 409) {
                setError("The run changed before cancellation. Authoritative state was refreshed.");
                await loadRuns(false); await loadDetail(); setCancelTarget(null);
              } else setError((err as Error).message);
            }
          }}
        />
      )}
    </DeliveryShell>
  );
}

function RunDetail({ run, loading, taskTitle, onCancel }: { run: ExecutionRun | null; loading: boolean; taskTitle?: string; onCancel: (run: ExecutionRun) => void }) {
  if (!run) return <aside className="rounded-xl border border-dashed bg-card p-8 text-center text-sm text-muted-foreground" aria-busy={loading}>{loading ? "Loading run details…" : "Select a run to inspect its prompt, output, and lifecycle details."}</aside>;
  return (
    <aside className="min-w-0 space-y-4 rounded-xl border bg-card p-5" aria-busy={loading}>
      <div className="flex items-start justify-between gap-3"><div><p className="font-mono text-xs text-muted-foreground">{run.run_id}</p><h2 className="mt-1 font-semibold">{taskTitle ?? run.task_id}</h2></div><StateBadge state={run.state} /></div>
      <dl className="grid grid-cols-2 gap-3 text-xs"><Fact label="Adapter" value={run.adapter} /><Fact label="Version" value={String(run.version)} /><Fact label="Exit code" value={run.exit_code === null ? "—" : String(run.exit_code)} /><Fact label="PID" value={run.pid === null ? "—" : String(run.pid)} /><Fact label="Timeout" value={`${run.timeout_seconds}s`} /><Fact label="Duration" value={duration(run)} /></dl>
      {run.error_message && <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive" role="alert">{run.error_message}</div>}
      <DataBlock title="Prompt" value={run.prompt} />
      <DataBlock title="stdout tail" value={run.stdout_tail || "No stdout captured."} />
      <DataBlock title="stderr tail" value={run.stderr_tail || "No stderr captured."} tone={Boolean(run.stderr_tail)} />
      <details className="rounded-lg border p-3 text-xs"><summary className="cursor-pointer font-medium">Process metadata</summary><div className="mt-3 space-y-2 text-muted-foreground"><p className="break-all"><strong>Workspace:</strong> {run.workspace}</p><p className="break-all"><strong>Command:</strong> {run.command.join(" ")}</p><pre className="max-h-48 overflow-auto whitespace-pre-wrap">{JSON.stringify(maskMetadata(run.result_metadata), null, 2)}</pre></div></details>
      {isCancellable(run) && <Button variant="destructive" className="w-full" onClick={() => onCancel(run)}><CircleStop /> Cancel run</Button>}
    </aside>
  );
}

function CancelDialog({ run, onClose, onConfirm }: { run: RunSummary; onClose: () => void; onConfirm: (reason: string) => Promise<void> }) {
  const [reason, setReason] = useState("");
  const [busy, setBusy] = useState(false);
  const dialogRef = useRef<HTMLFormElement>(null);
  const mountedRef = useRef(true);
  const busyRef = useRef(false);
  useEffect(() => {
    mountedRef.current = true;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const background = document.querySelector<HTMLElement>("[data-delivery-shell-root]");
    background?.setAttribute("inert", "");
    background?.setAttribute("aria-hidden", "true");
    const dialog = dialogRef.current;
    const items = () => Array.from(dialog?.querySelectorAll<HTMLElement>("button, textarea") ?? []).filter((item) => !item.hasAttribute("disabled"));
    items()[0]?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { if (!busyRef.current) onClose(); return; }
      if (event.key !== "Tab") return;
      const focusable = items(); const first = focusable[0]; const last = focusable[focusable.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", onKeyDown);
    return () => {
      mountedRef.current = false;
      document.removeEventListener("keydown", onKeyDown);
      background?.removeAttribute("inert");
      background?.removeAttribute("aria-hidden");
      opener?.focus();
    };
  }, [onClose]);
  useEffect(() => { busyRef.current = busy; }, [busy]);
  return createPortal(
    <div className="fixed inset-0 z-50 grid place-items-center bg-black/45 p-4" onMouseDown={(event) => event.target === event.currentTarget && !busy && onClose()}>
      <form ref={dialogRef} role="alertdialog" aria-modal="true" aria-labelledby="cancel-title" aria-describedby="cancel-description" className="w-full max-w-md space-y-4 rounded-xl bg-background p-6 shadow-2xl" onSubmit={async (event) => { event.preventDefault(); setBusy(true); try { await onConfirm(reason.trim()); } finally { if (mountedRef.current) setBusy(false); } }}>
        <div><h2 id="cancel-title" className="text-lg font-bold">Cancel this run?</h2><p id="cancel-description" className="mt-1 text-sm text-muted-foreground">Agora will terminate the {run.adapter} process. Partial workspace changes are not reverted.</p></div>
        <label className="block space-y-2 text-sm font-medium"><span>Reason (optional)</span><textarea className="field min-h-24 resize-y" maxLength={4000} value={reason} onChange={(event) => setReason(event.target.value)} /></label>
        <div className="flex justify-end gap-2"><Button type="button" variant="outline" onClick={onClose} disabled={busy}>Keep running</Button><Button type="submit" variant="destructive" disabled={busy}>{busy ? "Cancelling…" : "Cancel run"}</Button></div>
      </form>
    </div>,
    document.body,
  );
}

function Filter({ label, value, onChange, children }: { label: string; value: string; onChange: (value: string) => void; children: React.ReactNode }) {
  return <label className="space-y-1 text-xs font-medium text-muted-foreground"><span>{label}</span><select className="field text-foreground" value={value} onChange={(event) => onChange(event.target.value)}>{children}</select></label>;
}

function StateBadge({ state }: { state: RunState }) {
  const variant = state === "succeeded" ? "secondary" : ["failed", "timed_out", "abandoned"].includes(state) ? "destructive" : "outline";
  return <Badge variant={variant} className={cn(state === "running" && "border-blue-500/40 text-blue-600 dark:text-blue-300", state === "queued" && "border-amber-500/40 text-amber-700 dark:text-amber-300")}>{state.replace("_", " ")}</Badge>;
}

function Fact({ label, value }: { label: string; value: string }) { return <div><dt className="text-muted-foreground">{label}</dt><dd className="mt-0.5 font-medium capitalize">{value}</dd></div>; }
function DataBlock({ title, value, tone = false }: { title: string; value: string; tone?: boolean }) { return <section><h3 className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">{title}</h3><pre className={cn("max-h-72 overflow-auto whitespace-pre-wrap break-words rounded-lg border bg-muted/40 p-3 text-xs leading-relaxed", tone && "border-destructive/30 text-destructive")}>{value}</pre></section>; }
function isCancellable(run: RunSummary | ExecutionRun) { return run.state === "queued" || run.state === "running"; }
function shortId(value: string) { return value.length > 18 ? `${value.slice(0, 14)}…` : value; }
function formatDate(value: string) { const date = new Date(value); return Number.isNaN(date.getTime()) ? value : date.toLocaleString(); }
function duration(run: RunSummary | ExecutionRun) { const start = new Date(run.started_at ?? run.queued_at).getTime(); const end = new Date(run.finished_at ?? Date.now()).getTime(); if (Number.isNaN(start) || Number.isNaN(end)) return "—"; const seconds = Math.max(0, Math.round((end - start) / 1000)); return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${seconds % 60}s`; }
function setQuery(query: URLSearchParams, key: string, value: string) { if (value === "all") query.delete(key); else query.set(key, value); }
function maskMetadata(value: unknown, key = ""): unknown { if (key && /(key|secret|token|password|credential|auth)/i.test(key)) return "••••••••"; if (Array.isArray(value)) return value.map((item) => maskMetadata(item)); if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([itemKey, item]) => [itemKey, maskMetadata(item, itemKey)])); return value; }

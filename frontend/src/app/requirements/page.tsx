"use client";

import { useCallback, useEffect, useMemo, useState } from "react";
import { Check, ChevronRight, FileCheck2, RefreshCw, X } from "lucide-react";
import { DeliveryShell } from "@/components/delivery-shell";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  ApiError,
  approveSpec,
  createSpec,
  listSpecs,
  listTasks,
  rejectSpec,
  transitionTask,
  type RequirementSpec,
  type TaskManifest,
} from "@/lib/control-plane";
import { cn } from "@/lib/utils";

export default function RequirementsPage() {
  const [tasks, setTasks] = useState<TaskManifest[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [specs, setSpecs] = useState<RequirementSpec[]>([]);
  const [selectedSpecId, setSelectedSpecId] = useState("");
  const [loading, setLoading] = useState(true);
  const [specsLoading, setSpecsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [drafting, setDrafting] = useState(false);

  const selectedTask = tasks.find((task) => task.task_id === selectedId) ?? null;
  const selectedSpec = specs.find((spec) => spec.spec_id === selectedSpecId) ?? specs[0] ?? null;

  const loadTasks = useCallback(async (preferQuery = false) => {
    setLoading(true); setError(null);
    try {
      const loaded = await listTasks();
      setTasks(loaded);
      if (loaded.length === 0) setSpecsLoading(false);
      const queryTask = preferQuery && typeof window !== "undefined" ? new URLSearchParams(window.location.search).get("task") ?? "" : "";
      setSelectedId((current) => {
        const desired = queryTask || current;
        return loaded.some((task) => task.task_id === desired) ? desired : loaded[0]?.task_id ?? "";
      });
    } catch (err) { setError((err as Error).message); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => {
    const timeout = window.setTimeout(() => void loadTasks(true), 0);
    return () => window.clearTimeout(timeout);
  }, [loadTasks]);
  useEffect(() => {
    if (!selectedId) return;
    let cancelled = false;
    const timeout = window.setTimeout(() => {
      setSpecsLoading(true);
      listSpecs(selectedId)
        .then((loaded) => {
          if (cancelled) return;
          setSpecs(loaded);
          setSelectedSpecId(loaded[0]?.spec_id ?? "");
          setDrafting(loaded.length === 0 || !loaded.some((spec) => spec.state === "draft" || spec.state === "approved"));
        })
        .catch((err) => { if (!cancelled) setError((err as Error).message); })
        .finally(() => { if (!cancelled) setSpecsLoading(false); });
    }, 0);
    return () => { cancelled = true; window.clearTimeout(timeout); };
  }, [selectedId]);

  const replaceTask = (updated: TaskManifest) => setTasks((current) => current.map((task) => task.task_id === updated.task_id ? updated : task));
  const replaceSpec = (updated: RequirementSpec) => setSpecs((current) => current.map((spec) => spec.spec_id === updated.spec_id ? updated : spec));

  return (
    <DeliveryShell active="Requirements">
      <header className="border-b bg-background px-5 py-5 md:px-8">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center justify-between gap-4">
          <div><p className="text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground">Human approval gate</p><h1 className="mt-1 text-2xl font-bold">Requirements Studio</h1></div>
          <Button variant="outline" size="lg" onClick={() => void loadTasks(false)} disabled={loading}><RefreshCw className={cn(loading && "animate-spin")} /> Refresh</Button>
        </div>
      </header>

      <div className="mx-auto grid max-w-7xl gap-6 p-5 md:p-8 lg:grid-cols-[300px_minmax(0,1fr)]">
        <aside className="space-y-3">
          <h2 className="px-1 text-xs font-semibold uppercase tracking-widest text-muted-foreground">Delivery tasks</h2>
          {tasks.map((task) => (
            <button key={task.task_id} onClick={() => { if (task.task_id !== selectedId) { setSpecsLoading(true); setSelectedId(task.task_id); } }} className={cn("w-full rounded-xl border p-4 text-left transition-colors", selectedId === task.task_id ? "border-primary bg-primary/5" : "bg-card hover:bg-accent/50")}>
              <div className="flex items-center justify-between gap-2"><Badge variant="outline">{task.project_id}</Badge><span className="text-xs capitalize text-muted-foreground">{task.state}</span></div>
              <p className="mt-3 text-sm font-semibold">{task.title}</p>
            </button>
          ))}
          {!loading && tasks.length === 0 && <div className="rounded-xl border border-dashed p-6 text-center text-sm text-muted-foreground">Create a task in Portfolio first.</div>}
        </aside>

        <section className="min-w-0 space-y-5">
          {error && <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive">{error}</div>}
          {!selectedTask ? (
            <Empty title="Select a delivery task" detail="The structured specification and its approval history will appear here." />
          ) : selectedTask.state === "backlog" ? (
            <Empty title="Requirements have not started" detail="Move this task into the requirements lifecycle before drafting its spec.">
              <Button onClick={async () => { try { replaceTask(await transitionTask(selectedTask, "requirements")); } catch (err) { setError(conflictMessage(err)); if (err instanceof ApiError && err.status === 409) await loadTasks(false); } }}>Start requirements <ChevronRight /></Button>
            </Empty>
          ) : specsLoading ? (
            <Empty title="Loading specification" detail="Fetching the latest requirement versions and approval state." />
          ) : drafting || specs.length === 0 ? (
            selectedTask.state === "requirements" ? <SpecComposer key={selectedTask.task_id} task={selectedTask} onCreated={(spec) => { setSpecs([spec, ...specs]); setSelectedSpecId(spec.spec_id); setDrafting(false); }} /> : <Empty title="No requirement specification" detail={`This task is already in ${selectedTask.state}; return it to requirements through the lifecycle before authoring a spec.`} />
          ) : (
            <>
              <div className="flex flex-wrap items-start justify-between gap-3 rounded-xl border bg-card p-5">
                <div><p className="text-xs text-muted-foreground">{selectedTask.project_id} / {selectedTask.task_id}</p><h2 className="mt-1 text-xl font-bold">{selectedTask.title}</h2></div>
                <select value={selectedSpec?.spec_id ?? ""} onChange={(event) => setSelectedSpecId(event.target.value)} className="h-9 rounded-lg border bg-background px-3 text-sm">
                  {specs.map((spec) => <option key={spec.spec_id} value={spec.spec_id}>v{spec.version} · {spec.state} · r{spec.revision}</option>)}
                </select>
              </div>
              {selectedSpec && <SpecViewer
                key={selectedSpec.spec_id}
                task={selectedTask}
                spec={selectedSpec}
                onSpecUpdated={replaceSpec}
                onTaskUpdated={replaceTask}
                onDraftAgain={() => setDrafting(true)}
                onConflict={async () => {
                  const [loadedTasks, loadedSpecs] = await Promise.all([listTasks(), listSpecs(selectedTask.task_id)]);
                  setTasks(loadedTasks); setSpecs(loadedSpecs); setSelectedSpecId(loadedSpecs[0]?.spec_id ?? "");
                }}
                onError={setError}
              />}
            </>
          )}
        </section>
      </div>
    </DeliveryShell>
  );
}

function SpecComposer({ task, onCreated }: { task: TaskManifest; onCreated: (spec: RequirementSpec) => void }) {
  const [title, setTitle] = useState(task.title);
  const [summary, setSummary] = useState(task.description);
  const [requirement, setRequirement] = useState("");
  const [given, setGiven] = useState("");
  const [when, setWhen] = useState("");
  const [then, setThen] = useState("");
  const [constraints, setConstraints] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  return (
    <form className="space-y-5 rounded-xl border bg-card p-5 md:p-7" onSubmit={async (event) => {
      event.preventDefault(); setSubmitting(true); setError(null);
      try {
        const spec = await createSpec(task.task_id, {
          title: title.trim(), summary: summary.trim(),
          functional: [{ requirement_id: "FR-001", statement: requirement.trim() }],
          constraints: lines(constraints),
          acceptance_scenarios: [{ scenario_id: "AC-001", requirement_ids: ["FR-001"], given: given.trim(), when: when.trim(), then: then.trim() }],
          created_by: "user",
        });
        onCreated(spec);
      } catch (err) { setError((err as Error).message); setSubmitting(false); }
    }}>
      <div><p className="text-xs uppercase tracking-widest text-muted-foreground">New draft · {task.project_id}</p><h2 className="mt-1 text-xl font-bold">Define the outcome</h2><p className="mt-1 text-sm text-muted-foreground">This creates version 1. Approval remains a separate human action.</p></div>
      <Field label="Specification title"><input className="field" required value={title} onChange={(e) => setTitle(e.target.value)} /></Field>
      <Field label="Summary"><textarea className="field min-h-24 resize-y" value={summary} onChange={(e) => setSummary(e.target.value)} /></Field>
      <Field label="Functional requirement (FR-001)"><textarea className="field min-h-24 resize-y" required value={requirement} onChange={(e) => setRequirement(e.target.value)} placeholder="The system shall…" /></Field>
      <div className="rounded-xl bg-muted/50 p-4"><h3 className="mb-3 text-sm font-semibold">Acceptance scenario (AC-001)</h3><div className="grid gap-3 md:grid-cols-3"><Field label="Given"><textarea className="field min-h-20" required value={given} onChange={(e) => setGiven(e.target.value)} /></Field><Field label="When"><textarea className="field min-h-20" required value={when} onChange={(e) => setWhen(e.target.value)} /></Field><Field label="Then"><textarea className="field min-h-20" required value={then} onChange={(e) => setThen(e.target.value)} /></Field></div></div>
      <Field label="Constraints (one per line)"><textarea className="field min-h-20 resize-y" value={constraints} onChange={(e) => setConstraints(e.target.value)} /></Field>
      {error && <p className="text-sm text-destructive">{error}</p>}
      <Button type="submit" size="lg" disabled={submitting}>{submitting ? "Creating draft…" : "Create draft"}</Button>
    </form>
  );
}

function SpecViewer({ task, spec, onSpecUpdated, onTaskUpdated, onDraftAgain, onConflict, onError }: {
  task: TaskManifest;
  spec: RequirementSpec;
  onSpecUpdated: (spec: RequirementSpec) => void;
  onTaskUpdated: (task: TaskManifest) => void;
  onDraftAgain: () => void;
  onConflict: () => Promise<void>;
  onError: (error: string | null) => void;
}) {
  const [reason, setReason] = useState("");
  const [rejectReason, setRejectReason] = useState("");
  const [busy, setBusy] = useState(false);
  const unresolved = useMemo(() => spec.open_questions.filter((item) => !item.resolution), [spec]);
  return (
    <article className="space-y-6 rounded-xl border bg-card p-5 md:p-7">
      <div className="flex flex-wrap items-start justify-between gap-4">
        <div><div className="flex items-center gap-2"><Badge variant={spec.state === "approved" ? "default" : "secondary"}>{spec.state}</Badge><span className="text-xs text-muted-foreground">Version {spec.version}, revision {spec.revision}</span></div><h2 className="mt-3 text-2xl font-bold">{spec.title}</h2><p className="mt-2 max-w-3xl text-sm leading-relaxed text-muted-foreground">{spec.summary || "No summary provided."}</p></div>
        {spec.state === "approved" && <FileCheck2 className="size-8 text-emerald-500" />}
      </div>
      <SpecSection title="Functional requirements">{spec.functional.map((item) => <div key={item.requirement_id} className="rounded-lg border p-4"><Badge variant="outline">{item.requirement_id}</Badge><p className="mt-2 text-sm leading-relaxed">{item.statement}</p></div>)}</SpecSection>
      <SpecSection title="Acceptance scenarios">{spec.acceptance_scenarios.map((scenario) => <div key={scenario.scenario_id} className="rounded-lg bg-muted/50 p-4 text-sm"><p className="font-semibold">{scenario.scenario_id}</p><dl className="mt-2 grid gap-2 md:grid-cols-[60px_1fr]"><dt className="text-muted-foreground">Given</dt><dd>{scenario.given}</dd><dt className="text-muted-foreground">When</dt><dd>{scenario.when}</dd><dt className="text-muted-foreground">Then</dt><dd>{scenario.then}</dd></dl></div>)}</SpecSection>
      {spec.constraints.length > 0 && <SpecSection title="Constraints"><ul className="list-disc space-y-1 pl-5 text-sm">{spec.constraints.map((item) => <li key={item}>{item}</li>)}</ul></SpecSection>}
      {unresolved.length > 0 && <div className="rounded-lg border border-amber-500/30 bg-amber-500/5 p-4 text-sm text-amber-700 dark:text-amber-300">{unresolved.length} open question(s) remain. Review them before approval.</div>}
      {spec.state === "draft" && (
        <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-4">
          <h3 className="font-semibold">Human approval gate</h3><p className="mt-1 text-sm text-muted-foreground">Approval unlocks the task&apos;s transition into design.</p>
          <div className="mt-3 flex flex-col gap-2 sm:flex-row">
            <input value={reason} onChange={(e) => setReason(e.target.value)} className="field flex-1" placeholder="Approval note (optional)" />
            <Button disabled={busy} onClick={async () => {
              setBusy(true); onError(null);
              try { onSpecUpdated(await approveSpec(spec, reason)); }
              catch (err) { onError(conflictMessage(err)); if (err instanceof ApiError && err.status === 409) await onConflict(); }
              finally { setBusy(false); }
            }}><Check /> {busy ? "Approving…" : "Approve spec"}</Button>
          </div>
          <div className="mt-4 border-t pt-4">
            <p className="mb-2 text-sm font-medium">Needs revision?</p>
            <div className="flex flex-col gap-2 sm:flex-row">
              <input value={rejectReason} onChange={(e) => setRejectReason(e.target.value)} className="field flex-1" placeholder="Required rejection reason" />
              <Button variant="destructive" disabled={busy || !rejectReason.trim()} onClick={async () => {
                setBusy(true); onError(null);
                try { onSpecUpdated(await rejectSpec(spec, rejectReason.trim())); onDraftAgain(); }
                catch (err) { onError(conflictMessage(err)); if (err instanceof ApiError && err.status === 409) await onConflict(); }
                finally { setBusy(false); }
              }}><X /> Reject</Button>
            </div>
          </div>
        </div>
      )}
      {spec.state === "approved" && task.state === "requirements" && (
        <div className="rounded-xl border border-blue-500/30 bg-blue-500/5 p-4">
          <h3 className="font-semibold">Ready for design</h3><p className="mt-1 text-sm text-muted-foreground">The approved specification satisfies the lifecycle gate.</p>
          <Button className="mt-3" disabled={busy} onClick={async () => {
            setBusy(true); onError(null);
            try { onTaskUpdated(await transitionTask(task, "design")); }
            catch (err) { onError(conflictMessage(err)); if (err instanceof ApiError && err.status === 409) await onConflict(); }
            finally { setBusy(false); }
          }}>Advance to design <ChevronRight /></Button>
        </div>
      )}
      {spec.state === "rejected" && task.state === "requirements" && <Button onClick={onDraftAgain}>Create revised draft</Button>}
    </article>
  );
}

function Empty({ title, detail, children }: { title: string; detail: string; children?: React.ReactNode }) { return <div className="grid min-h-80 place-items-center rounded-xl border border-dashed bg-card p-8 text-center"><div><h2 className="text-lg font-semibold">{title}</h2><p className="mt-2 max-w-md text-sm text-muted-foreground">{detail}</p>{children && <div className="mt-5">{children}</div>}</div></div>; }
function Field({ label, children }: { label: string; children: React.ReactNode }) { return <label className="block space-y-2 text-sm font-medium"><span>{label}</span>{children}</label>; }
function SpecSection({ title, children }: { title: string; children: React.ReactNode }) { return <section><h3 className="mb-3 text-sm font-semibold uppercase tracking-wider text-muted-foreground">{title}</h3><div className="space-y-3">{children}</div></section>; }
function lines(value: string): string[] { return value.split("\n").map((item) => item.trim()).filter(Boolean); }
function conflictMessage(error: unknown): string { return error instanceof ApiError && error.status === 409 ? "This item changed elsewhere. Fresh data has been loaded; review it and try again." : (error as Error).message; }

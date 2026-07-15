"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { GitBranchPlus, Plus, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { TaskManifest } from "@/lib/control-plane";
import { activateWorkflow, createWorkflow, type WorkflowManifest } from "@/lib/workflows";

type DraftStep = { id: string; taskId: string; title: string; adapter: string; prompt: string; dependsOn: string[] };
const adapters = ["codex", "claude", "kiro"];

export function WorkflowComposer({ tasks, onClose, onCreated }: {
  tasks: TaskManifest[]; onClose: () => void;
  onCreated: (workflow: WorkflowManifest, activationError?: string) => void;
}) {
  const eligible = useMemo(() => tasks.filter((task) => task.state === "planned" || task.state === "running"), [tasks]);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [steps, setSteps] = useState<DraftStep[]>(() => eligible[0] ? [newStep(eligible[0], 0)] : []);
  const [activate, setActivate] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const submittingRef = useRef(false);
  const mountedRef = useRef(true);
  const valid = Boolean(title.trim() && steps.length && steps.every((step) => step.taskId && step.title.trim() && step.prompt.trim()));

  useEffect(() => { submittingRef.current = submitting; }, [submitting]);
  useEffect(() => { mountedRef.current = true; return () => { mountedRef.current = false; }; }, []);
  useEffect(() => {
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const background = document.querySelector<HTMLElement>("[data-delivery-shell-root]");
    background?.setAttribute("inert", ""); background?.setAttribute("aria-hidden", "true");
    const form = formRef.current;
    const focusable = () => Array.from(form?.querySelectorAll<HTMLElement>("button,input,textarea,select,[tabindex]:not([tabindex='-1'])") ?? []).filter((item) => !item.hasAttribute("disabled"));
    focusable()[0]?.focus();
    const keydown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); if (!submittingRef.current) onClose(); return; }
      if (event.key !== "Tab") return; const items = focusable(); if (!items.length) return;
      if (event.shiftKey && document.activeElement === items[0]) { event.preventDefault(); items.at(-1)?.focus(); }
      else if (!event.shiftKey && document.activeElement === items.at(-1)) { event.preventDefault(); items[0].focus(); }
    };
    document.addEventListener("keydown", keydown);
    return () => { document.removeEventListener("keydown", keydown); background?.removeAttribute("inert"); background?.removeAttribute("aria-hidden"); opener?.focus(); };
  }, [onClose]);

  const update = (id: string, patch: Partial<DraftStep>) => setSteps((current) => current.map((step) => step.id === id ? { ...step, ...patch } : step));
  const remove = (id: string) => setSteps((current) => current.filter((step) => step.id !== id).map((step) => ({ ...step, dependsOn: step.dependsOn.filter((dependency) => dependency !== id) })));

  return createPortal(<div className="fixed inset-0 z-50 flex justify-end bg-black/40" onMouseDown={(event) => event.target === event.currentTarget && !submitting && onClose()}>
    <form ref={formRef} role="dialog" aria-modal="true" aria-labelledby="workflow-composer-title" className="h-full w-full max-w-3xl space-y-6 overflow-y-auto bg-background p-6 shadow-2xl" onSubmit={async (event) => {
      event.preventDefault(); if (!valid || submittingRef.current) return;
      submittingRef.current = true; setSubmitting(true); setError(null);
      try {
        const payload = {
          title: title.trim(), description: description.trim(), created_by: "user",
          steps: steps.map((step, index) => {
            const task = eligible.find((item) => item.task_id === step.taskId)!;
            return { key: `step-${index + 1}`, title: step.title.trim(), project_id: task.project_id,
              task_id: task.task_id, adapter: step.adapter, prompt: step.prompt.trim(),
              depends_on: step.dependsOn.map((id) => { const dependencyIndex = steps.findIndex((item) => item.id === id); if (dependencyIndex < 0) throw new Error("A step dependency is no longer available"); return `step-${dependencyIndex + 1}`; }) };
          }),
        };
        let workflow = await createWorkflow(payload);
        if (activate) {
          try { workflow = await activateWorkflow(workflow); }
          catch (err) { if (mountedRef.current) onCreated(workflow, `Workflow was created as a draft, but activation failed: ${(err as Error).message}`); return; }
        }
        if (mountedRef.current) onCreated(workflow);
      } catch (err) { if (mountedRef.current) { submittingRef.current = false; setError((err as Error).message); setSubmitting(false); } }
    }}>
      <div className="flex items-start justify-between gap-4"><div><p className="text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground">DAG composer</p><h2 id="workflow-composer-title" className="mt-1 text-xl font-bold">Create cross-project workflow</h2></div><Button type="button" variant="ghost" size="icon" onClick={onClose} disabled={submitting} aria-label="Close workflow composer"><X /></Button></div>
      {!eligible.length ? <div className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">No planned or running tasks are available. Prepare tasks in Portfolio and Requirements first.</div> : <>
        <label className="block space-y-2 text-sm font-medium"><span>Workflow title</span><input className="field" value={title} maxLength={300} onChange={(event) => setTitle(event.target.value)} required /></label>
        <label className="block space-y-2 text-sm font-medium"><span>Description</span><textarea className="field min-h-20" value={description} maxLength={20_000} onChange={(event) => setDescription(event.target.value)} /></label>
        <fieldset className="space-y-4"><legend className="text-sm font-semibold">Steps</legend>{steps.map((step, index) => {
          const task = eligible.find((item) => item.task_id === step.taskId);
          const selectedElsewhere = new Set(steps.filter((item) => item.id !== step.id).map((item) => item.taskId));
          const choices = eligible.filter((item) => !selectedElsewhere.has(item.task_id));
          return <section key={step.id} className="rounded-xl border bg-muted/20 p-4"><div className="mb-4 flex items-center justify-between"><h3 className="font-semibold">Step {index + 1}</h3><Button type="button" variant="ghost" size="icon-sm" disabled={steps.length === 1 || submitting} onClick={() => remove(step.id)} aria-label={`Remove step ${index + 1}`}><Trash2 /></Button></div>
            <div className="grid gap-4 md:grid-cols-2"><label className="space-y-2 text-sm font-medium"><span>Task</span><select className="field" value={step.taskId} onChange={(event) => { const next = eligible.find((item) => item.task_id === event.target.value)!; update(step.id, { taskId: next.task_id, title: next.title, adapter: preferredAdapter(next) }); }}>{choices.map((item) => <option key={item.task_id} value={item.task_id}>{item.project_id} · {item.title} ({item.state})</option>)}</select></label>
              <label className="space-y-2 text-sm font-medium"><span>Agent</span><select className="field" value={step.adapter} onChange={(event) => update(step.id, { adapter: event.target.value })}>{adapters.map((adapter) => <option key={adapter}>{adapter}</option>)}</select></label></div>
            <label className="mt-4 block space-y-2 text-sm font-medium"><span>Step title</span><input className="field" value={step.title} maxLength={300} onChange={(event) => update(step.id, { title: event.target.value })} required /></label>
            <label className="mt-4 block space-y-2 text-sm font-medium"><span>Execution prompt</span><textarea className="field min-h-28 font-mono text-xs" value={step.prompt} maxLength={16_000} onChange={(event) => update(step.id, { prompt: event.target.value })} placeholder="Describe the exact deliverable and acceptance checks for this step." required /></label>
            {index > 0 && <fieldset className="mt-4"><legend className="text-sm font-medium">Depends on earlier steps</legend><div className="mt-2 flex flex-wrap gap-2">{steps.slice(0, index).map((candidate, dependencyIndex) => <label key={candidate.id} className="flex items-center gap-2 rounded-lg border bg-background px-3 py-2 text-xs"><input type="checkbox" checked={step.dependsOn.includes(candidate.id)} onChange={(event) => update(step.id, { dependsOn: event.target.checked ? [...step.dependsOn, candidate.id] : step.dependsOn.filter((id) => id !== candidate.id) })} /> Step {dependencyIndex + 1}: {candidate.title || "Untitled"}</label>)}</div></fieldset>}
            {task && <p className="mt-3 text-xs text-muted-foreground">{task.project_id} · task {task.task_id} · budget {task.budget.max_minutes ? `${task.budget.max_minutes} min` : "default timeout"}</p>}
          </section>;
        })}<Button type="button" variant="outline" disabled={submitting || steps.length >= eligible.length} onClick={() => { const used = new Set(steps.map((step) => step.taskId)); const next = eligible.find((task) => !used.has(task.task_id)); if (next) setSteps((current) => [...current, newStep(next, current.length)]); }}><Plus /> Add step</Button></fieldset>
        <label className="flex items-center gap-3 rounded-xl border p-4 text-sm"><input type="checkbox" checked={activate} onChange={(event) => setActivate(event.target.checked)} /><span><strong>Activate after creation</strong><span className="block text-xs text-muted-foreground">Root steps become ready, but no runs start until you explicitly dispatch.</span></span></label>
        {error && <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive" role="alert">{error}</div>}
        <Button type="submit" size="lg" className="w-full" disabled={!valid || submitting}><GitBranchPlus /> {submitting ? "Creating…" : activate ? "Create and activate" : "Create draft"}</Button>
      </>}
    </form>
  </div>, document.body);
}

function preferredAdapter(task: TaskManifest) { return adapters.includes(task.primary_agent ?? "") ? task.primary_agent! : "codex"; }
function newStep(task: TaskManifest, index: number): DraftStep { return { id: `${Date.now()}-${index}-${Math.random()}`, taskId: task.task_id, title: task.title, adapter: preferredAdapter(task), prompt: "", dependsOn: [] }; }

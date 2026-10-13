"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { GitBranchPlus, Plus, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import type { TaskManifest } from "@/lib/control-plane";
import { activateWorkflow, createWorkflow, type WorkflowManifest } from "@/lib/workflows";
import { useI18n } from "@/lib/i18n";

type DraftStep = { id: string; taskId: string; title: string; adapter: string; prompt: string; dependsOn: string[] };
const adapters = ["codex", "claude", "kiro"];

export function WorkflowComposer({ tasks, onClose, onCreated }: {
  tasks: TaskManifest[]; onClose: () => void;
  onCreated: (workflow: WorkflowManifest, activationError?: string) => void;
}) {
  const { t, te } = useI18n();
  const eligible = useMemo(() => tasks.filter((task) => task.state === "planned" || task.state === "running"), [tasks]);
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [steps, setSteps] = useState<DraftStep[]>(() => eligible[0] ? [newStep(eligible[0], 0)] : []);
  const [activate, setActivate] = useState(true);
  const [autoDispatch, setAutoDispatch] = useState(false);
  const [maxConcurrent, setMaxConcurrent] = useState("4");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const formRef = useRef<HTMLFormElement>(null);
  const submittingRef = useRef(false);
  const mountedRef = useRef(true);
  const maxConcurrentValue = Number(maxConcurrent);
  const maxConcurrentValid = maxConcurrent.trim() !== "" && Number.isInteger(maxConcurrentValue) && maxConcurrentValue >= 1 && maxConcurrentValue <= 32;
  const valid = Boolean(title.trim() && steps.length && steps.every((step) => step.taskId && step.title.trim() && step.prompt.trim()) && maxConcurrentValid);

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
    <form ref={formRef} role="dialog" aria-modal="true" aria-labelledby="workflow-composer-title" className="h-full w-full max-w-3xl space-y-6 overflow-y-auto border-l bg-background p-6 shadow-2xl" onSubmit={async (event) => {
      event.preventDefault(); if (!valid || submittingRef.current) return;
      submittingRef.current = true; setSubmitting(true); setError(null);
      try {
        const payload = {
          title: title.trim(), description: description.trim(), created_by: "user",
          auto_dispatch: autoDispatch, max_concurrent_runs: maxConcurrentValue,
          steps: steps.map((step, index) => {
            const task = eligible.find((item) => item.task_id === step.taskId)!;
            return { key: `step-${index + 1}`, title: step.title.trim(), project_id: task.project_id,
              task_id: task.task_id, adapter: step.adapter, prompt: step.prompt.trim(),
              depends_on: step.dependsOn.map((id) => { const dependencyIndex = steps.findIndex((item) => item.id === id); if (dependencyIndex < 0) throw new Error(t("workflows.composer.errors.dependencyUnavailable")); return `step-${dependencyIndex + 1}`; }) };
          }),
        };
        let workflow = await createWorkflow(payload);
        if (activate) {
          try { workflow = await activateWorkflow(workflow); }
          catch (err) { if (mountedRef.current) onCreated(workflow, t("workflows.composer.errors.activationFailed", { message: (err as Error).message })); return; }
        }
        if (mountedRef.current) onCreated(workflow);
      } catch (err) { if (mountedRef.current) { submittingRef.current = false; setError((err as Error).message); setSubmitting(false); } }
    }}>
      <div className="flex items-start justify-between gap-4"><div><p className="text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground">{t("workflows.composer.eyebrow")}</p><h2 id="workflow-composer-title" className="mt-1 text-xl font-bold">{t("workflows.composer.title")}</h2></div><Button type="button" variant="ghost" size="icon" onClick={onClose} disabled={submitting} aria-label={t("workflows.composer.close")}><X /></Button></div>
      {!eligible.length ? <div className="rounded-xl border border-dashed p-8 text-center text-sm text-muted-foreground">{t("workflows.composer.emptyTasks")}</div> : <>
        <label className="block space-y-2 text-sm font-medium"><span>{t("workflows.composer.titleLabel")}</span><input className="field" value={title} maxLength={300} onChange={(event) => setTitle(event.target.value)} required /></label>
        <label className="block space-y-2 text-sm font-medium"><span>{t("workflows.composer.descriptionLabel")}</span><textarea className="field min-h-20" value={description} maxLength={20_000} onChange={(event) => setDescription(event.target.value)} /></label>
        <fieldset className="space-y-4"><legend className="text-sm font-semibold">{t("workflows.composer.stepsLegend")}</legend>{steps.map((step, index) => {
          const task = eligible.find((item) => item.task_id === step.taskId);
          const selectedElsewhere = new Set(steps.filter((item) => item.id !== step.id).map((item) => item.taskId));
          const choices = eligible.filter((item) => !selectedElsewhere.has(item.task_id));
          return <section key={step.id} className="rounded-xl border bg-muted/20 p-4"><div className="mb-4 flex items-center justify-between"><h3 className="font-semibold">{t("workflows.composer.step.heading", { n: index + 1 })}</h3><Button type="button" variant="ghost" size="icon-sm" disabled={steps.length === 1 || submitting} onClick={() => remove(step.id)} aria-label={t("workflows.composer.step.remove", { n: index + 1 })}><Trash2 /></Button></div>
            <div className="grid gap-4 md:grid-cols-2"><label className="space-y-2 text-sm font-medium"><span>{t("workflows.composer.step.taskLabel")}</span><select className="field" value={step.taskId} onChange={(event) => { const next = eligible.find((item) => item.task_id === event.target.value)!; update(step.id, { taskId: next.task_id, title: next.title, adapter: preferredAdapter(next) }); }}>{choices.map((item) => <option key={item.task_id} value={item.task_id}>{item.project_id} · {item.title} ({te("taskState", item.state)})</option>)}</select></label>
              <label className="space-y-2 text-sm font-medium"><span>{t("workflows.composer.step.agentLabel")}</span><select className="field" value={step.adapter} onChange={(event) => update(step.id, { adapter: event.target.value })}>{adapters.map((adapter) => <option key={adapter}>{adapter}</option>)}</select></label></div>
            <label className="mt-4 block space-y-2 text-sm font-medium"><span>{t("workflows.composer.step.titleLabel")}</span><input className="field" value={step.title} maxLength={300} onChange={(event) => update(step.id, { title: event.target.value })} required /></label>
            <label className="mt-4 block space-y-2 text-sm font-medium"><span>{t("workflows.composer.step.promptLabel")}</span><textarea className="field min-h-28 font-mono text-xs" value={step.prompt} maxLength={16_000} onChange={(event) => update(step.id, { prompt: event.target.value })} placeholder={t("workflows.composer.step.promptPlaceholder")} required /></label>
            {index > 0 && <fieldset className="mt-4"><legend className="text-sm font-medium">{t("workflows.composer.step.dependsLegend")}</legend><div className="mt-2 flex flex-wrap gap-2">{steps.slice(0, index).map((candidate, dependencyIndex) => <label key={candidate.id} className="flex items-center gap-2 rounded-lg border bg-background px-3 py-2 text-xs"><input type="checkbox" checked={step.dependsOn.includes(candidate.id)} onChange={(event) => update(step.id, { dependsOn: event.target.checked ? [...step.dependsOn, candidate.id] : step.dependsOn.filter((id) => id !== candidate.id) })} /> {t("workflows.composer.step.dependencyLabel", { n: dependencyIndex + 1, title: candidate.title || t("workflows.composer.step.untitled") })}</label>)}</div></fieldset>}
            {task && <p className="mt-3 text-xs text-muted-foreground">{t("workflows.composer.step.taskMeta", { project: task.project_id, taskId: task.task_id, budget: task.budget.max_minutes ? t("workflows.composer.step.budgetMinutes", { minutes: task.budget.max_minutes }) : t("workflows.composer.step.budgetDefault") })}</p>}
          </section>;
        })}<Button type="button" variant="outline" disabled={submitting || steps.length >= eligible.length} onClick={() => { const used = new Set(steps.map((step) => step.taskId)); const next = eligible.find((task) => !used.has(task.task_id)); if (next) setSteps((current) => [...current, newStep(next, current.length)]); }}><Plus /> {t("workflows.composer.addStep")}</Button></fieldset>
        <label className="flex items-center gap-3 rounded-xl border p-4 text-sm"><input type="checkbox" checked={activate} onChange={(event) => setActivate(event.target.checked)} /><span><strong>{t("workflows.composer.activate.label")}</strong><span className="block text-xs text-muted-foreground">{autoDispatch ? t("workflows.composer.activate.autoHint") : t("workflows.composer.activate.manualHint")}</span></span></label>
        <section className="grid gap-4 rounded-xl border p-4 md:grid-cols-[1fr_180px]"><label className="flex items-center gap-3 text-sm"><input type="checkbox" checked={autoDispatch} onChange={(event) => setAutoDispatch(event.target.checked)} /><span><strong>{t("workflows.composer.auto.label")}</strong><span className="block text-xs text-muted-foreground">{t("workflows.composer.auto.hint")}</span></span></label><label className="space-y-2 text-sm font-medium"><span>{t("workflows.composer.maxRunsLabel")}</span><input className="field" type="number" min={1} max={32} step={1} value={maxConcurrent} onChange={(event) => setMaxConcurrent(event.target.value)} aria-invalid={!maxConcurrentValid} aria-describedby="workflow-max-concurrent-hint" required /><span id="workflow-max-concurrent-hint" className={`block text-xs ${maxConcurrentValid ? "text-muted-foreground" : "text-destructive"}`}>{maxConcurrentValid ? t("workflows.composer.maxRunsHintValid") : t("workflows.composer.maxRunsHintInvalid")}</span></label></section>
        {error && <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive" role="alert">{error}</div>}
        <Button type="submit" size="lg" className="w-full" disabled={!valid || submitting}><GitBranchPlus /> {submitting ? t("workflows.composer.submit.creating") : activate ? t("workflows.composer.submit.createActivate") : t("workflows.composer.submit.createDraft")}</Button>
      </>}
    </form>
  </div>, document.body);
}

function preferredAdapter(task: TaskManifest) { return adapters.includes(task.primary_agent ?? "") ? task.primary_agent! : "codex"; }
function newStep(task: TaskManifest, index: number): DraftStep { return { id: `${Date.now()}-${index}-${Math.random()}`, taskId: task.task_id, title: task.title, adapter: preferredAdapter(task), prompt: "", dependsOn: [] }; }

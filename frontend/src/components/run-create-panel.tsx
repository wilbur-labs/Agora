"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Bot, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ApiError, type TaskManifest } from "@/lib/control-plane";
import { createRun, type ExecutionAdapter, type ExecutionRun } from "@/lib/execution";

const adapters: Array<{ value: ExecutionAdapter; label: string; detail: string }> = [
  { value: "codex", label: "Codex", detail: "Implementation and repository work" },
  { value: "claude", label: "Claude Code", detail: "Deep reasoning and review" },
  { value: "kiro", label: "Kiro CLI", detail: "Specifications and structured planning" },
];

export function RunCreatePanel({
  tasks,
  onClose,
  onCreated,
  onRefreshTasks,
}: {
  tasks: TaskManifest[];
  onClose: () => void;
  onCreated: (run: ExecutionRun) => void;
  onRefreshTasks: () => Promise<void>;
}) {
  const eligible = useMemo(() => tasks.filter((task) => task.state === "planned" || task.state === "running"), [tasks]);
  const [taskId, setTaskId] = useState(eligible[0]?.task_id ?? "");
  const [adapter, setAdapter] = useState<ExecutionAdapter>("codex");
  const [prompt, setPrompt] = useState("");
  const [timeout, setTimeoutValue] = useState(600);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const panelRef = useRef<HTMLFormElement>(null);
  const mountedRef = useRef(true);
  const submittingRef = useRef(false);
  const selectedTask = eligible.find((task) => task.task_id === taskId) ?? null;
  const valid = Boolean(selectedTask && prompt.trim() && prompt.length <= 16_000 && Number.isInteger(timeout) && timeout >= 1 && timeout <= 7200);

  useEffect(() => {
    mountedRef.current = true;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const background = document.querySelector<HTMLElement>("[data-delivery-shell-root]");
    background?.setAttribute("inert", "");
    background?.setAttribute("aria-hidden", "true");
    const panel = panelRef.current;
    const focusable = () => Array.from(panel?.querySelectorAll<HTMLElement>(
      "button, input, textarea, select, [href], [tabindex]:not([tabindex='-1'])",
    ) ?? []).filter((item) => !item.hasAttribute("disabled"));
    focusable()[0]?.focus();
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") { if (!submittingRef.current) onClose(); return; }
      if (event.key !== "Tab") return;
      const items = focusable();
      if (!items.length) return;
      const first = items[0];
      const last = items[items.length - 1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }
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

  useEffect(() => { submittingRef.current = submitting; }, [submitting]);

  return createPortal(
    <div className="fixed inset-0 z-50 flex justify-end bg-black/40" onMouseDown={(event) => event.target === event.currentTarget && !submitting && onClose()}>
      <form
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="new-run-title"
        className="h-full w-full max-w-xl space-y-6 overflow-y-auto bg-background p-6 shadow-2xl"
        onSubmit={async (event) => {
          event.preventDefault();
          if (!selectedTask || !valid) return;
          setSubmitting(true); setError(null);
          try {
            const created = await createRun({
              task_id: selectedTask.task_id,
              adapter,
              prompt: prompt.trim(),
              timeout_seconds: timeout,
              expected_task_version: selectedTask.version,
              actor: "user",
            });
            if (mountedRef.current) onCreated(created);
          } catch (err) {
            if (!mountedRef.current) return;
            if (err instanceof ApiError && err.status === 409) {
              setError("The task changed while this panel was open. Tasks were refreshed; review and submit again.");
              await onRefreshTasks();
            } else setError((err as Error).message);
          } finally { if (mountedRef.current) setSubmitting(false); }
        }}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground">Dispatch agent</p>
            <h2 id="new-run-title" className="mt-1 text-xl font-bold">Queue an execution run</h2>
          </div>
          <Button type="button" variant="ghost" size="icon" onClick={onClose} disabled={submitting} aria-label="Close new run dialog"><X /></Button>
        </div>

        {eligible.length ? (
          <label className="block space-y-2 text-sm font-medium">
            <span>Delivery task</span>
            <select className="field" value={taskId} onChange={(event) => setTaskId(event.target.value)} required>
              {eligible.map((task) => (
                <option key={task.task_id} value={task.task_id}>{task.project_id} · {task.title} ({task.state})</option>
              ))}
            </select>
            {selectedTask && <span className="block text-xs font-normal text-muted-foreground">Version {selectedTask.version} · {selectedTask.task_id}</span>}
          </label>
        ) : (
          <div className="rounded-xl border border-dashed p-5 text-sm text-muted-foreground">
            No task is ready to run. Approve requirements and move a task to <strong>planned</strong> first.
          </div>
        )}

        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">Agent adapter</legend>
          <div className="grid gap-2 sm:grid-cols-3">
            {adapters.map((item) => (
              <label key={item.value} className={`cursor-pointer rounded-xl border p-3 text-sm ${adapter === item.value ? "border-primary bg-primary/5" : "hover:bg-accent/50"}`}>
                <input className="sr-only" type="radio" name="adapter" value={item.value} checked={adapter === item.value} onChange={() => setAdapter(item.value)} />
                <span className="flex items-center gap-2 font-semibold"><Bot className="size-4" /> {item.label}</span>
                <span className="mt-1 block text-xs font-normal leading-relaxed text-muted-foreground">{item.detail}</span>
              </label>
            ))}
          </div>
        </fieldset>

        <label className="block space-y-2 text-sm font-medium">
          <span className="flex items-center justify-between"><span>Prompt</span><span className="text-xs font-normal text-muted-foreground">{prompt.length.toLocaleString()} / 16,000</span></span>
          <textarea className="field min-h-56 resize-y font-mono text-xs leading-relaxed" maxLength={16_000} value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="Describe the deliverable, constraints, files to inspect, and acceptance checks. The agent can read the approved project specification from its workspace." required />
        </label>

        <label className="block space-y-2 text-sm font-medium">
          <span>Timeout (seconds)</span>
          <input className="field" type="number" min={1} max={7200} step={1} value={timeout} onChange={(event) => setTimeoutValue(Number(event.target.value))} required />
          <span className="block text-xs font-normal text-muted-foreground">1–7,200 seconds. Agora terminates and then kills a process that exceeds this limit.</span>
        </label>

        {error && <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive" role="alert">{error}</div>}
        <Button type="submit" size="lg" className="w-full" disabled={!valid || submitting}>{submitting ? "Queueing…" : `Queue ${adapter} run`}</Button>
      </form>
    </div>,
    document.body,
  );
}

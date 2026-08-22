"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { Bot, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { ApiError, type TaskManifest } from "@/lib/control-plane";
import { createRun, listExecutionAdapters, type AdapterCapability, type ExecutionAdapter, type ExecutionRun } from "@/lib/execution";
import { getWorkspaceStatus, provisionWorkspace, type WorkspaceStatus } from "@/lib/workspaces";
import { useI18n } from "@/lib/i18n";

const adapterLabels: Record<ExecutionAdapter, string> = {
  codex: "Codex", claude: "Claude Code", kiro: "Kiro CLI",
};
const fallbackAdapters: AdapterCapability[] = [
  { name: "codex", execution_mode: "unknown", attention_mode: "capture_only", supports_tool_approval: false, supports_user_questions: false, detail: "Capability status is unavailable." },
  { name: "claude", execution_mode: "unknown", attention_mode: "capture_only", supports_tool_approval: false, supports_user_questions: false, detail: "Capability status is unavailable." },
  { name: "kiro", execution_mode: "unknown", attention_mode: "capture_only", supports_tool_approval: false, supports_user_questions: false, detail: "Capability status is unavailable." },
];

export function RunCreatePanel({
  tasks,
  onClose,
  onCreated,
  onRefreshTasks,
  initialTaskId = "",
}: {
  tasks: TaskManifest[];
  onClose: () => void;
  onCreated: (run: ExecutionRun) => void;
  onRefreshTasks: () => Promise<void>;
  initialTaskId?: string;
}) {
  const { t, te } = useI18n();
  const eligible = useMemo(() => tasks.filter((task) => task.state === "planned" || task.state === "running"), [tasks]);
  const [taskId, setTaskId] = useState(initialTaskId || eligible[0]?.task_id || "");

  // Tasks load asynchronously, so the panel can mount (e.g. when deep-linked
  // with ?new=<taskId>) before `tasks` arrive. Once they do, select the
  // requested task if it is eligible, otherwise fall back to the first one.
  useEffect(() => {
    if (eligible.some((task) => task.task_id === taskId)) return;
    const preferred = eligible.find((task) => task.task_id === initialTaskId) ?? eligible[0];
    if (preferred) setTaskId(preferred.task_id);
  }, [eligible, initialTaskId, taskId]);
  const [adapter, setAdapter] = useState<ExecutionAdapter>("codex");
  const [adapters, setAdapters] = useState<AdapterCapability[]>(fallbackAdapters);
  const [prompt, setPrompt] = useState("");
  const [timeout, setTimeoutValue] = useState(600);
  const [submitting, setSubmitting] = useState(false);
  const [provisioningKey, setProvisioningKey] = useState<string | null>(null);
  const [workspaceRefresh, setWorkspaceRefresh] = useState(0);
  const [workspaceLoading, setWorkspaceLoading] = useState(false);
  const [workspace, setWorkspace] = useState<WorkspaceStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [capabilityWarning, setCapabilityWarning] = useState<string | null>(null);
  const panelRef = useRef<HTMLFormElement>(null);
  const mountedRef = useRef(true);
  const submittingRef = useRef(false);
  const selectionRef = useRef("");
  const provisionAbortRef = useRef<AbortController | null>(null);
  const selectedTask = eligible.find((task) => task.task_id === taskId) ?? null;
  const selectionKey = selectedTask ? `${selectedTask.project_id}:${adapter}` : "";
  const provisioning = provisioningKey === selectionKey;
  const valid = Boolean(selectedTask && workspace?.state === "ready" && prompt.trim() && prompt.length <= 16_000 && Number.isInteger(timeout) && timeout >= 1 && timeout <= 7200);

  useEffect(() => {
    if (!selectedTask) { setWorkspace(null); return; }
    const controller = new AbortController();
    let retryTimer: number | undefined;
    setWorkspaceLoading(true); setWorkspace(null); setError(null);
    const refresh = () => getWorkspaceStatus(selectedTask.project_id, adapter, controller.signal)
      .then((status) => {
        setWorkspace(status);
        if (status.state === "provisioning") retryTimer = window.setTimeout(refresh, 1500);
      })
      .catch((err) => { if ((err as Error).name !== "AbortError") setError((err as Error).message); })
      .finally(() => { if (!controller.signal.aborted) setWorkspaceLoading(false); });
    void refresh();
    return () => { controller.abort(); if (retryTimer !== undefined) window.clearTimeout(retryTimer); };
  }, [adapter, selectedTask, workspaceRefresh]);

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
      provisionAbortRef.current?.abort();
      document.removeEventListener("keydown", onKeyDown);
      background?.removeAttribute("inert");
      background?.removeAttribute("aria-hidden");
      opener?.focus();
    };
  }, [onClose]);

  useEffect(() => { submittingRef.current = submitting; }, [submitting]);
  useEffect(() => {
    const controller = new AbortController();
    listExecutionAdapters(controller.signal)
      .then((items) => {
        if (!items.length) return;
        setAdapters(items);
        setAdapter((current) => items.some((item) => item.name === current) ? current : items[0].name);
        setCapabilityWarning(null);
      })
      .catch((err) => {
        if ((err as Error).name !== "AbortError") setCapabilityWarning(t("runs.createPanel.capabilityWarning"));
      });
    return () => controller.abort();
  }, [t]);
  useEffect(() => {
    selectionRef.current = selectionKey;
    provisionAbortRef.current?.abort();
  }, [selectionKey]);

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
              setError(t("runs.createPanel.taskConflict"));
              await onRefreshTasks();
            } else setError((err as Error).message);
          } finally { if (mountedRef.current) setSubmitting(false); }
        }}
      >
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground">{t("runs.createPanel.eyebrow")}</p>
            <h2 id="new-run-title" className="mt-1 text-xl font-bold">{t("runs.createPanel.title")}</h2>
          </div>
          <Button type="button" variant="ghost" size="icon" onClick={onClose} disabled={submitting} aria-label={t("runs.createPanel.close")}><X /></Button>
        </div>

        {eligible.length ? (
          <label className="block space-y-2 text-sm font-medium">
            <span>{t("runs.createPanel.task")}</span>
            <select className="field" value={taskId} onChange={(event) => setTaskId(event.target.value)} required>
              {eligible.map((task) => (
                <option key={task.task_id} value={task.task_id}>{task.project_id} · {task.title} ({te("taskState", task.state)})</option>
              ))}
            </select>
            {selectedTask && <span className="block text-xs font-normal text-muted-foreground">{t("runs.createPanel.taskVersion", { version: selectedTask.version, taskId: selectedTask.task_id })}</span>}
          </label>
        ) : (
          <div className="rounded-xl border border-dashed p-5 text-sm text-muted-foreground">
            {t("runs.createPanel.noTaskBefore")}<strong>{te("taskState", "planned")}</strong>{t("runs.createPanel.noTaskAfter")}
          </div>
        )}

        <fieldset className="space-y-3">
          <legend className="text-sm font-medium">{t("runs.createPanel.adapterLegend")}</legend>
          <div className="grid gap-2 sm:grid-cols-3">
            {adapters.map((item) => (
              <label key={item.name} className={`cursor-pointer rounded-xl border p-3 text-sm ${adapter === item.name ? "border-primary bg-primary/5" : "hover:bg-accent/50"}`}>
                <input className="sr-only" type="radio" name="adapter" value={item.name} checked={adapter === item.name} onChange={() => setAdapter(item.name)} />
                <span className="flex items-center gap-2 font-semibold"><Bot className="size-4" /> {adapterLabels[item.name]}</span>
                <span className="mt-2 inline-flex rounded-full border px-2 py-0.5 text-[11px] font-medium">{item.attention_mode === "bidirectional" ? t("runs.createPanel.attentionApproval") : t("runs.createPanel.attentionCapture")}</span>
                <span className="mt-2 block text-xs font-normal leading-relaxed text-muted-foreground">{item.detail}</span>
              </label>
            ))}
          </div>
          {capabilityWarning && <p className="text-xs text-amber-700 dark:text-amber-300" role="status">{capabilityWarning}</p>}
        </fieldset>

        <section className="rounded-xl border p-4" aria-busy={workspaceLoading || provisioning} aria-live="polite">
          <div className="flex items-start justify-between gap-3">
            <div><h3 className="text-sm font-semibold">{t("runs.createPanel.workspaceTitle")}</h3><p className="mt-1 break-all text-xs text-muted-foreground">{workspace?.path ?? t("runs.createPanel.workspaceChecking")}</p></div>
            <span className="rounded-full border px-2 py-1 text-xs font-medium capitalize">{te("workspaceState", workspaceLoading ? "checking" : workspace?.state ?? "unknown")}</span>
          </div>
          {workspace?.state === "ready" && <p className="mt-3 text-xs text-emerald-700 dark:text-emerald-300">{t("runs.createPanel.workspaceReadyBefore")}<strong>{workspace.branch}</strong>{t("runs.createPanel.workspaceReadyAfter", { sha: workspace.head_sha?.slice(0, 10) ?? "" })}</p>}
          {workspace && workspace.state !== "ready" && (
            <div className="mt-3 space-y-3 text-xs text-muted-foreground">
              <p>{workspace.state === "foreign" ? t("runs.createPanel.workspaceForeign") : workspace.error || (workspace.source_is_git ? t("runs.createPanel.workspaceCreate") : t("runs.createPanel.workspaceNotGit"))}</p>
              {(workspace.state === "missing" || workspace.state === "error") && workspace.source_is_git && (
                <Button
                  type="button"
                  variant="outline"
                  disabled={provisioning}
                  onClick={async () => {
                    if (!selectedTask) return;
                    const requestKey = `${selectedTask.project_id}:${adapter}`;
                    provisionAbortRef.current?.abort();
                    const controller = new AbortController();
                    provisionAbortRef.current = controller;
                    setProvisioningKey(requestKey); setError(null);
                    setWorkspace((current) => current ? { ...current, state: "provisioning", error: null } : current);
                    try {
                      const result = await provisionWorkspace(selectedTask.project_id, adapter, controller.signal);
                      if (mountedRef.current && selectionRef.current === requestKey) setWorkspace(result.status);
                    } catch (err) {
                      if ((err as Error).name !== "AbortError" && mountedRef.current && selectionRef.current === requestKey) {
                        setError(t("runs.createPanel.provisionFailed", { message: (err as Error).message }));
                        setWorkspaceRefresh((value) => value + 1);
                      }
                    } finally {
                      if (provisionAbortRef.current === controller) provisionAbortRef.current = null;
                      if (mountedRef.current) setProvisioningKey((current) => current === requestKey ? null : current);
                    }
                  }}
                >{provisioning ? t("runs.createPanel.provisioning") : t("runs.createPanel.provision")}</Button>
              )}
            </div>
          )}
          {!workspaceLoading && !workspace && selectedTask && (
            <Button type="button" variant="outline" className="mt-3" onClick={() => setWorkspaceRefresh((value) => value + 1)}>{t("runs.createPanel.retryWorkspace")}</Button>
          )}
        </section>

        <label className="block space-y-2 text-sm font-medium">
          <span className="flex items-center justify-between"><span>{t("runs.createPanel.prompt")}</span><span className="text-xs font-normal text-muted-foreground">{prompt.length.toLocaleString()} / 16,000</span></span>
          <textarea className="field min-h-56 resize-y font-mono text-xs leading-relaxed" maxLength={16_000} value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder={t("runs.createPanel.promptPlaceholder")} required />
        </label>

        <label className="block space-y-2 text-sm font-medium">
          <span>{t("runs.createPanel.timeout")}</span>
          <input className="field" type="number" min={1} max={7200} step={1} value={timeout} onChange={(event) => setTimeoutValue(Number(event.target.value))} required />
          <span className="block text-xs font-normal text-muted-foreground">{t("runs.createPanel.timeoutHelp")}</span>
        </label>

        {error && <div className="rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-sm text-destructive" role="alert">{error}</div>}
        <Button type="submit" size="lg" className="w-full" disabled={!valid || submitting}>{submitting ? t("runs.createPanel.queueing") : t("runs.createPanel.queue", { adapter })}</Button>
      </form>
    </div>,
    document.body,
  );
}

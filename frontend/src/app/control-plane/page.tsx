"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  ArrowRight,
  DatabaseZap,
  Fingerprint,
  KeyRound,
  ListFilter,
  RefreshCw,
  ShieldCheck,
  X,
} from "lucide-react";
import { DeliveryShell } from "@/components/delivery-shell";
import { ProjectionDashboard } from "@/components/control-plane/projection-dashboard";
import { Field, messageFor } from "@/components/control-plane/shared";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  candidateDispositionActionReady,
  clearProtectedControlPlaneView,
  controlPlaneResponseBusy,
  planApprovalActionReady,
  ProjectionRequestLifecycle,
} from "@/lib/control-plane-view";
import {
  approveControlPlanePlan,
  disposeControlPlaneConsultationCandidate,
  getUnifiedTaskProjection,
  getControlPlaneTaskIndex,
  respondToControlPlaneAttention,
  type AttentionResponseAction,
  type CandidateDispositionAction,
  type UnifiedConsultationCandidate,
  type UnifiedAttentionItem,
  type UnifiedTaskProjection,
  type UnifiedTaskIndexItem,
} from "@/lib/unified-control-plane";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const TOKEN_KEY = "agora.controlPlaneBearer";

export default function ControlPlanePage() {
  const { t } = useI18n();
  // Callbacks below read `t` through this ref so their identity stays stable
  // across a locale switch. Depending on `t` directly would re-create
  // discoverTasks/loadProjection, which re-runs the bootstrap effect and would
  // reset inputs and re-fetch on every language toggle.
  const tRef = useRef(t);
  useEffect(() => { tRef.current = t; }, [t]);
  const [projectId, setProjectId] = useState("");
  const [taskId, setTaskId] = useState("");
  const [token, setToken] = useState("");
  const [projection, setProjection] = useState<UnifiedTaskProjection | null>(null);
  const [taskOptions, setTaskOptions] = useState<UnifiedTaskIndexItem[]>([]);
  const [taskTotal, setTaskTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [discovering, setDiscovering] = useState(false);
  const [respondingItemId, setRespondingItemId] = useState<string | null>(null);
  const [approvingPlan, setApprovingPlan] = useState(false);
  const [disposingCandidateId, setDisposingCandidateId] = useState<string | null>(null);
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
  const responseLifecycleRef = useRef<ProjectionRequestLifecycle | null>(null);
  if (responseLifecycleRef.current === null) {
    responseLifecycleRef.current = new ProjectionRequestLifecycle();
  }
  const responseLifecycle = responseLifecycleRef.current;

  const discoverTasks = useCallback(async (projectValue: string, bearer: string, selectedTask = "") => {
    const lease = discoveryLifecycle.begin();
    const project = projectValue.trim();
    if (!project || !bearer) {
      setTaskOptions([]);
      setTaskTotal(0);
      setDiscovering(false);
      setError(tRef.current("controlPlane.errors.discoverRequired"));
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
      setError(messageFor(err, tRef.current));
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
      setError(tRef.current("controlPlane.errors.inspectRequired"));
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
      setError(messageFor(err, tRef.current));
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

  const respondToAttention = useCallback(async (
    item: UnifiedAttentionItem,
    action: AttentionResponseAction,
    response: string,
    operationKey: string,
  ) => {
    lifecycle.invalidate();
    setLoading(false);
    const lease = responseLifecycle.begin();
    setApprovingPlan(false);
    setDisposingCandidateId(null);
    const project = projectId.trim();
    const task = taskId.trim();
    if (
      !project
      || !task
      || !token
      || item.project_id !== project
      || item.task_id !== task
    ) {
      setRespondingItemId(null);
      setError(tRef.current("controlPlane.errors.attentionUnbound"));
      responseLifecycle.finish(lease.requestId);
      return;
    }
    setRespondingItemId(item.item_id);
    setError(null);
    try {
      await respondToControlPlaneAttention(
        project,
        task,
        item.item_id,
        token,
        {
          action,
          response,
          expected_version: item.version,
          operation_key: operationKey,
        },
        lease.signal,
      );
      const next = await getUnifiedTaskProjection(
        project,
        task,
        token,
        lease.signal,
      );
      if (!responseLifecycle.isCurrent(lease.requestId)) return;
      setProjection(next);
    } catch (err) {
      if (
        (err as Error).name === "AbortError"
        || !responseLifecycle.isCurrent(lease.requestId)
      ) return;
      setError(messageFor(err, tRef.current));
    } finally {
      if (responseLifecycle.isCurrent(lease.requestId)) {
        setRespondingItemId(null);
        responseLifecycle.finish(lease.requestId);
      }
    }
  }, [lifecycle, projectId, responseLifecycle, taskId, token]);

  const approvePlan = useCallback(async (
    reason: string,
    operationKey: string,
  ) => {
    lifecycle.invalidate();
    setLoading(false);
    const lease = responseLifecycle.begin();
    setRespondingItemId(null);
    setDisposingCandidateId(null);
    const project = projectId.trim();
    const task = taskId.trim();
    const visible = projection;
    const planAction = visible && planApprovalActionReady(
      visible.required_human_actions,
      visible.plan.plan_id,
    );
    if (
      !project
      || !task
      || !token
      || !visible
      || visible.task.project_id !== project
      || visible.task.task_id !== task
      || visible.plan.project_id !== project
      || visible.plan.task_id !== task
      || visible.task_state !== "needs_review"
      || visible.task_state_version === null
      || visible.plan.state !== "awaiting_approval"
      || !planAction
    ) {
      setApprovingPlan(false);
      setError(tRef.current("controlPlane.errors.planUnbound"));
      responseLifecycle.finish(lease.requestId);
      return;
    }
    setApprovingPlan(true);
    setError(null);
    try {
      await approveControlPlanePlan(
        project,
        task,
        token,
        {
          reason,
          expected_task_version: visible.task_state_version,
          expected_plan_version: visible.plan.version,
          operation_key: operationKey,
        },
        lease.signal,
      );
      const next = await getUnifiedTaskProjection(
        project,
        task,
        token,
        lease.signal,
      );
      if (!responseLifecycle.isCurrent(lease.requestId)) return;
      setProjection(next);
    } catch (err) {
      if (
        (err as Error).name === "AbortError"
        || !responseLifecycle.isCurrent(lease.requestId)
      ) return;
      setError(messageFor(err, tRef.current));
    } finally {
      if (responseLifecycle.isCurrent(lease.requestId)) {
        setApprovingPlan(false);
        responseLifecycle.finish(lease.requestId);
      }
    }
  }, [lifecycle, projectId, projection, responseLifecycle, taskId, token]);

  const disposeCandidate = useCallback(async (
    candidate: UnifiedConsultationCandidate,
    action: CandidateDispositionAction,
    reason: string,
    operationKey: string,
  ) => {
    lifecycle.invalidate();
    setLoading(false);
    const lease = responseLifecycle.begin();
    setRespondingItemId(null);
    setApprovingPlan(false);
    const project = projectId.trim();
    const task = taskId.trim();
    const visible = projection;
    const dispositionAction = visible && candidateDispositionActionReady(
      visible.required_human_actions,
      candidate.candidate_id,
    );
    if (
      !project
      || !task
      || !token
      || !visible
      || candidate.project_id !== project
      || candidate.task_id !== task
      || candidate.plan_id !== visible.plan.plan_id
      || candidate.plan_version_observed !== visible.plan.version
      || candidate.advisory_authority !== false
      || candidate.formal_artifact !== false
      || !dispositionAction
    ) {
      setDisposingCandidateId(null);
      setError(tRef.current("controlPlane.errors.candidateUnbound"));
      responseLifecycle.finish(lease.requestId);
      return;
    }
    setDisposingCandidateId(candidate.candidate_id);
    setError(null);
    try {
      await disposeControlPlaneConsultationCandidate(
        project,
        task,
        candidate.candidate_id,
        token,
        {
          action,
          reason,
          expected_candidate_sha256: candidate.content_sha256,
          expected_plan_version: visible.plan.version,
          operation_key: operationKey,
        },
        lease.signal,
      );
      const next = await getUnifiedTaskProjection(
        project,
        task,
        token,
        lease.signal,
      );
      if (!responseLifecycle.isCurrent(lease.requestId)) return;
      setProjection(next);
    } catch (err) {
      if (
        (err as Error).name === "AbortError"
        || !responseLifecycle.isCurrent(lease.requestId)
      ) return;
      setError(messageFor(err, tRef.current));
    } finally {
      if (responseLifecycle.isCurrent(lease.requestId)) {
        setDisposingCandidateId(null);
        responseLifecycle.finish(lease.requestId);
      }
    }
  }, [lifecycle, projectId, projection, responseLifecycle, taskId, token]);

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
    responseLifecycle.invalidate();
  }, [discoveryLifecycle, lifecycle, responseLifecycle]);

  return (
    <DeliveryShell active="Control Plane">
      <header className="border-b bg-background/90 px-5 py-5 backdrop-blur md:px-8">
        <div className="mx-auto flex max-w-[1520px] flex-wrap items-center justify-between gap-4">
          <div>
            <div className="flex items-center gap-2 text-xs font-medium uppercase tracking-[0.2em] text-muted-foreground"><ShieldCheck className="size-3.5" /> {t("controlPlane.header.eyebrow")}</div>
            <h1 className="mt-1 text-2xl font-bold">{t("controlPlane.header.title")}</h1>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant="outline" className="h-7 gap-1.5 border-emerald-500/30 bg-emerald-500/5 text-emerald-700 dark:text-emerald-300"><DatabaseZap /> {t("controlPlane.header.scopeBadge")}</Badge>
            {projection && <Button variant="outline" size="lg" onClick={() => void load()} disabled={loading || respondingItemId !== null || approvingPlan || disposingCandidateId !== null}><RefreshCw className={cn(loading && "animate-spin")} /> {t("controlPlane.header.refresh")}</Button>}
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
          responding={respondingItemId !== null || approvingPlan || disposingCandidateId !== null}
          ready={ready}
          onProject={(value) => {
            lifecycle.invalidate();
            discoveryLifecycle.invalidate();
            responseLifecycle.invalidate();
            setProjectId(value);
            setTaskOptions([]);
            setTaskTotal(0);
            setProjection(null);
            setLoading(false);
            setDiscovering(false);
            setRespondingItemId(null);
            setApprovingPlan(false);
            setDisposingCandidateId(null);
          }}
          onTask={(value) => {
            lifecycle.invalidate();
            responseLifecycle.invalidate();
            setTaskId(value);
            setProjection(null);
            setLoading(false);
            setRespondingItemId(null);
            setApprovingPlan(false);
            setDisposingCandidateId(null);
          }}
          onToken={(value) => {
            lifecycle.invalidate();
            discoveryLifecycle.invalidate();
            responseLifecycle.invalidate();
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
            setRespondingItemId(null);
            setApprovingPlan(false);
            setDisposingCandidateId(null);
          }}
          onConnect={() => void load()}
          onDiscover={() => void discoverTasks(projectId, token, taskId)}
          onForget={() => {
            lifecycle.invalidate();
            discoveryLifecycle.invalidate();
            responseLifecycle.invalidate();
            window.sessionStorage.removeItem(TOKEN_KEY);
            setToken("");
            setProjection(null);
            setTaskOptions([]);
            setTaskTotal(0);
            setLoading(false);
            setDiscovering(false);
            setRespondingItemId(null);
            setApprovingPlan(false);
            setDisposingCandidateId(null);
            setError(null);
          }}
        />

        {error && <div role="alert" className="flex items-center justify-between gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4 text-sm text-destructive"><span className="flex items-center gap-2"><AlertTriangle className="size-4" />{error}</span><Button variant="ghost" size="icon-sm" onClick={() => setError(null)} aria-label={t("controlPlane.errors.dismiss")}><X /></Button></div>}

        {!projection && !loading && (
          <section className="grid min-h-72 place-items-center rounded-2xl border border-dashed bg-card p-8 text-center">
            <div className="max-w-md"><Fingerprint className="mx-auto size-10 text-muted-foreground/50" /><h2 className="mt-4 text-lg font-semibold">{t("controlPlane.empty.title")}</h2><p className="mt-2 text-sm leading-6 text-muted-foreground">{t("controlPlane.empty.bodyBefore")}<code>{t("controlPlane.empty.bodyCode")}</code>{t("controlPlane.empty.bodyAfter")}</p></div>
          </section>
        )}

        {projection && (
          <ProjectionDashboard
            projection={projection}
            respondingItemId={respondingItemId}
            approvingPlan={approvingPlan}
            disposingCandidateId={disposingCandidateId}
            responseBusy={controlPlaneResponseBusy(
              loading,
              respondingItemId,
              approvingPlan,
              disposingCandidateId,
            )}
            onRespond={respondToAttention}
            onApprovePlan={approvePlan}
            onDisposeCandidate={disposeCandidate}
          />
        )}
      </main>
    </DeliveryShell>
  );
}

function ConnectionPanel({ projectId, taskId, token, tasks, taskTotal, loading, discovering, responding, ready, onProject, onTask, onToken, onConnect, onDiscover, onForget }: {
  projectId: string;
  taskId: string;
  token: string;
  tasks: UnifiedTaskIndexItem[];
  taskTotal: number;
  loading: boolean;
  discovering: boolean;
  responding: boolean;
  ready: boolean;
  onProject: (value: string) => void;
  onTask: (value: string) => void;
  onToken: (value: string) => void;
  onConnect: () => void;
  onDiscover: () => void;
  onForget: () => void;
}) {
  const { t, te } = useI18n();
  const taskLabel = tasks.length > 0
    ? (taskTotal > tasks.length
      ? t("controlPlane.connection.taskLabelShown", { shown: tasks.length, total: taskTotal })
      : t("controlPlane.connection.taskLabelInspectable", { total: taskTotal }))
    : t("controlPlane.connection.taskLabel");
  return (
    <form className="grid gap-3 rounded-2xl border bg-card p-4 shadow-sm md:grid-cols-2 xl:grid-cols-[minmax(0,1fr)_minmax(0,1.25fr)_minmax(0,1.5fr)_auto]" onSubmit={(event) => { event.preventDefault(); onConnect(); }} aria-label={t("controlPlane.connection.ariaLabel")}>
      <Field label={t("controlPlane.connection.projectLabel")}><input className="field font-mono" value={projectId} onChange={(event) => onProject(event.target.value)} placeholder={t("controlPlane.connection.projectPlaceholder")} autoComplete="off" /></Field>
      <Field label={taskLabel}><input list="control-plane-task-options" className="field font-mono" value={taskId} onChange={(event) => onTask(event.target.value)} placeholder={t("controlPlane.connection.taskPlaceholder")} autoComplete="off" /><datalist id="control-plane-task-options">{tasks.map((task) => <option key={task.task_id} value={task.task_id}>{`[${te("status", task.task_state)}] ${task.title}`}</option>)}</datalist></Field>
      <Field label={t("controlPlane.connection.bearerLabel")}><div className="relative"><KeyRound className="pointer-events-none absolute left-3 top-2.5 size-4 text-muted-foreground" /><input className="field pl-9 font-mono" type="password" value={token} onChange={(event) => onToken(event.target.value)} placeholder={t("controlPlane.connection.bearerPlaceholder")} autoComplete="off" /></div></Field>
      <div className="flex flex-wrap items-end gap-2 md:col-span-2 xl:col-span-1"><Button type="button" variant="outline" size="lg" onClick={onDiscover} disabled={!ready || discovering || loading || responding}>{discovering ? <RefreshCw className="animate-spin" /> : <ListFilter />}{discovering ? t("controlPlane.connection.finding") : t("controlPlane.connection.find")}</Button><Button type="submit" size="lg" className="min-w-24" disabled={!ready || loading || discovering || responding}>{loading ? <RefreshCw className="animate-spin" /> : <ArrowRight />}{loading ? t("controlPlane.connection.loading") : t("controlPlane.connection.connect")}</Button>{token && <Button type="button" variant="ghost" size="lg" onClick={onForget}>{t("controlPlane.connection.forget")}</Button>}</div>
    </form>
  );
}

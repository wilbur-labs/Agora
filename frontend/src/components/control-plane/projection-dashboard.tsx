"use client";

// Authoritative Task projection dashboard: status, progress, budgets, and the
// stage / human-action / run / artifact / evidence / approval / audit ledgers.

import { Activity, AlertTriangle, FileCheck2, Gauge, Route, UserRoundCheck } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import {
  type AttentionResponseAction,
  type CandidateDispositionAction,
  type UnifiedConsultationCandidate,
  type UnifiedAttentionItem,
  type UnifiedTaskProjection,
} from "@/lib/unified-control-plane";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import {
  Fact,
  Metric,
  StatusBadge,
  formatCurrency,
  formatDate,
  formatNumber,
  routeSourceLabel,
} from "./shared";
import {
  ApprovalLedger,
  ArtifactLedger,
  AuditLedger,
  EvidenceLedger,
  RunLedger,
  StageLedger,
} from "./ledgers";
import { HumanActions } from "./human-actions";

export function ProjectionDashboard({ projection, respondingItemId, approvingPlan, disposingCandidateId, responseBusy, onRespond, onApprovePlan, onDisposeCandidate }: {
  projection: UnifiedTaskProjection;
  respondingItemId: string | null;
  approvingPlan: boolean;
  disposingCandidateId: string | null;
  responseBusy: boolean;
  onRespond: (
    item: UnifiedAttentionItem,
    action: AttentionResponseAction,
    response: string,
    operationKey: string,
  ) => Promise<void>;
  onApprovePlan: (reason: string, operationKey: string) => Promise<void>;
  onDisposeCandidate: (
    candidate: UnifiedConsultationCandidate,
    action: CandidateDispositionAction,
    reason: string,
    operationKey: string,
  ) => Promise<void>;
}) {
  const { t, te } = useI18n();
  const unmeasured = t("controlPlane.metrics.unmeasured");
  const state = projection.task_state;
  const progressPercent = projection.progress.total_stages ? Math.round(((projection.progress.completed_stages ?? 0) / projection.progress.total_stages) * 100) : null;
  const drift = state !== null && projection.task.state !== state;
  const safeAction = projection.next_safe_action.value ?? projection.next_safe_action.unavailable_reason ?? t("controlPlane.dashboard.noSafeAction");
  return (
    <div className="space-y-5">
      <section className="overflow-hidden rounded-2xl border bg-[radial-gradient(circle_at_top_right,var(--color-muted),transparent_42%)] shadow-sm">
        <div className="grid gap-6 p-5 md:p-7 lg:grid-cols-[minmax(0,1.5fr)_minmax(300px,0.8fr)]">
          <div>
            <div className="flex flex-wrap items-center gap-2"><StatusBadge value={state ?? "unavailable"} label={state ? te("status", state) : t("controlPlane.dashboard.unavailable")} prominent /><Badge variant="outline">{t("controlPlane.dashboard.authority", { source: projection.task_state_source })}</Badge><Badge variant="outline">{t("controlPlane.dashboard.version", { version: projection.task_state_version ?? "—" })}</Badge>{projection.task_state_lifecycle === "reconciliation_required" && <Badge variant="destructive">{t("controlPlane.dashboard.reconciliationRequired")}</Badge>}</div>
            <p className="mt-5 font-mono text-xs text-muted-foreground">{projection.task.task_id}</p>
            <h2 className="mt-1 max-w-3xl text-2xl font-bold tracking-tight md:text-3xl">{projection.task.title}</h2>
            {projection.task.description && <p className="mt-3 max-w-3xl text-sm leading-6 text-muted-foreground">{projection.task.description}</p>}
            <div className="mt-5 flex flex-wrap gap-x-6 gap-y-2 text-xs text-muted-foreground"><span>{t("controlPlane.dashboard.project")} <strong className="text-foreground">{projection.task.project_id}</strong></span><span>{t("controlPlane.dashboard.schema")} <strong className="text-foreground">{projection.schema_version}</strong></span><span>{t("controlPlane.dashboard.snapshot")} <strong className="text-foreground">{formatDate(projection.snapshot_at)}</strong></span></div>
          </div>
          <div className="rounded-xl border bg-background/80 p-4">
            <div className="flex items-center justify-between text-sm"><span className="font-medium">{t("controlPlane.dashboard.progressTitle")}</span><span className="font-mono text-muted-foreground">{progressPercent === null ? "—" : `${progressPercent}%`}</span></div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-muted"><div className="h-full rounded-full bg-foreground transition-[width]" style={{ width: `${progressPercent ?? 0}%` }} /></div>
            <dl className="mt-4 grid grid-cols-2 gap-3 text-xs"><Fact label={t("controlPlane.dashboard.completed")} value={projection.progress.completed_stages === null ? "—" : `${projection.progress.completed_stages} / ${projection.progress.total_stages}`} /><Fact label={t("controlPlane.dashboard.current")} value={projection.progress.current_stage_key ?? t("controlPlane.dashboard.notRouted")} /><Fact label={t("controlPlane.dashboard.routeSource")} value={routeSourceLabel(projection.progress.current_stage_source, t)} /><Fact label={t("controlPlane.dashboard.inventory")} value={projection.progress.inventory_complete ? t("controlPlane.dashboard.inventoryComplete") : t("controlPlane.dashboard.inventoryUnavailable")} /><Fact label={t("controlPlane.dashboard.plan")} value={te("status", projection.plan.state)} /></dl>
          </div>
        </div>
        <div className="grid border-t bg-background/60 md:grid-cols-[minmax(0,1fr)_minmax(280px,0.35fr)]">
          <div className="p-5 md:px-7"><p className="flex items-center gap-2 text-xs font-medium uppercase tracking-wider text-muted-foreground"><Route className="size-3.5" /> {t("controlPlane.dashboard.nextSafeAction")}</p><p className="mt-2 text-sm font-medium leading-6">{safeAction}</p>{projection.next_safe_action.source_gate_key && <p className="mt-1 font-mono text-xs text-muted-foreground">{t("controlPlane.dashboard.gate", { key: projection.next_safe_action.source_gate_key })}</p>}</div>
          <div className={cn("border-t p-5 md:border-l md:border-t-0", drift && "bg-amber-500/5")}><p className="text-xs font-medium uppercase tracking-wider text-muted-foreground">{t("controlPlane.dashboard.legacyState")}</p><p className="mt-2 flex items-center gap-2 text-sm font-semibold capitalize">{te("taskState", projection.task.state)}{drift && <AlertTriangle className="size-4 text-amber-600" />}</p><p className="mt-1 text-xs text-muted-foreground">{t("controlPlane.dashboard.legacyNote")}</p></div>
        </div>
      </section>

      {(projection.task_state_unavailable_reason || projection.progress.inventory_unavailable_reason || projection.stage_route_unavailable_reason) && (
        <section className="rounded-xl border border-amber-500/30 bg-amber-500/5 p-4 text-sm"><p className="flex items-center gap-2 font-semibold text-amber-800 dark:text-amber-200"><AlertTriangle className="size-4" /> {t("controlPlane.dashboard.authorityReadiness")}</p><ul className="mt-2 space-y-1 text-xs leading-5 text-muted-foreground">{[projection.task_state_unavailable_reason, projection.progress.inventory_unavailable_reason, projection.stage_route_unavailable_reason].filter((item): item is string => Boolean(item)).map((item) => <li key={item}>• {item}</li>)}</ul></section>
      )}

      <section className="grid gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <Metric icon={Gauge} label={t("controlPlane.metrics.tokens")} value={formatNumber(projection.budget.token_remaining, unmeasured)} detail={t("controlPlane.metrics.tokensDetail", { reserved: formatNumber(projection.budget.token_reserved, unmeasured), measurement: projection.budget.token_measurement })} />
        <Metric icon={Gauge} label={t("controlPlane.metrics.costRemaining")} value={formatCurrency(projection.budget.cost_remaining_usd, unmeasured)} detail={t("controlPlane.metrics.costDetail", { reserved: formatCurrency(projection.budget.cost_reserved_usd, unmeasured), measurement: projection.budget.cost_measurement })} />
        <Metric icon={Activity} label={t("controlPlane.metrics.runs")} value={String(projection.collection_totals.runs ?? 0)} detail={t("controlPlane.metrics.runsDetail", { count: projection.runs.filter((run) => run.attention_required).length })} />
        <Metric icon={FileCheck2} label={t("controlPlane.metrics.evidence")} value={String(projection.collection_totals.evidence ?? 0)} detail={t("controlPlane.metrics.evidenceDetail", { artifacts: projection.collection_totals.artifacts ?? 0, approvals: projection.collection_totals.approvals ?? 0 })} />
        <Metric icon={UserRoundCheck} label={t("controlPlane.metrics.humanActions")} value={String(projection.required_human_actions.length)} detail={t("controlPlane.metrics.humanActionsDetail", { count: projection.collection_totals.attention ?? 0 })} />
      </section>

      <section className="grid items-start gap-5 xl:grid-cols-[minmax(0,1.35fr)_minmax(330px,0.65fr)]"><StageLedger stages={projection.stages} currentStageSource={projection.progress.current_stage_source} /><HumanActions projection={projection} respondingItemId={respondingItemId} approvingPlan={approvingPlan} disposingCandidateId={disposingCandidateId} responseBusy={responseBusy} onRespond={onRespond} onApprovePlan={onApprovePlan} onDisposeCandidate={onDisposeCandidate} /></section>
      <RunLedger runs={projection.runs} />
      <section className="grid items-start gap-5 xl:grid-cols-2 2xl:grid-cols-4"><ArtifactLedger projection={projection} /><EvidenceLedger projection={projection} /><ApprovalLedger projection={projection} /><AuditLedger projection={projection} /></section>
    </div>
  );
}

"use client";

// Read-only ledgers rendered inside the Control Plane dashboard.

import {
  Activity,
  Clock3,
  DatabaseZap,
  FileCheck2,
  Fingerprint,
  Route,
  ShieldCheck,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { runProtocolDimensions } from "@/lib/control-plane-view";
import type {
  UnifiedRunProjection,
  UnifiedStageProjection,
  UnifiedTaskProjection,
} from "@/lib/unified-control-plane";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import {
  EmptyState,
  SectionHeader,
  StatusBadge,
  formatDate,
  formatDuration,
  routeSourceLabel,
  shortId,
} from "./shared";

export function StageLedger({ stages, currentStageSource }: { stages: UnifiedStageProjection[]; currentStageSource: "control_plane_route" | "compatibility_plan" | null }) {
  const { t, te } = useI18n();
  const sourceLabel = routeSourceLabel(currentStageSource, t);
  return <section className="overflow-hidden rounded-xl border bg-card"><SectionHeader icon={Route} title={t("controlPlane.stages.title")} meta={t("controlPlane.stages.meta", { count: stages.length, source: sourceLabel })} /><div className="divide-y">{stages.map((stage, index) => <article key={stage.stage_key} className={cn("grid gap-3 p-4 sm:grid-cols-[36px_minmax(0,1fr)_auto]", stage.current && "bg-primary/5")}><div className={cn("grid size-9 place-items-center rounded-full border font-mono text-xs", stage.current && currentStageSource === "control_plane_route" && "border-primary bg-primary text-primary-foreground")}>{stage.sequence ?? index + 1}</div><div className="min-w-0"><div className="flex flex-wrap items-center gap-2"><h3 className="font-medium">{stage.title ?? stage.stage_key}</h3>{stage.current && currentStageSource === "control_plane_route" && <Badge>{t("controlPlane.stages.authorityCurrent")}</Badge>}{stage.current && currentStageSource === "compatibility_plan" && <Badge variant="outline" className="border-amber-500/40 text-amber-700 dark:text-amber-300">{t("controlPlane.stages.compatibilityCursor")}</Badge>}</div><p className="mt-1 font-mono text-xs text-muted-foreground">{stage.stage_key} · {stage.runtime ?? t("controlPlane.stages.runtimeUnassigned")}</p>{stage.semantic_summary && <p className="mt-2 text-xs leading-5 text-muted-foreground">{stage.semantic_summary}</p>}{stage.blockers.length > 0 && <p className="mt-2 text-xs text-destructive">{stage.blockers.join(" · ")}</p>}</div><div className="flex items-start gap-2 sm:justify-end"><StatusBadge value={stage.authoritative_stage?.status ?? "not initialized"} label={stage.authoritative_stage?.status ? te("status", stage.authoritative_stage.status) : t("controlPlane.stages.notInitialized")} /><StatusBadge value={stage.gate?.status ? "gate" : "no gate"} label={stage.gate?.status ? `${t("controlPlane.stages.gatePrefix")}${te("status", stage.gate.status)}` : t("controlPlane.stages.noGate")} muted /></div></article>)}{stages.length === 0 && <EmptyState text={t("controlPlane.stages.empty")} />}</div></section>;
}

export function RunLedger({ runs }: { runs: UnifiedRunProjection[] }) {
  const { t, te } = useI18n();
  return <section className="overflow-hidden rounded-xl border bg-card"><SectionHeader icon={Activity} title={t("controlPlane.runs.title")} meta={t("controlPlane.runs.meta", { count: runs.length })} /><div className="overflow-x-auto"><table className="w-full min-w-[1180px] text-left text-sm"><caption className="sr-only">{t("controlPlane.runs.caption")}</caption><thead className="bg-muted/40 text-xs text-muted-foreground"><tr><th className="px-4 py-3">{t("controlPlane.runs.colRun")}</th><th className="px-3 py-3">{t("controlPlane.runs.colRuntime")}</th><th className="px-3 py-3">{t("controlPlane.runs.colWait")}</th><th className="px-3 py-3">{t("controlPlane.runs.colProcess")}</th><th className="px-3 py-3">{t("controlPlane.runs.colExit")}</th><th className="px-3 py-3">{t("controlPlane.runs.colTransport")}</th><th className="px-3 py-3">{t("controlPlane.runs.colSchema")}</th><th className="px-3 py-3">{t("controlPlane.runs.colSemantic")}</th><th className="px-3 py-3">{t("controlPlane.runs.colElapsed")}</th></tr></thead><tbody className="divide-y">{runs.map((run) => { const dimensions = runProtocolDimensions(run); return <tr key={run.run_id}><th scope="row" className="px-4 py-3"><span className="block font-mono text-xs">{shortId(run.run_id)}</span><span className="mt-1 block font-normal text-muted-foreground">{run.stage_key}</span></th><td className="px-3 py-3 capitalize">{run.runtime ?? "—"}</td><td className="px-3 py-3"><StatusBadge value={dimensions.wait} label={te("status", dimensions.wait)} muted /></td><td className="px-3 py-3"><StatusBadge value={dimensions.process} label={te("status", dimensions.process)} /></td><td className={cn("px-3 py-3 font-mono text-xs", dimensions.exitFailed && "font-semibold text-destructive")}>{dimensions.exit}</td><td className="px-3 py-3"><StatusBadge value={run.transport_status ?? "unavailable"} label={te("status", run.transport_status ?? "unavailable")} muted /></td><td className="px-3 py-3"><StatusBadge value={run.schema_status ?? "unavailable"} label={te("status", run.schema_status ?? "unavailable")} muted /></td><td className="px-3 py-3"><StatusBadge value={dimensions.semantic} label={te("status", dimensions.semantic)} /><span className="mt-1 block text-[0.75rem] text-muted-foreground">{run.semantic_source}</span></td><td className="px-3 py-3 font-mono text-xs text-muted-foreground">{formatDuration(run.elapsed_seconds)}</td></tr>; })}</tbody></table></div>{runs.length === 0 && <EmptyState icon={Clock3} text={t("controlPlane.runs.empty")} />}</section>;
}

export function ArtifactLedger({ projection }: { projection: UnifiedTaskProjection }) {
  const { t } = useI18n();
  return <section className="overflow-hidden rounded-xl border bg-card"><SectionHeader icon={DatabaseZap} title={t("controlPlane.artifacts.title")} meta={t("controlPlane.artifacts.meta", { count: projection.artifacts.length })} /><div className="divide-y">{projection.artifacts.slice(0, 6).map((item) => <article key={`${item.version_ref.artifact_id}-${item.version_ref.version}`} className="p-4"><div className="flex items-center justify-between gap-2"><p className="truncate font-mono text-xs">{item.version_ref.artifact_id}</p><Badge variant="outline">v{item.version_ref.version}</Badge></div><p className="mt-2 text-sm">{item.version_ref.kind}</p><p className="mt-1 text-[0.75rem] text-muted-foreground">{item.stage_key} · {item.producer_runtime}</p><p className="mt-2 truncate font-mono text-[0.6875rem] text-muted-foreground" title={item.version_ref.sha256}>sha256 {item.version_ref.sha256}</p></article>)}{projection.artifacts.length === 0 && <EmptyState text={t("controlPlane.artifacts.empty")} />}</div></section>;
}

export function EvidenceLedger({ projection }: { projection: UnifiedTaskProjection }) {
  const { t, te } = useI18n();
  return <section className="overflow-hidden rounded-xl border bg-card"><SectionHeader icon={FileCheck2} title={t("controlPlane.evidence.title")} meta={t("controlPlane.evidence.meta", { count: projection.evidence.length })} /><div className="divide-y">{projection.evidence.slice(0, 6).map((item) => <article key={item.evidence_id} className="p-4"><div className="flex items-center justify-between gap-2"><p className="truncate font-mono text-xs">{item.evidence_id}</p><StatusBadge value={item.status} label={te("status", item.status)} /></div><p className="mt-2 line-clamp-2 text-sm">{item.summary}</p><p className="mt-2 text-[0.75rem] text-muted-foreground">{item.kind} · {formatDate(item.observed_at)}</p></article>)}{projection.evidence.length === 0 && <EmptyState text={t("controlPlane.evidence.empty")} />}</div></section>;
}

export function ApprovalLedger({ projection }: { projection: UnifiedTaskProjection }) {
  const { t, te } = useI18n();
  return <section className="overflow-hidden rounded-xl border bg-card"><SectionHeader icon={ShieldCheck} title={t("controlPlane.approvals.title")} meta={t("controlPlane.approvals.meta", { count: projection.approvals.length })} /><div className="divide-y">{projection.approvals.slice(0, 6).map((item) => <article key={item.approval_id} className="p-4"><div className="flex items-center justify-between gap-2"><p className="truncate font-mono text-xs">{item.approval_id}</p><StatusBadge value={item.status} label={te("status", item.status)} /></div><p className="mt-2 text-sm">{item.approved_by}</p><p className="mt-1 text-[0.75rem] text-muted-foreground">{item.gate_key} · {formatDate(item.approved_at)}</p>{item.stale_reason && <p className="mt-2 text-xs text-destructive">{item.stale_reason}</p>}</article>)}{projection.approvals.length === 0 && <EmptyState text={t("controlPlane.approvals.empty")} />}</div></section>;
}

export function AuditLedger({ projection }: { projection: UnifiedTaskProjection }) {
  const { t } = useI18n();
  return <section className="overflow-hidden rounded-xl border bg-card"><SectionHeader icon={Fingerprint} title={t("controlPlane.audit.title")} meta={t("controlPlane.audit.meta", { count: projection.audit_events.length })} /><div className="max-h-[430px] divide-y overflow-y-auto">{projection.audit_events.slice(0, 12).map((event) => <article key={`${event.source}-${event.event_id}`} className="p-4"><div className="flex items-center justify-between gap-2"><p className="truncate text-sm font-medium">{event.event_type}</p><Badge variant="outline">{event.source}</Badge></div><p className="mt-1 text-xs text-muted-foreground">{event.actor} · {formatDate(event.created_at)}</p>{event.payload_truncated && <p className="mt-1 text-[0.75rem] text-amber-600">{t("controlPlane.audit.truncated")}</p>}</article>)}{projection.audit_events.length === 0 && <EmptyState text={t("controlPlane.audit.empty")} />}</div></section>;
}

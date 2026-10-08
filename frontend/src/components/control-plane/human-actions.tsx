"use client";

// Human checkpoint: attention responses, plan approval, and consultation
// candidate disposition. Each form binds to versioned authoritative state and
// generates an idempotent operation key on submit.

import { useRef, useState } from "react";
import { CheckCircle2, CircleDot, RefreshCw, Send, ShieldCheck, UserRoundCheck, X } from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  AttentionResponseRetryKey,
  CandidateDispositionRetryKey,
  candidateDispositionActionReady,
  PlanApprovalRetryKey,
  planApprovalActionReady,
} from "@/lib/control-plane-view";
import {
  type AttentionResponseAction,
  type CandidateDispositionAction,
  type UnifiedConsultationCandidate,
  type UnifiedAttentionItem,
  type UnifiedTaskProjection,
} from "@/lib/unified-control-plane";
import { useI18n, type Translate } from "@/lib/i18n";
import { EmptyState, Field, SectionHeader, formatDate } from "./shared";

function attentionActionLabel(action: AttentionResponseAction, t: Translate): string {
  if (action === "approve") return t("controlPlane.attentionForm.approve");
  if (action === "reject") return t("controlPlane.attentionForm.reject");
  return t("controlPlane.attentionForm.answer");
}

export function HumanActions({ projection, respondingItemId, approvingPlan, disposingCandidateId, responseBusy, onRespond, onApprovePlan, onDisposeCandidate }: {
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
  const { t } = useI18n();
  const attention = new Map(
    projection.attention.map((item) => [item.item_id, item]),
  );
  const candidates = new Map(
    projection.consultation_candidates.map((candidate) => [candidate.candidate_id, candidate]),
  );
  return (
    <section className="overflow-hidden rounded-xl border bg-card">
      <SectionHeader icon={UserRoundCheck} title={t("controlPlane.human.title")} meta={t("controlPlane.human.meta", { count: projection.required_human_actions.length })} />
      <div className="divide-y">
        {projection.required_human_actions.map((action) => {
          const item = action.kind === "attention" ? attention.get(action.source_id) : undefined;
          const candidate = action.kind === "candidate_disposition"
            ? candidates.get(action.source_id)
            : undefined;
          return (
            <article key={action.action_id} className="p-4">
              <div className="flex items-start gap-3">
                <CircleDot className="mt-0.5 size-4 text-amber-600" />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">{action.title}</p>
                  <p className="mt-1 font-mono text-[0.75rem] text-muted-foreground">{action.kind} · {action.source_id}</p>
                  {item && item.state === "open" && (
                    <AttentionResponseForm
                      item={item}
                      busy={responseBusy}
                      active={respondingItemId === item.item_id}
                      onRespond={onRespond}
                    />
                  )}
                  {candidate
                    && candidate.project_id === projection.task.project_id
                    && candidate.task_id === projection.task.task_id
                    && candidate.plan_id === projection.plan.plan_id
                    && candidate.plan_version_observed === projection.plan.version
                    && candidate.advisory_authority === false
                    && candidate.formal_artifact === false
                    && candidateDispositionActionReady(
                      projection.required_human_actions,
                      candidate.candidate_id,
                    )
                    && (
                      <CandidateDispositionForm
                        candidate={candidate}
                        busy={responseBusy}
                        active={disposingCandidateId === candidate.candidate_id}
                        onDispose={onDisposeCandidate}
                      />
                    )}
                  {action.kind === "plan_approval"
                    && action.source_id === projection.plan.plan_id
                    && projection.task_state === "needs_review"
                    && projection.task_state_version !== null
                    && projection.plan.state === "awaiting_approval"
                    && planApprovalActionReady(
                      projection.required_human_actions,
                      projection.plan.plan_id,
                    )
                    && (
                      <PlanApprovalForm
                        projection={projection}
                        busy={responseBusy}
                        active={approvingPlan}
                        onApprove={onApprovePlan}
                      />
                    )}
                </div>
              </div>
            </article>
          );
        })}
        {projection.required_human_actions.length === 0 && <EmptyState icon={CheckCircle2} text={t("controlPlane.human.empty")} />}
      </div>
      <div className="border-t bg-muted/25 p-4"><p className="text-xs font-medium text-muted-foreground">{t("controlPlane.human.methodology")}</p><p className="mt-1 text-sm">{projection.plan.methodology_id} <span className="text-muted-foreground">v{projection.plan.methodology_version}{projection.plan.provisional ? t("controlPlane.human.provisional") : ""}</span></p></div>
    </section>
  );
}

function CandidateDispositionForm({ candidate, busy, active, onDispose }: {
  candidate: UnifiedConsultationCandidate;
  busy: boolean;
  active: boolean;
  onDispose: (
    candidate: UnifiedConsultationCandidate,
    action: CandidateDispositionAction,
    reason: string,
    operationKey: string,
  ) => Promise<void>;
}) {
  const { t } = useI18n();
  const [action, setAction] = useState<CandidateDispositionAction>("adopt");
  const [reason, setReason] = useState("");
  const retryKeyRef = useRef<CandidateDispositionRetryKey | null>(null);
  if (retryKeyRef.current === null) {
    retryKeyRef.current = new CandidateDispositionRetryKey();
  }
  const retryKey = retryKeyRef.current;
  return (
    <form
      className="mt-4 space-y-3 rounded-lg border bg-background/70 p-3"
      aria-label={t("controlPlane.candidateForm.aria", { title: candidate.title })}
      onSubmit={(event) => {
        event.preventDefault();
        const operationKey = retryKey.forDraft(
          {
            candidateId: candidate.candidate_id,
            expectedCandidateSha256: candidate.content_sha256,
            expectedPlanVersion: candidate.plan_version_observed,
            action,
            reason,
          },
          () => window.crypto.randomUUID(),
        );
        void onDispose(candidate, action, reason, operationKey);
      }}
    >
      <div className="flex flex-wrap gap-2 text-[0.75rem] text-muted-foreground">
        <Badge variant="outline">{t("controlPlane.candidateForm.advisoryOnly")}</Badge>
        <Badge variant="outline">{t("controlPlane.candidateForm.notFormal")}</Badge>
        <Badge variant="outline">{candidate.runtime}</Badge>
        <Badge variant="outline">{t("controlPlane.candidateForm.planVersion", { version: candidate.plan_version_observed })}</Badge>
      </div>
      <div className="space-y-2 rounded-md border bg-muted/20 p-3 text-xs leading-5">
        <p><span className="font-medium text-muted-foreground">{t("controlPlane.candidateForm.decisionKey")}</span><span className="ml-2 font-mono">{candidate.decision_key}</span></p>
        <p className="font-medium">{candidate.decision_value}</p>
        <p className="whitespace-pre-wrap text-muted-foreground">{candidate.analysis}</p>
        {candidate.source_refs.length > 0 && (
          <p className="text-[0.75rem] text-muted-foreground">{t("controlPlane.candidateForm.sources", { refs: candidate.source_refs.join(", ") })}</p>
        )}
        <p className="break-all font-mono text-[0.6875rem] text-muted-foreground">{t("controlPlane.candidateForm.sha", { hash: candidate.content_sha256 })}</p>
      </div>
      <Field label={t("controlPlane.candidateForm.disposition")}>
        <select
          className="field capitalize"
          value={action}
          onChange={(event) => setAction(event.target.value as CandidateDispositionAction)}
          disabled={busy}
        >
          <option value="adopt">{t("controlPlane.candidateForm.adopt")}</option>
          <option value="reject">{t("controlPlane.candidateForm.reject")}</option>
        </select>
      </Field>
      <Field label={t("controlPlane.candidateForm.rationale")}>
        <textarea
          className="field min-h-24 resize-y"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          maxLength={500}
          disabled={busy}
        />
      </Field>
      <div className="flex items-center justify-between gap-3">
        <p className="text-[0.75rem] leading-4 text-muted-foreground">
          {t("controlPlane.candidateForm.note")}
        </p>
        <Button type="submit" size="sm" disabled={busy || !reason.trim()}>
          {active
            ? <RefreshCw className="animate-spin" />
            : (action === "adopt" ? <CheckCircle2 /> : <X />)}
          {active ? t("controlPlane.candidateForm.saving") : (action === "adopt" ? t("controlPlane.candidateForm.adopt") : t("controlPlane.candidateForm.reject"))}
        </Button>
      </div>
    </form>
  );
}

function PlanApprovalForm({ projection, busy, active, onApprove }: {
  projection: UnifiedTaskProjection;
  busy: boolean;
  active: boolean;
  onApprove: (reason: string, operationKey: string) => Promise<void>;
}) {
  const { t } = useI18n();
  const [reason, setReason] = useState("");
  const retryKeyRef = useRef<PlanApprovalRetryKey | null>(null);
  if (retryKeyRef.current === null) retryKeyRef.current = new PlanApprovalRetryKey();
  const retryKey = retryKeyRef.current;
  return (
    <form
      className="mt-4 space-y-3 rounded-lg border bg-background/70 p-3"
      aria-label={t("controlPlane.planForm.aria")}
      onSubmit={(event) => {
        event.preventDefault();
        if (projection.task_state_version === null) return;
        const operationKey = retryKey.forDraft(
          {
            taskId: projection.task.task_id,
            expectedTaskVersion: projection.task_state_version,
            planId: projection.plan.plan_id,
            expectedPlanVersion: projection.plan.version,
            reason,
          },
          () => window.crypto.randomUUID(),
        );
        void onApprove(reason, operationKey);
      }}
    >
      <div className="flex flex-wrap gap-2 text-[0.75rem] text-muted-foreground">
        <Badge variant="outline">{t("controlPlane.planForm.taskVersion", { version: projection.task_state_version ?? "—" })}</Badge>
        <Badge variant="outline">{t("controlPlane.planForm.planVersion", { version: projection.plan.version })}</Badge>
        <span>{t("controlPlane.planForm.gatesNote")}</span>
      </div>
      <Field label={t("controlPlane.planForm.rationale")}>
        <textarea
          className="field min-h-24 resize-y"
          value={reason}
          onChange={(event) => setReason(event.target.value)}
          maxLength={4_000}
          disabled={busy}
        />
      </Field>
      <div className="flex items-center justify-between gap-3">
        <p className="text-[0.75rem] leading-4 text-muted-foreground">{t("controlPlane.planForm.note")}</p>
        <Button type="submit" size="sm" disabled={busy || !reason.trim()}>
          {active ? <RefreshCw className="animate-spin" /> : <ShieldCheck />}
          {active ? t("controlPlane.planForm.approving") : t("controlPlane.planForm.approve")}
        </Button>
      </div>
    </form>
  );
}

function AttentionResponseForm({ item, busy, active, onRespond }: {
  item: UnifiedAttentionItem;
  busy: boolean;
  active: boolean;
  onRespond: (
    item: UnifiedAttentionItem,
    action: AttentionResponseAction,
    response: string,
    operationKey: string,
  ) => Promise<void>;
}) {
  const { t } = useI18n();
  const actions: AttentionResponseAction[] = item.kind === "approval"
    ? ["approve", "reject"]
    : ["answer"];
  const [action, setAction] = useState<AttentionResponseAction>(actions[0]);
  const [response, setResponse] = useState("");
  const retryKeyRef = useRef<AttentionResponseRetryKey | null>(null);
  if (retryKeyRef.current === null) retryKeyRef.current = new AttentionResponseRetryKey();
  const retryKey = retryKeyRef.current;
  const requiresText = action === "answer";
  return (
    <form
      className="mt-4 space-y-3 rounded-lg border bg-background/70 p-3"
      aria-label={t("controlPlane.attentionForm.aria", { title: item.title })}
      onSubmit={(event) => {
        event.preventDefault();
        const operationKey = retryKey.forDraft(
          {
            itemId: item.item_id,
            expectedVersion: item.version,
            action,
            response,
          },
          () => window.crypto.randomUUID(),
        );
        void onRespond(item, action, response, operationKey);
      }}
    >
      {item.body && <p className="text-xs leading-5 text-muted-foreground">{item.body}</p>}
      <div className="flex flex-wrap gap-2 text-[0.75rem] text-muted-foreground">
        <Badge variant="outline">{item.kind}</Badge>
        <Badge variant="outline">{t("controlPlane.attentionForm.version", { version: item.version })}</Badge>
        <span>{item.assignee ? t("controlPlane.attentionForm.assignedTo", { assignee: item.assignee }) : t("controlPlane.attentionForm.anyResponder")}</span>
        {item.expires_at && <span>{t("controlPlane.attentionForm.expires", { date: formatDate(item.expires_at) })}</span>}
      </div>
      {item.options.length > 0 && (
        <div className="flex flex-wrap gap-2">
          {item.options.map((option) => (
            <Button key={option} type="button" size="sm" variant="outline" onClick={() => setResponse(option)} disabled={busy}>{option}</Button>
          ))}
        </div>
      )}
      {actions.length > 1 && (
        <Field label={t("controlPlane.attentionForm.decision")}>
          <select
            className="field capitalize"
            value={action}
            onChange={(event) => setAction(event.target.value as AttentionResponseAction)}
            disabled={busy}
          >
            {actions.map((value) => <option key={value} value={value}>{attentionActionLabel(value, t)}</option>)}
          </select>
        </Field>
      )}
      <Field label={requiresText ? t("controlPlane.attentionForm.responseRequired") : t("controlPlane.attentionForm.responseOptional")}>
        <textarea
          className="field min-h-24 resize-y"
          value={response}
          onChange={(event) => setResponse(event.target.value)}
          maxLength={32_000}
          disabled={busy}
        />
      </Field>
      <div className="flex items-center justify-between gap-3">
        <p className="text-[0.75rem] leading-4 text-muted-foreground">{t("controlPlane.attentionForm.note")}</p>
        <Button type="submit" size="sm" disabled={busy || (requiresText && !response.trim())}>
          {active ? <RefreshCw className="animate-spin" /> : <Send />}
          {active ? t("controlPlane.attentionForm.sending") : attentionActionLabel(action, t)}
        </Button>
      </div>
    </form>
  );
}

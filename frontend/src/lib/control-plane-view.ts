export interface ProjectionRequestLease {
  requestId: number;
  signal: AbortSignal;
}

export class ProjectionRequestLifecycle {
  private requestId = 0;
  private controller: AbortController | null = null;

  begin(): ProjectionRequestLease {
    this.requestId += 1;
    this.controller?.abort();
    this.controller = new AbortController();
    return { requestId: this.requestId, signal: this.controller.signal };
  }

  invalidate(): void {
    this.requestId += 1;
    this.controller?.abort();
    this.controller = null;
  }

  isCurrent(requestId: number): boolean {
    return this.requestId === requestId;
  }

  finish(requestId: number): void {
    if (this.isCurrent(requestId)) this.controller = null;
  }
}

export interface RunProtocolDimensions {
  process: string;
  wait: string;
  exit: string;
  exitFailed: boolean;
  semantic: string;
}

export function runProtocolDimensions(run: {
  process_status: string | null;
  wait_state: string;
  process_exit_code: number | null;
  timed_out: boolean;
  semantic_result: string | null;
}): RunProtocolDimensions {
  return {
    process: run.process_status ?? "unavailable",
    wait: run.wait_state,
    exit: run.timed_out
      ? "timed out"
      : (run.process_exit_code === null ? "unavailable" : String(run.process_exit_code)),
    exitFailed: run.timed_out || (
      run.process_exit_code !== null && run.process_exit_code !== 0
    ),
    semantic: run.semantic_result ?? "unavailable",
  };
}

export function controlPlaneTaskIndexPath(projectId: string): string {
  return `/api/control-plane/projects/${encodeURIComponent(projectId)}/tasks`;
}

export function controlPlaneAttentionResponsePath(
  projectId: string,
  taskId: string,
  itemId: string,
): string {
  return [
    "/api/control-plane/projects",
    encodeURIComponent(projectId),
    "tasks",
    encodeURIComponent(taskId),
    "attention",
    encodeURIComponent(itemId),
    "responses",
  ].join("/");
}

export function controlPlanePlanApprovalPath(
  projectId: string,
  taskId: string,
): string {
  return [
    "/api/control-plane/projects",
    encodeURIComponent(projectId),
    "tasks",
    encodeURIComponent(taskId),
    "plan-approvals",
  ].join("/");
}

export function controlPlaneCandidateDispositionPath(
  projectId: string,
  taskId: string,
  candidateId: string,
): string {
  return [
    "/api/control-plane/projects",
    encodeURIComponent(projectId),
    "tasks",
    encodeURIComponent(taskId),
    "consultation-candidates",
    encodeURIComponent(candidateId),
    "dispositions",
  ].join("/");
}

export interface AttentionResponseDraft {
  itemId: string;
  expectedVersion: number;
  action: "answer" | "approve" | "reject";
  response: string;
}

export class AttentionResponseRetryKey {
  private fingerprint: string | null = null;
  private operationKey: string | null = null;

  forDraft(draft: AttentionResponseDraft, createNonce: () => string): string {
    const fingerprint = JSON.stringify([
      draft.itemId,
      draft.expectedVersion,
      draft.action,
      draft.response,
    ]);
    if (this.fingerprint !== fingerprint || this.operationKey === null) {
      const nonce = createNonce().replace(/[^A-Za-z0-9_.:-]/g, "-").slice(0, 160);
      if (!nonce) throw new Error("Could not create an Attention retry key.");
      this.fingerprint = fingerprint;
      this.operationKey = `attention-response:${nonce}`;
    }
    return this.operationKey;
  }

  clear(): void {
    this.fingerprint = null;
    this.operationKey = null;
  }
}

export interface PlanApprovalDraft {
  taskId: string;
  expectedTaskVersion: number;
  planId: string;
  expectedPlanVersion: number;
  reason: string;
}

export class PlanApprovalRetryKey {
  private fingerprint: string | null = null;
  private operationKey: string | null = null;

  forDraft(draft: PlanApprovalDraft, createNonce: () => string): string {
    const fingerprint = JSON.stringify([
      draft.taskId,
      draft.expectedTaskVersion,
      draft.planId,
      draft.expectedPlanVersion,
      draft.reason,
    ]);
    if (this.fingerprint !== fingerprint || this.operationKey === null) {
      const nonce = createNonce().replace(/[^A-Za-z0-9_.:-]/g, "-").slice(0, 160);
      if (!nonce) throw new Error("Could not create a Plan approval retry key.");
      this.fingerprint = fingerprint;
      this.operationKey = `plan-approval:${nonce}`;
    }
    return this.operationKey;
  }

  clear(): void {
    this.fingerprint = null;
    this.operationKey = null;
  }
}

export interface CandidateDispositionDraft {
  candidateId: string;
  expectedCandidateSha256: string;
  expectedPlanVersion: number;
  action: "adopt" | "reject";
  reason: string;
}

export class CandidateDispositionRetryKey {
  private fingerprint: string | null = null;
  private operationKey: string | null = null;

  forDraft(draft: CandidateDispositionDraft, createNonce: () => string): string {
    const fingerprint = JSON.stringify([
      draft.candidateId,
      draft.expectedCandidateSha256,
      draft.expectedPlanVersion,
      draft.action,
      draft.reason,
    ]);
    if (this.fingerprint !== fingerprint || this.operationKey === null) {
      const nonce = createNonce().replace(/[^A-Za-z0-9_.:-]/g, "-").slice(0, 96);
      if (!nonce) throw new Error("Could not create a candidate disposition retry key.");
      this.fingerprint = fingerprint;
      this.operationKey = `candidate-disposition:${nonce}`;
    }
    return this.operationKey;
  }

  clear(): void {
    this.fingerprint = null;
    this.operationKey = null;
  }
}

export function controlPlaneResponseBusy(
  projectionLoading: boolean,
  respondingItemId: string | null,
  approvingPlan = false,
  disposingCandidateId: string | null = null,
): boolean {
  return projectionLoading
    || respondingItemId !== null
    || approvingPlan
    || disposingCandidateId !== null;
}

export function planApprovalActionReady(
  actions: Array<{ kind: string; source_id: string }>,
  planId: string,
): boolean {
  return actions.length === 1
    && actions[0].kind === "plan_approval"
    && actions[0].source_id === planId;
}

export function candidateDispositionActionReady(
  actions: Array<{ kind: string; source_id: string }>,
  candidateId: string,
): boolean {
  return actions.some(
    (action) => action.kind === "candidate_disposition"
      && action.source_id === candidateId,
  );
}

export interface ProtectedControlPlaneView<TProjection, TTask> {
  projection: TProjection | null;
  tasks: TTask[];
  taskTotal: number;
  error: string | null;
}

export function clearProtectedControlPlaneView<TProjection, TTask>(
  current: ProtectedControlPlaneView<TProjection, TTask>,
): ProtectedControlPlaneView<TProjection, TTask> {
  return {
    ...current,
    projection: null,
    tasks: [],
    taskTotal: 0,
    error: null,
  };
}

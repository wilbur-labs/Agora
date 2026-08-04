import { getApiBase } from "@/lib/api";
import { ApiError, type TaskManifest } from "@/lib/control-plane";
import {
  controlPlaneAttentionResponsePath,
  controlPlaneCandidateDispositionPath,
  controlPlanePlanApprovalPath,
  controlPlaneTaskIndexPath,
} from "@/lib/control-plane-view";

export type AuthoritativeTaskState =
  | "backlog"
  | "ready"
  | "active"
  | "blocked"
  | "needs_review"
  | "completed"
  | "failed"
  | "cancelled";

export interface UnifiedTaskProgress {
  source: "control_plane_stage_inventory" | "unavailable";
  inventory_complete: boolean;
  inventory_unavailable_reason: string | null;
  total_stages: number | null;
  completed_stages: number | null;
  current_stage_key: string | null;
  current_stage_source: "control_plane_route" | "compatibility_plan" | null;
  completed_stage_keys: string[];
  remaining_stage_keys: string[];
  groups: Array<{
    group_key: string;
    sequence: number;
    title: string;
    total_stages: number;
    completed_stages: number;
    remaining_stage_keys: string[];
  }>;
}

export interface UnifiedStageProjection {
  stage_key: string;
  group_key: string | null;
  sequence: number | null;
  title: string | null;
  runtime: string | null;
  current: boolean;
  operational_state: string | null;
  authoritative_stage: {
    stage_key: string;
    gate_key: string;
    status: string;
    version: number;
  } | null;
  gate: {
    gate_key: string;
    status: string;
    version: number;
    active_evidence_ids: string[];
  } | null;
  attempt_count: number;
  latest_run_id: string | null;
  semantic_summary: string | null;
  blockers: string[];
}

export interface UnifiedRunProjection {
  run_id: string;
  stage_key: string;
  runtime: string | null;
  attempt: number | null;
  operational_state: string | null;
  wait_state: string;
  process_status: string | null;
  transport_status: string | null;
  schema_status: string | null;
  semantic_result: string | null;
  semantic_source: "protocol" | "compatibility" | "unavailable";
  process_exit_code: number | null;
  timed_out: boolean;
  semantic_summary: string | null;
  findings: string[];
  failure: string | null;
  attention_required: boolean;
  token_reserved: number | null;
  token_settled: number | null;
  cost_reserved_usd: number | null;
  cost_settled_usd: number | null;
  started_at: string;
  finished_at: string | null;
  elapsed_seconds: number;
}

export interface UnifiedTaskProjection {
  schema_version: "12.0";
  snapshot_at: string;
  task: TaskManifest;
  task_state: AuthoritativeTaskState | null;
  task_state_source: "control_plane";
  task_state_version: number | null;
  task_state_unavailable_reason: string | null;
  task_state_lifecycle:
    | "control_plane_managed"
    | "reconciliation_required"
    | "unavailable";
  stage_inventory_unavailable_reason: string | null;
  stage_route_unavailable_reason: string | null;
  plan: {
    plan_id: string;
    task_id: string;
    project_id: string;
    methodology_id: string;
    methodology_version: string;
    provisional: boolean;
    state: string;
    current_stage_key: string | null;
    version: number;
    total_token_budget: number;
    total_cost_budget_usd: number | null;
  };
  progress: UnifiedTaskProgress;
  stages: UnifiedStageProjection[];
  runs: UnifiedRunProjection[];
  artifacts: Array<{
    version_ref: {
      artifact_id: string;
      version: number;
      sha256: string;
      kind: string;
    };
    stage_key: string;
    producer_runtime: string;
    media_type: string;
    created_at: string;
  }>;
  evidence: Array<{
    evidence_id: string;
    stage_key: string;
    requirement_id: string;
    kind: string;
    status: string;
    summary: string;
    observed_at: string;
  }>;
  approvals: Array<{
    approval_id: string;
    stage_key: string;
    gate_key: string;
    status: string;
    approved_by: string;
    approved_at: string;
    stale_reason: string | null;
  }>;
  consultation_candidates: UnifiedConsultationCandidate[];
  consultation_candidate_dispositions: UnifiedConsultationCandidateDisposition[];
  attention: UnifiedAttentionItem[];
  required_human_actions: Array<{
    action_id: string;
    kind: "attention" | "plan_approval" | "candidate_disposition";
    title: string;
    source_id: string;
  }>;
  audit_events: Array<{
    event_id: string;
    source: "task" | "control_plane";
    event_type: string;
    actor: string;
    payload_truncated: boolean;
    created_at: string;
  }>;
  budget: {
    token_allocated: number;
    token_reserved: number;
    token_settled: number | null;
    token_measurement: string;
    token_remaining: number | null;
    cost_allocated_usd: number | null;
    cost_reserved_usd: number | null;
    cost_settled_usd: number | null;
    cost_measurement: string;
    cost_remaining_usd: number | null;
  };
  next_safe_action: {
    value: string | null;
    source_gate_key: string | null;
    unavailable_reason: string | null;
  };
  compatibility_next_action: string;
  collection_totals: Record<string, number>;
}

export type AttentionResponseAction = "answer" | "approve" | "reject";
export type CandidateDispositionAction = "adopt" | "reject";

export interface UnifiedConsultationCandidate {
  schema_version: "1.0";
  candidate_id: string;
  consultation_id: string;
  operation_key: string;
  project_id: string;
  task_id: string;
  plan_id: string;
  plan_version_observed: number;
  inventory_id: string;
  inventory_sha256: string;
  stage_key: string;
  role: string;
  runtime: string;
  title: string;
  decision_key: string;
  decision_value: string;
  analysis: string;
  source_refs: string[];
  registered_by: string;
  advisory_authority: false;
  formal_artifact: false;
  created_at: string;
  content_sha256: string;
}

export interface UnifiedConsultationCandidateDisposition {
  schema_version: "1.0";
  disposition_id: string;
  operation_key: string;
  candidate_id: string;
  candidate_sha256: string;
  project_id: string;
  task_id: string;
  plan_id: string;
  stage_key: string;
  action: "adopted" | "rejected";
  plan_version_before: number;
  plan_version_after: number;
  claim_invalidated: boolean;
  decision_id: string | null;
  decision_sha256: string | null;
  decision_version: number | null;
  actor: string;
  reason: string;
  created_at: string;
  content_sha256: string;
}

export interface UnifiedAttentionItem {
  item_id: string;
  project_id: string;
  task_id: string;
  run_id: string | null;
  kind: "question" | "approval" | "blocker";
  state: "open" | "responded" | "cancelled" | "expired";
  urgency: "low" | "normal" | "high" | "critical";
  title: string;
  body: string;
  options: string[];
  context: Record<string, unknown>;
  requester: string;
  assignee: string | null;
  response: string | null;
  response_action: AttentionResponseAction | null;
  responded_by: string | null;
  cancellation_reason: string | null;
  version: number;
  expires_at: string | null;
  created_at: string;
  responded_at: string | null;
  updated_at: string;
}

export interface ControlPlaneAttentionResponseReceipt {
  schema_version: "1.0";
  operation_key: string;
  attention: UnifiedAttentionItem;
  response_effect:
    | "local_recorded"
    | "capture_only_recorded"
    | "delivery_ready";
  task_state_mutated: false;
  formal_approval_created: false;
}

export interface ControlPlanePlanApprovalReceipt {
  schema_version: "1.0";
  operation_key: string;
  task: {
    task_id: string;
    project_id: string;
    status: AuthoritativeTaskState;
    version: number;
    created_at: string;
    updated_at: string;
  };
  plan: UnifiedTaskProjection["plan"] & {
    approved_at: string | null;
    approved_by: string | null;
  };
  previous_task_status: AuthoritativeTaskState;
  previous_plan_state: string;
  task_completed: true;
  formal_approval_created: false;
  methodology_completion_approval_created: false;
  replayed: boolean;
}

export interface ControlPlaneCandidateDispositionReceipt {
  schema_version: "1.0";
  operation_key: string;
  disposition: UnifiedConsultationCandidateDisposition;
  candidate_authority: false;
  task_decision_bound: boolean;
  plan_version_changed: boolean;
  task_state_mutated: false;
  stage_state_mutated: false;
  gate_state_mutated: false;
  formal_approval_created: false;
  runtime_called: false;
  replayed: boolean;
}

export interface UnifiedTaskIndexItem {
  task_id: string;
  project_id: string;
  title: string;
  description: string;
  kind: string;
  risk: TaskManifest["risk"];
  priority: number;
  task_state: AuthoritativeTaskState;
  task_state_source: "control_plane";
  task_state_version: number;
  compatibility_state: TaskManifest["state"];
  plan_id: string;
  plan_state: string;
  methodology_id: string;
  methodology_version: string;
  provisional: boolean;
  task_updated_at: string;
  plan_updated_at: string;
}

export interface UnifiedTaskIndexPage {
  schema_version: "1.0";
  snapshot_at: string;
  project_id: string;
  tasks: UnifiedTaskIndexItem[];
  page: { limit: number; offset: number; total: number };
}

export async function getControlPlaneTaskIndex(
  projectId: string,
  bearerToken: string,
  signal?: AbortSignal,
): Promise<UnifiedTaskIndexPage> {
  const response = await fetch(
    `${getApiBase()}${controlPlaneTaskIndexPath(projectId)}`,
    { signal, headers: { Authorization: `Bearer ${bearerToken}` } },
  );
  if (!response.ok) throw await apiError(response);
  return response.json() as Promise<UnifiedTaskIndexPage>;
}

export async function getUnifiedTaskProjection(
  projectId: string,
  taskId: string,
  bearerToken: string,
  signal?: AbortSignal,
): Promise<UnifiedTaskProjection> {
  const path = [
    "/api/control-plane/projects",
    encodeURIComponent(projectId),
    "tasks",
    encodeURIComponent(taskId),
    "unified-projection",
  ].join("/");
  const response = await fetch(`${getApiBase()}${path}?limit=200&offset=0`, {
    signal,
    headers: { Authorization: `Bearer ${bearerToken}` },
  });
  if (!response.ok) throw await apiError(response);
  return response.json() as Promise<UnifiedTaskProjection>;
}

export async function respondToControlPlaneAttention(
  projectId: string,
  taskId: string,
  itemId: string,
  bearerToken: string,
  input: {
    action: AttentionResponseAction;
    response: string;
    expected_version: number;
    operation_key: string;
  },
  signal?: AbortSignal,
): Promise<ControlPlaneAttentionResponseReceipt> {
  const response = await fetch(
    `${getApiBase()}${controlPlaneAttentionResponsePath(projectId, taskId, itemId)}`,
    {
      method: "POST",
      signal,
      headers: {
        Authorization: `Bearer ${bearerToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(input),
    },
  );
  if (!response.ok) throw await apiError(response);
  return response.json() as Promise<ControlPlaneAttentionResponseReceipt>;
}

export async function approveControlPlanePlan(
  projectId: string,
  taskId: string,
  bearerToken: string,
  input: {
    reason: string;
    expected_task_version: number;
    expected_plan_version: number;
    operation_key: string;
  },
  signal?: AbortSignal,
): Promise<ControlPlanePlanApprovalReceipt> {
  const response = await fetch(
    `${getApiBase()}${controlPlanePlanApprovalPath(projectId, taskId)}`,
    {
      method: "POST",
      signal,
      headers: {
        Authorization: `Bearer ${bearerToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(input),
    },
  );
  if (!response.ok) throw await apiError(response);
  return response.json() as Promise<ControlPlanePlanApprovalReceipt>;
}

export async function disposeControlPlaneConsultationCandidate(
  projectId: string,
  taskId: string,
  candidateId: string,
  bearerToken: string,
  input: {
    action: CandidateDispositionAction;
    reason: string;
    expected_candidate_sha256: string;
    expected_plan_version: number;
    operation_key: string;
  },
  signal?: AbortSignal,
): Promise<ControlPlaneCandidateDispositionReceipt> {
  const response = await fetch(
    `${getApiBase()}${controlPlaneCandidateDispositionPath(
      projectId,
      taskId,
      candidateId,
    )}`,
    {
      method: "POST",
      signal,
      headers: {
        Authorization: `Bearer ${bearerToken}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify(input),
    },
  );
  if (!response.ok) throw await apiError(response);
  return response.json() as Promise<ControlPlaneCandidateDispositionReceipt>;
}

async function apiError(response: Response): Promise<ApiError> {
  let message = `Request failed (${response.status})`;
  try {
    const payload = (await response.json()) as { detail?: unknown };
    if (typeof payload.detail === "string") message = payload.detail;
  } catch {
    // Preserve the status-based message for non-JSON failures.
  }
  return new ApiError(response.status, message);
}

import { getApiBase } from "@/lib/api";
import { ApiError, type TaskManifest } from "@/lib/control-plane";

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
    methodology_id: string;
    methodology_version: string;
    provisional: boolean;
    state: string;
    current_stage_key: string | null;
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
  attention: Array<{
    item_id: string;
    kind: string;
    state: string;
    urgency: string;
    title: string;
    body: string;
    created_at: string;
  }>;
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
  const response = await fetch(`${getApiBase()}${path}`, {
    signal,
    headers: { Authorization: `Bearer ${bearerToken}` },
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const payload = (await response.json()) as { detail?: unknown };
      if (typeof payload.detail === "string") message = payload.detail;
    } catch {
      // Preserve the status-based message for non-JSON failures.
    }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<UnifiedTaskProjection>;
}

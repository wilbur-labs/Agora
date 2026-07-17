import { getApiBase } from "@/lib/api";
import { ApiError, type TaskManifest, type TaskRisk } from "@/lib/control-plane";

export type PlanState = "active" | "blocked" | "awaiting_approval" | "ready_for_implementation";
export type StageState = "pending" | "running" | "passed" | "blocked";
export type RunState = "running" | "passed" | "blocked" | "failed" | "interrupted";
export type Measurement = "exact" | "estimated" | "unavailable";

export interface OrchestrationPlan {
  plan_id: string; task_id: string; project_id: string; methodology_id: string;
  methodology_version: string; methodology_sha256: string; provisional: boolean;
  state: PlanState; total_token_budget: number; total_cost_budget_usd: number | null;
  current_stage_key: string | null; version: number; created_at: string; updated_at: string;
  approved_at: string | null; approved_by: string | null;
}

export interface OrchestrationStage {
  stage_id: string; plan_id: string; stage_key: string; sequence: number; title: string;
  role: string; adapter: string; state: StageState; token_budget: number;
  cost_budget_usd: number | null; attempt_count: number; latest_run_id: string | null;
  semantic_summary: string | null; blockers: string[]; updated_at: string;
}

export interface OrchestrationRun {
  run_id: string; plan_id: string; task_id: string; stage_key: string; adapter: string;
  state: RunState; operation_key: string; prompt_sha256: string; pid: number | null;
  exit_code: number | null; timed_out: boolean; output: string; error_message: string | null;
  semantic_status: "pass" | "needs_work" | "blocked" | null;
  semantic_summary: string | null; findings: string[]; token_reserved: number;
  token_used: number | null; token_measurement: Measurement; cost_reserved_usd: number | null;
  cost_used_usd: number | null; cost_measurement: Measurement; attempt: number;
  started_at: string; finished_at: string | null;
}

export interface UsageLedgerEntry {
  entry_id: string; task_id: string; plan_id: string; stage_key: string; run_id: string;
  entry_type: "reservation" | "settlement"; tokens: number | null;
  token_measurement: Measurement; cost_usd: number | null; cost_measurement: Measurement;
  adapter: string; created_at: string;
}

export interface TaskOrchestrationStatus {
  plan: OrchestrationPlan; stages: OrchestrationStage[]; runs: OrchestrationRun[];
  usage: UsageLedgerEntry[]; tokens_reserved: number; tokens_used: number;
  tokens_remaining: number; cost_used_usd: number | null; cost_measurement: Measurement;
  next_safe_action: string;
}

export interface CreateOrchestratedTaskInput {
  project_id: string; title: string; description?: string; risk?: TaskRisk;
  total_token_budget?: number; total_cost_budget_usd?: number | null;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${getApiBase()}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") message = payload.detail;
      else if (payload.detail) message = JSON.stringify(payload.detail);
    } catch { /* retain status fallback */ }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}

export function createOrchestratedTask(input: CreateOrchestratedTaskInput): Promise<TaskManifest> {
  return request("/api/orchestrations", { method: "POST", body: JSON.stringify(input) });
}

export function getOrchestration(taskId: string, signal?: AbortSignal): Promise<TaskOrchestrationStatus> {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/orchestration`, { signal });
}

export function attachOrchestration(taskId: string, totalTokenBudget = 30_000): Promise<TaskOrchestrationStatus> {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/orchestration`, {
    method: "POST", body: JSON.stringify({ total_token_budget: totalTokenBudget }),
  });
}

export function runNextStage(taskId: string): Promise<OrchestrationRun> {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/orchestration/next`, { method: "POST" });
}

export function resumeOrchestration(taskId: string): Promise<TaskOrchestrationStatus> {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/orchestration/resume`, { method: "POST" });
}

export function retryStage(taskId: string, stageKey: string): Promise<TaskOrchestrationStatus> {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/orchestration/stages/${encodeURIComponent(stageKey)}/retry`, { method: "POST" });
}

export function approveOrchestration(taskId: string, reason: string): Promise<TaskOrchestrationStatus> {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/orchestration/approve`, {
    method: "POST", body: JSON.stringify({ actor: "user", reason }),
  });
}

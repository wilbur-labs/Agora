import { getApiBase } from "@/lib/api";
import { ApiError } from "@/lib/control-plane";

export type WorkflowState = "draft" | "active" | "completed" | "failed" | "cancelled";
export type WorkflowStepState = "pending" | "ready" | "running" | "succeeded" | "failed" | "cancelled";

export interface WorkflowStep {
  step_id: string; workflow_id: string; key: string; title: string; project_id: string;
  task_id: string | null; adapter: string; prompt: string; depends_on: string[];
  state: WorkflowStepState; version: number; created_at: string; updated_at: string;
  run_id: string | null; dispatch_token: string | null; dispatch_error: string | null;
}

export interface WorkflowManifest {
  workflow_id: string; title: string; description: string; state: WorkflowState;
  steps: WorkflowStep[]; metadata: Record<string, unknown>; version: number;
  created_by: string; created_at: string; updated_at: string;
  auto_dispatch: boolean; max_concurrent_runs: number;
}

export interface WorkflowSummary {
  workflow_id: string; title: string; state: WorkflowState; step_count: number;
  ready_count: number; version: number; created_at: string; updated_at: string;
  auto_dispatch: boolean; max_concurrent_runs: number;
}

export interface WorkflowDispatchResult {
  workflow_id: string; dispatched_run_ids: string[];
  blockers: Array<{ step_id: string; reason: string }>;
}

export interface CreateWorkflowInput {
  title: string;
  description?: string;
  steps: Array<{
    key: string; title: string; project_id: string; task_id: string;
    adapter: string; prompt: string; depends_on: string[];
  }>;
  created_by?: string;
  auto_dispatch?: boolean;
  max_concurrent_runs?: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${getApiBase()}${path}`, {
    ...init, headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try { const payload = await response.json(); if (typeof payload.detail === "string") message = payload.detail; }
    catch { /* retain status fallback */ }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}

export function listWorkflows(signal?: AbortSignal): Promise<WorkflowSummary[]> {
  return request("/api/workflows?limit=200", { signal });
}

export function createWorkflow(input: CreateWorkflowInput): Promise<WorkflowManifest> {
  return request("/api/workflows", { method: "POST", body: JSON.stringify(input) });
}

export function getWorkflow(workflowId: string, signal?: AbortSignal): Promise<WorkflowManifest> {
  return request(`/api/workflows/${encodeURIComponent(workflowId)}`, { signal });
}

export function activateWorkflow(workflow: WorkflowManifest): Promise<WorkflowManifest> {
  return request(`/api/workflows/${encodeURIComponent(workflow.workflow_id)}/activate`, {
    method: "POST", body: JSON.stringify({ expected_version: workflow.version, actor: "user" }),
  });
}

export function dispatchWorkflow(workflowId: string): Promise<WorkflowDispatchResult> {
  return request(`/api/workflows/${encodeURIComponent(workflowId)}/dispatch`, { method: "POST" });
}

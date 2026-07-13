import { getApiBase } from "@/lib/api";

export type TaskState =
  | "backlog"
  | "requirements"
  | "design"
  | "planned"
  | "running"
  | "blocked"
  | "review"
  | "verified"
  | "done"
  | "failed"
  | "cancelled";

export type TaskRisk = "low" | "medium" | "high" | "critical";

export interface TaskManifest {
  task_id: string;
  project_id: string;
  title: string;
  description: string;
  kind: string;
  state: TaskState;
  risk: TaskRisk;
  priority: number;
  primary_agent: string | null;
  reviewers: string[];
  acceptance: string[];
  budget: { max_cost_usd: number | null; max_minutes: number | null };
  metadata: Record<string, unknown>;
  version: number;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface CreateTaskInput {
  project_id: string;
  title: string;
  description?: string;
  kind?: string;
  risk?: TaskRisk;
  priority?: number;
  primary_agent?: string | null;
  reviewers?: string[];
  acceptance?: string[];
  created_by?: string;
}

export interface RequirementItem {
  requirement_id: string;
  statement: string;
  rationale?: string | null;
}

export interface AcceptanceScenario {
  scenario_id: string;
  requirement_ids: string[];
  given: string;
  when: string;
  then: string;
}

export interface RequirementSpecInput {
  title: string;
  summary?: string;
  functional?: RequirementItem[];
  non_functional?: RequirementItem[];
  constraints?: string[];
  acceptance_scenarios?: AcceptanceScenario[];
  out_of_scope?: string[];
  glossary?: Record<string, string>;
  assumptions?: string[];
  open_questions?: Array<{ question_id: string; question: string; resolution?: string | null }>;
  links?: Array<{
    requirement_id: string;
    target_type: "design" | "task" | "test";
    target_id: string;
    label?: string | null;
  }>;
  created_by?: string;
}

export interface RequirementSpec extends Required<Omit<RequirementSpecInput, "created_by">> {
  spec_id: string;
  task_id: string;
  version: number;
  revision: number;
  state: "draft" | "approved" | "superseded" | "rejected";
  created_by: string;
  approved_by: string | null;
  approval_reason: string | null;
  rejected_by: string | null;
  rejection_reason: string | null;
  created_at: string;
  updated_at: string;
}

export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
    this.name = "ApiError";
  }
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
    } catch {
      // Keep the status-based fallback for non-JSON responses.
    }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}

export function listTasks(): Promise<TaskManifest[]> {
  return request("/api/tasks");
}

export function createTask(input: CreateTaskInput): Promise<TaskManifest> {
  return request("/api/tasks", { method: "POST", body: JSON.stringify(input) });
}

export function transitionTask(task: TaskManifest, targetState: TaskState): Promise<TaskManifest> {
  return request(`/api/tasks/${encodeURIComponent(task.task_id)}/state`, {
    method: "PATCH",
    body: JSON.stringify({
      target_state: targetState,
      actor: "user",
      expected_version: task.version,
    }),
  });
}

export function listSpecs(taskId: string): Promise<RequirementSpec[]> {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/specs`);
}

export function createSpec(taskId: string, input: RequirementSpecInput): Promise<RequirementSpec> {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/specs`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function approveSpec(spec: RequirementSpec, reason?: string): Promise<RequirementSpec> {
  return request(`/api/specs/${encodeURIComponent(spec.spec_id)}/approve`, {
    method: "POST",
    body: JSON.stringify({ actor: "user", expected_revision: spec.revision, reason: reason || null }),
  });
}

export function rejectSpec(spec: RequirementSpec, reason: string): Promise<RequirementSpec> {
  return request(`/api/specs/${encodeURIComponent(spec.spec_id)}/reject`, {
    method: "POST",
    body: JSON.stringify({ actor: "user", expected_revision: spec.revision, reason }),
  });
}

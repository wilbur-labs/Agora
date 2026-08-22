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
  archived_at: string | null;
  clarifications: TaskClarifications | null;
}

export interface ClarifyQuestion {
  id: string;
  question: string;
  why?: string;
  suggestions?: string[];
}

export interface TaskClarifications {
  status?: string;
  questions: ClarifyQuestion[];
  answers?: Record<string, string>;
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

export type AttentionKind = "question" | "approval" | "blocker";

export interface AttentionItem {
  item_id: string;
  project_id: string;
  task_id: string;
  kind: AttentionKind;
  state: string;
  urgency: string;
  title: string;
}

export function listOpenAttention(): Promise<AttentionItem[]> {
  return request("/api/attention?state=open&limit=500");
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

export interface HandoffResult {
  task: TaskManifest;
  run_id: string | null;
  workspace: string | null;
  commit: string | null;
  committed: boolean;
  files: { path: string; status: string; size: number | null }[];
  note: string | null;
}

// Box 8: complete a verified task by committing the worker's deliverable in its
// isolated workspace repo, binding the commit to the task, and advancing to done.
export function handoffTask(task: TaskManifest): Promise<HandoffResult> {
  return request(`/api/tasks/${encodeURIComponent(task.task_id)}/handoff?actor=user`, {
    method: "POST",
  });
}

// Display-only board archive: hide a task from the active board without
// changing its lifecycle state (avoids compat/authoritative divergence).
// Version-checked and idempotent on the server.
export function archiveTask(task: TaskManifest): Promise<TaskManifest> {
  const params = new URLSearchParams({ expected_version: String(task.version) });
  return request(`/api/tasks/${encodeURIComponent(task.task_id)}/archive?${params.toString()}`, {
    method: "POST",
  });
}

export function unarchiveTask(task: TaskManifest): Promise<TaskManifest> {
  const params = new URLSearchParams({ expected_version: String(task.version) });
  return request(`/api/tasks/${encodeURIComponent(task.task_id)}/unarchive?${params.toString()}`, {
    method: "POST",
  });
}

// Ask the agent to propose requirement-clarifying questions (slow — an agent
// call runs server-side). Returns the task with `clarifications.questions`.
export function generateClarifications(taskId: string): Promise<TaskManifest> {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/clarifications/generate`, { method: "POST" });
}

export function saveClarificationAnswers(taskId: string, answers: Record<string, string>): Promise<TaskManifest> {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/clarifications/answers`, {
    method: "POST",
    body: JSON.stringify({ answers }),
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

// --- Settings (read-only observations) ---

export interface RuntimeCapability {
  adapter: string;
  provider: string;
  installation_status: "installed" | "not_found" | "uninspectable";
  version: string | null;
  version_status: string;
  declared_capabilities: string[];
  execution_mode: string | null;
  attention_mode: string | null;
  supports_tool_approval: boolean | null;
  detail: string | null;
  authenticated: boolean | null;
  auth_method: string | null;
  auth_check_status: "checked" | "not_configured" | "probe_failed" | "timed_out";
  token_capture: "supported" | "unsupported";
  tokens_used: number;
}

export interface RuntimeCapabilities {
  platform: string;
  collected_at: string;
  adapters: RuntimeCapability[];
}

export interface WorkspaceInfo {
  adapter: string;
  path: string;
  exists: boolean;
  writable: boolean;
}

export interface ProjectSettings {
  project_id: string;
  name: string;
  root: string;
  root_exists: boolean;
  root_is_git: boolean;
  worker_agent: string;
  reviewer_agent: string;
  workspaces: WorkspaceInfo[];
}

export interface SettingsOverview {
  projects: { current: string; projects_base: string; items: ProjectSettings[] };
  execution: {
    max_concurrent_global: number;
    max_concurrent_per_project: number;
    network_mode: string;
    default_timeout_seconds: number;
  };
  runs: {
    total: number;
    truncated: boolean;
    by_state: Record<string, number>;
    by_adapter: Record<string, number>;
    running: number;
    queued: number;
    tokens_by_adapter: Record<string, number>;
    total_tokens: number;
    token_usage_recorded: boolean;
  };
  control_plane: {
    auth_configured: boolean;
    principals: number;
    principals_with_secret: number;
  };
}

export function getRuntimeCapabilities(): Promise<RuntimeCapabilities> {
  return request("/api/runtime-capabilities");
}

export function getSettingsOverview(): Promise<SettingsOverview> {
  return request("/api/settings/overview");
}

// --- Projects (registry) ---

export type AdapterName = "codex" | "claude" | "kiro";

export interface ProjectListItem {
  project_id: string;
  name: string;
  root: string;
  worker_agent: AdapterName;
  reviewer_agent: AdapterName;
}

export interface ProjectList {
  current: string;
  projects_base: string;
  items: ProjectListItem[];
}

export function listProjects(): Promise<ProjectList> {
  return request("/api/projects");
}

export function createProject(
  projectId: string,
  name?: string,
  agents?: { worker_agent: AdapterName; reviewer_agent: AdapterName },
): Promise<ProjectListItem> {
  return request("/api/projects", {
    method: "POST",
    body: JSON.stringify({ project_id: projectId, name: name || null, ...agents }),
  });
}

export function setProjectAgents(projectId: string, worker_agent: AdapterName, reviewer_agent: AdapterName): Promise<ProjectListItem> {
  return request(`/api/projects/${encodeURIComponent(projectId)}/agents`, {
    method: "PUT",
    body: JSON.stringify({ worker_agent, reviewer_agent }),
  });
}

export function setProjectsBase(path: string): Promise<{ projects_base: string }> {
  return request("/api/settings/projects-base", {
    method: "PUT",
    body: JSON.stringify({ path }),
  });
}

// Opens a native OS folder picker on the server host (local desktop only).
export function pickProjectsFolder(): Promise<{ path: string | null; available: boolean }> {
  return request("/api/settings/pick-folder", { method: "POST" });
}

import { getApiBase } from "@/lib/api";
import type { ProgressSnapshotLike } from "@/lib/progress-snapshot";

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
  constructor(
    public status: number,
    message: string,
    /**
     * The control plane's machine-readable discriminator for this failure, when
     * it sent one. Several distinct refusals share a status code (409 covers a
     * stale version, a lost race, an already-running run, and an open gate), and
     * their messages are prose — so a caller that has to say different things
     * needs a code rather than a substring match. null means none was sent.
     */
    public code: string | null = null,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

/**
 * Build an ApiError from a failed response.
 *
 * FastAPI's `detail` is usually a string. Where one status has to carry several
 * meanings the control plane sends an object instead — `{code, message, …}` — and
 * that shape is unwrapped here so the human still sees prose and the caller gets
 * the code. An unrecognised object is stringified rather than dropped, because a
 * detail we cannot parse is still the only thing the server said.
 *
 * Shared by every `request` helper in this app: two copies of this parsing would
 * be two chances for one of them to lose the code.
 */
export async function apiErrorFrom(response: Response): Promise<ApiError> {
  let message = `Request failed (${response.status})`;
  let code: string | null = null;
  try {
    const payload = await response.json();
    const detail = payload?.detail;
    if (typeof detail === "string") {
      message = detail;
    } else if (detail && typeof detail === "object") {
      if (typeof detail.code === "string") code = detail.code;
      message = typeof detail.message === "string" ? detail.message : JSON.stringify(detail);
    }
  } catch {
    // Keep the status-based fallback for non-JSON responses.
  }
  return new ApiError(response.status, message, code);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${getApiBase()}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    throw await apiErrorFrom(response);
  }
  return response.json() as Promise<T>;
}

// The task list is one page too, and the endpoint caps `limit` at 500. Unlike the
// attention page it returns a bare array — no `truncated`, no total — so a caller
// that wants to know whether it saw everything has only the page's own length to go
// on: a page filled to the cap is indistinguishable from "there is more". Exported
// so the reading side compares against the same number it asked for rather than a
// literal of its own that can drift from this default.
export const TASK_PAGE_LIMIT = 500;

export function listTasks(limit: number = TASK_PAGE_LIMIT): Promise<TaskManifest[]> {
  return request(`/api/tasks?limit=${limit}`);
}

export type AttentionKind = "question" | "approval" | "blocker";

export interface AttentionItem {
  item_id: string;
  project_id: string;
  task_id: string;
  // The run this item was raised against, when it was raised against one. Carried
  // because it is load-bearing, not decorative: the review path scopes its approval
  // and its blocker to the *worker run* they gate, and that pairing — with
  // `requester` below — is the fact the control plane releases a run's review
  // obligation on (`_has_review_gate_item`). A UI that drops it can only guess.
  run_id: string | null;
  kind: AttentionKind;
  state: string;
  urgency: string;
  title: string;
  // Who raised it. `agora-review` is a reserved requester the API refuses in public
  // input, which is what makes it readable as Agora's own review gate rather than as
  // any item that happens to be shaped like one.
  requester: string;
}

// The server caps this endpoint at 500 items per page (`limit` has le=500), so
// this read cannot be widened — it can only be reported honestly.
export const OPEN_ATTENTION_PAGE_LIMIT = 500;

/**
 * One page of the global open-attention list, with whether it may have been cut
 * off. A full page is indistinguishable from "there is more", so `truncated` is
 * true at the cap: callers must then treat "this task is absent from `items`" as
 * unknown, never as "this task has no open gate".
 */
export interface OpenAttentionPage {
  items: AttentionItem[];
  truncated: boolean;
}

export async function listOpenAttention(): Promise<OpenAttentionPage> {
  const items = await request<AttentionItem[]>(
    `/api/attention?state=open&limit=${OPEN_ATTENTION_PAGE_LIMIT}`,
  );
  return { items, truncated: items.length >= OPEN_ATTENTION_PAGE_LIMIT };
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

export interface TaskEvent {
  event_id: string;
  task_id: string;
  event_type: string;
  actor: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export function listTaskEvents(taskId: string, signal?: AbortSignal): Promise<TaskEvent[]> {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/events`, { signal });
}

// Append a human note / direction-correction to a task's event log. The control
// plane reserves task_created/state_changed and the spec./cr./run./attention.
// prefixes, so use an unreserved type (e.g. "user_message"). This records the
// message in the timeline without dispatching an agent (that's a later phase).
export function appendTaskEvent(
  taskId: string,
  eventType: string,
  payload: Record<string, unknown> = {},
  actor = "user",
): Promise<TaskEvent> {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/events`, {
    method: "POST",
    body: JSON.stringify({ event_type: eventType, payload, actor }),
  });
}

// The delivery bound to a completed task by handoff (the latest handoff.completed
// event's payload), if any.
export interface Delivery {
  commit: string | null;
  committed: boolean;
  fileCount: number;
  files: string[];
}

// What happened on this task's last active day — the "where did I leave off"
// recap. `kind` is a stable slug the UI localizes; `summary` is the server's
// English fallback for API consumers without the UI, not for display here.
export interface RecapItem {
  kind: string;
  summary: string;
  adapter?: string;
  // Files a run delivered. null means the count is unknown (no deliverable
  // snapshot), which is different from 0; `files_at_least` marks it as a floor
  // because the snapshot itself was truncated.
  files?: number | null;
  files_at_least?: boolean;
  state?: string;
  count?: number;
}

export interface TaskRecap {
  // null when nothing has ever happened to this task.
  day: string | null;
  items: RecapItem[];
  // Items beyond what the recap shows, so a busy day is not silently trimmed.
  more: number;
  // Whole days from `day` to today, in the viewer's zone. Drives the wording:
  // "today" / "yesterday" / "N days ago" / the date itself.
  days_ago: number | null;
  // The day held more than the endpoint read.
  partial?: boolean;
  // The display zone's UTC offset in minutes. Informational; use next_rollover
  // to schedule, since an offset alone cannot locate the next midnight across a
  // daylight-saving change.
  zone_offset_minutes?: number;
  // When the display zone's calendar day next changes, as a UTC ISO timestamp.
  // Computed server-side: the browser's zone is not a substitute (the display
  // zone is configurable and the backend may run elsewhere), and the current
  // offset is not enough on a DST boundary.
  next_rollover?: string;
}

export function fetchTaskRecap(taskId: string, signal?: AbortSignal): Promise<TaskRecap> {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/recap`, { signal });
}

export async function fetchDelivery(taskId: string, signal?: AbortSignal): Promise<Delivery | null> {
  const events = await listTaskEvents(taskId, signal);
  const handoff = [...events].reverse().find((e) => e.event_type === "handoff.completed");
  if (!handoff) return null;
  const p = handoff.payload;
  return {
    commit: (p.commit as string | null) ?? null,
    committed: Boolean(p.committed),
    fileCount: typeof p.file_count === "number" ? p.file_count : ((p.files as string[] | undefined)?.length ?? 0),
    files: Array.isArray(p.files) ? (p.files as string[]) : [],
  };
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
  /**
   * True when the workspace directory was observed, false only for a definite
   * "no such directory", null when the probe could not tell the two apart (a
   * denying ACL, an I/O error). An unreadable workspace is not a missing one, and
   * the two call for opposite responses, so null must render as unknown.
   */
  exists: boolean | null;
  /** Null whenever `exists` is: writability of a directory that was never observed. */
  writable: boolean | null;
}

export interface ProjectSettings {
  project_id: string;
  name: string;
  root: string;
  /** True / false / null — see `WorkspaceInfo.exists`. Null is unknown, not missing. */
  root_exists: boolean | null;
  /** True / false / null. Null means the `.git` entry could not be observed at all. */
  root_is_git: boolean | null;
  worker_agent: string;
  reviewer_agent: string;
  workspaces: WorkspaceInfo[];
}

export interface ProjectImportStatus {
  /** The YAML import source. Projects themselves live in the control-plane database. */
  source: string;
  /**
   * What the last open of the source established: true if it was read, false only
   * for a definite "no such file", null when the attempt could not tell the two
   * apart. Not an existence probe — a source that cannot be read is not absent.
   */
  source_present: boolean | null;
  /** Set when the source was reached but could not be read or understood. Never a shorter project list. */
  error: string | null;
  last_import: {
    source: string;
    imported: string[];
    updated: string[];
    unchanged: string[];
    conflicts: { project_id: string; fields: string[] }[];
    current_project_id: string | null;
    projects_base: string | null;
  } | null;
}

export interface SettingsOverview {
  projects: {
    current: string;
    projects_base: string;
    items: ProjectSettings[];
    import_status: ProjectImportStatus;
  };
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
  import_status: ProjectImportStatus;
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

// --- Project charter + dashboard (1.2 control-plane cockpit) ---

export interface Charter {
  goal: string;
  non_goals: string[];
  success_criteria: string[];
}

export type NextActionOwner = "human" | "agent" | "none";

// One deterministic next step for a task (from the Next Action engine).
export interface NextAction {
  owner: NextActionOwner;
  action: string; // a stable slug localized via te("nextAction", action)
  attention: boolean;
  summary: string; // English fallback; UI prefers the localized slug
}

// The project-wide rollup adds which task it points at and header counts.
export interface ProjectNextAction extends NextAction {
  task_id: string | null;
  title: string | null;
  attention_count: number;
  pending_count: number;
  partial: boolean; // the task page was truncated; non-attention rollup may undercount
}

export type CriterionStatus = "pending" | "produced" | "reviewed" | "verified";

export interface DashboardCriterion {
  id: string;
  description: string;
  weight: number;
  status: CriterionStatus;
}

export interface DashboardTask {
  task_id: string;
  title: string;
  state: TaskState;
  status: CriterionStatus;
  has_spec: boolean;
  criteria: DashboardCriterion[];
  produced_weight: number;
  verified_weight: number;
  total_weight: number;
  next_action: NextAction;
}

export interface DashboardMetrics {
  progress_pct: number;
  verified_pct: number;
  total_weight: number;
  produced_weight: number;
  verified_weight: number;
  health: "normal" | "attention";
}

export interface ProjectDashboard {
  project_id: string;
  name: string;
  charter: Charter;
  metrics: DashboardMetrics;
  // The progress bar's source: the project's RESUME progress snapshot, counted (see
  // lib/progress-snapshot.ts). Optional in the *type* on purpose — a server older
  // than this UI sends no such field, and that has to be distinguishable from a
  // project that keeps no snapshot, which is why `progressBarOf` treats `undefined`
  // as unknown rather than as an absence.
  progress_snapshot?: ProgressSnapshotLike | null;
  next_action: ProjectNextAction;
  tasks: DashboardTask[];
  truncated: boolean;
}

// The project cockpit projection: charter, progress vs verified completion, the
// one deterministic next action, and per-task criteria/next-action.
export function fetchProjectDashboard(projectId: string, signal?: AbortSignal): Promise<ProjectDashboard> {
  return request(`/api/projects/${encodeURIComponent(projectId)}/dashboard`, { signal });
}

// Human-authored charter (goal / non-goals / success criteria). No agent path
// writes this — it is the human's definition of what "done" means.
export function setProjectCharter(projectId: string, charter: Charter): Promise<ProjectListItem & { charter: Charter }> {
  return request(`/api/projects/${encodeURIComponent(projectId)}/charter`, {
    method: "PUT",
    body: JSON.stringify(charter),
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

import { getApiBase } from "@/lib/api";
import { MAX_PAGE, SERVER_PAGE_MAX, readPage } from "./paged";
import type { WorkspaceSourcePayload } from "./workspace-sources";
import type { CRLogLike } from "@/lib/cr-log";
import type { KickoffCharterLike, KickoffReadLike, KickoffTaskLike } from "@/lib/kickoff";
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

/** Agora's own /health: its version and which installation it is (CR-31). */
export async function fetchHealth(): Promise<import("./instance").AgoraHealth> {
  return request("/health");
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

/**
 * The task page a reader takes when it needs to *know* whether it saw everything: one
 * row smaller than the server's bound, so `readPage` can spend that row on the probe.
 *
 * This is not the same thing as "how many tasks a caller should ask for", and briefly
 * it was both — review caught it. Every unprobed caller (Workspace, the Runs page,
 * Requirements and its conflict refresh, the Workflow composer) takes the default
 * below, and pointing that default here quietly cost each of them a task: with exactly
 * 500 tasks the lowest-ranked one vanished from all of them, and a Requirements deep
 * link to it fell back to the first task loaded. A caller that wants completeness
 * *knowledge* pays a row for it; a caller that just wants the tasks should not.
 */
export const TASK_PAGE_LIMIT = MAX_PAGE;

/**
 * Defaults to the whole bound, not to the probed page. A caller that needs to know
 * whether more exists uses `readPage(TASK_PAGE_LIMIT, listTasks)` instead, which asks
 * for exactly this many and shows one fewer.
 */
export function listTasks(limit: number = SERVER_PAGE_MAX): Promise<TaskManifest[]> {
  return request(`/api/tasks?limit=${limit}`);
}

/**
 * One task, read now.
 *
 * Needed because a task's `version` moves underneath a page that is holding it:
 * dispatching the first run carries the task planned -> running, and `create`
 * refuses a stale `expected_task_version` (correctly — a queued run outlives the
 * state it was checked against). A conversation is a fast back-and-forth, so the
 * message box re-reads the version at send time instead of waiting for the page
 * around it to refresh between two sentences.
 */
export function getTask(taskId: string, signal?: AbortSignal): Promise<TaskManifest> {
  return request(`/api/tasks/${encodeURIComponent(taskId)}`, { signal });
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
export const OPEN_ATTENTION_PAGE_LIMIT = MAX_PAGE;

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
  // Asks for one more than it shows, so `truncated` is evidence rather than the guess
  // `items.length >= LIMIT` used to make — that guess called a complete inventory of
  // exactly LIMIT items truncated, and the rail then said there were more than it read.
  const page = await readPage(OPEN_ATTENTION_PAGE_LIMIT, (limit) =>
    request<AttentionItem[]>(`/api/attention?state=open&limit=${limit}`));
  return { items: [...page.items], truncated: page.truncated };
}

// --- workspace sources (W7) -----------------------------------------------------
//
// `probe` before `register`, deliberately: the panel shows what a path yields *before*
// anything is stored, so the person can tell "this directory holds no tasks" from
// "I could not read it" from "wrong directory" at the moment they choose it. Accepting
// a path and then rendering an empty list is the substitution this product exists to
// refuse.

export async function listWorkspaceSources(signal?: AbortSignal): Promise<WorkspaceSourcePayload[]> {
  const page = await request<{ sources: WorkspaceSourcePayload[] }>(
    "/api/workspace-sources", { signal });
  return page.sources;
}

export function probeWorkspaceSource(
  path: string, signal?: AbortSignal,
): Promise<WorkspaceSourcePayload> {
  return request("/api/workspace-sources/probe", {
    method: "POST", body: JSON.stringify({ path }), signal,
  });
}

export function registerWorkspaceSource(path: string): Promise<WorkspaceSourcePayload> {
  return request("/api/workspace-sources", {
    method: "POST", body: JSON.stringify({ path }),
  });
}

/** Forget a source. Touches no file — the tasks stay where they are. */
export function unregisterWorkspaceSource(path: string): Promise<{ was_registered: boolean }> {
  return request(`/api/workspace-sources?path=${encodeURIComponent(path)}`, {
    method: "DELETE",
  });
}

// --- Remote environments (registry, CR-4 slice 1) ---
//
// A registered SSH environment. The credential value is never carried. `credential_source`
// says where the credential lives — an env var name (`secret_ref`), a private-key file
// path (`secret_ref`), or a password in the OS keyring — and `credential_present` says
// only whether it is set: true/false, or null when this machine cannot tell (e.g. no
// keyring backend), which is not the same as "not set" (adr/0001).

/** `ssh_default`: this machine's own SSH keys / agent, like the `ssh` command (CR-24). */
export type CredentialSource = "env_var" | "key_path" | "keyring" | "ssh_default";

export interface RemoteEnvironment {
  environment_id: string;
  label: string;
  ssh_host: string;
  ssh_port: number;
  ssh_user: string;
  remote_workspace: string;
  remote_project_root: string;
  secret_ref: string;
  credential_source: CredentialSource;
  credential_present: boolean | null;
  host_key_mode: "strict" | "tofu";
  known_hosts_path: string | null;
  cleanup_worktree: boolean;
  /** The project whose root this is (one environment = one project). null only on a row
   * registered before the field existed — unusable for dispatch until edited. */
  project_id: string | null;
  created_at: string | null;
  updated_at: string | null;
}

/** The registerable fields. No credential value except a keyring `password` (write-only:
 * sent to store in the OS keyring, never returned). For key_path/env_var, `secret_ref`
 * carries the path / env-var name and `password` is null. */
export interface RemoteEnvironmentInput {
  environment_id: string;
  label: string;
  ssh_host: string;
  ssh_port: number;
  ssh_user: string;
  remote_workspace: string;
  remote_project_root: string;
  secret_ref: string;
  credential_source: CredentialSource;
  /** Only for credential_source "keyring": the password to store. null otherwise, and on
   * an update means "keep the existing keyring password". Never populated from a read. */
  password: string | null;
  host_key_mode: "strict" | "tofu";
  known_hosts_path: string | null;
  cleanup_worktree: boolean;
  project_id: string;
}

/** A host from this machine's ~/.ssh/config (CR-25) — only to pre-fill a registration. */
export interface SshConfigHost {
  alias: string;
  hostname: string | null;
  user: string | null;
  port: number | null;
  identity_file: string | null;
}

/** `status`: "absent" (no file), "unreadable" (exists, could not be read — not "none"), "ok". */
export interface SshConfigHosts {
  status: "absent" | "unreadable" | "ok";
  error: string | null;
  hosts: SshConfigHost[];
}

export function listSshConfigHosts(): Promise<SshConfigHosts> {
  return request("/api/ssh-config/hosts");
}

export function listRemoteEnvironments(): Promise<{ environments: RemoteEnvironment[] }> {
  return request("/api/remote-environments");
}

export function createRemoteEnvironment(input: RemoteEnvironmentInput): Promise<RemoteEnvironment> {
  return request("/api/remote-environments", {
    method: "POST", body: JSON.stringify(input),
  });
}

export function updateRemoteEnvironment(
  environmentId: string, input: RemoteEnvironmentInput,
): Promise<RemoteEnvironment> {
  return request(`/api/remote-environments/${encodeURIComponent(environmentId)}`, {
    method: "PUT", body: JSON.stringify(input),
  });
}

/** Forget a registered environment. Idempotent. */
export function deleteRemoteEnvironment(environmentId: string): Promise<{ was_registered: boolean }> {
  return request(`/api/remote-environments/${encodeURIComponent(environmentId)}`, {
    method: "DELETE",
  });
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
  // The run this item describes. Two different absences, and they are both real:
  // the key is missing on items that no run produced (a decision recorded that day),
  // and it is present-but-null on a run-derived item whose assembler did not supply
  // an id. Optional-and-nullable says exactly that, the way `files` below does — a
  // required `string` would let a consumer read an id that is not there, which is
  // the guess this field exists to replace.
  run_id?: string | null;
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
  /**
   * Whether this adapter's configured output format has a usage reader.
   * "unknown" is the adapter registry itself having failed to build — the
   * payload used to claim "unsupported" for every adapter in that case, which was
   * an assertion derived from a read that did not happen.
   */
  token_capture: "supported" | "unsupported" | "unknown";
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
  // Always sent (normalised server-side to the same shape, empty strings and empty
  // lists when nothing is recorded), so Today can print the goal line from the list
  // read alone without a dashboard request per project.
  charter: Charter;
}

/**
 * Today's top tier, as `GET /api/projects` reports it (docs/W9-mockup-alignment.md, D-1).
 * `is_full` is the server's verdict and the only one the UI repeats: unresolved ids
 * occupy seats on the server's count, and the fourth promotion is refused on that same
 * count, so a client that recomputed fullness from `project_ids.length` alone would
 * offer a seat the server does not have.
 */
export interface TodayFocusPayload {
  project_ids: string[];
  unresolved_ids: string[];
  limit: number;
  is_full: boolean;
}

export interface ProjectList {
  current: string;
  projects_base: string;
  import_status: ProjectImportStatus;
  items: ProjectListItem[];
  // Optional in the *type* for the reason the dashboard's snapshot and CR Log fields
  // are: a server older than this UI sends no such field, and "this server does not
  // report a focus tier" must stay distinguishable from "nothing is promoted" — the
  // second is 0/3 with a promote button, the first is neither.
  today_focus?: TodayFocusPayload;
}

export function listProjects(): Promise<ProjectList> {
  return request("/api/projects");
}

/**
 * Promote a project into today's focus tier. Idempotent. Refused with 409 when the tier
 * is full (the server does not evict — see lib/today-focus.ts `promoteRefusalOf`), 404
 * for an unregistered project. Returns the whole tier, so the caller replaces its copy
 * rather than patching it.
 */
export function promoteProjectToToday(projectId: string): Promise<TodayFocusPayload> {
  return request(`/api/projects/${encodeURIComponent(projectId)}/focus`, { method: "PUT" });
}

/** Take a project out of today's focus tier. Idempotent; accepts an id that no longer
 *  resolves, which is how a leftover seat is released. Returns the whole tier. */
export function demoteProjectFromToday(projectId: string): Promise<TodayFocusPayload> {
  return request(`/api/projects/${encodeURIComponent(projectId)}/focus`, { method: "DELETE" });
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

/** A repository a workspace task record names (CR-52), one per repository. */
export interface ImportCandidate {
  raw_path: string;
  root: string | null;
  /** `agora_tool`: Agora's own checkout or a workspace tool's directory (CR-56) — listed,
   *  never offered. */
  status: "ok" | "registered" | "not_this_machine" | "placeholder" | "missing" | "not_git" | "unreadable" | "agora_tool";
  registered_as: string | null;
  suggested_id: string | null;
  suggested_name: string | null;
  tasks: { source: string; task: string; title: string | null }[];
}

export interface ImportCandidates {
  candidates: ImportCandidate[];
  /** Task files that could not be read — listed, not skipped. */
  unreadable: { source: string; task: string; reason: string }[];
}

export function listImportCandidates(): Promise<ImportCandidates> {
  return request("/api/projects/import-candidates");
}

/** Register an existing git repository on this machine as a project, where it is. */
export function registerExistingProject(projectId: string, root: string, name?: string): Promise<ProjectListItem> {
  return request("/api/projects/register", {
    method: "POST",
    body: JSON.stringify({ project_id: projectId, root, name: name || null }),
  });
}

/**
 * Remove a project from Agora (CR-56) — the registration only; the server deletes no
 * file. 404 for an unknown project; 409, with the reason and counts in the message, when
 * it is the last project, still has tasks / runs / other rows, or is listed in the import
 * file. Returns the control plane's current project afterwards.
 */
export function removeProject(projectId: string): Promise<{ removed: string; current_project_id: string }> {
  return request(`/api/projects/${encodeURIComponent(projectId)}`, { method: "DELETE" });
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
  // The project's own CR Log, read from its repository (see lib/cr-log.ts and
  // tasks/agora/adr/0004). Optional in the *type* for the same reason as the
  // snapshot above: a server older than this UI sends no such field, and "this
  // server does not report it" must stay distinguishable from "this project keeps
  // no CR Log" — otherwise the UI states a fact about the project on the strength
  // of a fact about the server.
  cr_log?: CRLogLike | null;
  next_action: ProjectNextAction;
  tasks: DashboardTask[];
  truncated: boolean;
}

// The project's decision records. Its own request, not a dashboard field: reading
// them lists a directory and opens every record in it, and the dashboard is what a
// task switch waits for.
export interface DecisionRecordLike {
  number: number;
  filename: string;
  readable: boolean;
  title: string | null;
  status: string | null;
  /** "proposed" | "accepted" | "superseded" | "deprecated" | "rejected" | "unrecognised" */
  status_token: string;
  superseded_by: string | null;
  date: string | null;
}

export interface ProjectDecisions {
  project_id: string;
  available: boolean;
  /** "absent" | "unreadable" when unavailable. */
  reason: string | null;
  /** null — not [] — whenever `available` is false. */
  records: DecisionRecordLike[] | null;
  /** null when unavailable. Travels with `records` so a count of decisions can
   *  never be shown without the number of records that could not be opened. */
  unreadable_count: number | null;
  /** Files in the directory not named as records. Named rather than dropped, and
   *  `null` — not [] — when the directory could not be listed: whether anything is
   *  misplaced in there is then unknown, and a consumer reading `.length` off an
   *  empty array would be reading an answer nobody was able to look up. */
  unnumbered_files: string[] | null;
  source: string;
}

export function fetchProjectDecisions(projectId: string, signal?: AbortSignal): Promise<ProjectDecisions> {
  return request(`/api/projects/${encodeURIComponent(projectId)}/decisions`, { signal });
}

// The project's known defects. Its own request, parallel to decisions and for the same
// reason: each left-column resource reads on its own. The count shown from this is the
// open count, never a total that hides how many are already fixed.
export interface DefectRecordLike {
  /** The defect id as written ("D-1"); null only when the entry had none (then
   *  `readable` is false). */
  id: string | null;
  /** True when id / title / status were all present and usable. */
  readable: boolean;
  title: string | null;
  status: string | null;
  /** "open" | "fixed" | "unrecognised" */
  status_token: string;
  summary: string | null;
  milestone: string | null;
}

export interface ProjectDefects {
  project_id: string;
  available: boolean;
  /** "absent" | "unreadable" | "too_large" | "malformed" | "duplicate_key" when unavailable. */
  reason: string | null;
  /** null — not [] — whenever `available` is false. */
  records: DefectRecordLike[] | null;
  /** How many entries read positively as open. null when unavailable. The nav signal;
   *  fixed and unrecognised are not in it. */
  open_count: number | null;
  /** Entries whose status word was outside the vocabulary. Travels with the count so a
   *  consumer cannot show "N open" as complete while some entries went unclassified. */
  unrecognised_count: number | null;
  /** Entries that could not be read (missing a required field, or not a mapping). */
  unreadable_count: number | null;
  source: string;
}

export function fetchProjectDefects(projectId: string, signal?: AbortSignal): Promise<ProjectDefects> {
  return request(`/api/projects/${encodeURIComponent(projectId)}/defects`, { signal });
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

// --- goals already written in the workspaces' task records (CR-35) -------------------
export interface RecordedGoal {
  source: string;
  task: string;
  title: string | null;
  /** As the person wrote it; null when the record names this repository but states no goal. */
  goal: string | null;
  found_in: "task.yaml" | "RESUME.md" | null;
}

export interface RecordedGoals {
  project_id: string;
  goals: RecordedGoal[];
  unreadable: { source: string; task: string; reason: string }[];
}

export function listRecordedGoals(projectId: string): Promise<RecordedGoals> {
  return request(`/api/projects/${encodeURIComponent(projectId)}/recorded-goals`);
}

// --- project kickoff (W10) ------------------------------------------------------------
//
// The record lives in the project's repository (`.agora/kickoff.json`); every read
// reports it as available with a record, or unavailable with a reason (absent /
// unreadable / malformed). Only `adopt` writes to the control plane.

export interface KickoffPayload extends KickoffReadLike {
  project_id: string;
}

export function getKickoff(projectId: string, signal?: AbortSignal): Promise<KickoffPayload> {
  return request(`/api/projects/${encodeURIComponent(projectId)}/kickoff`, { signal });
}

// Every mutation names the revision of the record the caller was looking at (`null`
// for "no record"); the server refuses a stale one with 409 `kickoff_stale`. See the
// router's note on optimistic concurrency.

export function startKickoff(projectId: string, brief: string, expectedRevision: string | null): Promise<KickoffPayload> {
  return request(`/api/projects/${encodeURIComponent(projectId)}/kickoff`, {
    method: "POST",
    body: JSON.stringify({ brief, expected_revision: expectedRevision }),
  });
}

export function saveKickoffAnswers(
  projectId: string, answers: Record<string, string>, expectedRevision: string,
): Promise<KickoffPayload> {
  return request(`/api/projects/${encodeURIComponent(projectId)}/kickoff/answers`, {
    method: "POST",
    body: JSON.stringify({ answers, expected_revision: expectedRevision }),
  });
}

/** `note` is what the person said about the proposal on the table; the next draft is
 *  drawn with it. Empty when they simply asked for another one. */
export function refineKickoff(projectId: string, expectedRevision: string, note = ""): Promise<KickoffPayload> {
  return request(`/api/projects/${encodeURIComponent(projectId)}/kickoff/refine`, {
    method: "POST",
    body: JSON.stringify({ note, expected_revision: expectedRevision }),
  });
}

export function adoptKickoff(
  projectId: string,
  body: { charter: KickoffCharterLike; tasks: KickoffTaskLike[]; expected_revision: string },
): Promise<KickoffPayload> {
  return request(`/api/projects/${encodeURIComponent(projectId)}/kickoff/adopt`, {
    method: "POST",
    body: JSON.stringify(body),
  });
}

// --- the project conversation after adoption (CR-63) -----------------------------------
//
// The kickoff dialogue becomes the project's one conversation: the owner says something,
// the agent says what it is against the charter and proposes a card with a place in the
// development order. Proposing writes nothing; placing is the separate request that does.

export interface IdeaOrderEntry {
  task_id: string;
  title: string;
  state: string;
  priority: number;
}

export interface IdeaProposal {
  idea: string;
  verdict: "task" | "later" | "note";
  /** The agent's restatement, shown after "我记成：" so a wrong reading can be corrected. */
  echo: string;
  why: string;
  task: KickoffTaskLike | null;
  /** 1-based, in `order`; null unless the verdict is "task". */
  position: number | null;
  order: readonly IdeaOrderEntry[];
  /** The project has more live tasks than the order shown; "last" means last of these. */
  order_truncated: boolean;
}

export interface IdeaPlacement {
  task_id: string;
  title: string;
  state: string;
  asked_position: number;
  /** Where it actually landed, read back from the board; null when the board no longer
   *  shows it within the ceiling the order is read to. */
  landed_at: number | null;
  order: readonly IdeaOrderEntry[];
  order_truncated: boolean;
}

export function proposeFromIdea(projectId: string, idea: string): Promise<IdeaProposal> {
  return request(`/api/projects/${encodeURIComponent(projectId)}/ideas`, {
    method: "POST",
    body: JSON.stringify({ idea }),
  });
}

export function placeIdeaTask(projectId: string, task: KickoffTaskLike, position: number): Promise<IdeaPlacement> {
  return request(`/api/projects/${encodeURIComponent(projectId)}/ideas/place`, {
    method: "POST",
    body: JSON.stringify({ task, position }),
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

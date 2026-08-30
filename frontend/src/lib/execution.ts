import { getApiBase } from "@/lib/api";
import { apiErrorFrom } from "@/lib/control-plane";
// One definition of "this run has settled". It lives in run-column.ts because the
// right column's whole state machine turns on it and that module must stay
// importable by `node --test` (no React, no "@/" aliases); listing the states here
// as well would be a second copy, and the copy that drifted would decide whether a
// live run is rendered as a finished one.
import { SETTLED_RUN_STATES } from "./run-column";

export type RunState =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "timed_out"
  | "cancelled"
  | "abandoned";

export type ExecutionAdapter = "codex" | "claude" | "kiro";

export interface AdapterCapability {
  name: ExecutionAdapter;
  execution_mode: string;
  attention_mode: "bidirectional" | "capture_only";
  supports_tool_approval: boolean;
  supports_user_questions: boolean;
  detail: string;
}

export const TERMINAL_RUN_STATES: ReadonlySet<RunState> = SETTLED_RUN_STATES as ReadonlySet<RunState>;

export interface RunSummary {
  run_id: string;
  task_id: string;
  project_id: string;
  adapter: ExecutionAdapter;
  state: RunState;
  version: number;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  exit_code: number | null;
}

export interface ExecutionRun extends RunSummary {
  prompt: string;
  workspace: string;
  command: string[];
  timeout_seconds: number;
  pid: number | null;
  stdout_tail: string;
  stderr_tail: string;
  result_metadata: Record<string, unknown>;
  error_message: string | null;
  actor: string;
}

export interface CreateRunInput {
  task_id: string;
  adapter: ExecutionAdapter;
  prompt: string;
  timeout_seconds: number;
  expected_task_version: number;
  actor?: string;
  metadata?: Record<string, unknown>;
}

export interface CancelRunInput {
  expected_version: number;
  actor?: string;
  reason?: string | null;
}

export interface ListRunsParams {
  task_id?: string;
  project_id?: string;
  state?: RunState;
  /** Several states in one request. Mutually exclusive with `state`. */
  states?: readonly RunState[];
  adapter?: ExecutionAdapter;
  limit?: number;
  offset?: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${getApiBase()}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    // Shared parser: it unwraps the control plane's structured `detail` so a 409
    // that carries a gate code arrives here as a code, not as prose to sniff.
    throw await apiErrorFrom(response);
  }
  return response.json() as Promise<T>;
}

export function listRuns(params: ListRunsParams = {}, signal?: AbortSignal): Promise<RunSummary[]> {
  const query = new URLSearchParams();
  if (params.task_id) query.set("task_id", params.task_id);
  if (params.project_id) query.set("project_id", params.project_id);
  if (params.state) query.set("state", params.state);
  // Repeated `states` params: the endpoint filters on the set in one query.
  for (const state of params.states ?? []) query.append("states", state);
  if (params.adapter) query.set("adapter", params.adapter);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  const suffix = query.size ? `?${query.toString()}` : "";
  return request(`/api/runs${suffix}`, { signal });
}

/**
 * The runs of one task that have not settled.
 *
 * "Is a run active?" asked as its own question, because it cannot be inferred
 * from a page of recent history: a long-lived task can fill a `limit` page with
 * terminal runs while an older one is still queued, and the absence of an active
 * run in that page is not its absence.
 *
 * One request, not one per state. Two filtered reads are two moments: a run that
 * moves queued -> running between them appears in neither, and the caller reads
 * "nothing is active" while a run is active. Sequencing them queued-then-running
 * would happen to be sound (run states only move forward), but it would rest on
 * an implicit invariant, and a mirror that can drift is worse than no mirror — so
 * the set is asked for atomically instead.
 */
export function listActiveRuns(taskId: string, signal?: AbortSignal): Promise<RunSummary[]> {
  const states: RunState[] = ["queued", "running"];
  return listRuns({ task_id: taskId, states, limit: 500 }, signal);
}

export function listExecutionAdapters(signal?: AbortSignal): Promise<AdapterCapability[]> {
  return request("/api/execution-adapters", { signal });
}

export function getRun(runId: string, signal?: AbortSignal): Promise<ExecutionRun> {
  return request(`/api/runs/${encodeURIComponent(runId)}`, { signal });
}

export interface Deliverable {
  path: string;
  status: string;
  size: number | null;
  // Digest of the file as the run left it. Absent for pre-snapshot runs, null
  // for files too large to hash.
  sha256?: string | null;
}

export interface RunDeliverables {
  run_id: string;
  workspace: string;
  files: Deliverable[];
  // Where the list came from. "snapshot": frozen when the run ended — what this
  // run actually delivered. "live": the worktree right now, only meaningful
  // while the run is active. "none": the run ended without a snapshot, so its
  // files are unknown and are NOT guessed from the worktree.
  source?: "snapshot" | "live" | "none";
  captured_at?: string | null;
  // Snapshot reads only: the recorded list hit MAX_FILES, so it is "at least
  // these" and a path absent from it is unknown rather than not delivered.
  truncated?: boolean;
  // Snapshot reads only: whether the worktree behind these files still exists,
  // i.e. whether any of them can actually be opened. The list stays true about
  // the run either way.
  content_available?: boolean;
  unavailable?: string;
}

/**
 * Whether one delivered file can actually be opened — the single place this rule
 * lives, because both the Task Workspace and the Run Center render it and two
 * copies would be two chances to drift.
 *
 * Two conditions, and each mirrors a way the backend refuses the read:
 * - the worktree behind the files must still exist (`content_available`);
 * - a *snapshot* read must carry a digest, since the backend verifies the bytes
 *   against it and refuses an entry that has none (deleted, not a regular file,
 *   or above the hash limit). A live listing of an active run has no digests and
 *   the backend allows those reads, so requiring one there would wrongly hide
 *   the preview while a run is in progress.
 */
export function canOpenDeliverable(
  record: Pick<RunDeliverables, "source" | "content_available">,
  file: Deliverable,
): boolean {
  if (record.content_available === false) return false;
  return record.source !== "snapshot" || !!file.sha256;
}

export interface DeliverableContent {
  path: string;
  content: string;
  truncated: boolean;
}

export function getRunDeliverables(runId: string, signal?: AbortSignal): Promise<RunDeliverables> {
  return request(`/api/runs/${encodeURIComponent(runId)}/deliverables`, { signal });
}

export function getDeliverableContent(runId: string, path: string, signal?: AbortSignal): Promise<DeliverableContent> {
  return request(`/api/runs/${encodeURIComponent(runId)}/deliverables/content?path=${encodeURIComponent(path)}`, { signal });
}

// Absolute URL to open/render a deliverable file in a new tab. The server
// serves it sandboxed via CSP so a rendered page cannot touch the app origin.
export function deliverableRawUrl(runId: string, path: string): string {
  return `${getApiBase()}/api/runs/${encodeURIComponent(runId)}/deliverables/raw?path=${encodeURIComponent(path)}`;
}

export function createRun(input: CreateRunInput): Promise<ExecutionRun> {
  return request("/api/runs", { method: "POST", body: JSON.stringify(input) });
}

export function cancelRun(runId: string, input: CancelRunInput): Promise<ExecutionRun> {
  return request(`/api/runs/${encodeURIComponent(runId)}/cancel`, {
    method: "POST",
    body: JSON.stringify(input),
  });
}

export function asRunSummary(run: ExecutionRun): RunSummary {
  const { run_id, task_id, project_id, adapter, state, version, queued_at, started_at, finished_at, exit_code } = run;
  return { run_id, task_id, project_id, adapter, state, version, queued_at, started_at, finished_at, exit_code };
}

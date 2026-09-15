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
  /**
   * The run's identity, carried on the summary because every view that nests a
   * review under the run it reviews reads it. Absent, it reads as "worker" — so
   * a page of summaries drew each review as a top-level turn of its own.
   */
  role: string;
  reviews_run_id: string | null;
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
  return listRuns({ task_id: taskId, states: [...ACTIVE_RUN_STATES], limit: 500 }, signal);
}

/**
 * What "active" means, in one place. Two call sites asking the same question with two
 * spellings of the set is how they start disagreeing about whether a queued run counts.
 */
export const ACTIVE_RUN_STATES: readonly RunState[] = ["queued", "running"];

/**
 * The same question as `listActiveRuns`, asked for a whole project at once.
 *
 * The plan needs it for every step it shows, and one request per row would be N moments
 * rather than one — the same drift the per-task reader's comment above rejects, one
 * level up. `limit` is the caller's because it pages: unlike the per-task set, a project
 * could in principle hold more active runs than one page, and the caller has to be able
 * to tell a full page from the whole set.
 */
export function listActiveProjectRuns(
  projectId: string,
  limit: number,
  signal?: AbortSignal,
): Promise<RunSummary[]> {
  return listRuns({ project_id: projectId, states: [...ACTIVE_RUN_STATES], limit }, signal);
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

/** One polled window into a run's live transcript. */
export interface RunOutputSlice {
  run_id: string;
  text: string;
  offset: number;
  next_offset: number;
  /** False means the transcript could not be read — not that the run is quiet. */
  reachable: boolean;
  unreachable_reason: string | null;
  more_available: boolean;
  /** Output the agent produced that could not be saved to the transcript. */
  lossy: boolean;
  complete: boolean;
  state: RunState;
  /**
   * True when the control plane rebuilt this text from the run's own record
   * because no transcript file exists — a run from before transcripts, or one
   * that ended before writing its first line. The window says so rather than
   * presenting it as a live transcript.
   */
  from_record: boolean;
}

/**
 * The run's transcript from `offset` bytes in.
 *
 * Polled with the previous read's `next_offset`, which is the contract a remote
 * environment can serve with `tail -c +<offset>` when runs move off this machine.
 */
export function getRunOutput(runId: string, offset: number, signal?: AbortSignal): Promise<RunOutputSlice> {
  const query = new URLSearchParams({ offset: String(Math.max(0, Math.trunc(offset))) });
  return request(`/api/runs/${encodeURIComponent(runId)}/output?${query.toString()}`, { signal });
}

/** The provider session an adapter is holding for one task. */
export interface AgentSession {
  task_id: string;
  adapter: ExecutionAdapter;
  state: "none" | "active" | "unreadable" | "unsupported";
  session_id: string | null;
  workspace: string | null;
  reason: string | null;
  first_seen_at: string | null;
  last_seen_at: string | null;
  last_run_id: string | null;
  turns: number;
}

export function listTaskSessions(taskId: string, signal?: AbortSignal): Promise<AgentSession[]> {
  return request(`/api/tasks/${encodeURIComponent(taskId)}/sessions`, { signal });
}

/**
 * Drop the recorded session so the next turn opens a new conversation.
 *
 * The only thing that clears a session id, and it is always the user's act: the
 * dispatcher never starts a new conversation to work around one it could not
 * reach (tasks/agora/adr/0007).
 */
export function retryTaskSession(
  taskId: string,
  adapter: ExecutionAdapter,
  role: string = "worker",
): Promise<AgentSession> {
  // The counterpart to forgetting: keep the session id and ask for it to be
  // tried again. A session the control plane recorded as unresumable told the
  // person to fix the cause and carry on, while offering exactly one action —
  // the one that drops the context.
  const query = new URLSearchParams({ role });
  return request(
    `/api/tasks/${encodeURIComponent(taskId)}/sessions/${encodeURIComponent(adapter)}/retry?${query}`,
    { method: "POST" },
  );
}

export function forgetTaskSession(
  taskId: string,
  adapter: ExecutionAdapter,
  role: string = "worker",
): Promise<AgentSession> {
  // The role is explicit even though the endpoint defaults to it: sessions are
  // keyed by role, and a caller that means "the conversation in this window"
  // should say so rather than inherit whichever default the API happens to have.
  const query = new URLSearchParams({ role });
  return request(
    `/api/tasks/${encodeURIComponent(taskId)}/sessions/${encodeURIComponent(adapter)}?${query}`,
    { method: "DELETE" },
  );
}

export function asRunSummary(run: ExecutionRun): RunSummary {
  const { run_id, task_id, project_id, adapter, state, version, queued_at, started_at, finished_at, exit_code } = run;
  // The identity comes off the metadata, with the same defaults the control
  // plane uses (`role_of`: an untagged run is somebody's implementation work).
  // Dropping it here would put a locally-built summary into a list whose other
  // rows carry it, and the run tree would place this one differently from the
  // same run after the next refresh.
  const metadata = run.result_metadata ?? {};
  const role = typeof metadata["agora_role"] === "string" ? metadata["agora_role"] : "worker";
  const reviews = metadata["reviews_run_id"];
  return {
    run_id, task_id, project_id, adapter, state, version, queued_at, started_at,
    finished_at, exit_code,
    role,
    reviews_run_id: typeof reviews === "string" && reviews ? reviews : null,
  };
}

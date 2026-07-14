import { getApiBase } from "@/lib/api";
import { ApiError } from "@/lib/control-plane";

export type RunState =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "timed_out"
  | "cancelled"
  | "abandoned";

export type ExecutionAdapter = "codex" | "claude" | "kiro";

export const TERMINAL_RUN_STATES: ReadonlySet<RunState> = new Set([
  "succeeded", "failed", "timed_out", "cancelled", "abandoned",
]);

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

export function listRuns(params: ListRunsParams = {}, signal?: AbortSignal): Promise<RunSummary[]> {
  const query = new URLSearchParams();
  if (params.task_id) query.set("task_id", params.task_id);
  if (params.project_id) query.set("project_id", params.project_id);
  if (params.state) query.set("state", params.state);
  if (params.adapter) query.set("adapter", params.adapter);
  if (params.limit !== undefined) query.set("limit", String(params.limit));
  if (params.offset !== undefined) query.set("offset", String(params.offset));
  const suffix = query.size ? `?${query.toString()}` : "";
  return request(`/api/runs${suffix}`, { signal });
}

export function getRun(runId: string, signal?: AbortSignal): Promise<ExecutionRun> {
  return request(`/api/runs/${encodeURIComponent(runId)}`, { signal });
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

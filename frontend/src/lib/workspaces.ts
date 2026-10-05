import { getApiBase } from "@/lib/api";
import { ApiError } from "@/lib/control-plane";
import type { ExecutionAdapter } from "@/lib/execution";

export type WorkspaceState = "missing" | "provisioning" | "ready" | "foreign" | "error";

export interface WorkspaceStatus {
  project_id: string;
  adapter: ExecutionAdapter;
  state: WorkspaceState;
  path: string;
  branch: string | null;
  head_sha: string | null;
  error: string | null;
  source_is_git: boolean;
}

export interface ProvisionResult {
  status: WorkspaceStatus;
  created: boolean;
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
    } catch { /* status fallback */ }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}

/** `environmentId` names a registered remote environment (CR-4): the worktree is
 * checked there instead of where the adapter's own configuration points. */
export function getWorkspaceStatus(
  projectId: string, adapter: ExecutionAdapter, signal?: AbortSignal, environmentId?: string | null,
  taskId?: string | null,
): Promise<WorkspaceStatus> {
  // `taskId`: the server resolves where that task's work is when no environment is named,
  // the same way its next run inherits it — so the check and the run agree on the place.
  const params = new URLSearchParams();
  if (environmentId) params.set("environment_id", environmentId);
  if (taskId) params.set("task_id", taskId);
  const query = params.size ? `?${params.toString()}` : "";
  return request(`/api/workspaces/${encodeURIComponent(projectId)}/${encodeURIComponent(adapter)}${query}`, { signal });
}

/** `taskId`, when given, makes the server check the task's placement rule first, so a
 * worktree is never built where that task's run would be refused. */
export function provisionWorkspace(
  projectId: string, adapter: ExecutionAdapter, signal?: AbortSignal, environmentId?: string | null,
  taskId?: string | null,
): Promise<ProvisionResult> {
  return request("/api/workspaces/provision", {
    method: "POST",
    body: JSON.stringify({
      project_id: projectId, adapter,
      ...(environmentId ? { environment_id: environmentId } : {}),
      ...(taskId ? { task_id: taskId } : {}),
    }),
    signal,
  });
}

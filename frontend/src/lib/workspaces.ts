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

export function getWorkspaceStatus(projectId: string, adapter: ExecutionAdapter, signal?: AbortSignal): Promise<WorkspaceStatus> {
  return request(`/api/workspaces/${encodeURIComponent(projectId)}/${encodeURIComponent(adapter)}`, { signal });
}

export function provisionWorkspace(projectId: string, adapter: ExecutionAdapter, signal?: AbortSignal): Promise<ProvisionResult> {
  return request("/api/workspaces/provision", {
    method: "POST",
    body: JSON.stringify({ project_id: projectId, adapter }),
    signal,
  });
}

import { getApiBase } from "@/lib/api";
import { ApiError } from "@/lib/control-plane";

export type AttentionKind = "question" | "approval" | "blocker";
export type AttentionState = "open" | "responded" | "cancelled" | "expired";
export type AttentionUrgency = "low" | "normal" | "high" | "critical";
export type ResponseAction = "answer" | "approve" | "reject";

export interface AttentionItem {
  item_id: string; project_id: string; task_id: string; run_id: string | null;
  kind: AttentionKind; state: AttentionState; urgency: AttentionUrgency;
  title: string; body: string; options: string[]; context: Record<string, unknown>;
  requester: string; assignee: string | null; response: string | null;
  response_action: ResponseAction | null; responded_by: string | null;
  cancellation_reason: string | null; version: number; expires_at: string | null;
  created_at: string; responded_at: string | null; updated_at: string;
}

export interface ListAttentionParams {
  project_id?: string; task_id?: string; run_id?: string;
  state?: AttentionState; kind?: AttentionKind; limit?: number; offset?: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${getApiBase()}${path}`, {
    ...init, headers: { "Content-Type": "application/json", ...init?.headers },
  });
  if (!response.ok) {
    let message = `Request failed (${response.status})`;
    try {
      const payload = await response.json();
      if (typeof payload.detail === "string") message = payload.detail;
    } catch { /* keep fallback */ }
    throw new ApiError(response.status, message);
  }
  return response.json() as Promise<T>;
}

export function listAttention(params: ListAttentionParams = {}, signal?: AbortSignal): Promise<AttentionItem[]> {
  const query = new URLSearchParams();
  Object.entries(params).forEach(([key, value]) => { if (value !== undefined) query.set(key, String(value)); });
  return request(`/api/attention${query.size ? `?${query}` : ""}`, { signal });
}

export function respondAttention(itemId: string, input: {
  action: ResponseAction; response: string; actor?: string; expected_version: number;
}): Promise<AttentionItem> {
  return request(`/api/attention/${encodeURIComponent(itemId)}/respond`, {
    method: "POST", body: JSON.stringify(input),
  });
}
export function cancelAttention(itemId: string, input: {
  actor?: string; reason?: string; expected_version: number;
}): Promise<AttentionItem> {
  return request(`/api/attention/${encodeURIComponent(itemId)}/cancel`, {
    method: "POST", body: JSON.stringify(input),
  });
}

/** The work page's requests (workspace CR-75 slice 1). Every one carries the terminal
 *  token, like the terminal routes: the list names this machine's projects. */
import { call } from "@/lib/terminal";
import type { RestoreEntry, WorkTerminal, WorkView } from "@/lib/work-state";

export * from "@/lib/work-state";

export const listWorkProjects = () => call<WorkView>("/api/work/projects");

export const openWorkProject = (key: string) =>
  call<{ result: string; detail?: string | null; terminal?: WorkTerminal }>(
    `/api/work/projects/${encodeURIComponent(key)}/open`, { method: "POST" });

export const closeWorkProject = (key: string) =>
  call<{ key: string; closed: boolean; still_running?: string[] }>(`/api/work/projects/${encodeURIComponent(key)}/close`, { method: "POST" });

export const restoreWorkProjects = () =>
  call<{ restore: Record<string, RestoreEntry> }>("/api/work/restore", { method: "POST" });

/** Pause or archive (workspace CR-80): written into the project's own task.yaml. */
export const shelveWorkProject = (key: string, action: "pause" | "archive") =>
  call<{ key: string; action: string }>(`/api/work/projects/${encodeURIComponent(key)}/shelve`, {
    method: "POST", body: JSON.stringify({ action }),
  });

/** Todo or doing (CR-81): Agora's own; "todo" is refused while the terminal is open. */
export const setWorkStage = (key: string, stage: "todo" | "doing") =>
  call<{ key: string; stage: string }>(`/api/work/projects/${encodeURIComponent(key)}/stage`, {
    method: "POST", body: JSON.stringify({ stage }),
  });

/** The order the person dragged the projects into (CR-82). */
export const setWorkOrder = (keys: string[]) =>
  call<{ keys: string[] }>("/api/work/order", { method: "PUT", body: JSON.stringify({ keys }) });

/** Back to active; an archived claude-workspace task moves out of tasks/_archive/ first. */
export const restoreShelvedProject = (key: string) =>
  call<{ key: string; restored: boolean }>(`/api/work/shelved/${encodeURIComponent(key)}/restore`, { method: "POST" });

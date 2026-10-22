/**
 * The embedded terminal's client side (CR-30): calls, and the pure decisions the page
 * makes from their answers. Rendering lives in components/terminal-view.tsx.
 */
import { getApiBase } from "./api.ts";

export type TerminalCommand = "shell" | "claude" | "codex" | "kiro";

export interface TerminalRow {
  terminal_id: string;
  project_id: string;
  label: string;
  cwd: string;
  command: string[];
  started_at: number;
  exited_at: number | null;
  exit_code: number | null;
  /** How it ended: the program exited, the person closed it, the SSH connection dropped
   *  (the remote program may still be running), or it never started. Null while running. */
  end_reason: "exited" | "closed" | "disconnected" | "failed" | null;
  end_detail: string | null;
  /** `user@host` for a terminal on a registered remote environment; null on this machine. */
  remote: string | null;
  alive: boolean;
}

export interface TerminalEnvironmentTarget {
  environment_id: string;
  label: string;
  target: string;
  root: string;
  /** Set when "open as task" can be asked for on this environment (CR-30 slice 3). */
  workspace_tool_dir: string | null;
}

/** The tasks on a remote host that name the environment's root (CR-30 slice 3). Three
 *  states kept apart: read whole, read with gaps, could not read. */
export interface RemoteTaskTargets {
  environment_id: string;
  state: "complete" | "partial" | "unavailable";
  reason: string | null;
  tool_dir: string | null;
  work_root: string | null;
  unreadable: number;
  truncated: boolean;
  tasks: { task: string; title: string | null }[];
  remote_repo: { head: string | null; dirty: boolean | null } | null;
  local_repos: { source: string; head: string | null; dirty: boolean | null }[];
}

export interface TerminalTaskTarget {
  source: string;
  task: string;
  title: string | null;
  tool_dir: string | null;
  reason: string | null;
  /** Which workspace the task is in: claude opens it like `cw open`, kiro on its agent. */
  workspace: "claude" | "kiro";
  agent: string | null;
}

export interface TerminalTargets {
  project_id: string;
  root: string;
  tasks: TerminalTaskTarget[];
  /** Registered remote environments of this project (CR-30 slice 2). */
  environments: TerminalEnvironmentTarget[];
  /** Set when the environment list could not be read: "none" is not claimed then. */
  environments_error: string | null;
}

export class TerminalAuthRequired extends Error {}

/**
 * The install's terminal token, kept in localStorage: scoped to scheme, host AND port, so
 * no service on another localhost port ever receives it — a cookie would be sent to all
 * of them (review). The launcher's one-time code is redeemed for it on page load.
 */
export const TERMINAL_TOKEN_KEY = "agora.terminalToken";
export const LAUNCH_CODE_PARAM = "terminal_code";

type Store = Pick<Storage, "getItem" | "setItem">;

function storage(): Store | null {
  try { return typeof window === "undefined" ? null : window.localStorage; } catch { return null; }
}

export function readTerminalToken(store: Store | null = storage()): string | null {
  try { return store?.getItem(TERMINAL_TOKEN_KEY) ?? null; } catch { return null; }
}

/** The launch code in a URL, if any, and the same URL without it (it works once anyway,
 *  but it does not belong in the address bar or history). */
export function launchCodeOf(href: string): { code: string | null; clean: string } {
  const url = new URL(href);
  const code = url.searchParams.get(LAUNCH_CODE_PARAM);
  url.searchParams.delete(LAUNCH_CODE_PARAM);
  return { code, clean: `${url.pathname}${url.search}${url.hash}` };
}

export async function redeemLaunchCode(code: string, store: Store | null = storage()): Promise<boolean> {
  const response = await fetch(`${getApiBase()}/api/terminal-session`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ code }),
  });
  if (!response.ok) return false;
  const { token } = await response.json();
  if (typeof token !== "string" || !token) return false;
  try { store?.setItem(TERMINAL_TOKEN_KEY, token); } catch { return false; }
  return true;
}

async function call<T>(path: string, init?: RequestInit): Promise<T> {
  const token = readTerminalToken();
  const response = await fetch(`${getApiBase()}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(token ? { "X-Agora-Terminal-Token": token } : {}), ...init?.headers },
  });
  if (response.status === 401) throw new TerminalAuthRequired();
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const body = await response.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch { /* keep the status */ }
    throw new Error(detail);
  }
  return response.json() as Promise<T>;
}

export const terminalSession = () => call<{ authorized: boolean }>("/api/terminals/session");
export const listTerminals = () => call<{ terminals: TerminalRow[] }>("/api/terminals");
export const terminalTargets = (projectId: string) =>
  call<TerminalTargets>(`/api/projects/${encodeURIComponent(projectId)}/terminal-targets`);
export const remoteTaskTargets = (projectId: string, environmentId: string) =>
  call<RemoteTaskTargets>(`/api/projects/${encodeURIComponent(projectId)}/remote-task-targets?environment_id=${encodeURIComponent(environmentId)}`);
/** Test connection (environment page). Each step is a fact or "skipped"/"unknown"; the
 *  check stops before signing in to a host whose key is not trusted. */
export interface ConnectionCheck {
  environment_id: string;
  host_key:
    | { status: "unreachable"; detail: string }
    | { status: "trusted" | "mismatch" | "revoked" | "ca" | "unknown"; fingerprint: string; algorithm: string; mode: "strict" | "tofu" }
    | null;
  auth: "ok" | "failed" | "refused" | "skipped";
  auth_detail: string | null;
  root: "dir" | "absent" | "not_dir" | "unknown" | "skipped";
  clis: Record<string, string | null> | null;
  tool: { ok: true; tool_dir: string; work_root: string } | { ok: false; reason: string } | null;
  timed_out: boolean;
}

export const testRemoteEnvironment = (environmentId: string) =>
  call<ConnectionCheck>(`/api/remote-environments/${encodeURIComponent(environmentId)}/test`, { method: "POST" });
export const trustRemoteHostKey = (environmentId: string, fingerprint: string) =>
  call<{ fingerprint: string; recorded: boolean }>(
    `/api/remote-environments/${encodeURIComponent(environmentId)}/trust-host-key`,
    { method: "POST", body: JSON.stringify({ fingerprint }) });

export interface CheckStep {
  id: string;
  key: string;
  vars?: Record<string, string>;
  tone: "ok" | "warn" | "bad" | "muted";
}

/** The check, as lines in the order it ran. A step that did not run says so. */
export function checkStepsOf(check: ConnectionCheck): CheckStep[] {
  if (check.timed_out) return [{ id: "timeout", key: "timedOut", tone: "bad" }];
  const steps: CheckStep[] = [];
  const key = check.host_key;
  if (key === null) return steps;
  if (key.status === "unreachable") return [{ id: "key", key: "key.unreachable", vars: { detail: key.detail }, tone: "bad" }];
  steps.push({
    id: "key",
    key: key.status === "unknown" ? (key.mode === "tofu" ? "key.unknownTofu" : "key.unknownStrict") : `key.${key.status}`,
    vars: { fingerprint: key.fingerprint },
    tone: key.status === "trusted" ? "ok" : key.status === "mismatch" || key.status === "revoked" ? "bad" : key.status === "ca" ? "muted" : "warn",
  });
  if (check.auth === "skipped") {
    steps.push({ id: "auth", key: "auth.skipped", tone: "muted" });
    return steps;
  }
  steps.push({ id: "auth", key: `auth.${check.auth}`, vars: { detail: check.auth_detail ?? "" }, tone: check.auth === "ok" ? "ok" : "bad" });
  if (check.auth !== "ok") return steps;
  steps.push({ id: "root", key: `root.${check.root}`, tone: check.root === "dir" ? "ok" : check.root === "unknown" ? "warn" : "bad" });
  if (check.clis === null) steps.push({ id: "clis", key: "clis.unknown", tone: "warn" });
  else for (const [name, where] of Object.entries(check.clis)) {
    steps.push({ id: `cli-${name}`, key: where ? "clis.found" : "clis.missing", vars: { name, where: where ?? "" }, tone: where ? "ok" : "warn" });
  }
  if (check.tool !== null) {
    steps.push(check.tool.ok
      ? { id: "tool", key: "tool.ok", vars: { tool: check.tool.tool_dir, work: check.tool.work_root }, tone: "ok" }
      : { id: "tool", key: "tool.bad", vars: { reason: check.tool.reason }, tone: "bad" });
  }
  return steps;
}

export const closeTerminal = (id: string) =>
  call<{ terminal_id: string }>(`/api/terminals/${encodeURIComponent(id)}`, { method: "DELETE" });

export function createTerminal(input: {
  project_id: string;
  command?: TerminalCommand;
  task?: { source: string; task: string };
  environment_id?: string;
  remote_task?: string;
  rows?: number;
  cols?: number;
}): Promise<TerminalRow> {
  return call<TerminalRow>("/api/terminals", { method: "POST", body: JSON.stringify(input) });
}

/** The stream's address, on the same server the page talks to. */
export function terminalStreamUrl(id: string, location: { protocol: string; host: string }, apiBase: string): string {
  const base = apiBase || `${location.protocol}//${location.host}`;
  return `${base.replace(/^http/, "ws")}/api/terminals/${encodeURIComponent(id)}/stream`;
}

/** What the page says about one terminal's state. A dropped SSH connection is not an
 *  exit: the remote program may still be running, and the page says that. */
export function terminalStateOf(
  row: Pick<TerminalRow, "alive" | "exit_code"> & Partial<Pick<TerminalRow, "end_reason">>,
): "running" | "exited" | "exitedUnknown" | "closed" | "disconnected" | "failed" {
  if (row.alive) return "running";
  if (row.end_reason === "closed" || row.end_reason === "disconnected" || row.end_reason === "failed") return row.end_reason;
  return row.exit_code === null ? "exitedUnknown" : "exited";
}

/** The sentence under a remote host's task list (CR-30 slice 3): what the read could not
 *  cover, and the two work-data repositories as facts. No verdict on whether the task
 *  records agree, and no "pull over there": HEAD alone cannot say either. */
export type RemoteTaskNote =
  | { kind: "unavailable"; reason: string }
  | { kind: "partial"; unreadable: number; truncated: boolean }
  | { kind: "none" }
  | { kind: "listed" };

export function remoteTaskNoteOf(scan: Pick<RemoteTaskTargets, "state" | "reason" | "unreadable" | "truncated" | "tasks">): RemoteTaskNote {
  if (scan.state === "unavailable") return { kind: "unavailable", reason: scan.reason ?? "" };
  if (scan.state === "partial") return { kind: "partial", unreadable: scan.unreadable, truncated: scan.truncated };
  return scan.tasks.length === 0 ? { kind: "none" } : { kind: "listed" };
}

/** One side's work-data repository, said as it is: HEAD and whether it has uncommitted
 *  changes, each "unknown" when it could not be read. */
export function repoFactsOf(repo: { head: string | null; dirty: boolean | null } | null): { head: string; dirty: "yes" | "no" | "unknown" } {
  return {
    head: repo?.head ?? "?",
    dirty: repo?.dirty === true ? "yes" : repo?.dirty === false ? "no" : "unknown",
  };
}

/** Launch choices for a project: its claude-workspace tasks first (the way the person
 *  works), then the repository with each agent. A task with no tool directory is listed
 *  with why it cannot open, never silently dropped. */
export type LaunchChoice =
  | { kind: "task"; target: TerminalTaskTarget; openable: boolean }
  | { kind: "repo"; command: TerminalCommand }
  | { kind: "remote"; environment: TerminalEnvironmentTarget; command: TerminalCommand };

const COMMANDS = ["claude", "codex", "kiro", "shell"] as const;

export function launchChoicesOf(targets: TerminalTargets | null): LaunchChoice[] {
  const tasks: LaunchChoice[] = (targets?.tasks ?? []).map((target) => ({ kind: "task", target, openable: target.tool_dir !== null }));
  const repo: LaunchChoice[] = COMMANDS.map((command) => ({ kind: "repo", command }));
  const remote: LaunchChoice[] = (targets?.environments ?? []).flatMap((environment) =>
    COMMANDS.map((command) => ({ kind: "remote" as const, environment, command })));
  return [...tasks, ...repo, ...remote];
}

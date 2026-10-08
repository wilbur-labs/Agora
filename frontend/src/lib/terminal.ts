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
  alive: boolean;
}

export interface TerminalTaskTarget {
  source: string;
  task: string;
  title: string | null;
  tool_dir: string | null;
  reason: string | null;
}

export interface TerminalTargets {
  project_id: string;
  root: string;
  tasks: TerminalTaskTarget[];
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
export const closeTerminal = (id: string) =>
  call<{ terminal_id: string }>(`/api/terminals/${encodeURIComponent(id)}`, { method: "DELETE" });

export function createTerminal(input: {
  project_id: string;
  command?: TerminalCommand;
  task?: { source: string; task: string };
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

/** What the page says about one terminal's state. */
export function terminalStateOf(row: Pick<TerminalRow, "alive" | "exit_code">): "running" | "exited" | "exitedUnknown" {
  if (row.alive) return "running";
  return row.exit_code === null ? "exitedUnknown" : "exited";
}

/** Launch choices for a project: its claude-workspace tasks first (the way the person
 *  works), then the repository with each agent. A task with no tool directory is listed
 *  with why it cannot open, never silently dropped. */
export type LaunchChoice =
  | { kind: "task"; target: TerminalTaskTarget; openable: boolean }
  | { kind: "repo"; command: TerminalCommand };

export function launchChoicesOf(targets: TerminalTargets | null): LaunchChoice[] {
  const tasks: LaunchChoice[] = (targets?.tasks ?? []).map((target) => ({ kind: "task", target, openable: target.tool_dir !== null }));
  const repo: LaunchChoice[] = (["claude", "codex", "kiro", "shell"] as const).map((command) => ({ kind: "repo", command }));
  return [...tasks, ...repo];
}

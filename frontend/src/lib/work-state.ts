/**
 * The work page's judgements (workspace CR-75 slice 1): what the left column says about a
 * project and what the restore banner says about each one. Design:
 * docs/CR75-slice1-work-page.md §5.
 *
 * Everything here reads what the server returned and nothing else: a terminal that
 * started proves a shell started, and "resumed" is said only when the launch's first
 * report says so. Free of React and "@/" aliases so `node --test` runs it directly.
 */

export interface WorkTerminal {
  terminal_id: string;
  alive: boolean;
  exit_code?: number | null;
  end_reason?: string | null;
}

export type LaunchResult =
  | "resume" | "fresh_no_record" | "fresh_missing" | "fresh_untrusted" | "fresh_unsupported" | "no_hook";

export type Confirm = "resumed" | "resumed_other" | "not_resumed" | "started" | "unreadable_report" | "unconfirmed" | "pending";

export interface WorkProjectRow {
  key: string;
  source: string;
  task: string;
  title: string | null;
  workspace: "claude" | "kiro";
  openable: boolean;
  reason: string | null;
  want_open: boolean;
  terminal: WorkTerminal | null;
  starting: boolean;
  launch: { result: LaunchResult; detail: string | null; restored: boolean; confirm: Confirm | null } | null;
  stale: boolean;
  agent_exited: boolean;
}

export type RestoreResult =
  | LaunchResult | "already_open" | "closed" | "unavailable" | "maybe_running" | "failed";

export interface RestoreEntry {
  task: string;
  source: string;
  result: RestoreResult;
  detail?: string | null;
  /** A launch the person started later replaced this one: it no longer describes the terminal. */
  superseded?: boolean;
}

export interface WorkView {
  projects: WorkProjectRow[];
  unreadable: { source: string; task: string; reason: string; key?: string; want_open?: boolean }[];
  inactive: number;
  missing: { key: string; task: string; source: string }[];
  /** Running terminals of projects the scan no longer lists: still reachable, to close. */
  detached: { key: string; task: string; terminal: WorkTerminal }[];
  restore: Record<string, RestoreEntry> | null;
  restoring: boolean;
  hook_problem: string | null;
}

/** The left column's state for one project: about the terminal, never about claude. */
export type ProjectState = "starting" | "open" | "agentExited" | "ended" | "notOpen";

export function projectStateOf(row: Pick<WorkProjectRow, "starting" | "terminal" | "agent_exited">): ProjectState {
  if (row.starting) return "starting";
  if (!row.terminal) return "notOpen";
  if (!row.terminal.alive) return "ended";
  return row.agent_exited ? "agentExited" : "open";
}

/** One banner line: a message key under `work.restore.*`, its tone, and the vars. */
export interface BannerLine {
  key: string;
  task: string;
  tone: "good" | "warn" | "bad" | "pending";
  message: string;
  detail: string | null;
  stale: boolean;
}

/**
 * What the banner says about one restored project. A launch that was decided as a resume
 * is "resumed" only once its first report confirms it; until then it is pending, and past
 * the window it is unconfirmed — a state, not the end: a late report still moves it.
 */
export function bannerLineOf(key: string, entry: RestoreEntry, row: WorkProjectRow | undefined): BannerLine | null {
  if (entry.superseded) return null;
  const base = { key, task: entry.task, detail: entry.detail ?? null, stale: Boolean(row?.stale) };
  switch (entry.result) {
    case "already_open":
    case "closed":
      return null; // nothing was restored: it was never gone, or the person closed it
    case "unavailable":
      return { ...base, tone: "bad", message: "unavailable" };
    case "maybe_running":
      return { ...base, tone: "warn", message: "maybeRunning" };
    case "failed":
      return { ...base, tone: "bad", message: "failed" };
    case "fresh_unsupported":
      return { ...base, tone: "warn", message: "freshUnsupported" };
    case "no_hook":
      return { ...base, tone: "warn", message: "noHook" };
  }
  // A claude launch: what it turned out to be comes from the launch's first report. A
  // project no longer listed has no report to read here: that is unconfirmed, not pending.
  if (!row) return { ...base, tone: "warn", message: entry.result === "resume" ? "unconfirmed" : "freshNoRecordUnconfirmed" };
  const confirm = row.launch?.restored ? row.launch.confirm : null;
  if (entry.result === "resume") {
    switch (confirm) {
      case "resumed": return { ...base, tone: "good", message: "resumed" };
      case "resumed_other": return { ...base, tone: "warn", message: "resumedOther" };
      case "not_resumed": return { ...base, tone: "warn", message: "notResumed" };
      case "unreadable_report": return { ...base, tone: "warn", message: "unreadableReport" };
      case "unconfirmed": return { ...base, tone: "warn", message: "unconfirmed" };
      default: return { ...base, tone: "pending", message: "resuming" };
    }
  }
  // A fresh launch, and why it is fresh.
  const why = entry.result === "fresh_missing" ? "freshMissing" : entry.result === "fresh_untrusted" ? "freshUntrusted" : "freshNoRecord";
  if (confirm === "started") return { ...base, tone: "warn", message: why };
  if (confirm === "unconfirmed" || confirm === "unreadable_report") return { ...base, tone: "warn", message: `${why}Unconfirmed` };
  return { ...base, tone: "pending", message: `${why}Pending` };
}

export function bannerLinesOf(view: Pick<WorkView, "restore" | "projects">): BannerLine[] {
  if (!view.restore) return [];
  const rows = new Map(view.projects.map((row) => [row.key, row]));
  return Object.entries(view.restore)
    .map(([key, entry]) => bannerLineOf(key, entry, rows.get(key)))
    .filter((line): line is BannerLine => line !== null);
}

/** Whether the banner may close itself: only when every line is a confirmed resume. */
export function bannerSettled(lines: BannerLine[]): boolean {
  return lines.every((line) => line.tone === "good" && !line.stale);
}

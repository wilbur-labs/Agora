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
  | "resume" | "fresh_no_record" | "fresh_missing" | "fresh_empty" | "fresh_untrusted" | "fresh_unsupported" | "no_hook";

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
  /** The person's board (CR-81): Agora's own, not in the task record. */
  stage: Stage;
  /** Where the person dragged it (CR-82); null for one never placed. */
  position: number | null;
  /** The name the person gave its card (CR-85): Agora's own; null when none. Absent
   *  from a server older than CR-85. */
  name?: string | null;
}

export type Stage = "todo" | "doing";

/** The projects in the person's order: placed ones by position, then the rest as the
 *  server listed them. */
export function orderedRows<T extends Pick<WorkProjectRow, "position">>(rows: readonly T[]): T[] {
  return rows
    .map((row, index) => ({ row, index }))
    .sort((a, b) => {
      const pa = a.row.position ?? Number.POSITIVE_INFINITY;
      const pb = b.row.position ?? Number.POSITIVE_INFINITY;
      return pa === pb ? a.index - b.index : pa - pb;
    })
    .map(({ row }) => row);
}

/** The work page's list: what is doing — and anything whose terminal is open, opening or
 *  meant to be, whatever its stage says: the page never hides a running terminal. */
export function workListOf(rows: readonly WorkProjectRow[]): WorkProjectRow[] {
  return orderedRows(rows).filter((row) =>
    row.stage === "doing" || row.starting || row.want_open || Boolean(row.terminal?.alive));
}

/** `keys` with `moved` taken out and put back before `before` (at the end for null, or
 *  when `before` is not there). The same array's order if nothing moves. */
export function moveKey(keys: readonly string[], moved: string, before: string | null): string[] {
  const rest = keys.filter((key) => key !== moved);
  const at = before === null || before === moved ? -1 : rest.indexOf(before);
  if (before === moved) return [...keys];
  if (at < 0) return [...rest, moved];
  return [...rest.slice(0, at), moved, ...rest.slice(at)];
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

export interface ShelvedProject {
  key: string;
  source: string;
  task: string;
  title: string | null;
  workspace: "claude" | "kiro";
  /** What its task.yaml says: `paused`, `archived`, or whatever else is not `active`. */
  status: string;
  /** It sits in tasks/_archive/ (claude-workspace's `cw archive`). */
  archived_dir: boolean;
}

/** Why a pause/archive/restore did not happen, from the error the request threw: the
 *  terminal is open (said in the page's words), or the server's own reason. */
export function shelfErrorOf(message: string): { kind: "terminalOpen" } | { kind: "refused"; reason: string } {
  try {
    const body = JSON.parse(message) as { code?: unknown; message?: unknown };
    if (body && body.code === "terminal_open") return { kind: "terminalOpen" };
    if (body && typeof body.message === "string") return { kind: "refused", reason: body.message };
  } catch { /* not the structured detail */ }
  return { kind: "refused", reason: message };
}

/** The shelved list split as the page shows it: paused first, then archived, then any
 *  other status — a status the page has no word for is shown as written, not hidden. */
export function shelvedGroupsOf(shelved: readonly ShelvedProject[]) {
  const paused = shelved.filter((p) => p.status === "paused");
  const archived = shelved.filter((p) => p.status !== "paused" && (p.status === "archived" || p.archived_dir));
  const other = shelved.filter((p) => !paused.includes(p) && !archived.includes(p));
  return { paused, archived, other };
}

export interface WorkView {
  projects: WorkProjectRow[];
  unreadable: { source: string; task: string; reason: string; key?: string; want_open?: boolean }[];
  inactive: number;
  /** Paused or archived projects (CR-80): not listed for work, offered for restoring. */
  shelved: ShelvedProject[];
  /** Keys (as they were while active) the person paused or archived from this server. */
  put_away: string[];
  missing: { key: string; task: string; source: string }[];
  /** Running terminals of projects the scan no longer lists: still reachable, to close. */
  detached: { key: string; task: string; terminal: WorkTerminal }[];
  restore: Record<string, RestoreEntry> | null;
  restoring: boolean;
  hook_problem: string | null;
}

/** Which project the work page shows; the overview writes it before sending the person
 *  there. A convenience only: browser storage that is not there loses nothing. */
export const WORK_SELECTED_KEY = "agora.work.selected";

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
 *
 * Only what asks something of the person gets a line (owner, 11-03): a confirmed resume,
 * and a fresh start where there was nothing to bring back, say nothing — nor do they
 * while they are still on their way to that. The line appears once the launch turns out
 * otherwise. Saying nothing claims nothing: no line here ever stands for "resumed".
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
      case "resumed": return base.stale ? { ...base, tone: "good", message: "resumed" } : null;
      case "resumed_other": return { ...base, tone: "warn", message: "resumedOther" };
      case "not_resumed": return { ...base, tone: "warn", message: "notResumed" };
      case "unreadable_report": return { ...base, tone: "warn", message: "unreadableReport" };
      case "unconfirmed": return { ...base, tone: "warn", message: "unconfirmed" };
      default: return base.stale ? { ...base, tone: "pending", message: "resuming" } : null;
    }
  }
  // A fresh launch, and why it is fresh. Nothing recorded, the file gone, or nobody ever
  // spoke in it: there was nothing to bring back, so a start that went well is not news.
  const quiet = entry.result !== "fresh_untrusted";
  const why = entry.result === "fresh_missing" ? "freshMissing" : entry.result === "fresh_untrusted" ? "freshUntrusted" : "freshNoRecord";
  if (confirm === "started") return quiet ? null : { ...base, tone: "warn", message: why };
  if (confirm === "unconfirmed" || confirm === "unreadable_report") return { ...base, tone: "warn", message: `${why}Unconfirmed` };
  return quiet ? null : { ...base, tone: "pending", message: `${why}Pending` };
}

export function bannerLinesOf(view: Pick<WorkView, "restore" | "projects"> & Partial<Pick<WorkView, "put_away">>): BannerLine[] {
  if (!view.restore) return [];
  const rows = new Map(view.projects.map((row) => [row.key, row]));
  // A project the person paused or archived here since the restore: they put it away, so
  // the restore has nothing left to tell them — and with it no longer listed, its report
  // is not read, which would otherwise be said as "not confirmed" (CR-80 follow-up). By
  // key, never by name: a same-named archive must not hide another project's warning.
  const putAway = new Set(view.put_away ?? []);
  return Object.entries(view.restore)
    .filter(([key]) => rows.has(key) || !putAway.has(key))
    .map(([key, entry]) => bannerLineOf(key, entry, rows.get(key)))
    .filter((line): line is BannerLine => line !== null);
}

/** Whether the banner may close itself: only when every line is a confirmed resume. */
export function bannerSettled(lines: BannerLine[]): boolean {
  return lines.every((line) => line.tone === "good" && !line.stale);
}

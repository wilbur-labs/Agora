/**
 * The project rail (CR-39 / CR-40): every project, one click away, each saying whether it
 * is waiting for the person. User, 2026-10-08: "切换项目也不方便，不知道哪个项目已经进入到
 * 我审批的阶段了".
 *
 * "Waiting for you" is what this control plane can see: open attention items (approval
 * gates, review verdicts, questions) for the project. A read that failed is said as
 * unknown — never as "nothing waiting" — and a read that was cut says "at least".
 */
export type RailStatus = "waiting" | "running" | "noGoal" | "idle" | "unknown";

export interface RailProject {
  project_id: string;
  name: string;
  /** null when the charter could not be read. */
  hasGoal: boolean | null;
}

export interface RailRead<T> {
  items: readonly T[];
  /** More exist than were read. */
  truncated: boolean;
}

export interface RailRow {
  project_id: string;
  name: string;
  status: RailStatus;
  /** Open items for the person; null when the attention read failed. */
  waiting: number | null;
  /** True when the count is a floor (the read was cut). */
  waitingAtLeast: boolean;
  running: number | null;
}

const ORDER: Record<RailStatus, number> = { waiting: 0, running: 1, noGoal: 2, unknown: 3, idle: 4 };

export function railOf(
  projects: readonly RailProject[],
  attention: RailRead<{ project_id: string }> | null,
  activeRuns: RailRead<{ project_id: string | null }> | null,
): RailRow[] {
  const count = <T,>(read: RailRead<T> | null, key: (item: T) => string | null) => {
    if (read === null) return null;
    const map = new Map<string, number>();
    for (const item of read.items) {
      const id = key(item);
      if (id) map.set(id, (map.get(id) ?? 0) + 1);
    }
    return map;
  };
  const waiting = count(attention, (item) => item.project_id);
  const running = count(activeRuns, (item) => item.project_id);
  const rows = projects.map((project): RailRow => {
    const w = waiting === null ? null : waiting.get(project.project_id) ?? 0;
    const r = running === null ? null : running.get(project.project_id) ?? 0;
    let status: RailStatus;
    if (w !== null && w > 0) status = "waiting";
    else if (r !== null && r > 0) status = "running";
    else if (project.hasGoal === false) status = "noGoal";
    // "Idle" is a claim that nothing waits and nothing runs: only when both reads landed
    // whole. A cut read may have missed this project's item.
    else if (w === 0 && r === 0 && !attention?.truncated && !activeRuns?.truncated) status = "idle";
    else status = "unknown";
    return {
      project_id: project.project_id, name: project.name, status,
      waiting: w, waitingAtLeast: Boolean(attention?.truncated) && w !== null, running: r,
    };
  });
  return rows.sort((a, b) =>
    ORDER[a.status] - ORDER[b.status]
    || (b.waiting ?? 0) - (a.waiting ?? 0)
    || a.name.localeCompare(b.name));
}

/** The top bar's number: open items for the person across every project. */
export function waitingTotalOf(attention: RailRead<unknown> | null): { count: number; atLeast: boolean } | null {
  return attention === null ? null : { count: attention.items.length, atLeast: attention.truncated };
}

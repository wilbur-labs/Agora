/**
 * The goals a project's repository already has in the workspaces' task records (CR-35).
 * An imported project should not start its goal from nothing when the person wrote one
 * down long ago; but which one to adopt stays theirs to pick.
 */
import type { RecordedGoal } from "./control-plane.ts";

export type RecordedGoalView = {
  withGoal: RecordedGoal[];
  /** Records that name this repository but state no goal: listed, never hidden. */
  withoutGoal: RecordedGoal[];
};

export function recordedGoalViewOf(goals: RecordedGoal[]): RecordedGoalView {
  const seen = new Set<string>();
  const withGoal: RecordedGoal[] = [];
  const withoutGoal: RecordedGoal[] = [];
  for (const g of goals) {
    const text = (g.goal ?? "").trim();
    if (!text) { withoutGoal.push(g); continue; }
    // The same sentence in two workspaces is one choice, not two.
    if (seen.has(text)) continue;
    seen.add(text);
    withGoal.push(g);
  }
  return { withGoal, withoutGoal };
}

/** What the charter route accepts for a goal (`SetCharterRequest.goal`). */
export const GOAL_MAX_CHARS = 2000;

/** A goal that the charter route would refuse is shown, but not offered as one click. */
export function adoptableGoal(g: RecordedGoal): boolean {
  const text = (g.goal ?? "").trim();
  return text.length > 0 && text.length <= GOAL_MAX_CHARS;
}

/** "signal-hunt · Work" — which task, in which workspace. */
export function recordLabelOf(g: RecordedGoal): string {
  const parts = g.source.split(/[\\/]/).filter(Boolean);
  return `${g.task} · ${parts[parts.length - 1] ?? g.source}`;
}

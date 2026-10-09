/**
 * Whether the workbench's right-hand column is folded away (CR-53: "右边栏这些内容
 * 平时能不能隐藏"). Kept per browser, like the text size; open is the default so a
 * first visit still shows the goal and the progress.
 */
export const SIDE_COLLAPSED_KEY = "agora.workspace.sideCollapsed";

export function sideCollapsedOf(raw: string | null | undefined): boolean {
  return raw === "1";
}

export function readSideCollapsed(): boolean {
  try { return sideCollapsedOf(window.localStorage.getItem(SIDE_COLLAPSED_KEY)); } catch { return false; }
}

export function writeSideCollapsed(collapsed: boolean): void {
  try { window.localStorage.setItem(SIDE_COLLAPSED_KEY, collapsed ? "1" : "0"); } catch { /* the column still folds */ }
}

/**
 * What the folded strip says about waiting items. Folding must not turn "could not be
 * read" into silence, and must not hide that something waits: a count shows as itself,
 * an unreadable read as "?", and only a complete zero shows nothing.
 */
export function foldedWaitingMark(openGates: number | null): string | null {
  if (openGates === null) return "?";
  return openGates > 0 ? String(openGates) : null;
}

/**
 * Judgement for "import projects from your workspaces" (CR-52).
 *
 * The server reads the claude-workspace / kiro-workspace task records and returns one
 * candidate per repository with a status. This module only sorts them for the panel and
 * names why a candidate cannot be imported — it never turns "could not inspect" into
 * "not there", and never offers a candidate the server did not call importable.
 */
import type { ImportCandidate } from "./control-plane";

export interface ImportGroups {
  /** Existing git repositories on this machine, not yet projects. */
  importable: ImportCandidate[];
  /** Already projects — shown so the list is complete, not offered again. */
  registered: ImportCandidate[];
  /** On this machine but not importable as is (missing / not a repository / unreadable /
   *  Agora itself or a workspace tool's directory — CR-56). Listed so the person can see
   *  why a repository their records name is not offered; never ticked. */
  blocked: ImportCandidate[];
  /** Not on this machine at all (another machine's paths, unfilled templates). */
  elsewhere: ImportCandidate[];
}

export function importGroupsOf(candidates: ImportCandidate[]): ImportGroups {
  const groups: ImportGroups = { importable: [], registered: [], blocked: [], elsewhere: [] };
  for (const c of candidates) {
    if (c.status === "ok" && c.suggested_id) groups.importable.push(c);
    else if (c.status === "registered") groups.registered.push(c);
    else if (c.status === "not_this_machine" || c.status === "placeholder") groups.elsewhere.push(c);
    else groups.blocked.push(c);
  }
  return groups;
}

/** The dictionary key for why a candidate is not importable. */
export function statusKeyOf(status: ImportCandidate["status"]): string {
  return `portfolio.import.status.${status}`;
}

/** "signal-hunt · Work, sh · kiro-workspace" — which task records named this repository,
 * each with the last folder of the workspace it came from. */
export function tasksLabelOf(candidate: ImportCandidate): string {
  return candidate.tasks
    .map((t) => `${t.task} · ${t.source.split(/[\\/]/).filter(Boolean).pop() ?? t.source}`)
    .join(", ");
}

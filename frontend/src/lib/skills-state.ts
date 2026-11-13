/** The skills page's types and pure helpers (CR-75 slice 5), kept free of imports so
 *  the node tests can load them. */

export type CopyStateKind = "absent" | "current" | "outdated" | "modified" | "foreign" | "unreadable";
export type InstallSummary = "installed" | "not_installed" | "outdated" | "modified" | "foreign" | "unreadable" | "partial";

/** A departure from the Agent Skills standard (agentskills.io), as the server found it. */
export interface StandardNote {
  code: "no_front_matter" | "bad_name" | "name_mismatch" | "no_description" | "description_too_long"
    | "bad_compatibility" | "extra_fields" | "long_body";
  [arg: string]: string | number;
}

export interface SkillEntry {
  name: string;
  description: string | null;
  sha: string | null;
  updated: string | null;
  error: string | null;
  notes?: StandardNote[];
}

export interface SkillProject {
  key: string;
  task: string;
  title: string | null;
  workspace: string;
  repo: string | null;
  reason: string | null;
  /** "other_machine": the declared repository is a path on another machine. */
  reason_code?: string | null;
  /** Paused or archived: listed only where a copy is installed, so it can be removed. */
  shelved: boolean;
}

export interface FoundSkill {
  name: string;
  description: string | null;
  kind: "workflow" | "importable";
  where: string;
  path: string;
  error: string | null;
  notes?: StandardNote[];
}

export interface SkillsView {
  library: { state: "ok" | "absent" | "unreadable" | "unknown"; root: string | null; reason: string | null; skills: SkillEntry[] };
  projects: SkillProject[];
  installs: Record<string, Record<string, { summary: InstallSummary; copies: { agent: string; state: CopyStateKind; reason: string | null }[] }>>;
  workflow: FoundSkill[];
  importable: FoundSkill[];
  reserved: string[];
  targets: Record<string, string>;
}

export interface SkillContent extends SkillEntry {
  content: string;
  content_sha: string;
  files: string[];
}

/** A listed skill outside the library, with what it says (read-only). */
export interface FoundSkillContent extends FoundSkill {
  content: string;
  files: string[];
}

/** The server's refusal text out of `call`'s error message (a JSON `{code, message}`). */
export function refusalOf(message: string): { code: string; message: string } {
  try {
    const body = JSON.parse(message);
    if (body && typeof body.message === "string") return { code: String(body.code ?? "refused"), message: body.message };
  } catch { /* not JSON */ }
  return { code: "failed", message };
}

/** A new skill's starting text: the front matter every CLI needs. */
export const skillTemplate = (name: string) =>
  `---\nname: ${name}\ndescription: What this skill does and when the agent should use it.\n---\n\n# ${name}\n\n`;

/** Names the library accepts (the Agent Skills standard): lower-case words joined by
 *  single hyphens, at most 64. */
export const SKILL_NAME_RE = /^(?!.*--)[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;

/** One project's skills, for the card's settings (CR-90): the ones it has, each with its
 *  state, and the library skills it could take. A skill the library cannot read is in
 *  neither list: it cannot be installed, and a copy of it shows under `has` only when the
 *  project has one. */
export interface ProjectSkill { name: string; description: string | null; summary: InstallSummary }
export type ProjectSkills =
  | { show: "no_project" }
  | { show: "no_repo"; reason: string | null; elsewhere?: true }
  | { show: "library"; state: "unknown" | "unreadable"; reason: string | null }
  | { show: "lists"; has: ProjectSkill[]; canTake: ProjectSkill[]; libraryEmpty: boolean;
      /** Library skills that cannot be read and are not in this project: not offered,
       *  and not to be mistaken for "already installed" (codex review). */
      unreadable: number };

export function projectSkillsOf(view: SkillsView, key: string): ProjectSkills {
  if (view.library.state === "unknown" || view.library.state === "unreadable") {
    return { show: "library", state: view.library.state, reason: view.library.reason };
  }
  const project = view.projects.find((p) => p.key === key);
  if (!project) return { show: "no_project" };
  if (!project.repo) {
    return project.reason_code === "other_machine"
      ? { show: "no_repo", reason: project.reason, elsewhere: true }
      : { show: "no_repo", reason: project.reason };
  }
  const has: ProjectSkill[] = [];
  const canTake: ProjectSkill[] = [];
  let unreadable = 0;
  for (const skill of view.library.skills) {
    const summary = view.installs[skill.name]?.[key]?.summary ?? "not_installed";
    const row = { name: skill.name, description: skill.description, summary };
    if (summary !== "not_installed") has.push(row);
    else if (skill.error) unreadable += 1;
    else canTake.push(row);
  }
  return { show: "lists", has, canTake, libraryEmpty: view.library.skills.length === 0, unreadable };
}

/** The dropdown's filter: every word typed must be in the name or the description. */
export function matchSkills<T extends { name: string; description: string | null }>(skills: readonly T[], query: string): T[] {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return [...skills];
  return skills.filter((s) => {
    const text = `${s.name} ${s.description ?? ""}`.toLowerCase();
    return words.every((w) => text.includes(w));
  });
}

/** What a button on a project × skill cell does next. */
export function nextAction(summary: InstallSummary | undefined): "install" | "update" | "uninstall" | "force" | "blocked" {
  switch (summary ?? "not_installed") {
    case "not_installed": return "install";
    case "outdated":
    case "partial": return "update";
    case "installed": return "uninstall";
    case "modified": return "force";
    default: return "blocked";
  }
}

/** A skill listed by an outside source (CR-98): a repository of published skills. */
export interface SourceSkill {
  /** Directory inside the source; the skill's id there. */
  path: string;
  name: string;
  description: string | null;
  license: string | null;
  error: string | null;
  notes?: StandardNote[];
}

export interface SkillSource {
  id: string;
  title: string;
  url: string;
  /** `not_fetched`: Agora has no copy yet, which is not an empty source. */
  state: "ok" | "not_fetched" | "unreadable";
  fetched: string | null;
  commit: string | null;
  reason: string | null;
  /** The listing stopped early: what is shown is a part. */
  truncated: boolean;
  skills: SourceSkill[];
}

/** One source skill opened for reading. `sha` is what an import must hand back: the
 *  server takes a skill only as it was shown. */
export interface SourceSkillContent extends SourceSkill {
  source: string;
  content: string;
  sha: string;
  files: { path: string; bytes: number }[];
}

/** Whether a source skill can go into the library, and if not, which reason to show.
 *  `unknown`: the library's own state is not known, so "not there yet" cannot be said. */
export function sourceSkillState(skill: SourceSkill, view: SkillsView | null): "available" | "in_library" | "reserved" | "unreadable" | "unknown" {
  if (skill.error) return "unreadable";
  if (!view || view.library.state === "unknown" || view.library.state === "unreadable") return "unknown";
  if (view.library.skills.some((s) => s.name === skill.name)) return "in_library";
  if (view.reserved.includes(skill.name)) return "reserved";
  return "available";
}

export const formatBytes = (n: number) => (n < 1024 ? `${n} B` : n < 1024 * 1024 ? `${(n / 1024).toFixed(1)} KB` : `${(n / 1024 / 1024).toFixed(1)} MB`);

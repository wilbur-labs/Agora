/** The skills page's types and pure helpers (CR-75 slice 5), kept free of imports so
 *  the node tests can load them. */

export type CopyStateKind = "absent" | "current" | "outdated" | "modified" | "foreign" | "unreadable";
export type InstallSummary = "installed" | "not_installed" | "outdated" | "modified" | "foreign" | "unreadable" | "partial";

export interface SkillEntry {
  name: string;
  description: string | null;
  sha: string | null;
  updated: string | null;
  error: string | null;
}

export interface SkillProject {
  key: string;
  task: string;
  title: string | null;
  workspace: string;
  repo: string | null;
  reason: string | null;
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

/** Names the library accepts: lower-case words joined by hyphens, at most 64. */
export const SKILL_NAME_RE = /^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$/;

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

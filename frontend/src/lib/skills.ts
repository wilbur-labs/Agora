/** The skills page's requests (workspace CR-75 slice 5). Every one carries the terminal
 *  token: they name this machine's projects and write into their repositories. */
import { call } from "@/lib/terminal";
import type { FoundSkillContent, SkillContent, SkillEntry, SkillsView } from "@/lib/skills-state";

export * from "@/lib/skills-state";

export const getSkills = () => call<SkillsView>("/api/skills");
export const getSkill = (name: string) => call<SkillContent>(`/api/skills/library/${encodeURIComponent(name)}`);
export const getFoundSkill = (path: string) => call<FoundSkillContent>(`/api/skills/found?path=${encodeURIComponent(path)}`);
export const createSkill =(name: string, content: string) =>
  call<SkillEntry>("/api/skills/library", { method: "POST", body: JSON.stringify({ name, content }) });
export const updateSkill = (name: string, content: string, base: string) =>
  call<SkillEntry>(`/api/skills/library/${encodeURIComponent(name)}`, { method: "PUT", body: JSON.stringify({ content, base }) });
export const deleteSkill = (name: string) =>
  call<{ deleted: boolean }>(`/api/skills/library/${encodeURIComponent(name)}`, { method: "DELETE" });
export const importSkill = (path: string, name: string) =>
  call<SkillEntry>("/api/skills/import", { method: "POST", body: JSON.stringify({ path, name }) });
export const installSkill = (name: string, project: string, force = false) =>
  call<unknown>(`/api/skills/library/${encodeURIComponent(name)}/install`, { method: "POST", body: JSON.stringify({ project, force }) });
export const uninstallSkill = (name: string, project: string, force = false) =>
  call<unknown>(`/api/skills/library/${encodeURIComponent(name)}/uninstall`, { method: "POST", body: JSON.stringify({ project, force }) });

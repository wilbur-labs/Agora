"use client";

/**
 * One project's skills, inside its overview card's settings (workspace CR-90): the ones
 * it has, and a dropdown with a filter to add one from the library — a list of every
 * skill does not scale as the library grows. Choosing installs at once (it copies files
 * into the project's repository), so nothing here waits for the panel's Save.
 *
 * The library itself — writing, editing, deleting a skill — stays on the Skills page.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { ChevronDown, RefreshCw, X } from "lucide-react";

import { useI18n } from "@/lib/i18n";
import { TerminalAuthRequired } from "@/lib/terminal";
import {
  getSkills, installSkill, matchSkills, nextAction, projectSkillsOf, refusalOf, uninstallSkill,
  type ProjectSkill, type SkillsView,
} from "@/lib/skills";

const TONE: Record<string, string> = {
  installed: "border-emerald-500/50 bg-emerald-500/10 text-emerald-800 dark:text-emerald-300",
  outdated: "border-sky-500/50 bg-sky-500/10 text-sky-800 dark:text-sky-300",
  partial: "border-sky-500/50 bg-sky-500/10 text-sky-800 dark:text-sky-300",
  modified: "border-amber-500/50 bg-amber-500/10 text-amber-800 dark:text-amber-300",
  foreign: "border-red-500/50 bg-red-500/10 text-red-800 dark:text-red-300",
  unreadable: "border-red-500/50 bg-red-500/10 text-red-800 dark:text-red-300",
};

export function ProjectSkills({ projectKey, projectName }: { projectKey: string; projectName: string }) {
  const { t } = useI18n();
  const [view, setView] = useState<SkillsView | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "auth" | "failed">("loading");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const mounted = useRef(true);

  const reload = useCallback(() => {
    getSkills()
      .then((v) => { if (mounted.current) { setView(v); setState("ok"); } })
      .catch((err) => {
        if (!mounted.current) return;
        // What was read before stays on screen, marked as not refreshed, rather than
        // being replaced by nothing.
        setState(err instanceof TerminalAuthRequired ? "auth" : "failed");
      });
  }, []);

  useEffect(() => {
    mounted.current = true;
    reload();
    return () => { mounted.current = false; };
  }, [reload]);

  const act = (name: string, run: () => Promise<unknown>) => {
    setBusy(name);
    setError(null);
    run()
      .catch((err) => { if (mounted.current) setError(refusalOf(String((err as Error).message)).message); })
      .finally(() => { if (mounted.current) { setBusy(null); reload(); } });
  };

  const onChip = (skill: ProjectSkill, action: ReturnType<typeof nextAction>) => {
    if (action === "uninstall") act(skill.name, () => uninstallSkill(skill.name, projectKey));
    else if (action === "update") act(skill.name, () => installSkill(skill.name, projectKey));
    else if (action === "force" && window.confirm(t("skills.confirmForce", { name: skill.name, project: projectName }))) {
      act(skill.name, () => installSkill(skill.name, projectKey, true));
    }
  };

  const skills = view ? projectSkillsOf(view, projectKey) : null;
  const matches = skills?.show === "lists" ? matchSkills(skills.canTake, query) : [];

  return (
    <div className="flex min-w-0 basis-full flex-col gap-1.5">
      <p className="font-medium text-foreground">{t("overview.skills")}</p>
      {state === "loading" && <p className="text-muted-foreground">{t("skills.loading")}</p>}
      {state === "auth" && <p className="text-amber-700 dark:text-amber-400">{t("skills.authRequired")}</p>}
      {state === "failed" && (
        <p className="text-red-700 dark:text-red-400">{view ? t("overview.skillsStale") : t("skills.failed")}</p>
      )}
      {error && <p className="text-red-700 dark:text-red-400">{error}</p>}

      {skills?.show === "no_project" && <p className="text-muted-foreground">{t("overview.skillsNoProject")}</p>}
      {skills?.show === "no_repo" && (
        <p className="text-muted-foreground">
          {t(skills.elsewhere ? "overview.skillsElsewhere" : "overview.skillsNoRepo", { reason: skills.reason ?? "" })}
        </p>
      )}
      {skills?.show === "library" && (
        <p className="text-amber-700 dark:text-amber-400">
          {t(skills.state === "unknown" ? "skills.libraryUnknown" : "skills.libraryUnreadable", { reason: skills.reason ?? "" })}
        </p>
      )}

      {skills?.show === "lists" && (
        <>
          {skills.has.length === 0
            ? <p className="text-muted-foreground">{t("overview.skillsNone")}</p>
            : (
              <div className="flex flex-wrap gap-1.5">
                {skills.has.map((skill) => {
                  const action = nextAction(skill.summary);
                  return (
                    <span key={skill.name} title={skill.description ?? undefined}
                      className={`flex items-center gap-1.5 rounded-md border px-2 py-0.5 ${TONE[skill.summary] ?? ""}`}>
                      <span className="font-mono font-medium">{skill.name}</span>
                      {skill.summary !== "installed" && <span>· {t(`skills.summary.${skill.summary}`)}</span>}
                      {(action === "update" || action === "force") && (
                        <button type="button" disabled={!!busy} onClick={() => onChip(skill, action)}
                          className="flex items-center gap-1 rounded border border-current/30 px-1 hover:bg-current/10 disabled:opacity-50">
                          <RefreshCw className="size-3" /> {t(`skills.action.${action}`)}
                        </button>
                      )}
                      {/* Removing is offered for every copy Agora made; one edited in place
                          is refused by the server and says so, rather than being hidden. */}
                      {action !== "blocked" && (
                        <button type="button" disabled={!!busy} aria-label={t("overview.skillsRemove", { name: skill.name })}
                          title={t("overview.skillsRemove", { name: skill.name })}
                          onClick={() => act(skill.name, () => uninstallSkill(skill.name, projectKey))}
                          className="rounded hover:bg-current/10 disabled:opacity-50">
                          {busy === skill.name ? "…" : <X className="size-3" />}
                        </button>
                      )}
                    </span>
                  );
                })}
              </div>
            )}

          {skills.libraryEmpty ? (
            <p className="text-muted-foreground">
              {t("overview.skillsLibraryEmpty")}{" "}
              <Link href="/skills" className="underline hover:text-foreground">{t("overview.skillsToLibrary")}</Link>
            </p>
          ) : (
            <div className="flex min-w-0 flex-col gap-1">
              <button type="button" aria-expanded={open} disabled={skills.canTake.length === 0}
                onClick={() => { setOpen(!open); setQuery(""); }}
                className="flex items-center justify-between gap-2 rounded border bg-background px-2 py-1 text-left text-foreground hover:bg-accent/50 disabled:opacity-60">
                <span>
                  {skills.canTake.length > 0 ? t("overview.skillsAdd", { count: skills.canTake.length })
                    : skills.unreadable > 0 ? t("overview.skillsNothingToAdd") : t("overview.skillsAllIn")}
                </span>
                <ChevronDown className={`size-3.5 shrink-0 transition-transform ${open ? "rotate-180" : ""}`} />
              </button>
              {open && skills.canTake.length > 0 && (
                <div className="min-w-0 overflow-hidden rounded border bg-background">
                  <input type="text" autoFocus value={query} placeholder={t("overview.skillsFilter")}
                    onChange={(e) => setQuery(e.target.value)}
                    // Escape here closes this list only — not the settings window around it,
                    // which would drop an unsaved name or workflow edit (codex review).
                    onKeyDown={(e) => { if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setOpen(false); } }}
                    className="w-full border-b bg-transparent px-2 py-1 text-foreground outline-none" />
                  <ul className="max-h-48 overflow-auto">
                    {matches.length === 0 && <li className="px-2 py-1 text-muted-foreground">{t("overview.skillsNoMatch")}</li>}
                    {matches.map((skill) => (
                      <li key={skill.name}>
                        <button type="button" disabled={!!busy}
                          onClick={() => act(skill.name, () => installSkill(skill.name, projectKey))}
                          title={skill.description ?? undefined}
                          className="flex w-full min-w-0 flex-col px-2 py-1 text-left hover:bg-accent disabled:opacity-50">
                          <span className="break-all font-mono text-foreground">{skill.name}{busy === skill.name ? " …" : ""}</span>
                          {skill.description && <span className="line-clamp-2 break-words text-muted-foreground">{skill.description}</span>}
                        </button>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {skills.unreadable > 0 && (
                <p className="text-amber-700 dark:text-amber-400">{t("overview.skillsUnreadable", { count: skills.unreadable })}</p>
              )}
              <p className="text-muted-foreground">
                {t("overview.skillsHint")}{" "}
                <Link href="/skills" className="underline hover:text-foreground">{t("overview.skillsToLibrary")}</Link>
              </p>
            </div>
          )}
        </>
      )}
    </div>
  );
}

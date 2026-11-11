"use client";

/**
 * Skills (workspace CR-75 slice 5): the library Agora owns, installed per project.
 *
 * One original per skill in the library (`<work root>/skills/<name>/SKILL.md`), and a
 * copy in each project that installs it — `.claude/skills`, `.agents/skills`,
 * `.kiro/skills`, one per CLI, kept out of the project's git. A copy edited in place,
 * or a directory Agora did not install, is shown as such and never overwritten without
 * the person saying so. The commands a workflow provides are listed, not edited here.
 * Skills found in the CLIs' own places can be imported; the original stays where it is.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Download, Eye, Library, Pencil, Plus, Trash2 } from "lucide-react";

import { DeliveryShell } from "@/components/delivery-shell";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { useI18n } from "@/lib/i18n";
import { TerminalAuthRequired } from "@/lib/terminal";
import {
  createSkill, deleteSkill, getFoundSkill, getSkill, getSkills, importSkill, installSkill, nextAction, refusalOf,
  SKILL_NAME_RE, skillTemplate, uninstallSkill, updateSkill,
  type FoundSkill, type FoundSkillContent, type InstallSummary, type SkillsView, type StandardNote,
} from "@/lib/skills";

type Editor = { mode: "new"; name: string; content: string } | { mode: "edit"; name: string; content: string; base: string };

const SUMMARY_TONE: Record<InstallSummary, string> = {
  installed: "border-emerald-500/50 bg-emerald-500/10 text-emerald-800 dark:text-emerald-300",
  not_installed: "border-border text-muted-foreground",
  outdated: "border-sky-500/50 bg-sky-500/10 text-sky-800 dark:text-sky-300",
  partial: "border-sky-500/50 bg-sky-500/10 text-sky-800 dark:text-sky-300",
  modified: "border-amber-500/50 bg-amber-500/10 text-amber-800 dark:text-amber-300",
  foreign: "border-red-500/50 bg-red-500/10 text-red-800 dark:text-red-300",
  unreadable: "border-red-500/50 bg-red-500/10 text-red-800 dark:text-red-300",
};

export default function SkillsPage() {
  const { t } = useI18n();
  const [view, setView] = useState<SkillsView | null>(null);
  const [state, setState] = useState<"loading" | "ok" | "auth" | "failed">("loading");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [editor, setEditor] = useState<Editor | null>(null);
  const [viewer, setViewer] = useState<FoundSkillContent | null>(null);
  const mounted = useRef(true);

  const reload = useCallback(() => {
    getSkills()
      .then((v) => { if (mounted.current) { setView(v); setState("ok"); } })
      .catch((err) => {
        if (!mounted.current) return;
        setState(err instanceof TerminalAuthRequired ? "auth" : "failed");
      });
  }, []);

  useEffect(() => {
    mounted.current = true;
    reload();
    return () => { mounted.current = false; };
  }, [reload]);

  const act = (key: string, run: () => Promise<unknown>, after?: () => void) => {
    setBusy(key);
    setError(null);
    run()
      .then(() => { if (mounted.current) after?.(); })
      .catch((err) => { if (mounted.current) setError(refusalOf(String((err as Error).message)).message); })
      .finally(() => { if (mounted.current) { setBusy(null); reload(); } });
  };

  const openEdit = (name: string) =>
    act(`edit:${name}`, () => getSkill(name).then((s) => setEditor({ mode: "edit", name, content: s.content, base: s.content_sha })));

  // One listed skill outside the library, open read-only; a second click closes it.
  const toggleView = (path: string) => {
    if (viewer?.path === path) { setViewer(null); return; }
    act(`view:${path}`, () => getFoundSkill(path).then((s) => { if (mounted.current) setViewer(s); }));
  };

  const viewButton = (f: FoundSkill) => (
    <Button size="sm" variant="outline" disabled={!!busy || !!f.error} onClick={() => toggleView(f.path)}>
      <Eye className="size-3.5" /> {viewer?.path === f.path ? t("skills.hide") : t("skills.view")}
    </Button>
  );

  const viewPanel = (f: FoundSkill) => viewer?.path === f.path && (
    <div className="space-y-2 pt-2">
      <p className="text-xs text-muted-foreground">
        {t(f.kind === "workflow" ? "skills.viewWorkflow" : "skills.viewImportable", { path: viewer.path })}
      </p>
      {viewer.files.length > 1 && (
        <p className="text-xs text-muted-foreground">{t("skills.viewFiles", { files: viewer.files.join(", ") })}</p>
      )}
      <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-lg border bg-muted/40 p-3 font-mono text-xs">{viewer.content}</pre>
    </div>
  );

  // What the standard's checks found, under a skill. Other CLIs ignore fields they do
  // not know, so an extra field is information; the rest are things to fix.
  const standardNotes = (notes: StandardNote[] | undefined) => !!notes?.length && (
    <ul className="text-xs text-amber-700 dark:text-amber-400">
      {notes.map((n) => <li key={n.code}>{t(`skills.note.${n.code}`, n)}</li>)}
    </ul>
  );

  const save = () => {
    if (!editor) return;
    act("save", () => editor.mode === "new" ? createSkill(editor.name, editor.content)
      : updateSkill(editor.name, editor.content, editor.base), () => setEditor(null));
  };

  const projects = view?.projects ?? [];
  const usable = projects.filter((p) => p.repo);
  const skills = view?.library.skills ?? [];
  const newNameOk = editor?.mode === "new" && SKILL_NAME_RE.test(editor.name) && !view?.reserved.includes(editor.name);

  return (
    <DeliveryShell active="Skills">
      <div className="mx-auto max-w-6xl space-y-6 p-6">
        <header className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1">
            <h1 className="flex items-center gap-2 text-2xl font-bold">
              <Library className="size-6" />
              {t("skills.title")}
            </h1>
            <p className="text-sm text-muted-foreground">{t("skills.subtitle")}</p>
            {view?.library.root && (
              <p className="text-xs text-muted-foreground">{t("skills.libraryAt", { path: view.library.root })}</p>
            )}
          </div>
          <Button size="sm" disabled={!view || view.library.state === "unknown" || view.library.state === "unreadable"}
            onClick={() => setEditor({ mode: "new", name: "", content: skillTemplate("new-skill") })}>
            <Plus className="size-4" /> {t("skills.new")}
          </Button>
        </header>

        {state === "loading" && <p className="text-sm text-muted-foreground">{t("skills.loading")}</p>}
        {state === "auth" && <p className="text-sm text-amber-700 dark:text-amber-400">{t("skills.authRequired")}</p>}
        {state === "failed" && <p className="text-sm text-red-700 dark:text-red-400">{t("skills.failed")}</p>}
        {error && (
          <p className="flex items-start gap-2 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm text-red-800 dark:text-red-300">
            <AlertTriangle className="mt-0.5 size-4 shrink-0" /> {error}
          </p>
        )}

        {view && view.library.state === "unknown" && (
          <p className="text-sm text-amber-700 dark:text-amber-400">{t("skills.libraryUnknown", { reason: view.library.reason ?? "" })}</p>
        )}
        {view && view.library.state === "unreadable" && (
          <p className="text-sm text-red-700 dark:text-red-400">{t("skills.libraryUnreadable", { reason: view.library.reason ?? "" })}</p>
        )}

        {editor && (
          <section className="space-y-3 rounded-xl border bg-card p-4">
            <h2 className="text-sm font-semibold">
              {editor.mode === "new" ? t("skills.editor.newTitle") : t("skills.editor.editTitle", { name: editor.name })}
            </h2>
            {editor.mode === "new" && (
              <div className="space-y-1">
                <input
                  className="w-full rounded-lg border bg-transparent px-2.5 py-1.5 font-mono text-sm"
                  placeholder="my-skill" value={editor.name}
                  onChange={(e) => {
                    const name = e.target.value.trim();
                    // Keep the front matter's name in step while it still says the old one.
                    setEditor({ ...editor, name, content: editor.content.replace(/^name: .*$/m, `name: ${name}`) });
                  }}
                />
                <p className="text-xs text-muted-foreground">
                  {editor.name && view?.reserved.includes(editor.name) ? t("skills.editor.reserved") : t("skills.editor.nameRule")}
                </p>
              </div>
            )}
            <Textarea className="min-h-80 font-mono text-xs" value={editor.content}
              onChange={(e) => setEditor({ ...editor, content: e.target.value })} />
            <p className="text-xs text-muted-foreground">{t("skills.editor.hint")}</p>
            <div className="flex gap-2">
              <Button size="sm" disabled={busy === "save" || (editor.mode === "new" && !newNameOk)} onClick={save}>
                {t("skills.editor.save")}
              </Button>
              <Button size="sm" variant="outline" onClick={() => setEditor(null)}>{t("skills.editor.cancel")}</Button>
            </div>
          </section>
        )}

        {view && view.library.state !== "unknown" && view.library.state !== "unreadable" && (
          <section className="space-y-3">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">{t("skills.library")}</h2>
            {skills.length === 0 && <p className="text-sm text-muted-foreground">{t("skills.empty")}</p>}
            {skills.length > 0 && usable.length === 0 && (
              <p className="text-sm text-muted-foreground">{t("skills.noProjects")}</p>
            )}
            {skills.map((skill) => (
              <div key={skill.name} className="space-y-3 rounded-lg border bg-card p-4">
                <div className="flex flex-wrap items-start justify-between gap-2">
                  <div className="min-w-0 space-y-1">
                    <h3 className="font-mono text-sm font-semibold">{skill.name}</h3>
                    {skill.error
                      ? <p className="text-sm text-red-700 dark:text-red-400">{t("skills.entryError", { reason: skill.error })}</p>
                      : <p className="text-sm text-muted-foreground">{skill.description}</p>}
                    {standardNotes(skill.notes)}
                    {skill.updated && <p className="text-xs text-muted-foreground">{t("skills.updated", { when: new Date(skill.updated).toLocaleString() })}</p>}
                  </div>
                  <div className="flex gap-1">
                    <Button size="sm" variant="outline" disabled={!!busy || !!skill.error} onClick={() => openEdit(skill.name)}>
                      <Pencil className="size-3.5" /> {t("skills.edit")}
                    </Button>
                    <Button size="sm" variant="outline" disabled={!!busy}
                      onClick={() => { if (window.confirm(t("skills.confirmDelete", { name: skill.name }))) act(`del:${skill.name}`, () => deleteSkill(skill.name)); }}>
                      <Trash2 className="size-3.5" /> {t("skills.delete")}
                    </Button>
                  </div>
                </div>
                {usable.length > 0 && !skill.error && (
                  <div className="flex flex-wrap gap-2">
                    {usable.map((p) => {
                      const cell = view.installs[skill.name]?.[p.key];
                      const summary = cell?.summary ?? "not_installed";
                      // Only where a copy is: choosing a project's skills is done in its
                      // overview card's settings (CR-90). What stays here is seeing where a
                      // skill is and updating or removing a copy — including in a paused
                      // or archived project, which has no card to do it from.
                      if (summary === "not_installed") return null;
                      const action = nextAction(summary);
                      const key = `${skill.name}:${p.key}`;
                      const detail = cell?.copies.filter((c) => c.state !== "current")
                        .map((c) => `${view.targets[c.agent]}: ${t(`skills.copy.${c.state}`)}`).join("\n");
                      return (
                        <div key={p.key} title={detail || undefined}
                          className={`flex items-center gap-2 rounded-md border px-2 py-1 text-xs ${SUMMARY_TONE[summary]}`}>
                          <span className="font-medium">{p.title || p.task}{p.shelved ? ` · ${t("skills.shelved")}` : ""}</span>
                          <span>· {t(`skills.summary.${summary}`)}</span>
                          {action !== "blocked" && (
                            <button type="button" disabled={!!busy}
                              className="rounded border border-current/30 px-1.5 hover:bg-current/10 disabled:opacity-50"
                              onClick={() => {
                                if (action === "uninstall") act(key, () => uninstallSkill(skill.name, p.key));
                                else if (action === "force") {
                                  if (window.confirm(t("skills.confirmForce", { name: skill.name, project: p.task })))
                                    act(key, () => installSkill(skill.name, p.key, true));
                                } else act(key, () => installSkill(skill.name, p.key));
                              }}>
                              {busy === key ? "…" : t(`skills.action.${action}`)}
                            </button>
                          )}
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            ))}
            {projects.some((p) => !p.repo) && (
              <details className="text-xs text-muted-foreground">
                <summary>{t("skills.projectsWithout", { count: projects.filter((p) => !p.repo).length })}</summary>
                <ul className="mt-1 space-y-0.5 pl-4">
                  {projects.filter((p) => !p.repo).map((p) => <li key={p.key}>{p.title || p.task}: {p.reason}</li>)}
                </ul>
              </details>
            )}
            <p className="text-xs text-muted-foreground">{t("skills.installFromCard")}</p>
            <p className="text-xs text-muted-foreground">{t("skills.whereCopies")}</p>
          </section>
        )}

        {view && view.importable.length > 0 && view.library.state !== "unknown" && (
          <details className="space-y-2">
            <summary className="cursor-pointer text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              {t("skills.importable")} ({view.importable.length})
            </summary>
            <p className="pt-2 text-xs text-muted-foreground">{t("skills.importHint")}</p>
            <div className="divide-y rounded-lg border bg-card">
              {view.importable.map((f) => {
                const taken = skills.some((s) => s.name === f.name) || view.reserved.includes(f.name);
                return (
                  <div key={f.path} className="px-4 py-2">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="font-mono text-sm">{f.name} <span className="text-xs text-muted-foreground">· {f.where}</span></p>
                        <p className="truncate text-xs text-muted-foreground">{f.error ? t("skills.entryError", { reason: f.error }) : f.description}</p>
                        {standardNotes(f.notes)}
                      </div>
                      <div className="flex shrink-0 gap-1">
                        {viewButton(f)}
                        <Button size="sm" variant="outline" disabled={!!busy || taken || !!f.error}
                          title={taken ? t("skills.importTaken") : undefined}
                          onClick={() => act(`imp:${f.path}`, () => importSkill(f.path, f.name))}>
                          <Download className="size-3.5" /> {t("skills.import")}
                        </Button>
                      </div>
                    </div>
                    {viewPanel(f)}
                  </div>
                );
              })}
            </div>
          </details>
        )}

        {view && view.workflow.length > 0 && (
          <section className="space-y-2">
            <h2 className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">{t("skills.workflow")}</h2>
            <p className="text-xs text-muted-foreground">{t("skills.workflowHint")}</p>
            <div className="divide-y rounded-lg border bg-card">
              {view.workflow.map((f) => (
                <div key={f.path} className="px-4 py-2">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <p className="font-mono text-sm">{f.name} <span className="text-xs text-muted-foreground">· {f.where}</span></p>
                      <p className="truncate text-xs text-muted-foreground">{f.error ? t("skills.entryError", { reason: f.error }) : f.description}</p>
                    </div>
                    <div className="shrink-0">{viewButton(f)}</div>
                  </div>
                  {viewPanel(f)}
                </div>
              ))}
            </div>
          </section>
        )}

        <p className="text-xs text-muted-foreground">{t("skills.notYet")}</p>
      </div>
    </DeliveryShell>
  );
}

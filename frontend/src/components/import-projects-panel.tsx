"use client";

/**
 * "Import projects from your workspaces" (CR-52): the repositories your claude-workspace
 * and kiro-workspace task records name, registered as Agora projects where they are.
 *
 * Judgement lives in `lib/project-import.ts`; this only renders. Registering never
 * creates, initialises or commits anything in your repository — the server adds Agora's
 * own empty `.agora/` folders and a local `.git/info/exclude` line, nothing else.
 */
import { useEffect, useMemo, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  ApiError,
  listImportCandidates,
  registerExistingProject,
  type ImportCandidate,
  type ImportCandidates,
} from "@/lib/control-plane";
import { emptyImportNoteOf, importGroupsOf, statusKeyOf, tasksLabelOf } from "@/lib/project-import";
import { useI18n } from "@/lib/i18n";

type Outcome = { ok: true } | { ok: false; message: string };

export function ImportProjectsPanel({ onClose, onImported }: { onClose: () => void; onImported: () => Promise<void> }) {
  const { t } = useI18n();
  const [read, setRead] = useState<ImportCandidates | "loading" | "failed">("loading");
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [outcomes, setOutcomes] = useState<Record<string, Outcome>>({});
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    listImportCandidates()
      .then((data) => {
        if (!alive) return;
        setRead(data);
        // Everything importable starts ticked: the common case is "bring them all".
        setChosen(new Set(importGroupsOf(data.candidates).importable.map((c) => c.root as string)));
      })
      .catch(() => { if (alive) setRead("failed"); });
    return () => { alive = false; };
  }, []);

  const groups = useMemo(() => (typeof read === "object" ? importGroupsOf(read.candidates) : null), [read]);

  async function importChosen() {
    if (!groups) return;
    setBusy(true);
    const next: Record<string, Outcome> = { ...outcomes };
    for (const c of groups.importable) {
      if (!c.root || !chosen.has(c.root) || next[c.root]?.ok) continue;
      try {
        await registerExistingProject(c.suggested_id as string, c.root, c.suggested_name ?? undefined);
        next[c.root] = { ok: true };
      } catch (err) {
        next[c.root] = { ok: false, message: err instanceof ApiError ? err.message : String(err) };
      }
      setOutcomes({ ...next });
    }
    setBusy(false);
    await onImported();
  }

  const toggle = (root: string) => setChosen((prev) => {
    const set = new Set(prev);
    if (set.has(root)) set.delete(root); else set.add(root);
    return set;
  });

  return (
    <div className="fixed inset-0 z-50 flex justify-end bg-black/35" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div role="dialog" aria-modal="true" aria-labelledby="import-projects-title"
        className="h-full w-full max-w-2xl space-y-5 overflow-y-auto border-l bg-background p-6 shadow-2xl">
        <div>
          <h2 id="import-projects-title" className="text-lg font-semibold">{t("portfolio.import.title")}</h2>
          <p className="mt-1 text-sm text-muted-foreground">{t("portfolio.import.body")}</p>
        </div>

        {read === "loading" && <p className="text-sm text-muted-foreground">{t("portfolio.import.loading")}</p>}
        {read === "failed" && <p className="text-sm text-red-700 dark:text-red-400">{t("portfolio.import.failed")}</p>}

        {groups && (
          <>
            <Section title={t("portfolio.import.importable", { count: groups.importable.length })}>
              {groups.importable.length === 0 && typeof read === "object" && (
                emptyImportNoteOf(read) === "noSources" ? (
                  <p className="text-sm text-muted-foreground">
                    {t("portfolio.import.noSources")}{" "}
                    <a href="/settings#work-sources" className="underline underline-offset-2">{t("portfolio.import.noSourcesLink")}</a>
                  </p>
                ) : (
                  <p className="text-sm text-muted-foreground">{t(`portfolio.import.${emptyImportNoteOf(read)}`)}</p>
                )
              )}
              {groups.importable.map((c) => (
                <label key={c.root} className="flex items-start gap-3 rounded-md border px-3 py-2 text-sm">
                  <input type="checkbox" className="mt-1" checked={chosen.has(c.root as string)}
                    disabled={busy || outcomes[c.root as string]?.ok} onChange={() => toggle(c.root as string)} />
                  <span className="min-w-0 flex-1">
                    <span className="font-medium">{c.suggested_name}</span>
                    <span className="ml-2 font-mono text-xs text-muted-foreground">{c.suggested_id}</span>
                    <span className="block truncate font-mono text-xs text-muted-foreground">{c.root}</span>
                    <span className="block text-xs text-muted-foreground">{t("portfolio.import.from", { tasks: tasksLabelOf(c) })}</span>
                    {outcomes[c.root as string] && (
                      <span className={outcomes[c.root as string].ok ? "block text-xs text-emerald-700 dark:text-emerald-400" : "block text-xs text-red-700 dark:text-red-400"}>
                        {outcomes[c.root as string].ok ? t("portfolio.import.done")
                          : t("portfolio.import.error", { message: (outcomes[c.root as string] as { message: string }).message })}
                      </span>
                    )}
                  </span>
                </label>
              ))}
            </Section>

            {groups.registered.length > 0 && (
              <Section title={t("portfolio.import.registered", { count: groups.registered.length })}>
                {groups.registered.map((c) => <Row key={c.root} c={c} note={t("portfolio.import.registeredAs", { id: c.registered_as ?? "" })} />)}
              </Section>
            )}
            {groups.blocked.length > 0 && (
              <Section title={t("portfolio.import.blocked", { count: groups.blocked.length })}>
                {groups.blocked.map((c) => <Row key={`${c.root}-${c.status}`} c={c} note={t(statusKeyOf(c.status))} />)}
              </Section>
            )}
            {groups.elsewhere.length > 0 && (
              <details className="rounded-md border px-3 py-2">
                <summary className="cursor-pointer text-sm font-medium">{t("portfolio.import.elsewhere", { count: groups.elsewhere.length })}</summary>
                <div className="mt-2 space-y-2">
                  {groups.elsewhere.map((c, i) => <Row key={`${c.raw_path}-${i}`} c={c} note={t(statusKeyOf(c.status))} />)}
                </div>
              </details>
            )}
            {typeof read === "object" && read.unreadable.length > 0 && (
              <p className="text-xs text-amber-700 dark:text-amber-400">
                {t("portfolio.import.unreadable", { tasks: read.unreadable.map((u) => u.task || u.source).join(", ") })}
              </p>
            )}
          </>
        )}

        <div className="flex gap-2">
          <Button type="button" disabled={busy || !groups || chosen.size === 0} onClick={() => void importChosen()}>
            {busy ? t("portfolio.import.importing") : t("portfolio.import.submit", { count: chosen.size })}
          </Button>
          <Button type="button" variant="outline" onClick={onClose} disabled={busy}>{t("portfolio.import.close")}</Button>
        </div>
      </div>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="space-y-2">
      <h3 className="text-sm font-semibold">{title}</h3>
      {children}
    </section>
  );
}

function Row({ c, note }: { c: ImportCandidate; note: string }) {
  return (
    <div className="rounded-md border border-dashed px-3 py-2 text-sm">
      <span className="block truncate font-mono text-xs">{c.root ?? c.raw_path}</span>
      <span className="block text-xs text-muted-foreground">{note} · {tasksLabelOf(c)}</span>
    </div>
  );
}

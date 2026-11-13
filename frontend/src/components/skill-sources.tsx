"use client";

/**
 * Skills from outside (workspace CR-98): a published repository of skills, listed here,
 * read before anything is taken, and copied into the library one at a time.
 *
 * The listing comes from a copy Agora keeps; only "fetch" reaches the network. A skill
 * can be imported only from its open panel: the server asks for the hash of what that
 * panel showed, so there is no way to take one unseen. Installing into a project is
 * still done from the project's overview card — importing only fills the library.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { AlertTriangle, Download, Eye, Globe, RefreshCw, Sparkles } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import {
  fetchSkillSource, formatBytes, getSkillSources, getSourceSkill, getSourceSkillSummary, importSourceSkill, matchSkills,
  refusalOf, sourceSkillState, summaryLineOf, type SkillSource, type SkillsView, type SourceSkillContent,
  type SourceSkillSummary,
} from "@/lib/skills";

export function SkillSources({ view, onImported }: { view: SkillsView | null; onImported: () => void }) {
  const { t, locale } = useI18n();
  const [sources, setSources] = useState<SkillSource[] | null>(null);
  const [failed, setFailed] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState<SourceSkillContent | null>(null);
  const [imported, setImported] = useState<string | null>(null);
  const [summary, setSummary] = useState<{ key: string; result?: SourceSkillSummary; error?: string } | null>(null);
  const mounted = useRef(true);

  // The open skill, said in one sentence by a model, in the page's language. Asked when
  // a skill is opened and again when the language changes; the key ties an answer to
  // the skill, content and language it was asked for, so a late one is not shown.
  const summaryKey = open ? `${open.source}\n${open.path}\n${open.sha}\n${locale}` : null;
  useEffect(() => {
    if (!open || !summaryKey) return;
    let current = true;
    getSourceSkillSummary(open.source, open.path, locale)
      .then((result) => { if (current) setSummary({ key: summaryKey, result }); })
      .catch((err) => { if (current) setSummary({ key: summaryKey, error: refusalOf(String((err as Error).message)).message }); });
    return () => { current = false; };
  }, [open, summaryKey, locale]);

  const reload = useCallback(() => {
    getSkillSources()
      .then((r) => { if (mounted.current) { setSources(r.sources); setFailed(false); } })
      .catch(() => { if (mounted.current) setFailed(true); });
  }, []);

  useEffect(() => {
    mounted.current = true;
    reload();
    return () => { mounted.current = false; };
  }, [reload]);

  const act = (key: string, run: () => Promise<unknown>) => {
    setBusy(key);
    setError(null);
    run()
      .catch((err) => { if (mounted.current) setError(refusalOf(String((err as Error).message)).message); })
      .finally(() => { if (mounted.current) setBusy(null); });
  };

  const fetchSource = (id: string) => act(`fetch:${id}`, () => fetchSkillSource(id).then((fresh) => {
    if (!mounted.current) return;
    // What was open may not be what the new copy holds: it has to be opened again.
    setOpen(null);
    setSources((old) => old?.map((s) => (s.id === id ? fresh : s)) ?? [fresh]);
  }));

  const toggle = (id: string, path: string) => {
    if (open?.source === id && open.path === path) { setOpen(null); return; }
    setImported(null);
    act(`view:${id}:${path}`, () => getSourceSkill(id, path).then((c) => { if (mounted.current) setOpen(c); }));
  };

  const take = (shown: SourceSkillContent) => act(`import:${shown.path}`, () =>
    importSourceSkill(shown.source, shown.path, shown.sha).then(() => {
      if (!mounted.current) return;
      setImported(shown.name);
      onImported();
    }));

  return (
    <section className="space-y-3">
      <h2 className="flex items-center gap-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
        <Globe className="size-4" /> {t("skills.sources.title")}
      </h2>
      <p className="text-xs text-muted-foreground">{t("skills.sources.hint")}</p>
      {failed && <p className="text-sm text-red-700 dark:text-red-400">{t("skills.sources.failed")}</p>}
      {error && (
        <p className="flex items-start gap-2 rounded-lg border border-red-500/40 bg-red-500/10 px-4 py-2 text-sm text-red-800 dark:text-red-300">
          <AlertTriangle className="mt-0.5 size-4 shrink-0" /> <span className="min-w-0 break-words">{error}</span>
        </p>
      )}
      {sources?.map((source) => {
        const matches = matchSkills(source.skills, query);
        const fetching = busy === `fetch:${source.id}`;
        return (
          <div key={source.id} className="space-y-3 rounded-lg border bg-card p-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="min-w-0 space-y-1">
                <h3 className="text-sm font-semibold">{source.title}</h3>
                <p className="break-all font-mono text-xs text-muted-foreground">{source.url}</p>
                <p className="text-xs text-muted-foreground">
                  {source.state === "not_fetched" && t("skills.sources.notFetched")}
                  {source.state === "unreadable" && t("skills.sources.unreadable", { reason: source.reason ?? "" })}
                  {source.state === "ok" && t("skills.sources.fetched", {
                    count: source.skills.length,
                    when: source.fetched ? new Date(source.fetched).toLocaleString() : "?",
                    commit: source.commit ? source.commit.slice(0, 7) : "?",
                  })}
                </p>
                {source.truncated && <p className="text-xs text-amber-700 dark:text-amber-400">{t("skills.sources.truncated")}</p>}
              </div>
              <Button size="sm" variant="outline" disabled={!!busy} onClick={() => fetchSource(source.id)}>
                <RefreshCw className={`size-3.5 ${fetching ? "animate-spin" : ""}`} />
                {fetching ? t("skills.sources.fetching") : t(source.state === "ok" ? "skills.sources.refetch" : "skills.sources.fetch")}
              </Button>
            </div>
            {source.state === "ok" && (
              <>
                <input
                  className="w-full rounded-lg border bg-transparent px-2.5 py-1.5 text-sm"
                  placeholder={t("skills.sources.filter")} value={query} onChange={(e) => setQuery(e.target.value)}
                />
                {matches.length === 0 && <p className="text-sm text-muted-foreground">{t("skills.sources.noMatch")}</p>}
                <div className="divide-y rounded-lg border">
                  {matches.map((skill) => {
                    const shown = open?.source === source.id && open.path === skill.path ? open : null;
                    const state = sourceSkillState(skill, view);
                    return (
                      <div key={skill.path} className="px-4 py-2">
                        <div className="flex items-start justify-between gap-3">
                          <div className="min-w-0 space-y-0.5">
                            <p className="break-all font-mono text-sm">
                              {skill.name} <span className="text-xs text-muted-foreground">· {skill.path}</span>
                              {state === "in_library" && (
                                <span className="ml-2 rounded border border-emerald-500/50 bg-emerald-500/10 px-1.5 text-xs text-emerald-800 dark:text-emerald-300">
                                  {t("skills.sources.inLibrary")}
                                </span>
                              )}
                            </p>
                            <p className="line-clamp-2 break-words text-xs text-muted-foreground" title={skill.description ?? undefined}>
                              {skill.error ? t("skills.entryError", { reason: skill.error }) : skill.description}
                            </p>
                            {skill.license && <p className="break-words text-xs text-muted-foreground">{t("skills.sources.license", { license: skill.license })}</p>}
                            {!!skill.notes?.length && (
                              <ul className="text-xs text-amber-700 dark:text-amber-400">
                                {skill.notes.map((n) => <li key={n.code}>{t(`skills.note.${n.code}`, n)}</li>)}
                              </ul>
                            )}
                          </div>
                          <Button size="sm" variant="outline" className="shrink-0" disabled={!!busy || !!skill.error}
                            onClick={() => toggle(source.id, skill.path)}>
                            <Eye className="size-3.5" /> {shown ? t("skills.hide") : t("skills.view")}
                          </Button>
                        </div>
                        {shown && (
                          <div className="space-y-2 pt-2">
                            <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
                              {t("skills.sources.readFirst")}
                            </p>
                            {(() => {
                              const line = summaryLineOf(summary, summaryKey ?? "", shown.sha, locale);
                              return (
                                <div className="rounded-lg border bg-muted/40 px-3 py-2 text-sm">
                                  <p className="flex items-start gap-2">
                                    <Sparkles className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                                    <span className="min-w-0 break-words">
                                      {line.show === "loading" && <span className="text-muted-foreground">{t("skills.sources.summaryLoading")}</span>}
                                      {line.show === "failed" && <span className="text-muted-foreground">{t("skills.sources.summaryFailed", { reason: line.reason })}</span>}
                                      {line.show === "text" && line.text}
                                    </span>
                                  </p>
                                  {line.show === "text" && <p className="pl-6 pt-1 text-xs text-muted-foreground">{t("skills.sources.summaryBy")}</p>}
                                </div>
                              );
                            })()}
                            <details className="text-xs text-muted-foreground" open={shown.files.length <= 12}>
                              <summary className="cursor-pointer">
                                {t("skills.sources.files", { count: shown.files.length, size: formatBytes(shown.files.reduce((a, f) => a + f.bytes, 0)) })}
                              </summary>
                              <ul className="mt-1 max-h-48 space-y-0.5 overflow-auto pl-4 font-mono">
                                {shown.files.map((f) => <li key={f.path} className="break-all">{f.path} · {formatBytes(f.bytes)}</li>)}
                              </ul>
                            </details>
                            <pre className="max-h-96 overflow-auto whitespace-pre-wrap break-words rounded-lg border bg-muted/40 p-3 font-mono text-xs">{shown.content}</pre>
                            <div className="flex flex-wrap items-center gap-2">
                              <Button size="sm" disabled={!!busy || state !== "available"} onClick={() => take(shown)}>
                                <Download className="size-3.5" /> {t("skills.sources.import")}
                              </Button>
                              <span className="text-xs text-muted-foreground">
                                {imported === skill.name ? t("skills.sources.imported")
                                  : state === "in_library" ? t("skills.sources.inLibraryHint")
                                  : state === "reserved" ? t("skills.importTaken")
                                  : state === "unknown" ? t("skills.sources.libraryUnknown")
                                  : t("skills.sources.importHint")}
                              </span>
                            </div>
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>
              </>
            )}
          </div>
        );
      })}
      <p className="text-xs text-muted-foreground">{t("skills.sources.limits")}</p>
    </section>
  );
}

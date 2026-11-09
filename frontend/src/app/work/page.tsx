"use client";

/**
 * The work page (workspace CR-75 slice 1): the projects on the left, one project's
 * terminal filling the right. Clicking a project opens its terminal or switches to it;
 * the others keep running. On the first visit after a restart the server brings back
 * what was open, and the banner says, per project, what the reports support — a
 * started terminal is not a resumed conversation. Design: docs/CR75-slice1-work-page.md.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";

import { TopBar } from "@/components/delivery-shell";
import { TerminalView } from "@/components/terminal-view";
import { Button } from "@/components/ui/button";
import { TerminalAuthRequired } from "@/lib/terminal";
import { GRAPE, TERMINAL_CHROME } from "@/lib/terminal-theme";
import {
  bannerLinesOf,
  bannerSettled,
  closeWorkProject,
  listWorkProjects,
  moveKey,
  openWorkProject,
  orderedRows,
  projectStateOf,
  restoreWorkProjects,
  setWorkOrder,
  workListOf,
  type BannerLine,
  type ProjectState,
  type WorkView,
  WORK_SELECTED_KEY as SELECTED_KEY,
} from "@/lib/work";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const POLL_MS = 5000;
const RESTORE_BACKOFF_MIN_MS = 5000;
const RESTORE_BACKOFF_MAX_MS = 60000;

/** The list with each project's position replaced by the one being saved. */
function withOrder(view: WorkView, position: ReadonlyMap<string, number>): WorkView {
  return { ...view, projects: view.projects.map((row) => ({ ...row, position: position.get(row.key) ?? row.position })) };
}

function readSelected(): string | null {
  try { return window.localStorage.getItem(SELECTED_KEY); } catch { return null; }
}

function writeSelected(key: string): void {
  try { window.localStorage.setItem(SELECTED_KEY, key); } catch { /* a convenience only */ }
}

const DOT: Record<ProjectState, string> = {
  starting: "bg-sky-600",
  open: "bg-emerald-600",
  agentExited: "bg-amber-600",
  ended: "border border-muted-foreground bg-transparent",
  notOpen: "border border-muted-foreground/60 bg-transparent",
};

const TONE: Record<BannerLine["tone"], string> = {
  good: "text-emerald-700 dark:text-emerald-400",
  warn: "text-amber-700 dark:text-amber-400",
  bad: "text-red-700 dark:text-red-400",
  pending: "text-muted-foreground",
};

export default function WorkPage() {
  const { t } = useI18n();
  const [view, setView] = useState<WorkView | "auth" | "failed" | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [bannerHidden, setBannerHidden] = useState(false);
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const [listFailed, setListFailed] = useState(false);

  // A slow older answer must not overwrite a newer one already shown — but every answer
  // newer than what is shown is used, so scans slower than the poll still land.
  const sequence = useRef(0);
  const shown = useRef(0);
  // A restore request in flight. Whenever the server says it has no restore answer — the
  // first load, a 401 before the launcher's code was redeemed, a server restarted under an
  // open page — it is asked; it restores at most once per run whatever we send.
  const restoring = useRef(false);
  // A failed restore is asked again no sooner than this, the wait doubling each time: a
  // server that keeps refusing must not be met with a request loop (codex review).
  const restoreRetryAt = useRef(0);
  const restoreBackoff = useRef(RESTORE_BACKOFF_MIN_MS);
  // Once the page is gone nothing more is sent, a retry already scheduled included.
  const mounted = useRef(true);
  // The order being saved (CR-82), applied over any list read until the save has ended.
  const pendingOrder = useRef<Map<string, number> | null>(null);

  const load = useCallback(() => {
    if (!mounted.current) return Promise.resolve();
    const mine = ++sequence.current;
    return listWorkProjects()
      .then((next) => {
        // An answer arriving after the page is gone starts nothing (codex review).
        if (!mounted.current || mine < shown.current) return;
        shown.current = mine;
        setView(pendingOrder.current ? withOrder(next, pendingOrder.current) : next);
        setListFailed(false);
        // The server has restored, whatever became of our own request's answer (kiro review).
        if (next.restore !== null) setRestoreError(null);
        if (next.restore === null && !next.restoring && !restoring.current
            && Date.now() >= restoreRetryAt.current) {
          restoring.current = true;
          void restoreWorkProjects()
            .then(() => {
              restoreBackoff.current = RESTORE_BACKOFF_MIN_MS;
              restoreRetryAt.current = 0;
              if (mounted.current) setRestoreError(null);
            })
            .catch((err) => {
              // The poll asks again once the wait is over.
              restoreRetryAt.current = Date.now() + restoreBackoff.current;
              restoreBackoff.current = Math.min(restoreBackoff.current * 2, RESTORE_BACKOFF_MAX_MS);
              if (mounted.current && !(err instanceof TerminalAuthRequired)) {
                setRestoreError(String((err as Error).message));
              }
            })
            .finally(() => { restoring.current = false; void load(); });
        }
      })
      .catch((err) => {
        if (!mounted.current || mine < shown.current) return;
        shown.current = mine;
        if (err instanceof TerminalAuthRequired) { setView("auth"); return; }
        // A list already shown stays, terminals included: one failed poll is not a reason
        // to drop a working terminal (kiro review round 12). It is marked out of date.
        setView((prev) => (typeof prev === "object" && prev ? prev : "failed"));
        setListFailed(true);
      });
  }, []);

  useEffect(() => {
    mounted.current = true;
    setSelected(readSelected());
    void load();
    return () => { mounted.current = false; };
  }, [load]);

  useEffect(() => {
    // Keep watching while the page is visible: a late report, a terminal that ended in the
    // background or claude quitting all change what the page should say.
    const tick = () => { if (document.visibilityState === "visible") void load(); };
    const timer = window.setInterval(tick, POLL_MS);
    document.addEventListener("visibilitychange", tick);
    return () => { window.clearInterval(timer); document.removeEventListener("visibilitychange", tick); };
  }, [load]);

  const rows = useMemo(() => (typeof view === "object" && view ? view.projects : []), [view]);
  // Only what is doing (CR-81), in the person's order (CR-82).
  const listed = useMemo(() => workListOf(rows), [rows]);
  const todoCount = rows.length - listed.length;
  const current = listed.find((row) => row.key === selected) ?? null;
  const [dragged, setDragged] = useState<string | null>(null);
  const [dropBefore, setDropBefore] = useState<string | null>(null);
  const [savingOrder, setSavingOrder] = useState(false);
  // Shown above the list, whichever project is selected (codex review CR-82 round 3).
  const [orderError, setOrderError] = useState<string | null>(null);

  // The new order is shown at once and sent. One save at a time — dragging waits for it —
  // and a list read landing meanwhile is shown in that order, not the one it read before
  // the save (codex review CR-82 round 2). After the save, the next read is the truth.
  const reorder = useCallback((moved: string, before: string | null) => {
    if (typeof view !== "object" || !view || pendingOrder.current) return;
    const keys = moveKey(orderedRows(view.projects).map((row) => row.key), moved, before);
    const position = new Map(keys.map((key, index) => [key, index]));
    pendingOrder.current = position;
    setSavingOrder(true);
    setView(withOrder(view, position));
    setOrderError(null);
    setWorkOrder(keys)
      .catch((err) => { if (mounted.current) setOrderError(t("work.orderFailed", { detail: String((err as Error).message) })); })
      .finally(() => {
        // Every read sent before this point read the old order: none of them may land
        // once the overlay is gone (codex review CR-82 round 3).
        shown.current = ++sequence.current;
        pendingOrder.current = null;
        if (mounted.current) setSavingOrder(false);
        void load();
      });
  }, [view, load, t]);
  const detached = typeof view === "object" && view ? view.detached.find((d) => d.key === selected) ?? null : null;
  const lines = useMemo(() => (typeof view === "object" && view ? bannerLinesOf(view) : []), [view]);

  const open = useCallback(async (key: string) => {
    setBusy(true);
    setError(null);
    try {
      const out = await openWorkProject(key);
      if (out.result === "failed" || out.result === "unavailable") setError(t("work.openFailed", { detail: out.detail ?? "?" }));
      else if (out.result === "no_hook") setError(t("work.hookProblem", { reason: out.detail ?? "?" }));
    } catch (err) {
      setError(err instanceof TerminalAuthRequired ? t("work.authRequired") : t("work.openFailed", { detail: String((err as Error).message) }));
    } finally {
      setBusy(false);
      void load();
    }
  }, [load, t]);

  // Close then open, as one action: nothing else can be clicked in between, so a close
  // clicked after it cannot be overtaken by its open (kiro review round 10).
  const reopen = useCallback(async (key: string) => {
    setBusy(true);
    try {
      const out = await closeWorkProject(key);
      if (!out.closed) {
        setBusy(false);
        setError(t("work.closeIncomplete"));
        return;
      }
    } catch (err) {
      setBusy(false);
      setError(t("work.openFailed", { detail: String((err as Error).message) }));
      return;
    }
    await open(key);
  }, [open, t]);

  const choose = (key: string) => {
    setSelected(key);
    writeSelected(key);
    setError(null);
    const row = rows.find((r) => r.key === key);
    if (row && row.openable && !row.starting && !row.terminal?.alive) void open(key);
  };

  const close = async (key: string) => {
    setBusy(true);
    try {
      const out = await closeWorkProject(key);
      if (!out.closed) setError(t("work.closeIncomplete"));
    } catch (err) { setError(String((err as Error).message)); }
    finally { setBusy(false); void load(); }
  };

  const showBanner = typeof view === "object" && view && (view.restoring || lines.length > 0)
    && !(bannerHidden && bannerSettled(lines));

  return (
    <div className="flex h-screen flex-col bg-background">
      <TopBar active="Work" />
      {showBanner && typeof view === "object" && view && (
        <div className="shrink-0 border-b bg-muted/40 px-4 py-2 text-sm">
          <div className="flex items-center gap-3">
            <span className="font-semibold">{t("work.restore.heading")}</span>
            {view.restoring && <span className="text-muted-foreground">{t("work.restore.restoring")}</span>}
            <div className="flex-1" />
            {bannerSettled(lines) && (
              <Button variant="ghost" size="sm" onClick={() => setBannerHidden(true)}>{t("work.restore.dismiss")}</Button>
            )}
          </div>
          <ul className="mt-1 space-y-0.5">
            {lines.map((line) => (
              <li key={line.key} className={TONE[line.tone]}>
                <span className="font-medium text-foreground">{line.task}</span>
                {" — "}
                {t(`work.restore.${line.message}`, { detail: line.detail ?? "?" })}
                {line.stale && <span className="text-amber-700 dark:text-amber-400"> {t("work.restore.stale")}</span>}
              </li>
            ))}
          </ul>
        </div>
      )}
      {restoreError && (
        <p className="shrink-0 border-b px-4 py-1 text-xs text-amber-700 dark:text-amber-400">
          {t("work.restoreFailed", { detail: restoreError })}
        </p>
      )}
      {typeof view === "object" && view?.hook_problem && (
        <p className="shrink-0 border-b px-4 py-1 text-xs text-amber-700 dark:text-amber-400">
          {t("work.hookProblem", { reason: view.hook_problem })}
        </p>
      )}
      <div className="flex min-h-0 flex-1">
        <aside className="flex w-[280px] shrink-0 flex-col gap-1 overflow-y-auto border-r p-2 text-sm">
          <p className="px-2 pb-1 text-xs text-muted-foreground">{t("work.projectsHeading")}</p>
          {orderError && <p className="px-2 text-xs text-red-700 dark:text-red-400">{orderError}</p>}
          {view === "auth" && <p className="px-2 text-amber-700 dark:text-amber-400">{t("work.authRequired")}</p>}
          {view === "failed" && <p className="px-2 text-red-700 dark:text-red-400">{t("work.failedToList")}</p>}
          {listFailed && typeof view === "object" && view && (
            <p className="px-2 text-amber-700 dark:text-amber-400">{t("work.listStale")}</p>
          )}
          {typeof view === "object" && view && rows.length === 0 && (
            <p className="px-2 text-muted-foreground">{t("work.noProjects")}</p>
          )}
          {typeof view === "object" && view && rows.length > 0 && listed.length === 0 && (
            <p className="px-2 text-muted-foreground">{t("work.noneDoing")}</p>
          )}
          {listed.map((row) => {
            const state = projectStateOf(row);
            return (
              <button key={row.key} type="button" onClick={() => choose(row.key)}
                draggable={!savingOrder}
                title={t("work.dragHint")}
                onDragStart={(event) => { setDragged(row.key); event.dataTransfer.effectAllowed = "move"; }}
                onDragEnd={() => { setDragged(null); setDropBefore(null); }}
                onDragOver={(event) => { if (dragged) { event.preventDefault(); setDropBefore(row.key); } }}
                onDrop={(event) => {
                  event.preventDefault();
                  if (dragged && dragged !== row.key) reorder(dragged, row.key);
                  setDragged(null); setDropBefore(null);
                }}
                className={cn("flex flex-col gap-0.5 rounded-md border px-2.5 py-2 text-left",
                  row.key === selected ? "border-border bg-accent" : "border-transparent hover:bg-accent/60",
                  dragged === row.key && "opacity-50",
                  dropBefore === row.key && dragged !== row.key && "border-t-2 border-t-primary")}>
                <span className="flex items-center gap-2">
                  <span className={cn("h-2 w-2 shrink-0 rounded-full", DOT[state])} />
                  <span className={cn("min-w-0 flex-1 truncate", row.key === selected && "font-semibold")}>{row.name || row.task}</span>
                </span>
                <span className="truncate pl-4 text-xs text-muted-foreground" title={row.title ?? undefined}>
                  {row.openable ? t(`work.state.${state}`) : t("work.cannotOpen", { reason: row.reason ?? "?" })}
                  {row.name ? ` · ${row.task}` : row.title ? ` · ${row.title}` : ""}
                </span>
              </button>
            );
          })}
          {dragged && listed.length > 1 && (
            // Dropping below the last project puts the dragged one at the end.
            <div className="h-6 rounded-md border border-dashed"
              onDragOver={(event) => { event.preventDefault(); setDropBefore(null); }}
              onDrop={(event) => {
                event.preventDefault();
                reorder(dragged, null);
                setDragged(null);
              }} />
          )}
          {todoCount > 0 && (
            <Link href="/" className="px-2 pt-2 text-xs text-muted-foreground underline-offset-2 hover:underline">
              {t("work.todoElsewhere", { count: String(todoCount) })}
            </Link>
          )}
          {typeof view === "object" && view && view.inactive > 0 && (
            <p className="px-2 pt-2 text-xs text-muted-foreground">{t("work.inactive", { count: String(view.inactive) })}</p>
          )}
          {typeof view === "object" && view && view.missing.length > 0 && (
            <div className="px-2 pt-2 text-xs">
              <p className="text-muted-foreground">{t("work.missingHeading")}</p>
              {view.missing.map((m) => (
                <div key={m.key} className="flex items-center gap-2 py-0.5">
                  <span className="flex-1 truncate">{m.task}</span>
                  <Button variant="ghost" size="sm" disabled={busy} onClick={() => void close(m.key)}>{t("work.close")}</Button>
                </div>
              ))}
            </div>
          )}
          {typeof view === "object" && view && view.detached.length > 0 && (
            <div className="px-2 pt-2 text-xs">
              <p className="text-muted-foreground">{t("work.detachedHeading")}</p>
              {view.detached.map((d) => (
                <div key={d.key} className="flex items-center gap-2 py-0.5">
                  <button type="button" className={cn("flex-1 truncate text-left", d.key === selected && "font-semibold")}
                    onClick={() => { setSelected(d.key); writeSelected(d.key); }}>{d.task}</button>
                  <Button variant="ghost" size="sm" disabled={busy} onClick={() => void close(d.key)}>{t("work.close")}</Button>
                </div>
              ))}
            </div>
          )}
          {typeof view === "object" && view && view.unreadable.length > 0 && (
            <div className="px-2 pt-2 text-xs">
              <p className="text-muted-foreground">{t("work.unreadableHeading")}</p>
              {view.unreadable.map((u) => (
                <div key={`${u.source}/${u.task}`} className="flex items-center gap-2 py-0.5">
                  <p className="min-w-0 flex-1 truncate text-amber-700 dark:text-amber-400" title={u.source}>
                    {u.task || u.source}: {u.reason}
                  </p>
                  {u.want_open && u.key && (
                    <Button variant="ghost" size="sm" disabled={busy} title={t("work.closeHint")} onClick={() => void close(u.key!)}>
                      {t("work.close")}
                    </Button>
                  )}
                </div>
              ))}
            </div>
          )}
        </aside>
        <section className="flex min-w-0 flex-1 flex-col" style={{ background: GRAPE.background, color: TERMINAL_CHROME.text, ["--term-muted" as string]: TERMINAL_CHROME.muted }}>
          {current ? (
            <>
              <div className="flex h-11 shrink-0 items-center gap-3 border-b px-4 text-sm" style={{ background: TERMINAL_CHROME.bar, borderColor: TERMINAL_CHROME.border }}>
                <span className="font-semibold">{current.name || current.task}</span>
                {current.name && <span className="text-xs text-[var(--term-muted)]">{current.task}</span>}
                <span className="text-xs text-[var(--term-muted)]">{t(`work.state.${projectStateOf(current)}`)}</span>
                <div className="flex-1" />
                <span className="text-xs text-[var(--term-muted)]">{t("work.where", { agent: current.workspace === "kiro" ? "kiro" : "claude" })}</span>
                {current.agent_exited && current.openable && (
                  <Button variant="outline" size="sm" disabled={busy}
                    onClick={() => void reopen(current.key)}>
                    {t("work.reopen")}
                  </Button>
                )}
                {current.terminal && !current.terminal.alive && current.openable && (
                  <Button variant="outline" size="sm" disabled={busy || current.starting} onClick={() => void open(current.key)}>
                    {t("work.reopen")}
                  </Button>
                )}
                {(current.terminal?.alive || current.want_open) && (
                  <Button variant="outline" size="sm" disabled={busy} title={t("work.closeHint")} onClick={() => void close(current.key)}>
                    {t("work.close")}
                  </Button>
                )}
              </div>
              {error && <p className="shrink-0 px-4 py-1 text-xs text-amber-400">{error}</p>}
              {current.agent_exited && <p className="shrink-0 px-4 py-1 text-xs text-amber-400">{t("work.agentExitedHint")}</p>}
              {current.terminal && current.launch?.result === "no_hook" && (
                // For as long as it runs, not only until the next click (kiro review round 11).
                <p className="shrink-0 px-4 py-1 text-xs text-amber-400">
                  {t("work.hookProblem", { reason: current.launch.detail ?? "?" })}
                </p>
              )}
              <div className="min-h-0 flex-1">
                {current.terminal ? (
                  <TerminalView key={current.terminal.terminal_id} terminalId={current.terminal.terminal_id} onExit={() => void load()} />
                ) : (
                  <div className="flex h-full items-center justify-center p-6 text-sm text-[var(--term-muted)]">
                    {current.starting ? t("work.state.starting") : current.openable ? (
                      <Button variant="outline" size="sm" disabled={busy} onClick={() => void open(current.key)}>{t("work.open")}</Button>
                    ) : t("work.cannotOpen", { reason: current.reason ?? "?" })}
                  </div>
                )}
              </div>
            </>
          ) : detached ? (
            <>
              <div className="flex h-11 shrink-0 items-center gap-3 border-b px-4 text-sm" style={{ background: TERMINAL_CHROME.bar, borderColor: TERMINAL_CHROME.border }}>
                <span className="font-semibold">{detached.task}</span>
                <span className="text-xs text-[var(--term-muted)]">{t("work.detachedHeading")}</span>
                <div className="flex-1" />
                <Button variant="outline" size="sm" disabled={busy} onClick={() => void close(detached.key)}>{t("work.close")}</Button>
              </div>
              <div className="min-h-0 flex-1">
                <TerminalView key={detached.terminal.terminal_id} terminalId={detached.terminal.terminal_id} onExit={() => void load()} />
              </div>
            </>
          ) : (
            <div className="flex h-full items-center justify-center p-6 text-sm text-[var(--term-muted)]">{t("work.pick")}</div>
          )}
        </section>
      </div>
    </div>
  );
}

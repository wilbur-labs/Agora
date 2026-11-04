"use client";

/**
 * The overview (workspace CR-75 slice 3), the front page: every active project at a
 * glance — what its own record says about progress and CRs not closed, and whether its
 * terminal is open. A card leads to that project on the work page.
 *
 * Judgements live in lib/overview.ts. The page also asks for the restore after a restart,
 * as the work page does: the server restores at most once per run, and the person should
 * not have to visit the work page first for what was open to come back.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { TopBar } from "@/components/delivery-shell";
import { ProgressSnapshotBar } from "@/components/progress-snapshot-bar";
import { listWorkspaceSources } from "@/lib/control-plane";
import { overviewCardsOf, overviewSummaryOf, type OverviewCard } from "@/lib/overview";
import { TerminalAuthRequired } from "@/lib/terminal";
import {
  WORK_SELECTED_KEY,
  bannerLinesOf,
  listWorkProjects,
  moveKey,
  restoreShelvedProject,
  restoreWorkProjects,
  setWorkOrder,
  setWorkStage,
  shelfErrorOf,
  shelveWorkProject,
  shelvedGroupsOf,
  type ProjectState,
  type ShelvedProject,
  type Stage,
  type WorkView,
} from "@/lib/work";
import type { WorkspaceSourcePayload } from "@/lib/workspace-sources";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const POLL_MS = 10000;
const RESTORE_RETRY_MS = 30000;

const DOT: Record<ProjectState, string> = {
  starting: "bg-sky-600",
  open: "bg-emerald-600",
  agentExited: "bg-amber-600",
  ended: "border border-muted-foreground bg-transparent",
  notOpen: "border border-muted-foreground/60 bg-transparent",
};

export default function OverviewPage() {
  const { t } = useI18n();
  const router = useRouter();
  const [view, setView] = useState<WorkView | "auth" | "failed" | null>(null);
  const [sources, setSources] = useState<WorkspaceSourcePayload[] | null>(null);
  const [sourcesFailed, setSourcesFailed] = useState(false);
  // A poll that failed after a list was shown: the cards stay, marked out of date (codex review).
  const [listFailed, setListFailed] = useState(false);
  // The restore request failed while the server still has no answer: said here, not only
  // on the work page (codex review).
  const [restoreError, setRestoreError] = useState<string | null>(null);
  const mounted = useRef(true);
  const sequence = useRef(0);
  const shown = useRef(0);
  const restoring = useRef(false);
  const restoreRetryAt = useRef(0);
  const reload = useRef<() => void>(() => {});
  // Pause / archive / restore (CR-80): which project a request is out for, what the last
  // one could not do, and — after one that did — that the record is written, not committed.
  const [busyKey, setBusyKey] = useState<string | null>(null);
  const [shelfError, setShelfError] = useState<{ key: string; text: string } | null>(null);
  const [uncommitted, setUncommitted] = useState(false);
  // A card being dragged, and where it would land: a column, before a card or at its end.
  const [dragged, setDragged] = useState<string | null>(null);
  const [dropAt, setDropAt] = useState<{ stage: Stage; before: string | null } | null>(null);

  useEffect(() => {
    mounted.current = true;
    // Plain functions inside the effect: the restore's answer reads the list again, so
    // the loader refers to itself.
    function loadWork() {
      if (!mounted.current) return;
      const mine = ++sequence.current;
      listWorkProjects()
        .then((next) => {
          if (!mounted.current || mine < shown.current) return;
          shown.current = mine;
          setView(next);
          setListFailed(false);
          if (next.restore !== null) setRestoreError(null);
          if (next.restore === null && !next.restoring && !restoring.current && Date.now() >= restoreRetryAt.current) {
            restoring.current = true;
            void restoreWorkProjects()
              .then(() => { if (mounted.current) setRestoreError(null); })
              .catch((err) => {
                restoreRetryAt.current = Date.now() + RESTORE_RETRY_MS;
                if (mounted.current && !(err instanceof TerminalAuthRequired)) setRestoreError(String((err as Error).message));
              })
              .finally(() => { restoring.current = false; loadWork(); });
          }
        })
        .catch((err) => {
          if (!mounted.current || mine < shown.current) return;
          shown.current = mine;
          if (err instanceof TerminalAuthRequired) { setView("auth"); return; }
          // A list already shown stays, marked out of date until a read succeeds again.
          setView((prev) => (typeof prev === "object" && prev ? prev : "failed"));
          setListFailed(true);
        });
    }
    // Only the latest read may land: a newer one cancels the one still out (codex review).
    let sourcesRead: AbortController | null = null;
    function loadSources() {
      sourcesRead?.abort();
      const mine = new AbortController();
      sourcesRead = mine;
      listWorkspaceSources(mine.signal)
        .then((next) => { if (mounted.current && sourcesRead === mine) { setSources(next); setSourcesFailed(false); } })
        .catch(() => { if (mounted.current && sourcesRead === mine) setSourcesFailed(true); });
    }
    reload.current = () => { loadWork(); loadSources(); };
    loadWork();
    loadSources();
    // The terminals change on their own; the records only when the person edits them, so
    // they are read again when the page comes back into view rather than on a timer.
    const timer = window.setInterval(() => { if (document.visibilityState === "visible") loadWork(); }, POLL_MS);
    const onVisible = () => { if (document.visibilityState === "visible") { loadWork(); loadSources(); } };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      mounted.current = false;
      sourcesRead?.abort();
      window.clearInterval(timer);
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, []);

  const workView = typeof view === "object" && view ? view : null;
  const attentionKeys = useMemo(
    () => new Set(workView ? bannerLinesOf(workView).map((line) => line.key) : []), [workView]);
  const cards = useMemo(
    () => (workView ? overviewCardsOf(workView, sources, sourcesFailed, attentionKeys) : []),
    [workView, sources, sourcesFailed, attentionKeys]);
  const summary = overviewSummaryOf(cards);

  const shelfAction = (key: string, request: () => Promise<unknown>) => {
    if (busyKey) return;
    setBusyKey(key);
    setShelfError(null);
    request()
      .then(() => { if (mounted.current) setUncommitted(true); })
      .catch((err) => {
        if (!mounted.current) return;
        if (err instanceof TerminalAuthRequired) { setView("auth"); return; }
        const why = shelfErrorOf(String((err as Error).message));
        setShelfError({ key, text: why.kind === "terminalOpen" ? t("overview.shelfTerminalOpen")
          : t("overview.shelfRefused", { reason: why.reason }) });
      })
      .finally(() => { if (mounted.current) setBusyKey(null); reload.current(); });
  };
  const shelve = (key: string, action: "pause" | "archive") => shelfAction(key, () => shelveWorkProject(key, action));
  const unshelve = (key: string) => shelfAction(key, () => restoreShelvedProject(key));

  // Todo / doing (CR-81) and the order (CR-82): Agora's own, nothing to commit. A drop
  // changes the stage first — "todo" may be refused — and only then the place.
  const boardAction = (key: string, request: () => Promise<unknown>, after?: () => void) => {
    if (busyKey) return;
    setBusyKey(key);
    setShelfError(null);
    request()
      .then(() => { if (mounted.current) after?.(); })
      .catch((err) => {
        if (!mounted.current) return;
        if (err instanceof TerminalAuthRequired) { setView("auth"); return; }
        const why = shelfErrorOf(String((err as Error).message));
        setShelfError({ key, text: why.kind === "terminalOpen" ? t("overview.todoTerminalOpen")
          : t("overview.shelfRefused", { reason: why.reason }) });
      })
      .finally(() => { if (mounted.current) setBusyKey(null); reload.current(); });
  };
  const move = (key: string, stage: Stage, before: string | null) => {
    const card = cards.find((c) => c.key === key);
    if (!card) return;
    const keys = moveKey(cards.map((c) => c.key), key, before);
    const unchanged = card.stage === stage && keys.every((k, i) => k === cards[i]?.key);
    if (unchanged) return;
    boardAction(key, async () => {
      if (card.stage !== stage) await setWorkStage(key, stage);
      await setWorkOrder(keys);
    });
  };
  const setStage = (key: string, stage: Stage) => boardAction(key, () => setWorkStage(key, stage));

  const go = (key: string) => {
    const card = cards.find((c) => c.key === key);
    const navigate = () => {
      try { window.localStorage.setItem(WORK_SELECTED_KEY, key); } catch { /* a convenience only */ }
      router.push("/work");
    };
    // The work page lists only what is doing: opening a todo project starts it (CR-81).
    if (card && card.stage === "todo") boardAction(key, () => setWorkStage(key, "doing"), navigate);
    else navigate();
  };
  const doing = cards.filter((card) => card.stage === "doing");
  const todo = cards.filter((card) => card.stage === "todo");

  return (
    <div className="flex h-screen flex-col bg-background">
      <TopBar active="Overview" />
      <main className="min-h-0 flex-1 overflow-y-auto">
        <div className="mx-auto max-w-[1600px] space-y-5 p-4 md:p-8">
          <header className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
            <h1 className="text-xl font-semibold">{t("overview.title")}</h1>
            {workView && <p className="text-sm text-muted-foreground">{t("overview.summary", summary)}</p>}
          </header>

          {view === null && <p className="text-sm text-muted-foreground">…</p>}
          {view === "auth" && <p className="text-sm text-amber-700 dark:text-amber-400">{t("work.authRequired")}</p>}
          {view === "failed" && <p className="text-sm text-red-700 dark:text-red-400">{t("work.failedToList")}</p>}

          {listFailed && workView && <p className="text-sm text-amber-700 dark:text-amber-400">{t("work.listStale")}</p>}
          {restoreError && <p className="text-sm text-red-700 dark:text-red-400">{t("work.restoreFailed", { detail: restoreError })}</p>}

          {attentionKeys.size > 0 && (
            <p className="rounded-lg border border-amber-500/40 bg-amber-500/10 px-4 py-2 text-sm text-amber-800 dark:text-amber-300">
              {t("overview.attention", { count: attentionKeys.size })}{" "}
              <Link href="/work" className="font-medium underline">{t("overview.attentionLink")}</Link>
            </p>
          )}
          {uncommitted && <p className="text-xs text-muted-foreground">{t("overview.shelfUncommitted")}</p>}
          {sourcesFailed && <p className="text-sm text-amber-700 dark:text-amber-400">{t("overview.sourcesFailed")}</p>}

          {workView && cards.length === 0 && workView.unreadable.length === 0 && (
            <p className="text-sm text-muted-foreground">{t("work.noProjects")}</p>
          )}

          {workView && cards.length > 0 && ([["doing", doing], ["todo", todo]] as const).map(([stage, items]) => (
            <section
              key={stage}
              className={cn("space-y-2 rounded-xl p-2 transition-colors", dragged && dropAt?.stage === stage && "bg-accent/40")}
              onDragOver={(event) => {
                if (!dragged) return;
                event.preventDefault();
                // Anywhere in the column but a card (cards stop the event): its end — the
                // heading and the gaps included (codex review CR-81 round 1).
                setDropAt({ stage, before: null });
              }}
              onDrop={(event) => {
                event.preventDefault();
                // The column receiving the drop decides the stage; a target left over from
                // another column is not used.
                const at = dropAt?.stage === stage ? dropAt : { stage, before: null };
                if (dragged) move(dragged, at.stage, at.before);
                setDragged(null); setDropAt(null);
              }}
            >
              <h2 className="flex items-baseline gap-2 px-1 text-sm font-semibold">
                {t(stage === "doing" ? "overview.doingHeading" : "overview.todoHeading", { count: items.length })}
                <span className="text-xs font-normal text-muted-foreground">
                  {t(stage === "doing" ? "overview.doingHint" : "overview.todoHint")}
                </span>
              </h2>
              {items.length === 0 && (
                <p className="rounded-lg border border-dashed px-4 py-6 text-center text-xs text-muted-foreground"
                  onDragOver={(event) => { if (dragged) { event.preventDefault(); setDropAt({ stage, before: null }); } }}>
                  {t("overview.dropHere")}
                </p>
              )}
              <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 2xl:grid-cols-4">
                {items.map((card) => (
                  <div
                    key={card.key}
                    draggable={busyKey === null}
                    onDragStart={(event) => { setDragged(card.key); event.dataTransfer.effectAllowed = "move"; }}
                    onDragEnd={() => { setDragged(null); setDropAt(null); }}
                    onDragOver={(event) => {
                      if (!dragged) return;
                      event.preventDefault();
                      event.stopPropagation();
                      setDropAt({ stage, before: card.key });
                    }}
                    className={cn("flex min-w-0 rounded-xl", dragged === card.key && "opacity-50",
                      dragged && dragged !== card.key && dropAt?.before === card.key && "ring-2 ring-primary ring-offset-2")}
                  >
                    <Card
                      card={card}
                      onOpen={() => go(card.key)}
                      onShelve={(action) => shelve(card.key, action)}
                      onStage={(next) => setStage(card.key, next)}
                      busy={busyKey !== null}
                      working={busyKey === card.key}
                      error={shelfError?.key === card.key ? shelfError.text : null}
                    />
                  </div>
                ))}
              </div>
            </section>
          ))}

          {workView && workView.unreadable.length > 0 && (
            <section className="space-y-1">
              <h2 className="text-sm font-semibold">{t("work.unreadableHeading")}</h2>
              <ul className="space-y-0.5 text-xs text-muted-foreground">
                {workView.unreadable.map((u) => (
                  <li key={`${u.source}\0${u.task}`} className="break-all">{u.task} — {u.reason} ({u.source})</li>
                ))}
              </ul>
            </section>
          )}
          {workView && workView.shelved.length > 0 && (
            <Shelved
              shelved={workView.shelved}
              onRestore={unshelve}
              busy={busyKey !== null}
              workingKey={busyKey}
              error={shelfError}
            />
          )}
        </div>
      </main>
    </div>
  );
}

function Card({ card, onOpen, onShelve, onStage, busy, working, error }: {
  card: OverviewCard;
  onOpen: () => void;
  onShelve: (action: "pause" | "archive") => void;
  onStage: (stage: Stage) => void;
  busy: boolean;
  working: boolean;
  error: string | null;
}) {
  const { t } = useI18n();
  const [confirming, setConfirming] = useState(false);
  const record = card.record;
  const title = record.show === "read" && record.title ? record.title : card.task;
  return (
    <div className={cn("flex w-full min-w-0 flex-col rounded-xl border bg-card", card.attention && "border-amber-500/60")}>
    <button
      type="button"
      onClick={onOpen}
      title={t("overview.openHint")}
      className="flex min-w-0 flex-1 flex-col gap-2 rounded-t-xl p-4 text-left transition-colors hover:bg-accent/60"
    >
      <div className="flex min-w-0 items-start gap-2">
        <span className={cn("mt-1.5 size-2 shrink-0 rounded-full", DOT[card.state])} />
        <div className="min-w-0 flex-1">
          <p className="line-clamp-2 text-sm font-semibold leading-snug">{title}</p>
          <p className="truncate text-xs text-muted-foreground">
            {card.task} · {t(`work.state.${card.state}`)}
          </p>
        </div>
        <WorkspaceBadge workspace={card.workspace} />
      </div>

      {record.show === "loading" && <p className="text-xs text-muted-foreground">{t("overview.loadingRecord")}</p>}
      {record.show === "unread" && <p className="text-xs text-amber-700 dark:text-amber-400">{t("overview.recordUnread")}</p>}
      {record.show === "read" && (
        <>
          {record.progress.show === "bar"
            ? <ProgressSnapshotBar bar={record.progress.bar} compact />
            : <p className="text-xs text-amber-700 dark:text-amber-400">{t("overview.recordUnread")}</p>}
          <p className="text-xs text-muted-foreground">
            {record.cr.show === "counts" && (
              <>
                {record.cr.notClosed > 0 ? t("overview.crCounts", { count: record.cr.notClosed }) : t("overview.crNone")}
                {record.cr.unrecognised > 0 && <> · {t("overview.crUnrecognised", { count: record.cr.unrecognised })}</>}
              </>
            )}
            {record.cr.show === "unreadable" && t("overview.crUnreadable", { reason: record.cr.reason })}
          </p>
        </>
      )}
      {card.attention && <p className="text-xs text-amber-700 dark:text-amber-400">{t("overview.restoreAttention")}</p>}
    </button>
    <div className="flex flex-wrap items-center gap-2 border-t px-4 py-2 text-xs">
      {working ? (
        <span className="text-muted-foreground">{t("overview.working")}</span>
      ) : confirming ? (
        <>
          <button type="button" disabled={busy} onClick={() => { setConfirming(false); onShelve("archive"); }}
            className="rounded border border-amber-500/60 px-2 py-0.5 font-medium text-amber-800 hover:bg-amber-500/10 disabled:opacity-50 dark:text-amber-300">
            {t("overview.archiveConfirm")}
          </button>
          <button type="button" onClick={() => setConfirming(false)}
            className="rounded px-2 py-0.5 text-muted-foreground hover:bg-accent">
            {t("overview.cancel")}
          </button>
          <p className="basis-full text-muted-foreground">{t("overview.archiveConfirmHint")}</p>
        </>
      ) : (
        <>
          {card.stage === "todo" ? (
            <button type="button" disabled={busy} title={t("overview.toDoingHint")} onClick={() => onStage("doing")}
              className="rounded px-2 py-0.5 font-medium text-foreground hover:bg-accent disabled:opacity-50">
              {t("overview.toDoing")}
            </button>
          ) : (
            <button type="button" disabled={busy || card.busy}
              title={card.busy ? t("overview.todoTerminalOpen") : t("overview.toTodoHint")} onClick={() => onStage("todo")}
              className="rounded px-2 py-0.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50">
              {t("overview.toTodo")}
            </button>
          )}
          <button type="button" disabled={busy} title={t("overview.pauseHint")} onClick={() => onShelve("pause")}
            className="rounded px-2 py-0.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50">
            {t("overview.pause")}
          </button>
          <button type="button" disabled={busy} title={t("overview.archiveHint")} onClick={() => setConfirming(true)}
            className="rounded px-2 py-0.5 text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50">
            {t("overview.archive")}
          </button>
        </>
      )}
      {error && <p className="basis-full text-red-700 dark:text-red-400">{error}</p>}
    </div>
    </div>
  );
}

/** Which workspace keeps the project's record (CR-80): the same task name can sit in
 *  both, so it is a badge with its own colour, not a word in a grey line. */
const WORKSPACE_BADGE: Record<"claude" | "kiro", { label: string; className: string }> = {
  claude: { label: "Claude", className: "border-orange-500/50 bg-orange-500/10 text-orange-800 dark:text-orange-300" },
  kiro: { label: "Kiro", className: "border-violet-500/50 bg-violet-500/10 text-violet-800 dark:text-violet-300" },
};

function WorkspaceBadge({ workspace }: { workspace: "claude" | "kiro" }) {
  const badge = WORKSPACE_BADGE[workspace];
  return (
    <span
      title={`${workspace}-workspace`}
      className={cn("shrink-0 rounded border px-1.5 py-0.5 text-[0.7rem] font-semibold leading-none", badge.className)}
    >
      {badge.label}
    </span>
  );
}

function Shelved({ shelved, onRestore, busy, workingKey, error }: {
  shelved: ShelvedProject[];
  onRestore: (key: string) => void;
  busy: boolean;
  workingKey: string | null;
  error: { key: string; text: string } | null;
}) {
  const { t } = useI18n();
  const groups = shelvedGroupsOf(shelved);
  const sections: [string, ShelvedProject[]][] = [
    [t("overview.shelvedPaused"), groups.paused],
    [t("overview.shelvedArchived"), groups.archived],
    [t("overview.shelvedOtherHeading"), groups.other],
  ];
  return (
    <details className="rounded-xl border bg-card px-4 py-3">
      <summary className="cursor-pointer text-sm font-semibold">
        {t("overview.shelvedHeading", { paused: groups.paused.length, archived: groups.archived.length })}
        {groups.other.length > 0 && <> · {t("overview.shelvedOther", { count: groups.other.length })}</>}
      </summary>
      <div className="mt-3 space-y-3">
        {sections.filter(([, items]) => items.length > 0).map(([heading, items]) => (
          <section key={heading} className="space-y-1">
            <h3 className="text-xs font-semibold text-muted-foreground">{heading}</h3>
            <ul className="divide-y">
              {items.map((p) => (
                <li key={p.key} className="flex flex-wrap items-center gap-x-3 gap-y-1 py-1.5 text-sm">
                  <span className="min-w-0 flex-1">
                    <span className="font-medium">{p.title ?? p.task}</span>
                    <span className="ml-2 text-xs text-muted-foreground">{p.task} · {p.status}</span>
                  </span>
                  <WorkspaceBadge workspace={p.workspace} />
                  {workingKey === p.key ? (
                    <span className="text-xs text-muted-foreground">{t("overview.working")}</span>
                  ) : (
                    <button type="button" disabled={busy} onClick={() => onRestore(p.key)}
                      className="rounded px-2 py-0.5 text-xs text-muted-foreground hover:bg-accent hover:text-foreground disabled:opacity-50">
                      {t("overview.restore")}
                    </button>
                  )}
                  {error?.key === p.key && <p className="basis-full text-xs text-red-700 dark:text-red-400">{error.text}</p>}
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </details>
  );
}

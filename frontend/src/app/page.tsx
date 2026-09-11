"use client";

/**
 * Today — mockup v2, screen 1.
 *
 * The question this screen answers is "what should I push today", and the mockup's
 * answer is three tiers of *projects*: "今天主推" (at most three big cards, the user's own
 * choice, stored by the server), "在推进，但不是今天的重点" (compact rows: an open task moved
 * in the last fourteen days), and "N 个休眠项目" (folded to one line). Under them, one
 * cross-project list of what is stuck — deliberately not grouped by project, because
 * blockers are scanned in one pass. The inventory of every project is a different
 * question and a different screen (/portfolio).
 *
 * Every placement and every count is decided in lib/today-focus.ts, from three reads
 * (projects, tasks, open attention) issued together and each carrying its own outcome —
 * sharing one catch is how a failing task read once made the attention read look
 * unreadable too. They are published together once all three have answered, as every
 * board in this app does; the three are not one snapshot, and what that costs is recorded
 * in .agora/development/PROGRESS.md rather than papered over here. Two of the tiers are
 * absence claims, and this page makes neither on its own: "dormant" is only said when
 * the task read covered everything and no open task of the project moved, and "3/3 已满"
 * is the server's verdict repeated, never `length >= limit` recomputed here.
 *
 * The focus cards read one dashboard each (three at most) for the next step, the
 * blocking count and the progress snapshot. The compact rows read none: what they state
 * — an open task moved recently, N open tasks — is measured from the task read alone.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { ArrowRight, CircleDot, RefreshCw } from "lucide-react";

import { DeliveryShell } from "@/components/delivery-shell";
import { TodayFocusCard, TodayProjectRow } from "@/components/today-focus-card";
import { Button } from "@/components/ui/button";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { MAX_PAGE, readPage } from "@/lib/paged";
import {
  ApiError,
  demoteProjectFromToday,
  fetchProjectDashboard,
  listOpenAttention,
  listProjects,
  listTasks,
  promoteProjectToToday,
  type AttentionItem,
  type ProjectDashboard,
  type ProjectListItem,
  type TaskManifest,
  type TodayFocusPayload,
} from "@/lib/control-plane";
import {
  attentionReadCovers,
  portfolioAllClearSayable,
  portfolioBucketOf,
  taskInventoryReachOf,
  type AttentionRead,
  type PortfolioBucket,
  type TaskInventoryReach,
} from "@/lib/portfolio-bucket";
import { settleRead } from "@/lib/rail-group";
import {
  ADVANCING_WINDOW_DAYS,
  advancingCountOf,
  continueTaskOf,
  focusCapacityOf,
  openTaskCountOf,
  promoteRefusalOf,
  todayTiersOf,
  type PromoteRefusal,
} from "@/lib/today-focus";

/** Buckets that mean something is waiting on the human. */
const NEEDS_YOU: readonly PortfolioBucket[] = ["needsReply", "needsApproval", "blocked"];

type ProjectsReach = "pending" | "complete" | "unreadable";

interface DashboardRead {
  dashboard: ProjectDashboard | null;
  readFailed: boolean;
}

export default function TodayPage() {
  const { t, te, locale } = useI18n();
  const [projects, setProjects] = useState<readonly ProjectListItem[]>([]);
  const [projectsReach, setProjectsReach] = useState<ProjectsReach>("pending");
  // `null` when the server sent no `today_focus` field: a fact about the server, kept
  // apart from "nothing is promoted" (which is a payload with an empty list).
  const [focus, setFocus] = useState<TodayFocusPayload | null>(null);
  const [tasks, setTasks] = useState<readonly TaskManifest[]>([]);
  const [taskReach, setTaskReach] = useState<TaskInventoryReach>("pending");
  const [attention, setAttention] = useState<readonly AttentionItem[]>([]);
  const [attentionRead, setAttentionRead] = useState<AttentionRead>("pending");
  // When the reads landed: "today" and "the last fourteen days" are measured from here,
  // so a tab left open overnight does not re-tier projects on every render.
  const [loadedAt, setLoadedAt] = useState<number | null>(null);
  const [dashboards, setDashboards] = useState<ReadonlyMap<string, DashboardRead>>(new Map());
  // Which focus ids have had their dashboard requested since the last load. A ref, not
  // state: it is read and written inside one effect and must never itself cause a render.
  const dashboardsRequested = useRef<{ loadedAt: number | null; ids: Set<string> }>({ loadedAt: null, ids: new Set() });
  // Bumped by every refresh. An answer that arrives after the map it was meant for has
  // been emptied would otherwise land in the new one — React applies the two writes in
  // order, and the effect that aborts the request runs only after the render. A request
  // writes its answer only if its generation is current and its id is still wanted.
  const dashboardGen = useRef(0);
  const [busy, setBusy] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<{ projectId: string; kind: PromoteRefusal } | null>(null);
  const [demoteError, setDemoteError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const loadToken = useRef(0);
  // Which write to `focus` is current. Every read or mutation that will replace the
  // whole focus snapshot takes a number here first and applies its response only if it
  // still holds the latest one: a refresh that started before a promotion, or two
  // mutations whose responses come back out of order, must not put an older tier back on
  // screen over a newer one. Buttons are also disabled while any mutation is in flight,
  // so ordinary use never has two in the air — this ref covers the refresh that can.
  const focusWrite = useRef(0);

  // Every path that replaces the focus snapshot goes through here, so a project that left
  // the tier loses its dashboard entry at the same moment — not one effect-and-timer later.
  // Otherwise a project demoted and promoted again would, for a frame, show the next step
  // and progress from before it left as if they were current.
  const applyFocus = useCallback((next: TodayFocusPayload | null) => {
    setFocus(next);
    const keep = new Set(next?.project_ids ?? []);
    // Forgotten here, synchronously, not in the effect: an in-flight answer for a project
    // that just left checks this set before writing, and the effect runs after the render.
    for (const id of Array.from(dashboardsRequested.current.ids)) {
      if (!keep.has(id)) dashboardsRequested.current.ids.delete(id);
    }
    setDashboards((current) => {
      let changed = false;
      const pruned = new Map<string, DashboardRead>();
      for (const [id, read] of current) {
        if (keep.has(id)) pruned.set(id, read);
        else changed = true;
      }
      return changed ? pruned : current;
    });
  }, []);

  const load = useCallback(async () => {
    const token = ++loadToken.current;
    const write = ++focusWrite.current;
    setLoading(true);
    const [projectsRead, tasksRead, attentionResult] = await Promise.all([
      settleRead(() => listProjects()),
      settleRead(() => readPage(MAX_PAGE, (limit) => listTasks(limit))),
      settleRead(() => listOpenAttention()),
    ]);
    if (token !== loadToken.current) return; // a newer refresh owns the screen
    const focusCurrent = write === focusWrite.current;
    if (projectsRead.status === "fulfilled") {
      setProjects(projectsRead.value.items);
      if (focusCurrent) {
        const next = projectsRead.value.today_focus ?? null;
        applyFocus(next);
        // Every refusal is a sentence about a snapshot, and this is a newer one. "Full"
        // survives only while the re-read still says full; "no answer" and "not registered"
        // are answered by the re-read itself — the tier on screen is now the server's.
        setRefusal((current) => (current?.kind === "full" && next !== null && next.is_full ? current : null));
        // The tier on screen is now the server's answer, so "unconfirmed" no longer holds.
        setDemoteError(null);
      }
      setProjectsReach("complete");
    } else {
      setProjects([]);
      if (focusCurrent) applyFocus(null);
      setProjectsReach("unreadable");
    }
    if (tasksRead.status === "fulfilled") {
      setTasks(tasksRead.value.items as readonly TaskManifest[]);
    } else {
      setTasks([]);
    }
    setTaskReach(taskInventoryReachOf(tasksRead));
    if (attentionResult.status === "fulfilled") {
      setAttention(attentionResult.value.items);
      setAttentionRead(attentionResult.value.truncated ? "capped" : "complete");
    } else {
      setAttention([]);
      setAttentionRead("unreadable");
    }
    // A refresh re-reads every card's dashboard. Dropped here, in the same batch as the
    // new tiers, so the refreshed screen never renders a card from the dashboard it had
    // before the refresh — the effect below re-requests them for the new `loadedAt`. The
    // generation bump keeps a pre-refresh answer still in flight from landing in the new map.
    dashboardGen.current += 1;
    setDashboards(new Map());
    setLoadedAt(Date.now());
    setLoading(false);
  }, [applyFocus]);

  useEffect(() => {
    const timeout = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timeout);
  }, [load]);

  // The focus tier is a list of project ids, and "which of these resolve" is a question
  // about the project list. When that list could not be read, the retained focus must
  // not be placed against an empty registry: every id would come out "unresolved", each
  // with a release button that really demotes a project that is still registered.
  const projectsKnown = projectsReach === "complete";
  const tiers = useMemo(
    () => todayTiersOf({
      projects,
      focus: projectsKnown ? focus : null,
      tasks,
      taskReach,
      nowMs: loadedAt ?? Number.NEGATIVE_INFINITY,
    }),
    [projects, projectsKnown, focus, tasks, taskReach, loadedAt],
  );
  const capacity = focusCapacityOf(projectsKnown ? focus : null);
  const byId = useMemo(() => new Map(projects.map((project) => [project.project_id, project])), [projects]);

  // One dashboard per focus card, each settled on its own. A promotion reads the new
  // card's dashboard only; a demotion reads nothing; a refresh (a new `loadedAt`) reads
  // them all again. Ids being read are first dropped from the map: a card whose dashboard
  // is in flight shows "reading", not the answer from before it was demoted and promoted
  // again, or from before the refresh — a retained dashboard rendered as current is a
  // stale next step wearing a fresh face. An id whose request was aborted by a re-run of
  // this effect is forgotten again, so the next run asks for it.
  const focusKey = tiers.focus.join("\n");
  useEffect(() => {
    const ids = focusKey === "" ? [] : focusKey.split("\n");
    const requested = dashboardsRequested.current;
    if (requested.loadedAt !== loadedAt) {
      requested.loadedAt = loadedAt;
      requested.ids = new Set();
    }
    // A project that left the tier is forgotten: if it comes back, its state may have
    // changed in between, and the dashboard from before it left must not stand in.
    for (const id of Array.from(requested.ids)) if (!ids.includes(id)) requested.ids.delete(id);
    const toRead = ids.filter((id) => !requested.ids.has(id));
    if (toRead.length === 0) return;
    for (const id of toRead) requested.ids.add(id);
    const gen = dashboardGen.current;
    // A late answer writes only into the map it was asked for: same generation (no refresh
    // since) and an id still wanted (not pruned by a demotion since).
    const stillWanted = (id: string) => gen === dashboardGen.current && requested.ids.has(id);
    const ctrl = new AbortController();
    // Ids this run has answered for. The cleanup forgets every other id of this run
    // synchronously, so a re-run that starts in the same tick sees them as unread —
    // waiting for the abort to reject would let the next run miss them.
    const settled = new Set<string>();
    const timeout = window.setTimeout(() => {
      setDashboards((current) => {
        const next = new Map(current);
        for (const id of toRead) next.delete(id);
        return next;
      });
      for (const id of toRead) {
        fetchProjectDashboard(id, ctrl.signal).then(
          (dashboard) => {
            if (ctrl.signal.aborted || !stillWanted(id)) return;
            settled.add(id);
            setDashboards((current) => new Map(current).set(id, { dashboard, readFailed: false }));
          },
          () => {
            if (ctrl.signal.aborted || !stillWanted(id)) return;
            settled.add(id);
            setDashboards((current) => new Map(current).set(id, { dashboard: null, readFailed: true }));
          },
        );
      }
    }, 0);
    return () => {
      window.clearTimeout(timeout);
      // Anything this run did not answer for — never requested, or still in flight — is
      // forgotten, so the next run asks for it. Answered ones stay remembered.
      for (const id of toRead) if (!settled.has(id)) requested.ids.delete(id);
      ctrl.abort();
    };
  }, [focusKey, loadedAt]);

  const promote = useCallback(async (projectId: string) => {
    setBusy(projectId);
    setRefusal(null);
    const write = ++focusWrite.current;
    try {
      const next = await promoteProjectToToday(projectId);
      if (write === focusWrite.current) {
        // The response is the whole tier from the server, so every notice bound to an
        // older snapshot — a refusal, an unconfirmed demotion — is retired with it.
        applyFocus(next);
        setRefusal(null);
        setDemoteError(null);
      }
    } catch (error) {
      const kind = promoteRefusalOf(
        error instanceof ApiError ? { status: error.status, code: error.code } : { status: null, code: null },
      );
      setRefusal({ projectId, kind });
      // Refused, or unanswered: either way the tier this screen shows may be behind the
      // server's (someone else promoted, a project was removed, the PUT landed and its
      // answer did not). Re-read rather than reason about it; the re-read retires every
      // notice it answers.
      void load();
    } finally {
      setBusy(null);
    }
  }, [load, applyFocus]);

  const demote = useCallback(async (projectId: string) => {
    setBusy(projectId);
    setDemoteError(null);
    const write = ++focusWrite.current;
    try {
      const next = await demoteProjectFromToday(projectId);
      if (write === focusWrite.current) {
        applyFocus(next);
        setRefusal(null);
        setDemoteError(null);
      }
    } catch (error) {
      // The endpoint is idempotent and a lost response is not a refused request: the
      // server may well have demoted. Say the answer was lost and re-read the tier, rather
      // than assert a failure and keep a card the server no longer holds.
      setDemoteError(error instanceof Error ? error.message : String(error));
      void load();
    } finally {
      setBusy(null);
    }
  }, [load, applyFocus]);

  // --- the cross-project "stuck / needs you" list -----------------------------------
  //
  // One row per stuck *thing*, as the mockup draws it, not one per task: a task holding
  // an approval and a blocker is two things to deal with, and a list that showed one of
  // them would hide the other under a count that reads as exact. A lifecycle state that
  // demands action by itself (review / blocked / failed) is a thing too, and gets its own
  // row whether or not the task also carries items — a failed task with an open approval
  // is two facts, not one. Each row is coloured by its own signal: an item row by its
  // kind, a state row by its state, through the same judgement that places tasks.
  const itemsByTask = useMemo(() => {
    const map = new Map<string, AttentionItem[]>();
    for (const item of attention) {
      const list = map.get(item.task_id) ?? [];
      list.push(item);
      map.set(item.task_id, list);
    }
    return map;
  }, [attention]);

  const placed = useMemo(() => {
    const complete = attentionReadCovers(attentionRead);
    // Archived tasks are not filtered out here. Archiving is a display property, not a
    // lifecycle transition: an archived task can still hold an open approval or blocker,
    // and dropping it from this list while the reads were complete would let the
    // all-clear sentence below stand over an item that needs someone.
    return tasks.map((task) => ({
      task,
      bucket: portfolioBucketOf({
        task,
        kinds: new Set((itemsByTask.get(task.task_id) ?? []).map((item) => item.kind)),
        attentionComplete: complete,
      }),
    }));
  }, [tasks, itemsByTask, attentionRead]);

  const stuckRows = useMemo<readonly StuckRow[]>(() => {
    const rows: StuckRow[] = [];
    // The column one observed kind lands in, on its own. `portfolioBucketOf` answers that
    // before it consults the state or the read's reach, so an empty state and an
    // incomplete read change nothing here — the item is on the page.
    const bucketOfKind = (kind: string): PortfolioBucket =>
      portfolioBucketOf({ task: { state: "" }, kinds: new Set([kind]), attentionComplete: false });
    // The column a lifecycle state lands in by itself, with no item consulted; only the
    // three action columns count as "the state demands action".
    const bucketOfState = (state: string): PortfolioBucket | null => {
      const bucket = portfolioBucketOf({ task: { state }, kinds: new Set(), attentionComplete: true });
      return NEEDS_YOU.includes(bucket) ? bucket : null;
    };
    for (const { task, bucket } of placed) {
      if (!NEEDS_YOU.includes(bucket)) continue;
      const stateBucket = bucketOfState(task.state);
      if (stateBucket !== null) {
        rows.push({
          key: task.task_id, task_id: task.task_id, project_id: task.project_id, bucket: stateBucket,
          title: task.title, detail: { kind: "state", state: task.state },
        });
      }
      for (const item of itemsByTask.get(task.task_id) ?? []) {
        rows.push({
          key: item.item_id, task_id: task.task_id, project_id: task.project_id, bucket: bucketOfKind(item.kind),
          title: item.title, detail: { kind: "item", itemKind: item.kind, taskTitle: task.title },
        });
      }
    }
    // Open items whose task is not on the task page. The two reads are not one snapshot:
    // a task created between them arrives with its item and without its manifest. The
    // item is on the page, so it is shown — as itself, since the task's title and state
    // were not read — rather than dropped under an all-clear sentence that the two
    // "complete" reads would otherwise support. Its column comes from the same judgement
    // as every other row's, fed the one kind that was observed.
    const known = new Set(tasks.map((task) => task.task_id));
    for (const item of attention) {
      if (known.has(item.task_id)) continue;
      rows.push({
        key: item.item_id, task_id: item.task_id, project_id: item.project_id, bucket: bucketOfKind(item.kind),
        title: item.title, detail: { kind: "orphan", itemKind: item.kind },
      });
    }
    return rows;
  }, [placed, itemsByTask, attention, tasks]);

  // Tasks the reads could not place: no observed item, and an attention read that did not
  // cover everything. One row per task — there is no item to be the row.
  const unknownRows = useMemo<readonly StuckRow[]>(() => placed
    .filter((row) => row.bucket === "unknown")
    .map(({ task, bucket }) => ({
      key: task.task_id, task_id: task.task_id, project_id: task.project_id, bucket,
      title: task.title, detail: { kind: "state", state: task.state },
    })), [placed]);
  const allClear = portfolioAllClearSayable({ attentionRead, taskInventory: taskReach });

  // The heading counts every moving project, focus cards included (the mockup's "4 个在
  // 推进" does); the second tier's own badge counts only what it lists.
  const activeCount = advancingCountOf(tiers.active.length, taskReach);
  const advancingCount = advancingCountOf(tiers.advancing.length, taskReach);
  const refusalText = (projectId: string): string | null => {
    if (refusal === null || refusal.projectId !== projectId) return null;
    return t(`today.focus.refusal.${refusal.kind}`, { limit: focus?.limit ?? "" });
  };
  const dateLine = loadedAt === null
    ? null
    : new Date(loadedAt).toLocaleDateString(locale === "zh" ? "zh-CN" : "en-US", {
      year: "numeric", month: "long", day: "numeric", weekday: "short",
    });

  return (
    <DeliveryShell active="Today">
      <div className="mx-auto max-w-6xl space-y-6 p-6">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div className="space-y-1">
            <h1 className="text-2xl font-bold">{t("today.title")}</h1>
            <p className="text-sm text-muted-foreground">
              {dateLine && <span>{dateLine} · </span>}
              {projectsReach === "unreadable"
                ? t("today.header.projectsUnreadable")
                : projectsReach === "complete" && (
                  activeCount.kind === "exact"
                    ? t("today.header.countsExact", { projects: projects.length, advancing: activeCount.count })
                    : activeCount.kind === "atLeast"
                      ? t("today.header.countsAtLeast", { projects: projects.length, advancing: activeCount.count })
                      : t("today.header.projectsOnly", { projects: projects.length })
                )}
            </p>
            <p className="text-xs text-muted-foreground">
              {t("today.subtitle")}{" "}
              <Link href="/portfolio" className="text-primary hover:underline">{t("common.nav.projects")}</Link>
            </p>
          </div>
          <Button variant="outline" size="lg" onClick={() => void load()} disabled={loading || busy !== null} aria-label={t("portfolio.actions.refresh")}>
            <RefreshCw className={cn("size-4", loading && "animate-spin")} />
          </Button>
        </header>

        {/* Reach first, before any tier, because every tier below means something
            different depending on it. */}
        <div className="space-y-2">
          {taskReach === "unreadable" && <Notice>{t("today.reach.tasksUnreadable")}</Notice>}
          {taskReach === "capped" && <Notice>{t("today.reach.tasksCapped", { limit: MAX_PAGE })}</Notice>}
          {attentionRead === "unreadable" && <Notice>{t("today.reach.attentionUnreadable")}</Notice>}
          {attentionRead === "capped" && <Notice>{t("today.reach.attentionCapped")}</Notice>}
        </div>

        {/* ---- tier 1: today's focus ------------------------------------------------ */}
        <section className="space-y-3">
          <header className="flex flex-wrap items-baseline gap-3">
            <h2 className="text-base font-semibold">{t("today.focus.title")}</h2>
            {capacity.kind === "full" ? (
              <span className="text-xs font-medium text-amber-700 dark:text-amber-300">
                {t("today.focus.capacityFull", { used: capacity.used, limit: capacity.limit })}
              </span>
            ) : capacity.kind === "open" ? (
              <span className="text-xs tabular-nums text-muted-foreground">
                {t("today.focus.capacityOpen", { used: capacity.used, limit: capacity.limit })}
              </span>
            ) : null}
          </header>

          {projectsReach === "unreadable" && <Notice>{t("today.focus.projectsUnreadable")}</Notice>}
          {projectsReach === "complete" && !tiers.focusReported && (
            <p className="text-sm text-muted-foreground">{t("today.focus.unreported")}</p>
          )}
          {tiers.focusReported && tiers.focus.length === 0 && tiers.unresolvedFocus.length === 0 && (
            <p className="rounded-xl border border-dashed px-4 py-6 text-center text-sm text-muted-foreground">
              {t("today.focus.empty", { limit: focus?.limit ?? "" })}
            </p>
          )}
          {demoteError && <Notice>{t("today.focus.demoteFailed", { error: demoteError })}</Notice>}

          {tiers.focus.length > 0 && (
            <div className="grid gap-4 lg:grid-cols-3">
              {tiers.focus.map((id) => {
                const project = byId.get(id);
                if (!project) return null; // filtered upstream; the type does not know that
                const read = dashboards.get(id);
                const next = continueTaskOf(tasks, id, taskReach);
                return (
                  <TodayFocusCard
                    key={id}
                    project={project}
                    dashboard={read?.dashboard ?? null}
                    dashboardReadFailed={read?.readFailed ?? false}
                    continueTask={next === null ? null : { task_id: next.task_id, title: next.title }}
                    onDemote={() => void demote(id)}
                    busy={busy !== null || loading}
                  />
                );
              })}
            </div>
          )}

          {/* Seats held by ids that no longer resolve: shown, because the count above
              includes them, and a seat nobody can see is a seat nobody can release. */}
          {tiers.unresolvedFocus.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs text-amber-800 dark:text-amber-300">
              <span>
                {t("today.focus.unresolved", {
                  count: tiers.unresolvedFocus.length,
                  ids: tiers.unresolvedFocus.join(", "),
                })}
              </span>
              {tiers.unresolvedFocus.map((id) => (
                <Button key={id} variant="outline" size="sm" onClick={() => void demote(id)} disabled={busy !== null || loading}>
                  {t("today.focus.release")} · {id}
                </Button>
              ))}
            </div>
          )}
        </section>

        {/* ---- tier 2: advancing, not today's focus ------------------------------- */}
        <section className="overflow-hidden rounded-xl border bg-card">
          <header className="flex flex-wrap items-baseline gap-3 border-b bg-muted/40 px-4 py-2.5">
            <h2 className="text-sm font-semibold">{t("today.advancing.title")}</h2>
            <span className="text-xs text-muted-foreground">{t("today.advancing.note", { days: ADVANCING_WINDOW_DAYS })}</span>
            {advancingCount.kind !== "unknown" && (
              <span className="ml-auto text-xs tabular-nums text-muted-foreground">
                {advancingCount.kind === "exact"
                  ? advancingCount.count
                  : t("today.needsYou.atLeast", { count: advancingCount.count })}
              </span>
            )}
          </header>
          {tiers.advancing.length === 0 ? (
            // Only a complete task read can say no other project moved. On any other
            // read, the unplaced section below already says why this list is short.
            taskReach === "complete" && projectsReach === "complete" && (
              <p className="px-4 py-3 text-sm text-muted-foreground">{t("today.advancing.none", { days: ADVANCING_WINDOW_DAYS })}</p>
            )
          ) : (
            <ul>
              {tiers.advancing.map((id) => {
                const project = byId.get(id);
                if (!project) return null;
                const open = openTaskCountOf(tasks, id, taskReach);
                return (
                  <TodayProjectRow
                    key={id}
                    project={project}
                    detail={open.kind === "exact"
                      ? t("today.advancing.openExact", { count: open.count })
                      : open.kind === "atLeast"
                        ? t("today.advancing.openAtLeast", { count: open.count })
                        : t("today.advancing.openUnknown")}
                    onPromote={() => void promote(id)}
                    canPromote={tiers.focusReported}
                    busy={busy !== null || loading}
                    refusal={refusalText(id)}
                  />
                );
              })}
            </ul>
          )}
        </section>

        {/* ---- projects the task read cannot place ------------------------------- */}
        {tiers.unplaced.length > 0 && (
          <section className="overflow-hidden rounded-xl border bg-card">
            <header className="flex flex-wrap items-baseline gap-3 border-b bg-muted/40 px-4 py-2.5">
              <h2 className="text-sm font-semibold">{t("today.unplaced.title")}</h2>
              <span className="text-xs text-muted-foreground">{t("today.unplaced.note", { days: ADVANCING_WINDOW_DAYS })}</span>
              <span className="ml-auto text-xs tabular-nums text-muted-foreground">{tiers.unplaced.length}</span>
            </header>
            <ul>
              {tiers.unplaced.map((id) => {
                const project = byId.get(id);
                if (!project) return null;
                const open = openTaskCountOf(tasks, id, taskReach);
                // Why this row is here, then what was measured. Two different doubts: the
                // project's (an unreadable stamp on a complete read) or the read's.
                const why = tiers.undated.includes(id) ? t("today.unplaced.undated") : t("today.unplaced.readShort");
                const measured = open.kind === "exact"
                  ? t("today.advancing.openExact", { count: open.count })
                  : open.kind === "atLeast"
                    ? t("today.advancing.openAtLeast", { count: open.count })
                    : t("today.advancing.openUnknown");
                return (
                  <TodayProjectRow
                    key={id}
                    project={project}
                    detail={`${why} · ${measured}`}
                    onPromote={() => void promote(id)}
                    canPromote={tiers.focusReported}
                    busy={busy !== null || loading}
                    refusal={refusalText(id)}
                  />
                );
              })}
            </ul>
          </section>
        )}

        {/* ---- tier 3: dormant, folded ------------------------------------------- */}
        {tiers.dormant.length > 0 && (
          <details className="rounded-xl border bg-card">
            <summary className="cursor-pointer px-4 py-3 text-sm font-medium">
              {t("today.dormant.summary", { count: tiers.dormant.length, days: ADVANCING_WINDOW_DAYS })}
            </summary>
            <ul className="border-t">
              {tiers.dormant.map((id) => {
                const project = byId.get(id);
                if (!project) return null;
                return (
                  <TodayProjectRow
                    key={id}
                    project={project}
                    detail={t("today.dormant.note", { days: ADVANCING_WINDOW_DAYS })}
                    onPromote={() => void promote(id)}
                    canPromote={tiers.focusReported}
                    busy={busy !== null || loading}
                    refusal={refusalText(id)}
                  />
                );
              })}
            </ul>
          </details>
        )}

        {/* ---- stuck / needs you, across every project ---------------------------- */}
        <Group
          title={t("today.needsYou.title")}
          note={t("today.needsYou.note")}
          rows={stuckRows}
          emptyMessage={allClear ? t("today.needsYou.clear") : t("today.needsYou.unsure")}
          countExact={allClear}
          te={te}
          t={t}
        />

        {unknownRows.length > 0 && (
          <Group
            title={t("today.needsYou.unknownTitle")}
            note={t("today.needsYou.unknownNote")}
            rows={unknownRows}
            emptyMessage={null}
            countExact={allClear}
            te={te}
            t={t}
          />
        )}
      </div>
    </DeliveryShell>
  );
}

/**
 * One row of the stuck list. The three `detail` shapes are the three things a row can be:
 * a task reached on its state alone, one observed item on a known task, or one observed
 * item whose task the task page did not carry. Kept as data so the renderer decides
 * nothing: it prints the shape it was given.
 */
interface StuckRow {
  key: string;
  task_id: string;
  project_id: string;
  bucket: PortfolioBucket;
  title: string;
  detail:
    | { kind: "state"; state: string }
    | { kind: "item"; itemKind: string; taskTitle: string }
    | { kind: "orphan"; itemKind: string };
}

function Group({
  title, note, rows, emptyMessage, countExact, t, te,
}: {
  title: string;
  note?: string;
  rows: readonly StuckRow[];
  /** Null when there is nothing true to say about an empty group. */
  emptyMessage: string | null;
  /**
   * Whether `rows.length` is the whole count. False when either read fell short,
   * and then the badge shows "at least N" — a capped attention read could
   * otherwise put a confident `0` on the group that means "something needs you".
   */
  countExact: boolean;
  t: (key: string, vars?: Record<string, string | number>) => string;
  te: (group: string, value: string) => string;
}) {
  return (
    <section className="overflow-hidden rounded-xl border bg-card">
      <header className="flex flex-wrap items-baseline gap-3 border-b bg-muted/40 px-4 py-2.5">
        <h2 className="text-sm font-semibold">{title}</h2>
        {note && <span className="text-xs text-muted-foreground">{note}</span>}
        <span className="ml-auto text-xs text-muted-foreground">
          {countExact ? rows.length : t("today.needsYou.atLeast", { count: rows.length })}
        </span>
      </header>
      {rows.length === 0
        ? emptyMessage && <p className="px-4 py-3 text-sm text-muted-foreground">{emptyMessage}</p>
        : rows.map((row) => <Row key={row.key} row={row} t={t} te={te} />)}
    </section>
  );
}

const BUCKET_TONE: Record<PortfolioBucket, string> = {
  needsReply: "text-amber-600 dark:text-amber-400",
  needsApproval: "text-amber-600 dark:text-amber-400",
  blocked: "text-red-600 dark:text-red-400",
  running: "text-blue-600 dark:text-blue-400",
  unknown: "text-muted-foreground",
  verified: "text-emerald-600 dark:text-emerald-400",
  done: "text-emerald-600 dark:text-emerald-400",
  closed: "text-muted-foreground",
  shaping: "text-muted-foreground",
};

/** One stuck thing. The source project is on the right, as the mockup draws it. */
function Row({
  row, t, te,
}: {
  row: StuckRow;
  t: (key: string, vars?: Record<string, string | number>) => string;
  te: (group: string, value: string) => string;
}) {
  const detail = row.detail.kind === "state"
    ? `${te("taskState", row.detail.state)} · ${t(`portfolio.buckets.${row.bucket}`)}`
    : row.detail.kind === "item"
      ? `${te("attentionKind", row.detail.itemKind)} · ${row.detail.taskTitle}`
      : `${te("attentionKind", row.detail.itemKind)} · ${t("today.needsYou.orphanState")}`;
  return (
    <Link
      href={`/workspace?task=${encodeURIComponent(row.task_id)}`}
      className="flex items-center gap-3 border-b px-4 py-2.5 last:border-b-0 hover:bg-accent/50"
    >
      <CircleDot className={cn("size-3.5 shrink-0", BUCKET_TONE[row.bucket])} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">{row.title}</span>
        <span className="block truncate text-xs text-muted-foreground">{detail}</span>
      </span>
      <span className="shrink-0 text-xs text-muted-foreground">{row.project_id}</span>
      <ArrowRight className="size-4 shrink-0 text-muted-foreground" />
    </Link>
  );
}

function Notice({ children }: { children: React.ReactNode }) {
  return (
    <p className="rounded-md border border-amber-500/30 bg-amber-500/5 px-3 py-2 text-xs leading-relaxed text-amber-800 dark:text-amber-300">
      {children}
    </p>
  );
}

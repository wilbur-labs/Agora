"use client";

/**
 * Today — mockup v2, screen 1.
 *
 * The question this screen answers is "what should I pick up now", and the only
 * honest way to answer it is with two reads that can each fail: which tasks exist,
 * and which of them have something open against you. Every claim below is placed
 * by `lib/portfolio-bucket.ts`, which already knows the rule that matters here: an
 * empty action column is reassuring **only** when both reads were complete. A
 * capped or failed read puts a task in `unknown`, never in "nothing waiting".
 *
 * What the mockup has and this does not, said rather than faked:
 *
 * - the **three-tier focus picker** ("today's focus, max 3"). That is a choice the
 *   user makes and Agora would have to store; there is no place to store it, and a
 *   picker whose choice evaporates on reload is worse than none.
 * - the **dormant-project inventory** (nine projects, four advancing). Today reads
 *   tasks, not the disk, so it cannot see a project nobody has registered. The
 *   full board stays at /portfolio until it can.
 */

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { ArrowRight, CircleDot } from "lucide-react";

import { DeliveryShell } from "@/components/delivery-shell";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";
import { MAX_PAGE, readPage, type Page } from "@/lib/paged";
import {
  listOpenAttention,
  listTasks,
  type AttentionItem,
  type TaskManifest,
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

/** Buckets that mean something is waiting on the human. */
const NEEDS_YOU: readonly PortfolioBucket[] = ["needsReply", "needsApproval", "blocked"];

export default function TodayPage() {
  const { t, te } = useI18n();
  const [tasks, setTasks] = useState<readonly TaskManifest[]>([]);
  const [taskReach, setTaskReach] = useState<TaskInventoryReach>("pending");
  const [attention, setAttention] = useState<readonly AttentionItem[]>([]);
  const [attentionRead, setAttentionRead] = useState<AttentionRead>("pending");

  useEffect(() => {
    let cancelled = false;
    // Settled independently. Sharing one catch is how a failing task read once
    // made the attention read look unreadable too, and the board then said the
    // wrong thing for the wrong reason.
    void Promise.allSettled([
      readPage(MAX_PAGE, (limit) => listTasks(limit)),
      listOpenAttention(),
    ]).then(([taskResult, attentionResult]) => {
      if (cancelled) return;
      if (taskResult.status === "fulfilled") {
        setTasks(taskResult.value.items as readonly TaskManifest[]);
      } else {
        setTasks([]);
      }
      setTaskReach(taskInventoryReachOf(taskResult as PromiseSettledResult<Page<unknown>>));
      if (attentionResult.status === "fulfilled") {
        setAttention(attentionResult.value.items);
        setAttentionRead(attentionResult.value.truncated ? "capped" : "complete");
      } else {
        setAttention([]);
        setAttentionRead("unreadable");
      }
    });
    return () => { cancelled = true; };
  }, []);

  const kindsByTask = useMemo(() => {
    const map = new Map<string, Set<string>>();
    for (const item of attention) {
      const set = map.get(item.task_id) ?? new Set<string>();
      set.add(item.kind);
      map.set(item.task_id, set);
    }
    return map;
  }, [attention]);

  const placed = useMemo(() => {
    const complete = attentionReadCovers(attentionRead);
    return tasks.map((task) => ({
      task,
      bucket: portfolioBucketOf({
        task,
        kinds: kindsByTask.get(task.task_id) ?? new Set<string>(),
        attentionComplete: complete,
      }),
    }));
  }, [tasks, kindsByTask, attentionRead]);

  const needsYou = placed.filter((row) => NEEDS_YOU.includes(row.bucket));
  const running = placed.filter((row) => row.bucket === "running");
  const unknown = placed.filter((row) => row.bucket === "unknown");
  const rest = placed.filter(
    (row) => !NEEDS_YOU.includes(row.bucket) && row.bucket !== "running" && row.bucket !== "unknown",
  );
  const allClear = portfolioAllClearSayable({ attentionRead, taskInventory: taskReach });

  return (
    <DeliveryShell active="Today">
      <div className="mx-auto max-w-5xl space-y-6 p-6">
        <header className="space-y-1">
          <h1 className="text-2xl font-bold">{t("today.title")}</h1>
          <p className="text-sm text-muted-foreground">{t("today.subtitle")}</p>
        </header>

        {/* Reach first, before any list, because every list below means something
            different depending on it. */}
        <div className="space-y-2">
          {taskReach === "unreadable" && <Notice>{t("today.reach.tasksUnreadable")}</Notice>}
          {taskReach === "capped" && <Notice>{t("today.reach.tasksCapped", { limit: MAX_PAGE })}</Notice>}
          {attentionRead === "unreadable" && <Notice>{t("today.reach.attentionUnreadable")}</Notice>}
          {attentionRead === "capped" && <Notice>{t("today.reach.attentionCapped")}</Notice>}
        </div>

        <Group
          title={t("today.groups.needsYou")}
          note={t("today.groups.needsYouNote")}
          rows={needsYou}
          emptyMessage={allClear ? t("today.groups.needsYouClear") : t("today.groups.needsYouUnsure")}
          countExact={allClear}
          te={te}
          t={t}
        />

        <Group
          title={t("today.groups.running")}
          rows={running}
          // "Nothing is running" needs the attention read too, not just the task
          // read: a running task with no observed item is placed in `unknown`
          // when attention is incomplete, so this line could sit on screen above
          // the very tasks it denied.
          emptyMessage={allClear ? t("today.groups.runningNone") : null}
          countExact={allClear}
          te={te}
          t={t}
        />

        {unknown.length > 0 && (
          <Group
            title={t("today.groups.unknown")}
            note={t("today.groups.unknownNote")}
            rows={unknown}
            emptyMessage={null}
            countExact={allClear}
            te={te}
            t={t}
          />
        )}

        {rest.length > 0 && (
          <details className="rounded-xl border bg-card">
            <summary className="cursor-pointer px-4 py-3 text-sm font-medium">
              {t("today.groups.rest", { count: rest.length })}
            </summary>
            <div className="border-t">
              {rest.map((row) => <Row key={row.task.task_id} row={row} te={te} t={t} />)}
            </div>
          </details>
        )}

        <footer className="space-y-1 border-t pt-4 text-xs text-muted-foreground">
          <p>{t("today.missing.focus")}</p>
          <p>
            {t("today.missing.inventory")}{" "}
            <Link href="/portfolio" className="text-primary hover:underline">
              {t("common.nav.portfolio")}
            </Link>
          </p>
        </footer>
      </div>
    </DeliveryShell>
  );
}

type Placed = { task: TaskManifest; bucket: PortfolioBucket };

function Group({
  title, note, rows, emptyMessage, countExact, t, te,
}: {
  title: string;
  note?: string;
  rows: readonly Placed[];
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
          {countExact ? rows.length : t("today.groups.atLeast", { count: rows.length })}
        </span>
      </header>
      {rows.length === 0
        ? emptyMessage && <p className="px-4 py-3 text-sm text-muted-foreground">{emptyMessage}</p>
        : rows.map((row) => <Row key={row.task.task_id} row={row} t={t} te={te} />)}
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

function Row({
  row, t, te,
}: {
  row: Placed;
  t: (key: string, vars?: Record<string, string | number>) => string;
  te: (group: string, value: string) => string;
}) {
  const { task, bucket } = row;
  return (
    <Link
      href={`/workspace?task=${encodeURIComponent(task.task_id)}`}
      className="flex items-center gap-3 border-b px-4 py-2.5 last:border-b-0 hover:bg-accent/50"
    >
      <CircleDot className={cn("size-3.5 shrink-0", BUCKET_TONE[bucket])} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-sm font-medium">{task.title}</span>
        <span className="block truncate text-xs text-muted-foreground">
          {task.project_id} · {te("taskState", task.state)}
        </span>
      </span>
      <span className={cn("shrink-0 text-xs font-medium", BUCKET_TONE[bucket])}>
        {t(`portfolio.buckets.${bucket}`)}
      </span>
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

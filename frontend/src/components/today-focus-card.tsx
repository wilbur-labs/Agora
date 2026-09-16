"use client";

/**
 * One "今天主推" card — mockup v2, screen 1, the big card.
 *
 * The mockup's card carries: project name · current milestone · environment · branch +
 * commit + pushed · "昨天 / 上次" · "现在" (the next step) · "在册 / 拦路 / 发现" · the
 * goal in one sentence · the progress snapshot bar · "继续工作 →" / "先看看这个项目".
 *
 * What this product can read today, and what it cannot, is decided in
 * lib/today-focus.ts (`TODAY_CARD_NO_READER`, `TODAY_CARD_DEFECTS`) and
 * lib/portfolio-bucket.ts (`workspaceProjectStripOf`: the next step and the blocking
 * count, with the dashboard's own reach) and lib/progress-snapshot.ts (`progressBarOf`).
 * Nothing is derived here: the card receives facts and draws them, so a field the
 * product cannot read is drawn as the sentence saying so — the mockup's own rule for a
 * project without a snapshot is "留空不编数字", and the constitution's is that "could not
 * read" is never rendered as "none".
 *
 * Not on the card, by recorded decision: a "换个 agent" button (the mockup cut it — the
 * executor is chosen when work is dispatched, not here). Not on the card, because there
 * is nothing behind it: the mockup's resume footnote ("恢复 worktree … 会话 …"); sessions
 * are per task and nothing rolls one up per project.
 */

import Link from "next/link";
import { ArrowDownToLine, ArrowRight, Eye, Sparkles } from "lucide-react";

import { Button, buttonVariants } from "@/components/ui/button";
import { CapabilitySlot } from "@/components/capability-slot";
import { ProgressSnapshotBar } from "@/components/progress-snapshot-bar";
import { useI18n } from "@/lib/i18n";
import type { ProjectDashboard, ProjectListItem } from "@/lib/control-plane";
import { workspaceProjectStripOf } from "@/lib/portfolio-bucket";
import { progressBarOf } from "@/lib/progress-snapshot";
import { TODAY_CARD_DEFECTS, TODAY_CARD_NO_READER, goalLineOf } from "@/lib/today-focus";
import { cn } from "@/lib/utils";

export interface TodayFocusCardProps {
  project: ProjectListItem;
  /** The project's dashboard once read; `null` while in flight or after a failed read. */
  dashboard: ProjectDashboard | null;
  /** Whether the most recent dashboard read failed (as opposed to not having answered). */
  dashboardReadFailed: boolean;
  /** Where "继续工作 →" goes: the most recently updated open task, or nothing. */
  continueTask: { task_id: string; title: string } | null;
  onDemote: () => void;
  busy: boolean;
}

export function TodayFocusCard({
  project, dashboard, dashboardReadFailed, continueTask, onDemote, busy,
}: TodayFocusCardProps) {
  const { t, te } = useI18n();
  const goal = goalLineOf(project.charter.goal);
  // The dashboard's own reach decides what the next step and the blocking count may
  // say: a partial one says "at least". (`readFailed` with a dashboard in hand — the
  // strip's "stale" — cannot arise from this page, which drops a dashboard before
  // re-reading it; the branch below stays because the strip's type has it.)
  const strip = dashboard
    ? workspaceProjectStripOf({
      readFailed: dashboardReadFailed,
      truncated: dashboard.truncated,
      done: dashboard.tasks.filter((it) => it.state === "done").length,
      pageSize: dashboard.tasks.length,
      action: dashboard.next_action.action,
      partial: dashboard.next_action.partial,
      attentionCount: dashboard.next_action.attention_count,
      attention: dashboard.next_action.attention,
      title: dashboard.next_action.title,
    })
    : null;
  const bar = progressBarOf({
    snapshot: dashboard?.progress_snapshot,
    dashboardLoaded: dashboard !== null,
    dashboardReadFailed,
  });
  const workbenchHref = `/workspace?project=${encodeURIComponent(project.project_id)}`;

  return (
    <article className="flex flex-col gap-4 rounded-2xl border bg-card p-5 shadow-sm">
      <header className="space-y-1.5">
        <h3 className="truncate text-lg font-bold leading-tight" title={project.project_id}>
          {project.name}
        </h3>
        {/* Milestone · environment · git state: the mockup's second line. None of the
            three has a reader yet, so each is the sentence saying so, labelled. */}
        <dl className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
          <Field label={t("today.card.fields.milestone")}>
            <CapabilitySlot capability={TODAY_CARD_NO_READER.milestone} />
          </Field>
          <Field label={t("today.card.fields.environment")}>
            <CapabilitySlot capability={TODAY_CARD_NO_READER.environment} />
          </Field>
          <Field label={t("today.card.fields.gitState")}>
            <CapabilitySlot capability={TODAY_CARD_NO_READER.gitState} />
          </Field>
        </dl>
      </header>

      <dl className="space-y-2 text-sm">
        <Row label={t("today.card.fields.lastTime")}>
          <CapabilitySlot capability={TODAY_CARD_NO_READER.lastTime} className="text-xs" />
        </Row>

        <Row label={t("today.card.fields.now")} emphasis>
          {strip === null ? (
            <span className="text-xs text-muted-foreground">
              {dashboardReadFailed ? t("today.card.dashboardUnreadable") : t("today.card.dashboardLoading")}
            </span>
          ) : strip.next.kind === "unknown" ? (
            <span className="text-xs text-muted-foreground">
              {strip.claim === "stale" ? t("today.card.dashboardStale") : t("portfolio.cockpit.nextAction.unknown")}
            </span>
          ) : strip.next.kind === "allClear" ? (
            <span className="font-medium">{t("portfolio.cockpit.nextAction.allClear")}</span>
          ) : (
            <span className={cn("font-semibold", strip.next.attention && "text-orange-600 dark:text-orange-400")}>
              {te("nextAction", strip.next.action)}
              {strip.next.title && (
                <span className="ml-2 font-normal text-muted-foreground">
                  · {t("portfolio.cockpit.nextAction.forTask", { title: strip.next.title })}
                </span>
              )}
            </span>
          )}
        </Row>

        {/* Blocking: the dashboard's attention count, with its reach. Hidden when the
            dashboard says zero on a current read — a "0 blocking" line is a claim the
            mockup does not draw either; absent when stale, because a stale zero is not
            a zero. */}
        {strip !== null && strip.attention.kind !== "hidden" && (
          <Row label={t("today.card.fields.blocking")}>
            <span className="font-medium text-orange-600 dark:text-orange-400">
              {strip.attention.kind === "exact"
                ? t("today.card.blockingExact", { count: strip.attention.count })
                : t("today.card.blockingAtLeast", { count: strip.attention.count })}
            </span>
          </Row>
        )}

        <Row label={t("today.card.fields.defects")}>
          <CapabilitySlot capability={TODAY_CARD_DEFECTS} className="text-xs" />
        </Row>
        <Row label={t("today.card.fields.discovery")}>
          <CapabilitySlot capability={TODAY_CARD_NO_READER.discovery} className="text-xs" />
        </Row>

        <Row label={t("today.card.fields.goal")}>
          {goal.kind === "goal" ? (
            <span className="leading-relaxed">{goal.text}</span>
          ) : (
            // No link here any more. The way in used to be a few words at the end of this
            // line, and the owner looked at this card repeatedly without ever seeing it
            // (CR-59); the card's own action is the entry now, and it is the only one.
            <span className="text-xs text-muted-foreground">{t("today.card.goalNone")}</span>
          )}
        </Row>
      </dl>

      <div className="rounded-xl border bg-muted/30 p-3">
        <p className="text-xs font-medium text-muted-foreground">{t("today.card.fields.progress")}</p>
        <ProgressSnapshotBar bar={bar} source={dashboard?.progress_snapshot?.source} className="mt-1" />
      </div>

      <footer className="mt-auto flex flex-wrap items-center gap-2 pt-1">
        {/* A project with no goal gets one action and no others (CR-59, CR-61): "continue"
            and "look at this project" both invite pushing a project nobody has said what
            is for, and the goal is settled in the workbench's middle column. */}
        {goal.kind !== "goal" ? (
          <>
            <Link href={workbenchHref} className={cn(buttonVariants({ size: "lg" }))}>
              <Sparkles /> {t("today.card.actions.settleGoal")}
            </Link>
            <span className="text-xs text-muted-foreground">{t("today.card.actions.settleGoalHint")}</span>
          </>
        ) : (
          <>
        {continueTask !== null && (
          <Link
            // The project's conversation, not a task page (W12): the step to continue is
            // the one the conversation's box already points at.
            href={workbenchHref}
            title={t("today.card.actions.continueTitle", { title: continueTask.title })}
            className={cn(buttonVariants({ size: "lg" }))}
          >
            {t("today.card.actions.continue")}
          </Link>
        )}
        <Link
          href={workbenchHref}
          title={t("today.card.actions.lookTitle")}
          className={cn(buttonVariants({ size: "lg", variant: continueTask === null ? "default" : "outline" }))}
        >
          <Eye /> {t("today.card.actions.look")}
        </Link>
          </>
        )}
        <Button
          variant="ghost"
          size="lg"
          className="ml-auto text-muted-foreground"
          onClick={onDemote}
          disabled={busy}
        >
          <ArrowDownToLine /> {t("today.card.actions.demote")}
        </Button>
      </footer>
    </article>
  );
}

/** A compact row for "advancing" and "dormant" projects, with the one-click promote. */
export function TodayProjectRow({
  project, detail, onPromote, canPromote, busy, refusal,
}: {
  project: ProjectListItem;
  /** The measured fact the row states about the project. */
  detail: string;
  onPromote: () => void;
  /**
   * False when the server reported no focus tier: the endpoint the button would call
   * does not exist there, and its 404 would read as "project not registered". No button
   * rather than a disabled one — the heading already says why nothing can be promoted.
   */
  canPromote: boolean;
  busy: boolean;
  /** The refusal sentence for the last attempt on this row, if there was one. */
  refusal: string | null;
}) {
  const { t } = useI18n();
  return (
    <li className="flex flex-wrap items-center gap-3 border-b px-4 py-2.5 last:border-b-0">
      <Link
        href={`/workspace?project=${encodeURIComponent(project.project_id)}`}
        className="min-w-0 flex-1 hover:underline"
      >
        <span className="block truncate text-sm font-medium" title={project.project_id}>{project.name}</span>
        <span className="block truncate text-xs text-muted-foreground">{detail}</span>
      </Link>
      {refusal && (
        <span role="status" className="basis-full text-xs text-amber-700 dark:text-amber-300 sm:basis-auto">
          {refusal}
        </span>
      )}
      {canPromote && (
        <Button variant="outline" size="sm" onClick={onPromote} disabled={busy}>
          {t("today.advancing.promote")} <ArrowRight />
        </Button>
      )}
    </li>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline gap-1.5">
      <dt className="shrink-0">{label}</dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

function Row({ label, emphasis = false, children }: { label: string; emphasis?: boolean; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-[4.5rem_1fr] gap-x-3">
      <dt className={cn("text-xs uppercase tracking-wider text-muted-foreground", emphasis && "font-semibold")}>
        {label}
      </dt>
      <dd className="min-w-0">{children}</dd>
    </div>
  );
}

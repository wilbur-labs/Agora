"use client";

/**
 * The project's plan, in the page: how many steps, which one it is on, what each is
 * waiting for, and the way into the one to work on now.
 *
 * It sits above the project conversation rather than replacing it — the conversation is
 * where a goal gets settled and where new ideas become cards (CR-62, CR-63), and the
 * plan is what those produce. Seeing the whole plan was the thing the workbench could
 * not do: the order existed in the data from the moment a kickoff was adopted, and the
 * only way to read it was to open one task at a time.
 *
 * Every judgment is in `lib/task-plan.ts` (components cannot be tested here — CR-18).
 * This file renders those verdicts and adds nothing of its own. Two things it must never
 * do, both of which would make it lie:
 *
 *   - **Draw a bar for a step's progress.** There is no source (`PlanProgress`); the
 *     cell says so, and says why on hover.
 *   - **Dispatch a run.** "Continue" opens the step, where the seed prompt is shown and
 *     editable before anything is sent (`continue-action.tsx`). A table that fired a
 *     run would be sending a prompt nobody read.
 */
import { ArrowRight, CircleCheck, CircleDashed, CircleSlash, Loader2 } from "lucide-react";

import { cn } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";
import { CONTINUE_BLOCK_KEY } from "@/lib/continue-run";
import type { PlanHeadline, PlanRow, PlanWaiting } from "@/lib/task-plan";

function WaitingCell({ waiting }: { waiting: PlanWaiting }) {
  const { t } = useI18n();
  if (waiting.kind === "none") {
    return <span className="text-muted-foreground">{t("workspace.plan.waiting.none")}</span>;
  }
  if (waiting.kind === "unknown") {
    // Not an empty cell and not a zero: the read could not vouch for this step.
    return (
      <span className="text-amber-700 dark:text-amber-400">
        {t("workspace.plan.waiting.unknown")}
      </span>
    );
  }
  return (
    <span className="font-medium text-amber-700 dark:text-amber-400">
      {/* "at least" when the read behind it was capped or failed: the items are real,
          the total is not. */}
      {t(waiting.exact ? "workspace.plan.waiting.open" : "workspace.plan.waiting.openAtLeast",
        { count: waiting.count })}
      {" · "}
      {waiting.kinds.map((kind) => t(`workspace.plan.waiting.kinds.${kind}`)).join(" / ")}
    </span>
  );
}

function StandingIcon({ row }: { row: PlanRow }) {
  if (row.running) return <Loader2 className="size-3.5 shrink-0 animate-spin text-emerald-500" />;
  if (row.standing === "done") return <CircleCheck className="size-3.5 shrink-0 text-emerald-500" />;
  if (row.standing === "abandoned") return <CircleSlash className="size-3.5 shrink-0 text-muted-foreground" />;
  return <CircleDashed className="size-3.5 shrink-0 text-muted-foreground/60" />;
}

export interface TaskPlanProps {
  rows: PlanRow[];
  headline: PlanHeadline;
  /** Opens a step. The one way in — see the note on dispatching above. */
  onOpenStep: (taskId: string) => void;
}

export function TaskPlan({ rows, headline, onOpenStep }: TaskPlanProps) {
  const { t, te } = useI18n();

  return (
    <section className="w-full min-w-0 shrink-0 border-b bg-background px-4 py-4">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <h2 className="text-sm font-semibold">{t("workspace.plan.title")}</h2>
        {headline.total > 0 && (
          <>
            <span className="text-[12px] text-muted-foreground">
              {t(
                !headline.totalKnown
                  ? "workspace.plan.headlineNotWhole"
                  : headline.abandoned > 0
                    ? "workspace.plan.headlineWithAbandoned"
                    : "workspace.plan.headline",
                {
                  total: headline.total,
                  done: headline.done,
                  running: headline.running,
                  abandoned: headline.abandoned,
                },
              )}
            </span>
            {headline.percent !== null && (
              <span
                className="text-[12px] font-medium"
                title={t("workspace.plan.percentRule")}
              >
                {t("workspace.plan.percent", { percent: headline.percent })}
              </span>
            )}
          </>
        )}
      </div>
      <p className="mt-0.5 text-[11px] leading-snug text-muted-foreground">
        {t("workspace.plan.subtitle")}
      </p>

      {/* The headline counts are true even when a gate could not be read; what is not
          true is reading them as the whole picture of what needs the owner. */}
      {/* The read did not cover the whole board: what is shown is a part of the plan, and
          the line above says "at least" rather than a total. */}
      {!headline.totalKnown && (
        <p className="mt-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[11px] leading-snug text-amber-700 dark:text-amber-400">
          {t("workspace.plan.totalNotKnown")}
        </p>
      )}

      {!headline.waitingKnown && rows.length > 0 && (
        <p className="mt-2 rounded-lg border border-amber-500/40 bg-amber-500/10 px-3 py-2 text-[11px] leading-snug text-amber-700 dark:text-amber-400">
          {t("workspace.plan.waitingNotKnown")}
        </p>
      )}

      {rows.length === 0 ? (
        // "This project has no tasks yet" is a claim, so only a read that covered the
        // whole board may make it. Otherwise the rows are simply not here, and the
        // notice above already says the read did not reach them.
        <p className="mt-3 text-[12px] leading-relaxed text-muted-foreground">
          {t(headline.totalKnown ? "workspace.plan.empty" : "workspace.plan.emptyUnknown")}
        </p>
      ) : (
        <div className="mt-3 w-full min-w-0 overflow-x-auto">
          <table className="w-full min-w-[42rem] border-collapse text-left text-[12px]">
            <thead>
              <tr className="border-b text-[10px] uppercase tracking-wide text-muted-foreground">
                <th scope="col" className="w-14 py-1.5 pr-2 font-medium">{t("workspace.plan.columns.step")}</th>
                <th scope="col" className="py-1.5 pr-2 font-medium">{t("workspace.plan.columns.task")}</th>
                <th scope="col" className="w-24 py-1.5 pr-2 font-medium">{t("workspace.plan.columns.state")}</th>
                <th scope="col" className="w-24 py-1.5 pr-2 font-medium">{t("workspace.plan.columns.progress")}</th>
                <th scope="col" className="w-44 py-1.5 pr-2 font-medium">{t("workspace.plan.columns.waiting")}</th>
                <th scope="col" className="w-28 py-1.5 font-medium">{t("workspace.plan.columns.continue")}</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.task_id}
                  className={cn(
                    "border-b last:border-b-0",
                    row.standing !== "open" && "text-muted-foreground",
                  )}
                >
                  <td className="py-2 pr-2 align-top tabular-nums">
                    {/* "2/2" names a last step. When the read did not cover the board
                        there is no last step to name, and printing one contradicts the
                        "at least N" the headline just said. */}
                    <span
                      title={t(
                        headline.totalKnown ? "workspace.plan.step" : "workspace.plan.stepUnknownTotal",
                        { step: row.step, total: headline.total },
                      )}
                    >
                      {headline.totalKnown
                        ? t("workspace.plan.stepShort", { step: row.step, total: headline.total })
                        : row.step}
                    </span>
                  </td>
                  <td className="py-2 pr-2 align-top">
                    <button
                      type="button"
                      onClick={() => onOpenStep(row.task_id)}
                      className="flex w-full items-start gap-1.5 text-left hover:underline"
                    >
                      <StandingIcon row={row} />
                      <span className="min-w-0 break-words">
                        <span className="font-medium">{row.title}</span>
                        <span className="ml-1.5 text-[10px] uppercase tracking-wide text-muted-foreground">
                          {row.kind}
                        </span>
                      </span>
                    </button>
                  </td>
                  <td className="py-2 pr-2 align-top capitalize">{te("taskState", row.state)}</td>
                  <td className="py-2 pr-2 align-top">
                    {/* No bar. The reason is a sentence, not a shrug. */}
                    <span
                      className="text-muted-foreground underline decoration-dotted underline-offset-2"
                      title={t("workspace.plan.progressUnreadableWhy")}
                    >
                      {t(`workspace.plan.progressUnreadable.${row.progress.reason}`)}
                    </span>
                  </td>
                  <td className="py-2 pr-2 align-top">
                    <WaitingCell waiting={row.waiting} />
                  </td>
                  <td className="py-2 align-top">
                    {row.decision.ready ? (
                      <button
                        type="button"
                        onClick={() => onOpenStep(row.task_id)}
                        className="inline-flex items-center gap-1 rounded-lg bg-primary px-2 py-1 text-[11px] font-medium text-primary-foreground hover:bg-primary/90"
                      >
                        {t("workspace.plan.continue")}
                        <ArrowRight className="size-3" />
                      </button>
                    ) : (
                      // Why not, in the words the workbench already uses for it — one
                      // decision, one vocabulary (the module owns it, this does not).
                      <span className="text-[11px] leading-snug text-muted-foreground">
                        {row.decision.block ? t(CONTINUE_BLOCK_KEY[row.decision.block]) : null}
                      </span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

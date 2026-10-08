"use client";

/**
 * The centre column's head when a step is open: **every** step as a card, the open one
 * marked, and switching between them right here.
 *
 * Straight from the design's `renderTask()`, which draws the whole list rather than the
 * one task you clicked. The reason is the thing this workbench kept getting wrong: a
 * step is not a destination. You arrive at one and immediately want to know where it sits
 * among the others, and the answer has to be in front of you, not behind a "back".
 *
 * It does not replace the step's own detail below it — the design's version of that is a
 * run block, and the real one carries more (deliverables, gates, the session). This is
 * the list the detail was missing.
 */
import { cn } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";
import type { PlanRow } from "@/lib/task-plan";

export function TaskCards({
  rows, selectedTaskId, onSelectStep,
}: {
  rows: PlanRow[];
  selectedTaskId: string | null;
  onSelectStep: (taskId: string) => void;
}) {
  const { t, te } = useI18n();
  if (rows.length === 0) return null;

  return (
    <div className="grid shrink-0 gap-2 border-b px-4 py-3">
      {rows.map((row) => {
        const open = row.task_id === selectedTaskId;
        return (
          <button
            key={row.task_id}
            type="button"
            onClick={() => onSelectStep(row.task_id)}
            aria-current={open ? "true" : undefined}
            className={cn(
              "flex min-w-0 items-center gap-2.5 rounded-lg border px-3 py-2 text-left text-[0.875rem] transition-colors",
              open ? "border-primary bg-primary/5" : "bg-muted/30 hover:border-foreground/30",
            )}
          >
            <span className="shrink-0 tabular-nums text-[0.75rem] text-muted-foreground">{row.step}</span>
            <span className="shrink-0 rounded border px-1 text-[0.6875rem] uppercase tracking-wide text-muted-foreground">
              {row.kind}
            </span>
            <span className="min-w-0 flex-1">
              <span className={cn("block truncate", open && "font-medium")}>{row.title}</span>
            </span>
            {row.waiting.kind === "open" && (
              <span className="shrink-0 rounded-full border border-amber-500/40 bg-amber-500/10 px-2 py-0.5 text-[0.6875rem] text-amber-700 dark:text-amber-400">
                {t("workspace.side.needsYou")}
              </span>
            )}
            <span className="shrink-0 text-[0.75rem] capitalize text-muted-foreground">
              {te("taskState", row.state)}
            </span>
          </button>
        );
      })}
    </div>
  );
}

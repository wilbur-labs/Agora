"use client";

/**
 * The workbench's right-hand column, as the design draws it: one column, always there,
 * whatever the centre is showing.
 *
 * Three blocks, in this order (mockup v3.4, `renderSide`):
 *
 *   1. **目标草稿 · 边聊边长** — the kickoff draft, which grows a field at a time while
 *      the goal is being settled. Its state belongs to the conversation, so the chat
 *      hands it up rather than drawing it: an aside owned by the chat disappears the
 *      moment the centre switches to a step, and this column must not.
 *   2. **开发顺序 · N 个** — the adopted plan. Clicking one **selects it in place**; it
 *      is not a link, and nothing here navigates. That was the whole complaint about the
 *      version this replaces: a table that threw you onto another page and left you to
 *      find your way back.
 *   3. **这一步会写什么** — what has actually been written into the project, and what has
 *      not. Before adoption it says nothing has been; after, it names what was.
 *
 * What it must not do: claim a count it cannot support. The plan's headline already
 * carries that verdict (`PlanHeadline.totalKnown`), and the heading repeats it rather
 * than printing a number over an incomplete read.
 */
import { cn } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";
import type { PlanHeadline, PlanRow } from "@/lib/task-plan";

export interface ProjectSideProps {
  rows: PlanRow[];
  headline: PlanHeadline;
  /** The step the centre is showing, if it is showing one. */
  selectedTaskId: string | null;
  /** Selects a step **in this window**. Never a navigation. */
  onSelectStep: (taskId: string) => void;
  /**
   * The draft block, lifted out of the conversation so it survives a step being open.
   * Takes the order as a slot because the design puts the order *between* the draft and
   * "what this writes", and those two are one component — asking for a slot is how the
   * order lands there without a second copy of a block that already exists.
   */
  draft: (order: React.ReactNode) => React.ReactNode;
  /**
   * Whether the centre is showing a step. The design's `renderTask` gives this column
   * to the step then — 任务 / Run, then 目标 — and draws no order, because the order is
   * the card list in the centre. The step's blocks are the step's own (their reads live
   * with it), so the column only offers the element they render into.
   */
  taskOpen: boolean;
  hostRef: (el: HTMLElement | null) => void;
}

export function ProjectSide({
  rows, headline, selectedTaskId, onSelectStep, draft, taskOpen, hostRef,
}: ProjectSideProps) {
  const { t } = useI18n();

  // `hidden lg:flex`, and never `w-full`. The shell is a horizontal flex row that does
  // not wrap, so a full-width, non-shrinking column here does not stack below the centre
  // — it takes the whole row, pushes the centre out of view, and the row's
  // `overflow-hidden` clips what is left. Below `lg` the column is dropped instead.
  const order = rows.length === 0 ? null : (
    <section className="min-w-0">
      <h5 className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {/* The count is only a count when the read covered the board. */}
        {t(headline.totalKnown ? "workspace.side.order" : "workspace.side.orderNotWhole",
          { count: headline.total })}
      </h5>
      <ol className="grid gap-1.5 rounded-lg border p-3">
        {rows.map((row) => (
          <li key={row.task_id} className="flex min-w-0 items-baseline gap-2 text-[12px]">
            <span className="shrink-0 tabular-nums text-muted-foreground">{row.step}</span>
            <span className="shrink-0 rounded border px-1 text-[10px] uppercase tracking-wide text-muted-foreground">
              {row.kind}
            </span>
            <button
              type="button"
              onClick={() => onSelectStep(row.task_id)}
              className={cn(
                "min-w-0 flex-1 text-left underline decoration-dotted underline-offset-2 hover:text-foreground",
                row.task_id === selectedTaskId ? "font-medium text-foreground" : "text-muted-foreground",
              )}
            >
              {row.title}
            </button>
            {row.waiting.kind === "open" && (
              <span className="shrink-0 text-[10px] text-amber-700 dark:text-amber-400">
                {t("workspace.side.needsYou")}
              </span>
            )}
            {row.running && (
              <span className="shrink-0 text-[10px] text-emerald-600 dark:text-emerald-400">
                {t("workspace.plan.running")}
              </span>
            )}
          </li>
        ))}
      </ol>
    </section>
  );

  return (
    <aside className="hidden h-full min-h-0 w-80 min-w-0 shrink-0 flex-col gap-4 overflow-y-auto border-l px-4 py-4 lg:flex">
      {taskOpen
        ? <div ref={hostRef} className="flex min-w-0 flex-col gap-4" />
        : draft(order)}
    </aside>
  );
}

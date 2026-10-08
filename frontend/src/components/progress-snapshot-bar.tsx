"use client";

// The project progress bar: two segments, one percentage, and several ways to say
// "there is no number here".
//
// The decision it renders (and why it no longer reads `verified_pct`) is recorded in
// lib/progress-snapshot.ts, which also owns every judgement this component makes.
// Nothing is derived here: the component receives one `ProgressBar` and draws it, so
// the rule "an unknown is never rendered as 0%" is enforced by a type rather than by
// this file remembering to check.

import { cn } from "@/lib/utils";
import { useI18n } from "@/lib/i18n";
import { PROGRESS_BAR_KEY, type ProgressBar } from "@/lib/progress-snapshot";

/**
 * `source` is the path the backend consulted, used only in the "no snapshot"
 * sentence so it names where one would be read from. `compact` is the inline form
 * used on the workspace strip; the default is the block form used in the cockpit.
 */
export function ProgressSnapshotBar(
  { bar, source, compact = false, className }:
  { bar: ProgressBar; source?: string | null; compact?: boolean; className?: string },
) {
  const { t } = useI18n();

  // Loading keeps the dash here, unlike the outcome header: this is a labelled
  // figure, and a dash next to "Progress" reads as "not in yet" rather than as a
  // value. It is also never mistaken for an absence, because the label stays.
  if (bar.kind === "loading") {
    return <span className={cn("text-xs text-muted-foreground", className)}>—</span>;
  }

  if (bar.kind !== "counts" && bar.kind !== "countsDoneOnly") {
    // Empty of numbers, deliberately. The sentence differs by reason because "this
    // project keeps no snapshot", "the snapshot tracks nothing yet", "it uses marks
    // this reader does not count", "it mixes two conventions", "it could not be read"
    // and "this server does not report one" are different facts, and none of them is
    // a progress figure.
    const key = PROGRESS_BAR_KEY[bar.because];
    return (
      <span className={cn("text-xs text-muted-foreground", className)}>
        {t(key, { source: source ?? "" })}
      </span>
    );
  }

  // Each variant gets its own sentence, because the two conventions do not carry the
  // same information: a checkbox snapshot knows what is done and what is not, and
  // knows nothing about what is in flight. Saying "to do" for its remainder would
  // turn an unknown into a claim — the one thing this bar exists not to do.
  const counts = bar.kind === "counts"
    ? t("common.progress.counts", { done: bar.done, inProgress: bar.inProgress, todo: bar.todo })
    : t("common.progress.countsDoneOnly", { done: bar.done, notDone: bar.notDone });
  return (
    <span
      className={cn(compact ? "inline-flex items-center gap-2" : "block", className)}
      title={counts}
    >
      {/* The percentage reports done only — an in-progress item is not half credit. */}
      <span className={cn("font-semibold tabular-nums", compact ? "text-xs" : "text-2xl")}>
        {bar.donePct}%
      </span>
      <span
        className={cn(
          "overflow-hidden rounded-full bg-muted",
          compact ? "inline-flex h-1.5 w-24 align-middle" : "mt-2 flex h-1.5 w-full",
        )}
        role="img"
        aria-label={counts}
      >
        {/* Solid: done. Translucent: in progress. Two segments, never summed into one
            number — and on a checkbox snapshot the second segment is not drawn at
            all, because there is no in-progress count to draw it from. A zero-width
            segment would be this component inventing the zero the payload refused. */}
        <span className="h-full bg-emerald-500" style={{ width: `${bar.donePct}%` }} />
        {bar.kind === "counts" && (
          <span className="h-full bg-emerald-500/40" style={{ width: `${bar.inProgressPct}%` }} />
        )}
      </span>
      {!compact && <span className="mt-1.5 block text-[0.75rem] text-muted-foreground">{counts}</span>}
    </span>
  );
}

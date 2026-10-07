"use client";

/**
 * "Your task records already say what this project is for" (CR-35). Shown while a
 * project has no goal: each goal the workspaces' task records hold for this repository,
 * with where it came from, and one click to make it the project's goal. The click is the
 * person's confirmation — the text is shown in full before it — and it goes through the
 * same human-only charter route as the charter editor.
 */
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import { fetchProjectDashboard, listRecordedGoals, setProjectCharter, type RecordedGoals as RecordedGoalsPayload } from "@/lib/control-plane";
import { adoptableGoal, GOAL_MAX_CHARS, recordedGoalViewOf, recordLabelOf } from "@/lib/recorded-goals";
import { useI18n } from "@/lib/i18n";

export function RecordedGoals({ projectId, onAdopted }: { projectId: string; onAdopted: () => void }) {
  const { t } = useI18n();
  const [read, setRead] = useState<RecordedGoalsPayload | "failed" | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    // Mounted per project (keyed by it at the call site), so no reset is needed here.
    let alive = true;
    listRecordedGoals(projectId)
      .then((data) => { if (alive) setRead(data); })
      .catch(() => { if (alive) setRead("failed"); });
    return () => { alive = false; };
  }, [projectId]);

  if (read === null) return null;
  if (read === "failed") {
    return <p className="text-xs text-amber-700 dark:text-amber-400">{t("projectChat.recordedGoals.failed")}</p>;
  }
  const view = recordedGoalViewOf(read.goals);
  if (view.withGoal.length === 0 && view.withoutGoal.length === 0 && read.unreadable.length === 0) return null;

  async function adopt(goal: string) {
    setBusy(true); setError(null);
    try {
      // The charter route replaces all three fields: keep whatever non-goals and success
      // criteria the project already has, read fresh rather than from a render ago (review).
      const { charter } = await fetchProjectDashboard(projectId);
      await setProjectCharter(projectId, {
        goal,
        non_goals: charter?.non_goals ?? [],
        success_criteria: charter?.success_criteria ?? [],
      });
      onAdopted();
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <section className="space-y-2 rounded-lg border border-primary/30 bg-primary/5 p-3 text-sm">
      <p className="font-medium">{t(view.withGoal.length > 0 ? "projectChat.recordedGoals.title" : "projectChat.recordedGoals.noneTitle")}</p>
      {view.withGoal.map((g) => (
        <div key={`${g.source}/${g.task}`} className="space-y-1.5 rounded-md border bg-background p-2.5">
          <p className="whitespace-pre-wrap leading-relaxed">{g.goal}</p>
          <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
            <span>{t("projectChat.recordedGoals.from", { record: recordLabelOf(g), file: g.found_in ?? "" })}</span>
            {adoptableGoal(g) ? (
              <Button size="sm" className="ml-auto" disabled={busy} onClick={() => void adopt((g.goal as string).trim())}>
                {t("projectChat.recordedGoals.adopt")}
              </Button>
            ) : (
              <span className="ml-auto text-amber-700 dark:text-amber-400">
                {t("projectChat.recordedGoals.tooLong", { length: (g.goal ?? "").trim().length, max: GOAL_MAX_CHARS })}
              </span>
            )}
          </div>
        </div>
      ))}
      {view.withoutGoal.length > 0 && (
        <p className="text-xs text-muted-foreground">
          {t("projectChat.recordedGoals.withoutGoal", { records: view.withoutGoal.map(recordLabelOf).join(", ") })}
        </p>
      )}
      {read.unreadable.length > 0 && (
        <p className="text-xs text-amber-700 dark:text-amber-400">
          {t("projectChat.recordedGoals.unreadable", { records: read.unreadable.map((u) => u.task || u.source).join(", ") })}
        </p>
      )}
      {view.withGoal.length > 0 && <p className="text-xs text-muted-foreground">{t("projectChat.recordedGoals.note")}</p>}
      {error && <p className="text-xs text-red-700 dark:text-red-400">{error}</p>}
    </section>
  );
}

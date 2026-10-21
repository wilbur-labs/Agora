"use client";

/**
 * How long a running turn's transcript has been unchanged (CR-12 ①). Display only, and
 * silent whenever the reads cannot say — see `runActivityOf`.
 */
import { runActivityOf, type Transcript } from "@/lib/conversation";
import { useI18n } from "@/lib/i18n";

export function ActivityNote({ transcript, className }: { transcript: Transcript | null | undefined; className?: string }) {
  const { t } = useI18n();
  const activity = runActivityOf(transcript ?? undefined);
  if (activity.kind === "none") return null;
  if (activity.kind === "capped") return <span className={className}>{t("workspace.stream.activity.capped")}</span>;
  const key = activity.stuck ? "stuck" : activity.minutes < 1 ? "recent" : "quiet";
  return (
    <span className={activity.stuck ? `font-medium text-amber-700 dark:text-amber-400 ${className ?? ""}` : className}>
      {t(`workspace.stream.activity.${key}`, { minutes: activity.minutes })}
    </span>
  );
}

"use client";

/** The usage bar at the bottom of every page (workspace CR-87): each agent's limits as
 *  its own official source last reported them. Polls once a minute; hidden until the
 *  page holds the terminal token, as the work routes need it. */
import { useEffect, useState } from "react";

import { useI18n } from "@/lib/i18n";
import { TerminalAuthRequired } from "@/lib/terminal";
import { cn } from "@/lib/utils";
import {
  USAGE_AGENTS,
  getUsage,
  unavailableKeyOf,
  untilText,
  usageLevel,
  type UsageAgent,
  type UsageReading,
  type UsageView,
} from "@/lib/usage";

const POLL_MS = 60_000;
const LABEL: Record<UsageAgent, string> = { claude: "Claude", codex: "Codex", kiro: "Kiro" };
const LEVEL_CLASS = {
  ok: "text-foreground",
  warn: "text-amber-700 dark:text-amber-400",
  high: "font-semibold text-red-700 dark:text-red-400",
  unknown: "text-muted-foreground",
} as const;

export function UsageBar() {
  const { t } = useI18n();
  const [view, setView] = useState<UsageView | null>(null);
  const [failed, setFailed] = useState(false);
  const [hidden, setHidden] = useState(false);
  const [now, setNow] = useState(() => Date.now() / 1000);

  useEffect(() => {
    let alive = true;
    const load = () => {
      getUsage()
        .then((next) => { if (alive) { setView(next); setFailed(false); setHidden(false); setNow(Date.now() / 1000); } })
        .catch((err) => {
          if (!alive) return;
          if (err instanceof TerminalAuthRequired) setHidden(true);
          else setFailed(true); // the last reading stays, marked as not refreshed
        });
    };
    load();
    const timer = window.setInterval(load, POLL_MS);
    return () => { alive = false; window.clearInterval(timer); };
  }, []);

  if (hidden) return null;
  return (
    <footer
      className="fixed inset-x-0 bottom-0 z-40 flex h-[var(--usage-bar)] items-center gap-4 overflow-x-auto whitespace-nowrap border-t bg-background/95 px-3 text-[0.7rem] text-muted-foreground backdrop-blur"
      aria-label={t("work.usage.label")}
    >
      {view === null ? (
        <span>{failed ? t("work.usage.loadFailed") : t("work.usage.loading")}</span>
      ) : (
        USAGE_AGENTS.map((agent) => <Cell key={agent} agent={agent} reading={view[agent]} now={now} />)
      )}
      {view !== null && failed && <span className="text-amber-700 dark:text-amber-400">{t("work.usage.stale")}</span>}
    </footer>
  );
}

function Cell({ agent, reading, now }: { agent: UsageAgent; reading: UsageReading; now: number }) {
  const { t } = useI18n();
  if (reading.state !== "ok") {
    const why = unavailableKeyOf(reading.reason);
    return (
      <span title={why.detail ?? undefined}>
        <span className="font-medium text-foreground">{LABEL[agent]}</span>{" "}
        {t(`work.usage.${why.key}`)}
      </span>
    );
  }
  const age = reading.read_at !== null ? Math.max(0, Math.round((now - reading.read_at) / 60)) : null;
  return (
    <span className="flex items-center gap-2">
      <span className="font-medium text-foreground">{LABEL[agent]}</span>
      {reading.windows.map((w) => {
        const until = untilText(w.resets_at, now);
        return (
          <span key={w.label} className={LEVEL_CLASS[usageLevel(w.used)]}
            title={until ? t("work.usage.resetsIn", { until }) : undefined}>
            {w.label} {w.used === null ? t("work.usage.reset") : `${Math.round(w.used)}%`}
            {until && w.used !== null && usageLevel(w.used) !== "ok" && (
              <span className="text-muted-foreground"> ({t("work.usage.resetsIn", { until })})</span>
            )}
          </span>
        );
      })}
      {reading.limit_reached && <span className={LEVEL_CLASS.high}>{t("work.usage.limited")}</span>}
      {typeof reading.reset_credits === "number" && reading.reset_credits > 0 && (
        <span>{t("work.usage.resetCredits", { count: reading.reset_credits })}</span>
      )}
      {agent === "claude" && age !== null && age >= 10 && (
        <span className={cn("text-muted-foreground")}>{t("work.usage.ago", { minutes: age })}</span>
      )}
    </span>
  );
}

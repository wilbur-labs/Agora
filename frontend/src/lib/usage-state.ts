/** The usage bar's data (workspace CR-87): how much of each agent's subscription limits
 *  is used. Pure; `usage.ts` reads it from the server, which only uses official paths.
 *  An agent whose usage could not be read says why — it is never shown as unused. */
export type UsageAgent = "claude" | "codex" | "kiro";

export interface UsageWindow {
  /** "5h", "7d", … */
  label: string;
  /** 0–100; null once the window has reset since it was recorded. */
  used: number | null;
  /** Unix seconds; null when not said. */
  resets_at: number | null;
  reset: boolean;
}

export type UsageReading =
  | {
      state: "ok";
      windows: UsageWindow[];
      /** When this was read or recorded, Unix seconds. */
      read_at: number | null;
      reset_credits?: number | null;
      limit_reached?: boolean;
      /** kiro: credits left of the month's limit. */
      credits_left?: number | null;
      credits_limit?: number | null;
    }
  | { state: "unavailable"; reason: string; seen_at?: number | null };

export type UsageView = Record<UsageAgent, UsageReading>;

export const USAGE_AGENTS: readonly UsageAgent[] = ["claude", "codex", "kiro"];

/** How loud a window is: from 80% it warns, from 95% it is about to stop. */
export function usageLevel(used: number | null): "ok" | "warn" | "high" | "unknown" {
  if (used === null) return "unknown";
  if (used >= 95) return "high";
  if (used >= 80) return "warn";
  return "ok";
}

/** What is left of a window, 0–100, rounded down so "0% left" is never shown while
 *  something is still left and "100%" only when nothing is used; null when not known. */
export function leftPercent(used: number | null): number | null {
  if (used === null) return null;
  const left = 100 - Math.min(100, Math.max(0, used));
  return left > 0 && left < 1 ? 1 : Math.floor(left);
}

/** "2h 05m" / "3d 4h" / "12m" until `at`, from `now` (both Unix seconds); null when not
 *  said or already past. */
export function untilText(at: number | null, now: number): string | null {
  if (at === null) return null;
  const seconds = Math.round(at - now);
  if (seconds <= 0) return null;
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ${String(minutes % 60).padStart(2, "0")}m`;
  return `${Math.floor(hours / 24)}d ${hours % 24}h`;
}

/** The i18n key under `work.usage.` for why a reading is unavailable. Unknown reasons
 *  are shown as they came, under a generic key. */
export function unavailableKeyOf(reason: string): { key: string; detail: string | null } {
  const known: Record<string, string> = {
    no_hook: "noHook",
    not_reported: "notReported",
    no_limits: "noLimits",
    unreadable: "unreadable",
    kiro_no_api: "kiroNoApi",
    "codex is not installed": "notInstalled",
    "kiro-cli is not installed": "notInstalled",
  };
  return reason in known ? { key: known[reason], detail: null } : { key: "failed", detail: reason };
}

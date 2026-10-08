"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { useTheme } from "next-themes";
import {
  FolderKanban,
  Languages,
  Moon,
  Network,
  Settings,
  Sun,
} from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { fetchHealth, listOpenAttention, type RuntimeCapabilities } from "@/lib/control-plane";
import { waitingTotalOf } from "@/lib/project-rail";
import { instanceBadgeOf, type AgoraHealth } from "@/lib/instance";
import { isProbeSuperseded, probeRuntimes, subscribeRuntimes } from "@/lib/runtime-probe";
import { SHELL_TABS, cliStripOf } from "@/lib/shell";
import { cn } from "@/lib/utils";
import { applyUiScale, DEFAULT_UI_SCALE, nextUiScale, readUiScale, type UiScale } from "@/lib/ui-scale";

// The app shell: mockup v2's top bar over every screen.
//
// Four tabs — the four screens of the mockup — and, on the right, the environment
// runs execute in and the three agent CLIs with their versions. The tab list itself is
// `SHELL_TABS` in lib/shell.ts, so the shell and its test read one list.
//
// What is *not* a tab, and why:
//
// - **Control Plane / Requirements / Workflows** were the top of the frozen AI-DLC
//   layer. adr/0001 froze those 45k lines and said the UI must not expose them; they
//   sat in the first-level navigation for eleven days anyway. The pages still answer at
//   their URLs — nothing was deleted — they are simply no longer presented as the
//   product.
// - **Runs** is not a place any more. A run is a turn inside a task's conversation
//   (adr/0007), so it is reached through the task it belongs to.
// - **Attention** is what Today is made of: the foot of Today is the cross-project
//   "卡住 / 需要你" list.
// - **The projects inventory** (/portfolio) and **Settings** are in the bar's right-hand
//   cluster, not the tab row. The mockup's top bar has exactly four tabs; the inventory
//   is named in Today's design note ("全部项目的库存视图另开「项目」页") and drawn nowhere,
//   so it gets a door on every screen without a fifth tab. It sat in the tab row for one
//   cut (W9's second) as Reach before polish — Today was still task rows then and an
//   entry that existed only on the rebuilt Today was one nobody could reach.
//
// The environment on the right is a label, not a picker: every run executes on this
// machine until W3-a lands. The CLI versions come from the one shared probe
// (lib/runtime-probe.ts) that Environments and Settings read too — it spawns the CLIs,
// so it is asked once and reused for a minute, and a Settings refresh reaches this bar
// through the same module rather than leaving two answers on one screen.

/** Where the inventory and Settings live on the bar; `label` matches pages' `active`. */
const UTILITIES = [
  { href: "/portfolio", key: "projects", label: "Portfolio", icon: FolderKanban },
  { href: "/settings", key: "settings", label: "Settings", icon: Settings },
] as const;

/** The bar. `active` is the stable English label of the screen rendering it. */
export function TopBar({ active }: { active: string }) {
  const { resolvedTheme, setTheme } = useTheme();
  const { locale, toggleLocale, t } = useI18n();
  const [runtimes, setRuntimes] = useState<RuntimeCapabilities | null>(null);
  const [probeFailed, setProbeFailed] = useState(false);
  const [health, setHealth] = useState<AgoraHealth | null | "failed">(null);

  useEffect(() => {
    let cancelled = false;
    fetchHealth()
      .then((data) => { if (!cancelled) setHealth(data); })
      .catch(() => { if (!cancelled) setHealth("failed"); });
    return () => { cancelled = true; };
  }, []);
  const badge = instanceBadgeOf(health);
  // CR-40: how many things wait for the person, across every project. null = unread.
  const [waiting, setWaiting] = useState<{ count: number; atLeast: boolean } | null | "failed">(null);
  useEffect(() => {
    // Re-read every 30 s and whenever the window comes back into focus: approvals arrive
    // and get answered while a page stays open, and a badge that kept its first count
    // would contradict the page under it (review).
    let cancelled = false;
    const read = () => {
      listOpenAttention()
        .then((page) => { if (!cancelled) setWaiting(waitingTotalOf(page)); })
        .catch(() => { if (!cancelled) setWaiting("failed"); });
    };
    read();
    const timer = window.setInterval(read, 30_000);
    window.addEventListener("focus", read);
    return () => { cancelled = true; window.clearInterval(timer); window.removeEventListener("focus", read); };
  }, []);

  useEffect(() => {
    let cancelled = false;
    // Every answer that lands while this bar is mounted — including one another screen
    // forced — so the bar and the page under it cannot say two things about one CLI.
    const unsubscribe = subscribeRuntimes((outcome) => {
      if (cancelled) return;
      if (outcome.ok) { setRuntimes(outcome.value); setProbeFailed(false); }
      else { setRuntimes(null); setProbeFailed(true); }
    });
    // The subscription carries every later outcome; this call only seeds the bar from
    // the cache or the ask in flight. An ask overtaken by a forced one says nothing here —
    // the newer one reaches the bar through the subscription.
    probeRuntimes()
      .then((data) => { if (!cancelled) { setRuntimes(data); setProbeFailed(false); } })
      .catch((error: unknown) => { if (!cancelled && !isProbeSuperseded(error)) { setRuntimes(null); setProbeFailed(true); } });
    return () => { cancelled = true; unsubscribe(); };
  }, []);

  const strip = cliStripOf(runtimes, probeFailed);

  return (
    <header className="flex shrink-0 flex-wrap items-center gap-x-4 gap-y-2 border-b bg-sidebar px-4 py-2">
      <Link href="/" className="flex items-center gap-2">
        <span className="grid size-8 place-items-center rounded-lg bg-primary text-primary-foreground"><Network className="size-4" /></span>
        <span className="text-sm font-bold">{t("common.brand.name")}</span>
      </Link>
      {badge.kind !== "pending" && (
        <span
          className={cn(
            "rounded-md px-2 py-0.5 font-mono text-xs",
            badge.kind === "stable"
              ? "bg-muted text-muted-foreground"
              : "bg-amber-100 font-semibold text-amber-900 dark:bg-amber-900/40 dark:text-amber-200",
          )}
          title={badge.kind === "failed" ? undefined : t(`common.shell.instance.${badge.kind}Title`)}
        >
          {badge.kind === "failed"
            ? t("common.shell.instance.failed")
            : t(`common.shell.instance.${badge.kind}`, { version: badge.version ?? "?" })}
        </span>
      )}

      <nav className="flex items-center gap-1 overflow-x-auto" aria-label={t("common.shell.tabs")}>
        {SHELL_TABS.map(({ href, key, label }) => (
          <Link
            key={href}
            href={href}
            className={cn(
              "shrink-0 rounded-lg px-3 py-1.5 text-sm transition-colors",
              active === label
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-accent hover:text-foreground",
            )}
          >
            {t(`common.nav.${key}`)}
          </Link>
        ))}
      </nav>

      <div className="ml-auto flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted-foreground">
        {/* The environment runs execute in. A label until W3-a, and it says so on hover. */}
        <span className="font-mono" title={t("common.shell.environmentTitle")}>
          {t("common.shell.environmentLocal")}
          {runtimes?.platform ? ` · ${runtimes.platform}` : ""}
        </span>

        {/* CR-40: what waits for the person, everywhere — a way into it, not a number to
            decode. Unread is said as such; a cut read is a floor. */}
        {waiting !== null && (
          <Link
            href="/attention"
            className={cn(
              "rounded-full px-2.5 py-0.5 text-sm font-semibold",
              waiting === "failed" ? "bg-muted text-muted-foreground"
                : waiting.count > 0 ? "bg-amber-500 text-white" : "bg-muted text-muted-foreground",
            )}
          >
            {waiting === "failed"
              ? t("common.shell.waitingUnknown")
              : t("common.shell.waiting", { count: `${waiting.count}${waiting.atLeast ? "+" : ""}` })}
          </Link>
        )}

        {/* The three CLIs, folded into one chip (CR-38: the full strip crowded the bar).
            Hover reads every line; a CLI that is missing or could not be probed turns the
            chip amber, so folding it hides no problem. */}
        <span
          className={cn("rounded-md px-1.5 py-0.5 font-mono",
            strip.kind === "failed" || (strip.kind !== "pending" && strip.lines.some(({ line }) => line.kind !== "version"))
              ? "bg-amber-100 text-amber-900 dark:bg-amber-900/40 dark:text-amber-200" : "")}
          aria-live="polite"
          title={strip.kind === "pending"
            ? t("common.shell.versions.pending")
            : strip.kind === "failed"
              ? t("common.shell.versions.failed")
              : strip.lines.map(({ adapter, line }) => `${adapter} ${line.kind === "version" ? line.version : t(`common.shell.versions.${line.kind}`)}`).join("\n")}
        >
          {strip.kind === "failed" || (strip.kind !== "pending" && strip.lines.some(({ line }) => line.kind !== "version"))
            ? t("common.shell.cliProblem") : t("common.shell.cliChip")}
        </span>

        <span className="flex items-center gap-0.5">
          {UTILITIES.map(({ href, key, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={cn(
                "rounded-lg p-2 hover:bg-accent hover:text-foreground",
                active === label ? "bg-primary text-primary-foreground hover:bg-primary hover:text-primary-foreground" : "text-muted-foreground",
              )}
              aria-label={t(`common.nav.${key}`)}
              title={t(`common.nav.${key}`)}
            >
              <Icon className="size-4" />
            </Link>
          ))}
          <TextSizeButton />
          <button
            className="rounded-lg p-2 text-muted-foreground hover:bg-accent hover:text-foreground"
            onClick={toggleLocale}
            aria-label={t("common.action.toggleLanguage")}
            title={locale === "zh" ? t("common.language.en") : t("common.language.zh")}
          >
            <Languages className="size-4" />
          </button>
          <button
            className="rounded-lg p-2 text-muted-foreground hover:bg-accent hover:text-foreground"
            onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
            aria-label={t("common.action.toggleTheme")}
          >
            <Sun className="hidden size-4 dark:block" /><Moon className="size-4 dark:hidden" />
          </button>
        </span>
      </div>
    </header>
  );
}

/** CR-37: one click per step through the three text sizes; the label says the current one. */
function TextSizeButton() {
  const { t } = useI18n();
  const [scale, setScale] = useState<UiScale>(DEFAULT_UI_SCALE);
  // eslint-disable-next-line react-hooks/set-state-in-effect -- read the stored choice once in the browser
  useEffect(() => { setScale(readUiScale()); }, []);
  return (
    <button
      className="rounded-lg px-2 py-1 text-sm font-semibold text-muted-foreground hover:bg-accent hover:text-foreground"
      onClick={() => { const next = nextUiScale(scale); applyUiScale(next); setScale(next); }}
      aria-label={t("common.textSize.label")}
      title={t("common.textSize.label")}
    >
      {t(`common.textSize.${scale}`)}
    </button>
  );
}

/**
 * The bar over a screen that scrolls as a page (Today, Environments, Skills, the
 * inventory, Settings). The workbench mounts `TopBar` itself: its columns manage their
 * own height and scrolling.
 */
export function DeliveryShell({ active, children }: { active: string; children: ReactNode }) {
  return (
    <div data-delivery-shell-root className="flex min-h-screen flex-col bg-muted/20">
      <TopBar active={active} />
      <main className="min-w-0 flex-1">{children}</main>
    </div>
  );
}

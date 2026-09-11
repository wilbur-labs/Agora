"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { useTheme } from "next-themes";
import {
  FolderKanban,
  Languages,
  Library,
  MessagesSquare,
  Moon,
  Network,
  Server,
  Settings,
  Sun,
  Sunrise,
} from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

// The four screens of mockup v2, plus the projects inventory and Settings.
//
// What left this list, and why (W8, tasks/agora/adr/0001 and adr/0007):
//
// - **Control Plane / Requirements / Workflows** were the top of the frozen
//   AI-DLC layer. adr/0001 froze those 45k lines and said the UI must not expose
//   them; they sat in the first-level navigation for eleven days anyway. The pages
//   still answer at their URLs — nothing was deleted — they are simply no longer
//   presented as the product.
// - **Runs** is not a place any more. A run is a turn inside a task's
//   conversation (adr/0007), so it is reached through the task it belongs to
//   rather than through a global list of process launches.
// - **Attention** is what Today is made of: what needs you, across every project,
//   on one screen. The mockup draws exactly that at the foot of Today ("卡住 / 需要你
//   跨全部项目"), so it stays out of here while Today grows into it.
//
// **Portfolio came back (W9).** It was taken out with Attention on the reasoning that
// Today would absorb both. Re-reading the mockup says otherwise, in as many words:
// "全部项目的库存视图另开「项目」页：Today 回答「今天推什么」，项目页回答「我都有什么」".
// Those are two different questions, and one screen answering both is what the three-tier
// Today exists to avoid. So the inventory is its own screen again.
//
// Its position here is provisional. The mockup's top bar has four tabs and this is not
// one of them — the projects screen is named in Today's design note, not drawn. Putting
// it in the first-level navigation now is Reach before polish: with Today still showing
// task rows, an entry that only exists on the rebuilt Today is an entry nobody can reach
// today. When Today lands, this moves to where the design puts it.
const links = [
  { href: "/", key: "today", label: "Today", icon: Sunrise },
  { href: "/portfolio", key: "projects", label: "Portfolio", icon: FolderKanban },
  { href: "/workspace", key: "workbench", label: "Workbench", icon: MessagesSquare },
  { href: "/environments", key: "environments", label: "Environments", icon: Server },
  { href: "/skills", key: "skills", label: "Skills", icon: Library },
  { href: "/settings", key: "settings", label: "Settings", icon: Settings },
] as const;

// `active` is matched against the stable English label so callers stay simple.
export function DeliveryShell({ active, children }: { active: string; children: ReactNode }) {
  const { resolvedTheme, setTheme } = useTheme();
  const { locale, toggleLocale, t } = useI18n();

  return (
    <div data-delivery-shell-root className="min-h-screen bg-muted/20 lg:flex">
      <aside className="border-b bg-sidebar lg:sticky lg:top-0 lg:h-screen lg:w-64 lg:border-b-0 lg:border-r">
        <div className="flex items-center justify-between px-5 py-4 lg:block lg:py-6">
          <Link href="/" className="flex items-center gap-3">
            <span className="grid size-10 place-items-center rounded-xl bg-primary text-primary-foreground"><Network className="size-5" /></span>
            <span>
              <span className="block text-lg font-bold leading-tight">{t("common.brand.name")}</span>
              <span className="block text-xs text-muted-foreground">{t("common.brand.tagline")}</span>
            </span>
          </Link>
          <div className="flex items-center gap-1 lg:absolute lg:bottom-5 lg:left-5">
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
              <Sun className="hidden dark:block" /><Moon className="dark:hidden" />
            </button>
          </div>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-3 pb-3 lg:block lg:space-y-1 lg:pb-0">
          {links.map(({ href, key, label, icon: Icon }) => (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex shrink-0 items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
                active === label
                  ? "bg-primary text-primary-foreground"
                  : "text-muted-foreground hover:bg-accent hover:text-foreground",
              )}
            >
              <Icon className="size-4" />
              {t(`common.nav.${key}`)}
            </Link>
          ))}
        </nav>
      </aside>
      <main className="min-w-0 flex-1">{children}</main>
    </div>
  );
}

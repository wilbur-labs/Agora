"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { useTheme } from "next-themes";
import {
  BellRing,
  ClipboardCheck,
  Columns3,
  GitBranch,
  Languages,
  Moon,
  Network,
  Play,
  ShieldCheck,
  Sun,
} from "lucide-react";
import { useI18n } from "@/lib/i18n";
import { cn } from "@/lib/utils";

const links = [
  { href: "/portfolio", key: "portfolio", label: "Portfolio", icon: Columns3 },
  { href: "/control-plane", key: "controlPlane", label: "Control Plane", icon: ShieldCheck },
  { href: "/requirements", key: "requirements", label: "Requirements", icon: ClipboardCheck },
  { href: "/runs", key: "runs", label: "Runs", icon: Play },
  { href: "/workflows", key: "workflows", label: "Workflows", icon: GitBranch },
  { href: "/attention", key: "attention", label: "Attention", icon: BellRing },
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

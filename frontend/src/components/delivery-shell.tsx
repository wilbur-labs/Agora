"use client";

import type { ReactNode } from "react";
import Link from "next/link";
import { useTheme } from "next-themes";
import { BellRing, Bot, ClipboardCheck, Columns3, GitBranch, ListTodo, MessageSquare, Moon, Play, Settings, Sun } from "lucide-react";
import { cn } from "@/lib/utils";

const links = [
  { href: "/portfolio", label: "Portfolio", icon: Columns3 },
  { href: "/tasks", label: "Task Workbench", icon: ListTodo },
  { href: "/requirements", label: "Requirements", icon: ClipboardCheck },
  { href: "/runs", label: "Runs", icon: Play },
  { href: "/workflows", label: "Workflows", icon: GitBranch },
  { href: "/attention", label: "Attention", icon: BellRing },
  { href: "/chat", label: "Council Chat", icon: MessageSquare },
  { href: "/agents", label: "Agents", icon: Bot },
  { href: "/settings", label: "Settings", icon: Settings },
];

export function DeliveryShell({ active, children }: { active: string; children: ReactNode }) {
  const { resolvedTheme, setTheme } = useTheme();

  return (
    <div data-delivery-shell-root className="min-h-screen bg-muted/20 lg:flex">
      <aside className="border-b bg-sidebar lg:sticky lg:top-0 lg:h-screen lg:w-64 lg:border-b-0 lg:border-r">
        <div className="flex items-center justify-between px-5 py-4 lg:block lg:py-6">
          <Link href="/" className="flex items-center gap-3">
            <span className="grid size-10 place-items-center rounded-xl bg-primary text-xl text-primary-foreground">🏛</span>
            <span>
              <span className="block text-lg font-bold leading-tight">Agora</span>
              <span className="block text-xs text-muted-foreground">Delivery Control Plane</span>
            </span>
          </Link>
          <button
            className="rounded-lg p-2 text-muted-foreground hover:bg-accent hover:text-foreground lg:absolute lg:bottom-5 lg:left-5"
            onClick={() => setTheme(resolvedTheme === "dark" ? "light" : "dark")}
            aria-label="Toggle theme"
          >
            <Sun className="hidden dark:block" /><Moon className="dark:hidden" />
          </button>
        </div>
        <nav className="flex gap-1 overflow-x-auto px-3 pb-3 lg:block lg:space-y-1 lg:pb-0">
          {links.map(({ href, label, icon: Icon }) => (
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
              {label}
            </Link>
          ))}
        </nav>
      </aside>
      <main className="min-w-0 flex-1">{children}</main>
    </div>
  );
}
